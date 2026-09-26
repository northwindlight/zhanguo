# -*- coding: utf-8 -*-
"""**只读评级**：从联赛库的逐局流水里把每一份的强度重算出来（Glicko-2）。

    python -m rl.elo rl/runs/league_v1_mem.db rl/runs/league_v1_base.db
    python -m rl.elo a.db b.db --extra /root/h2h_games.jsonl      # 跨臂对局（连图）

★ 为什么要有它（用户 2026-09-26 问「知道 elo 吗」）：
  池子的判据一直是**裸胜率 + 打满 10 局**，而这两样在池子长大之后都会失效：
    · **裸胜率有混淆**：一份的胜率取决于它**抽到谁**（随手抽 ⇒ 强度混杂的池子里，
      弱份的胜率可以被"抽到更弱的对手"抬起来）；
    · **"打满 10 局"几乎凑不齐**：一份要被抽到 10 次，期望要 ≈`1.7N` 个 iter
      （N=200 时 ≈340 iter）⇒ 池子越大，退役规则越像死代码。
  Elo/评级**每局更新一次**，且把"对手有多强"折进去 ⇒ 强度估计与抽签运气解耦。

★ 为什么是 **Glicko-2** 而不是裸 Elo（用户拍：「先做只读评级（Glicko-2 带不确定度）」）：
  这个池子里**大多数成员只打过一两局**，裸 Elo 会把"只赢过一局"的份顶得很高；
  Glicko-2 自带 **RD（评级不确定度）** ⇒ 样本少的成员 RD 大，一眼看得出"别信这个数"
  —— 退役/筛选判据用带 RD 的才不会被噪声驱动。

★ 多人局怎么折算（3 国混战，这是**约定**不是无偏估计）：
  把一局拆成**两两比较**：赢家"赢过"每个输家（score 1），输家对赢家 score 0，
  **输家之间 score 0.5**（同输 = 谁也没压过谁）。这是部分序的标准分解。

★ 时段（rating period）：缺省**一局一个时段**（更新最快）；`--period iter` 则把
  同一个 iter 里的对局合成一个时段一起更新（Glicko-2 的经典用法）。
  ★ 两种都会给出**顺序相关**的结果（Glicko 本来就按时间推），所以别拿"打乱顺序"
  当等价性检验 —— 同一时段**内部**的顺序才不影响。

★ 只读：不写任何库、不碰训练。库是"只增不删"的账本，重算随时可以再来一遍。
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

# Glicko-2 的经典常数（Glickman 2013 的参考实现口径）
SCALE = 173.7178          # 评级 → 内部尺度
INIT_R = 1500.0           # 初始评级
INIT_RD = 350.0           # 初始不确定度（"我什么都不知道"）
MIN_RD = 30.0
TAU = 0.5                 # 波动率约束（越小越稳）
EPS = 1e-6
# ★ `c`：一个时段不打，RD 会"长回去"多少（Glickman 例子里取"100 个时段后回到 350"）
C = 63.2 / SCALE


@dataclass
class Game:
    """一局：参与者 + 赢家（`winners` 可能不止一个 —— 联盟胜利时同实体各国都算赢）。"""
    period: object
    mids: tuple[str, ...]
    winners: tuple[str, ...]


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi ** 2))


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _volatility(phi: float, v: float, delta: float, sigma: float) -> float:
    """Glicko-2 的波动率迭代（Illinois 算法）—— 照 Glickman 的伪码实现。"""
    a = math.log(sigma * sigma)
    f = lambda x: (math.exp(x) * (delta ** 2 - phi ** 2 - v - math.exp(x))
                   / (2.0 * (phi ** 2 + v + math.exp(x)) ** 2)
                   - (x - a) / (TAU ** 2))
    A = a
    if delta ** 2 > phi ** 2 + v:
        B = math.log(delta ** 2 - phi ** 2 - v)
    else:
        k = 1.0
        while f(a - k * TAU) < 0 and k < 100:
            k += 1
        B = a - k * TAU
    fa, fb = f(A), f(B)
    while abs(B - A) > EPS:
        Cc = A + (A - B) * fa / (fb - fa)
        fc = f(Cc)
        if fc * fb <= 0:
            A, fa = B, fb
        else:
            fa = fa / 2.0
        B, fb = Cc, fc
    return math.exp(A / 2.0)


def rate(games: list[Game], *, period_key=None):
    """跑到最后，返回 `{mid: (rating, rd, n_games)}`。

    `period_key(game) -> 时段标识`：**同一个时段里的对局一起更新**（Glicko-2 的经典用法）。
    缺省（`None`）⇒ 一局一个时段。
    """
    r: dict[str, float] = {}
    rd: dict[str, float] = {}
    sig: dict[str, float] = {}
    n: dict[str, int] = {}

    def ensure(m):
        r.setdefault(m, INIT_R)
        rd.setdefault(m, INIT_RD)
        sig.setdefault(m, 0.06)
        n.setdefault(m, 0)

    groups: list[list[Game]] = []
    if period_key is None:
        groups = [[g] for g in games]
    else:
        cur_key, cur = object(), []
        for g in games:
            k = period_key(g)
            if k != cur_key and cur:
                groups.append(cur)
                cur = []
            cur_key = k
            cur.append(g)
        if cur:
            groups.append(cur)

    for period in groups:
        for g in period:
            for m in g.mids:
                ensure(m)
        # ① 时段开始：所有参与者 RD 先"长回去"（长时间没打 ⇒ 更不确定）
        touched = {m for g in period for m in g.mids}
        for m in touched:
            rd[m] = min(math.sqrt(rd[m] ** 2 + C ** 2), INIT_RD)
        # ② 每人攒这一时段的"两两结果"
        rows: dict[str, list[tuple[float, float, float]]] = {m: [] for m in touched}
        for g in period:
            win = set(g.winners)
            for m in g.mids:
                mu, phi = (r[m] - INIT_R) / SCALE, rd[m] / SCALE
                for o in g.mids:
                    if o == m:
                        continue
                    mu_j, phi_j = (r[o] - INIT_R) / SCALE, rd[o] / SCALE
                    if m in win and o not in win:
                        s = 1.0
                    elif o in win and m not in win:
                        s = 0.0
                    else:                       # 都赢（联盟）或都输 ⇒ 谁也没压过谁
                        s = 0.5
                    rows[m].append((mu_j, phi_j, s))
        # ③ Glicko-2 更新（一个人一个人算，用**时段开始**的对手评级/不确定度）
        new: dict[str, tuple[float, float, float]] = {}
        for m in touched:
            mu, phi = (r[m] - INIT_R) / SCALE, rd[m] / SCALE
            res = rows[m]
            if not res:
                new[m] = (r[m], rd[m], sig[m])
                continue
            v = 1.0 / sum(_g(pj) ** 2 * _e(mu, mj, pj) * (1 - _e(mu, mj, pj))
                          for mj, pj, _ in res)
            dsum = sum(_g(pj) * (s - _e(mu, mj, pj)) for mj, pj, s in res)
            delta = v * dsum
            sigma2 = _volatility(phi, v, delta, sig[m])
            phi_star = math.sqrt(phi ** 2 + sigma2 ** 2)
            phi_new = 1.0 / math.sqrt(1.0 / phi_star ** 2 + 1.0 / v)
            mu_new = mu + phi_new ** 2 * dsum
            new[m] = (mu_new * SCALE + INIT_R, max(phi_new * SCALE, MIN_RD), sigma2)
        for m, (nr, nrd, ns) in new.items():
            r[m], rd[m], sig[m] = nr, nrd, ns
            n[m] += 1
    return {m: (r[m], rd[m], n[m]) for m in r}


# ------------------------------------------------------------------ 读流水
def games_from_db(path: str) -> list[Game]:
    """从联赛库里把每局重组出来。

    ★ 分组键：**`(worker, game)`**（`game` 是 2026-09-26 加的列，每局一个 id）。
      老行没有 `game` ⇒ 退回 `(iter, worker, ts)` —— ⚠ `ts` 只到**秒**，
      同一秒的两局会被并成 6 行（评级输入就静默错了）。所以：
      **只信 `game` 非空的行**，老行只在"该 (iter,worker,ts) 下的行数正好是国数"时才用。
    """
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    # ★★ **老库没有 `game` 列**（2026-09-26 才加的）—— 只读地打开它时**不会**触发
    #   迁移（迁移在 `League.__init__` 里，而这里是只读）⇒ 直接 `SELECT game` 会
    #   `no such column` 当场崩。⇒ 先看列在不在，不在就按 NULL 取（走老的退路）。
    cols = {r[1] for r in con.execute("PRAGMA table_info(results)")}
    sel = "mid,won,iter,worker,ts," + ("game" if "game" in cols else "NULL")
    rows = con.execute(f"SELECT {sel} FROM results").fetchall()
    con.close()
    buckets: dict[tuple, list[tuple[str, int]]] = {}
    legacy: dict[tuple, list[tuple[str, int]]] = {}
    for mid, won, it, worker, ts, game in rows:
        if game:
            buckets.setdefault((worker, game), []).append((mid, int(won)))
        else:
            legacy.setdefault((it, worker, ts), []).append((mid, int(won)))
    out = []
    for (worker, game), ms in buckets.items():
        out.append(Game(period=game, mids=tuple(sorted(m for m, _ in ms)),
                        winners=tuple(sorted(m for m, w in ms if w))))
    for (it, worker, ts), ms in legacy.items():
        if len(ms) < 2:                     # 只有一行 ⇒ 看不出这是几人局 ⇒ 弃用（别猜）
            continue
        out.append(Game(period=f"L{it}:{ts}", mids=tuple(sorted(m for m, _ in ms)),
                        winners=tuple(sorted(m for m, w in ms if w))))
    return out


def games_from_jsonl(path: str) -> list[Game]:
    """跨臂对局的流水（`rl/head2head.py --record` 写的 JSONL）。

    ★ 它的作用是**把两个池子连成一张比较图** —— 评级只在连通分量内有意义，
      各自算各自的池子两张表**不可比**（而 A/B 要的正是可比）。
    """
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        mids = tuple(sorted(d["mids"].values()))
        winners = tuple(sorted(v for v in d["mids"].values() if v == d.get("winner")))
        out.append(Game(period=f"X{d.get('i', len(out))}", mids=mids, winners=winners))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="只读 Glicko-2 评级（从联赛流水重算）")
    ap.add_argument("dbs", nargs="*", help="联赛库路径（可给多个）")
    ap.add_argument("--extra", default=None,
                    help="★ 跨臂对局流水（`head2head --record`）—— 给了才**两臂可比**")
    ap.add_argument("--period", choices=("game", "iter"), default="game")
    ap.add_argument("--top", type=int, default=8, help="每臂列前几名")
    ap.add_argument("--established-rd", type=float, default=110.0,
                    help="RD 小于这个值才算「打出来了」（其余只有参考价值）")
    a = ap.parse_args()

    per_arm = {}
    for db in a.dbs:
        per_arm[db] = games_from_db(db)
    extra = games_from_jsonl(a.extra) if a.extra else []

    all_games = []
    for db, gs in per_arm.items():
        all_games += gs
    all_games += extra

    def period_key(g):
        return g.period if a.period == "iter" else None

    res = rate(all_games, period_key=(period_key if a.period == "iter" else None))
    print(f"★ 评级（Glicko-2，初始 {INIT_R:.0f} / RD {INIT_RD:.0f}，"
          f"时段 = {a.period}，对局 {len(all_games)} 局"
          f"{'，**已并入跨臂对局**' if extra else '（**未连图：各臂表不可比**）'}）")
    # 按"谁属于哪一臂"分（跨臂流水的 mid 形如 `A2`/`B3`；库里的 mid 无前缀 ⇒ 归该库）
    for db, gs in per_arm.items():
        mids = {m for g in gs for m in g.mids}
        rows = [(m, *res[m]) for m in mids if m in res]
        rows.sort(key=lambda x: -x[1])
        est = [x for x in rows if x[2] < a.established_rd]
        print(f"\n── {db}：{len(rows)} 份（其中 {len(est)} 份 RD<{a.established_rd:.0f} = 打出来了）──")
        if est:
            print(f"   **已确立的评级**：均值 {sum(x[1] for x in est) / len(est):.0f}，"
                  f"最高 {est[0][1]:.0f}，最低 {est[-1][1]:.0f}")
        for m, r_, rd_, n_ in rows[:a.top]:
            flag = "" if rd_ < a.established_rd else "  ← RD 大，别信"
            print(f"   {m:<34} 评级 {r_:7.1f}  RD {rd_:5.1f}  对局 {n_:>3}{flag}")
        tail = [x for x in rows if x[2] < a.established_rd][-3:]
        if tail:
            print("   …垫底（已确立的）：")
            for m, r_, rd_, n_ in tail:
                print(f"   {m:<34} 评级 {r_:7.1f}  RD {rd_:5.1f}  对局 {n_:>3}")


if __name__ == "__main__":
    main()