#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**战斗噪声探针** —— DP 期望 vs 引擎实测：偏差率有多大，噪声盖不盖得住信号。

    用户 2026-09-30：「帮我做个测试，dp期望和实际战斗的偏差率，我在想，
    **骰子噪声会不会太大，导致模型学坏了**」

━━ 为什么不能靠已有那份对拍 ━━
`tests/test_combat_probs.py` 已经手工摆过 2v1 / 2v2 / L3城 / 带撤退 的局面与引擎比
（5% 以内）。那证明的是「**公式抄对了**」，证明不了这件事 ——

  · 手工摆的局面是**挑过的**：兵种少、格主固定、没有旁观势力、地形随手设的；
  · ★ 更关键：「偏差率」是个**统计量**，要的是「在**真打起来的那些仗**上，
    DP 说的数和实际打出来的数差多少」⇒ 样本必须是**真实对局里长出来的**。

⇒ 本探针：① 从真实对局里**抓每一场新开打的仗**（首轮快照），与 DP 逐场对拍；
          ② 把这场仗折成**打分器分**这一个标量，量出**单次采样的噪声 σ**，
             再和「这场仗的期望收益」比 ⇒ **信噪比**。

━━ 两个数各自回答什么 ━━
  · **TV**（DP 的分布 vs 引擎实测分布）—— 若**超出 MC 噪声底**，说明 DP **在说谎**
    （与引擎漂了），模型看到的是假情报；
  · **σ(Δscore) 与 符号翻转率** —— 若 σ 比期望收益还大，那么**拿一局的结果去评价
    一个动作**就是在抛硬币：差动作运气好被强化、好动作运气差被惩罚。
    ★ **这才是"学坏"的机制**，而"学得慢"只是它的弱化版。

━━ 口径（写死，别悄悄改） ━━
  · 抓的是**新开打的格**（上回合没交战、这回合交战）⇒ 快照正好是**首轮**，
    与 `assess`（从首轮打到定局）同起点；
  · MC 每次从**同一初始态**重打，**只跑 `_resolve_battles`**（不补员、不移动）
    —— 这正是 DP 建模的过程（一回合一轮，直到一方没有活敌人）；
  · 打分器用 `mask=None`（**全知**）：这里量的是**客观摆动**；混进"当时看不看得见"
    会多一个变量，那是另一个实验；
  · ★ 引擎文件**只猴补**（给 `_resolve_battles` 包一层），仓库里 `mp.py` 一个字节不改；
  · ★ 绝不写盘、绝不碰 `mp_save.json`。

    ★★ **必须用 `-m` 跑**（与 `rl/` 下所有入口一致）：
       `python rl/combat_noise_probe.py` 会把 **`rl/` 本身**塞进 `sys.path[0]`，
       于是 `rl/tokenize.py` **遮蔽标准库 `tokenize`** ⇒ numpy 在 import 阶段就炸
       （`ModuleNotFoundError: No module named 'game'` —— 报的是别处，看不出真因）。
       ★ 这条在 Pi 上没复现、在 ECS 上必现（numpy 版本差异决定 import 顺序）——
         正是"只在一边坏"的那类坑，写在这里省下一次重踩。

    跑法：
        python -m rl.combat_noise_probe --games 8 --size 10 --t-max 400
        python -m rl.combat_noise_probe --driver net --ckpt rl/runs/backup_20260929/sp3_iter375.pt
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp                                              # noqa: E402  引擎本体（只猴补）
from rl import combat_probs as CP                      # noqa: E402
from rl import evaluate as EV                          # noqa: E402
from rl import scoring as S                            # noqa: E402
from rl.sandbox import Sandbox                         # noqa: E402

BARBARIAN = CP.BARBARIAN
MAX_ROUNDS = 60           # MC 重打轮数上限（与 `tests/test_combat_probs.py` 同）
# ★ 闸门自检开关：把「每 trial 整世界重来」故意破坏回「只复原 armies+tiles」。
SABOTAGE = False


# ============================================================ ⓪ 原始伤害计量
# ★★ 为什么需要它（2026-09-30）：DP 的 `Odds.e_loss` 累加的是 `u[1] - nh`，
#   而 `nh` **不夹到 0**（`combat_probs.py` 里那句 `lost += u[1] - nh` 有显式注释）
#   ⇒ 它是**"承受的伤害总量"（含过量击杀）**，**不是"实际掉的血"**。
#   而 MC 只能看见幸存者的 hp ⇒ 量出来的是**夹过的**。
#   两个数不是同一个量 ⇒ 直接相减会把"定义差"当成"DP 说谎"。
#   ⇒ 让预言机也量原始伤害：`_spread(dmg, units)` 收到的 `dmg` 就是它。
#   ⚠ 撤退减伤那条分支**不走 `_spread`**（引擎内联了 divmod）⇒ 那种战斗
#     的原始伤害统计不全，`mc()` 会把 `has_retreat` 标出来、闸门跳过它。
_RAW: dict = {}


def _install_raw_hook():
    if getattr(mp.World, "_raw_hooked", False):
        return
    _orig = mp.World._spread

    def _spread_raw(dmg, units):
        if units:
            a0 = units[0]
            k = ((a0["x"], a0["y"]), a0["owner"])
            _RAW[k] = _RAW.get(k, 0) + dmg
        return _orig(dmg, units)

    mp.World._spread = staticmethod(_spread_raw)
    mp.World._raw_hooked = True


_install_raw_hook()


# ============================================================ ① 抓真实战斗
class Hook:
    """包一层 `World._resolve_battles`，**只记录**、不改行为。

    ★ 为什么猴补而不是在沙盒里加钩子：`_resolve_battles` 是引擎在 `resolve_turn`
      里调的，沙盒层看不到"这一格这回合**新**开打"——而"新开打"正是首轮的定义。
    ★ `_prev` 每局清空：否则上一局末尾还在交战的格，会让这一局第一次结算被
      当成"老仗"漏掉。
    """

    def __init__(self, limit: int = 10 ** 9):
        self.recs: list[dict] = []
        self.limit = limit
        self._prev: set = set()
        self._orig = mp.World._resolve_battles
        self._game = -1

    def new_game(self) -> None:
        self._game += 1
        self._prev = set()

    def install(self) -> None:
        def patched(world):
            now = set(CP.engaged_cells(world))
            for c in sorted(now - self._prev):
                if len(self.recs) >= self.limit:
                    continue
                b = CP.build(world, *c)
                if b is None:
                    continue
                self.recs.append({
                    "game": self._game, "cell": c,
                    "order": list(b.order), "attacker": sorted(b.attacker),
                    "owner": b.owner, "n_units": {F: len(b.init[F]) for F in b.order},
                    "world": copy.deepcopy(world),
                })
            self._prev = now
            return self._orig(world)
        mp.World._resolve_battles = patched


def _load_nets(path: str):
    """读备份权重（`_load_seed` 自己会校**形状指纹**，对不上直接 SystemExit）。"""
    from rl.model import build_model
    from rl import train as T
    from rl import vocab as V
    nets = {}
    for slot, sd in T._load_seed(path, mem_slots=V.M_SLOTS).items():
        net = build_model(mem_slots=V.M_SLOTS)
        net.load_state_dict(sd)
        net.eval()
        nets[int(slot)] = net
    return nets


def collect(args, hook: Hook) -> dict:
    nets = _load_nets(args.ckpt) if args.driver == "net" else None
    from rl import opponents as OPP
    stat = {"turns": [], "winner": []}
    for g in range(args.games):
        sb = Sandbox(seed=args.seed + g, size=args.size, t_max=args.t_max,
                     n_nations=2, halls_known=True, territory=True).reset()
        hook.new_game()
        t0 = time.time()
        if args.driver == "rule":
            win = sb.rollout()["winner"]
        else:
            from rl import train as T
            _nets = {n: nets[0] for n in sb.players}
            _opp = {n: OPP.make("random", k=8) for n in sb.players[1:]}
            _s, info = T.collect_episode(_nets, sb, rng=np.random.default_rng(args.seed + g),
                                         opponents=_opp, device="cpu")
            win = info["winner"]
        stat["turns"].append(sb.turn)
        stat["winner"].append(win)
        print(f"  [局 {g + 1}/{args.games}] seed={args.seed + g} 回合 {sb.turn} "
              f"胜方 {win} · 累计 {len(hook.recs)} 场战斗 · {time.time() - t0:.1f}s",
              flush=True)
    stat["games"] = args.games
    return stat


# ============================================================ ② 引擎 MC
def mc(rec: dict, trials: int, seed: int, sides: list[str]):
    """从**同一初始态**重打 `trials` 次（真引擎）。

    ★ 把**不在这一格**的军 `engaged` 关掉 —— 否则同回合其它新战场会被一起结算，
      样本就不干净了。关掉它对这一格**没有影响**：`_resolve_battles` 按格分组，
      `_enemies` 看的是宣战关系、`soak` 看的是地块，都不依赖别处的军队。

    ★★★ **每个 trial 必须整世界 `deepcopy` 重来**（2026-09-30 踩出来的）——
      我第一版只复原 `armies` + `tiles`，跑出一场 `TV = 0.997` 的假警：
      **攻下一格的「核心领地」会让失主亡国、战争当场结束**，而那个状态**不在**
      那两样里 ⇒ 从第 2 次起 `war_between(甲,乙)` 恒为 False ⇒ 引擎**一轮都不掷骰**、
      却照样写「全歼守军」（`_claim_winner` 只认 `_enemies` 为空）⇒
      500 次里 499 次"双方都没掉血"。**DP 是对的，是对拍台错了。**
      ⇒ 教训与 `feedback_tools_that_lie` 同一条：**预言机本身也要自证**。
      ⇒ `--sabotage` 把这条故意破坏回去，用来确认下面那道闸真的会响。
    """
    x, y = rec["cell"]
    bw = copy.deepcopy(rec["world"])
    for a in bw.armies:
        if (a["x"], a["y"]) != (x, y):
            a["engaged"] = False
    rng = random.Random(seed)              # ★ **跨 trial 共享**（每 trial 独立掷骰）
    s0 = {F: EV.score(bw, F) for F in sides}
    hp0 = {F: sum(a["hp"] for a in bw.armies
                  if (a["x"], a["y"]) == (x, y) and a["owner"] == F) for F in sides}
    # ★ 有军带撤退减伤 ⇒ 引擎走内联分支、不过 `_spread` ⇒ 原始伤害统计不全
    has_retreat = any(a.get("retreat_cover", 100) != 100
                      for a in bw.armies if (a["x"], a["y"]) == (x, y))
    occ: Counter = Counter()
    rounds: Counter = Counter()
    dsc = {F: np.empty(trials) for F in sides}
    loss = {F: np.empty(trials) for F in sides}       # **夹过**（实际掉的血）
    raw = {F: np.empty(trials) for F in sides}        # **未夹**（= DP 的 `e_loss` 口径）
    left = {F: np.empty(trials) for F in sides}       # 存活**支数**
    w = copy.deepcopy(bw) if SABOTAGE else None
    if w is not None:
        w.rng = rng
    for t in range(trials):
        if SABOTAGE:
            w.armies = copy.deepcopy(bw.armies)       # ← 故意破坏：只复原这两样
            w.tiles = copy.deepcopy(bw.tiles)
        else:
            w = copy.deepcopy(bw)
            w.rng = rng
        _RAW.clear()
        n = 0
        while n < MAX_ROUNDS:
            if not [a for a in w.armies if a.get("engaged") and a["owner"] != BARBARIAN]:
                break
            w._resolve_battles()
            n += 1
        alive = [a for a in w.armies if a["hp"] > 0 and (a["x"], a["y"]) == (x, y)]
        occ[frozenset(a["owner"] for a in alive)] += 1
        rounds[n] += 1
        for F in sides:
            mine = [a for a in alive if a["owner"] == F]
            left[F][t] = len(mine)
            loss[F][t] = hp0[F] - sum(a["hp"] for a in mine)
            raw[F][t] = _RAW.get(((x, y), F), 0)
            dsc[F][t] = EV.score(w, F) - s0[F]
    return {"occ": occ, "rounds": rounds, "dsc": dsc, "loss": loss, "raw": raw,
            "left": left, "hp0": hp0, "has_retreat": has_retreat}


# ============================================================ ③ DP
def tv_floor(b, o, trials: int) -> float:
    """**MC 噪声底** —— 假设 DP 完全正确，M 次抽样自己会产生多大的 TV。

    `E|p̂ − p| ≈ sqrt(2 p(1−p) / (πM))`（半正态），三条互斥出路各一份。
    ★ 必须打出来：**不给噪声底，TV 这个数就没有意义** —— 会把 MC 自己的抖动
      当成"DP 在说谎"（这个项目已经栽过好几次"判据坐在噪声里"）。
    """
    ps = []
    for F in (x for x in b.order if x != BARBARIAN):
        ps += [o.p_win.get(F, 0.0), o.p_lose.get(F, 0.0)]
    ps.append(o.p_draw)
    return float(0.5 * sum(np.sqrt(2 * p * (1 - p) / (np.pi * trials)) for p in ps))


# ============================================================ 主
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=8)
    ap.add_argument("--size", type=int, default=10)
    ap.add_argument("--t-max", type=int, default=200)
    ap.add_argument("--trials", type=int, default=3000)
    ap.add_argument("--seed", type=int, default=1234)
    ap.add_argument("--max-records", type=int, default=80)
    ap.add_argument("--driver", choices=("rule", "net"), default="rule")
    ap.add_argument("--ckpt", default="rl/runs/backup_20260929/sp3_iter375.pt")
    ap.add_argument("--json", default="")
    ap.add_argument("--analyze", default="",
                    help="只读一份 --json 明细重打汇总（不重跑 MC）")
    ap.add_argument("--sabotage", action="store_true",
                    help="故意破坏对拍台（闸门自检：必须能看见它响）")
    args = ap.parse_args()
    global SABOTAGE
    SABOTAGE = bool(args.sabotage)
    if args.analyze:
        blob = json.loads(Path(args.analyze).read_text(encoding="utf-8"))
        rows = blob["rows"]
        for r in rows:
            r.setdefault("_trials", blob["args"].get("trials", 1))
        print(f"读入 {args.analyze}：{len(rows)} 场")
        return 10 if report(rows) else 0

    print("=" * 100)
    print(f"战斗噪声探针 · games={args.games} size={args.size} t_max={args.t_max} "
          f"trials={args.trials} driver={args.driver} seed={args.seed}")
    print(f"奖励口径  赢{S.REWARD_WIN:+.1f} 输{S.REWARD_LOSS:+.1f} 平{S.REWARD_DRAW:+.1f}"
          f"   势函数 SHAPING={S.SHAPING}   时间项={S.WIN_TIME_BONUS}")
    print(f"打分器    W_TILE={S.W_TILE:g} W_ARMY={S.W_ARMY:g} W_HP={S.W_HP:g} "
          f"W_KILL={S.W_KILL:g} W_HALL={S.W_HALL:g} "
          f"REWARD_TANH_SCALE={S.REWARD_TANH_SCALE:g}")
    print("=" * 100)

    hook = Hook()
    hook.install()
    print("\n【① 抓真实战斗】")
    t0 = time.time()
    stat = collect(args, hook)
    recs = hook.recs
    print(f"  ⇒ {stat['games']} 局 · {len(recs)} 场新开打的仗 · "
          f"平均回合 {np.mean(stat['turns']):.0f} · 采样耗时 {time.time() - t0:.0f}s")
    if not recs:
        print("★ 一场仗都没抓到 —— 这套参数下双方没打起来（探针无效）")
        return 10
    if len(recs) > args.max_records:
        idx = sorted({int(i) for i in
                      np.linspace(0, len(recs) - 1, args.max_records).round()})
        recs = [recs[i] for i in idx]
    print(f"  ⇒ 抽 {len(recs)} 场进入对拍")

    print(f"\n【② 逐场：DP vs 引擎 MC（每场 {args.trials} 次）】")
    print("   TV=分布的半变差（0=完全一致）  TV底=完美DP的抽样噪声  "
          "  伤DP=DP 的 e_loss（未夹 0 的伤害）  伤MC=引擎实测同口径  血MC=实际掉的血")
    hdr = (f"{'#':>3} {'格':>7} {'攻':>4} {'兵':>4} {'减伤':>4} │ "
           f"{'p赢DP':>6} {'p赢MC':>6} {'p负DP':>6} {'p负MC':>6} {'同归':>5} │ "
           f"{'TV':>6} {'TV底':>6} │ {'伤DP':>6} {'伤MC':>6} {'血MC':>6} │ "
           f"{'轮DP':>5} {'轮MC':>5}")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for i, rec in enumerate(recs):
        w = rec["world"]
        b = CP.build(w, *rec["cell"])
        if b is None:
            continue
        o = CP.assess(b)
        sides = [F for F in b.order if F != BARBARIAN]
        if not sides:
            continue
        F0 = sides[0]
        m = mc(rec, args.trials, args.seed * 7919 + i, sides)
        n = args.trials
        en = set(b.enemies[F0])
        pw_dp, pl_dp = o.p_win.get(F0, 0.0), o.p_lose.get(F0, 0.0)
        pw_mc = sum(v for s, v in m["occ"].items() if F0 in s and not (en & s)) / n
        pl_mc = sum(v for s, v in m["occ"].items() if F0 not in s and (en & s)) / n
        tv = 0.5 * (abs(pw_mc - pw_dp) + abs(pl_mc - pl_dp)
                    + abs((1 - pw_mc - pl_mc) - (1 - pw_dp - pl_dp)))
        tvf = tv_floor(b, o, n)
        e_loss_dp = o.e_loss.get(F0, 0.0)
        e_loss_mc = float(m["loss"][F0].mean())
        e_raw_mc = float(m["raw"][F0].mean())
        sig = float(m["loss"][F0].std())
        e_r_dp = o.e_rounds
        e_r_mc = sum(k * v for k, v in m["rounds"].items()) / n
        rows.append({
            "cell": list(rec["cell"]), "order": list(b.order),
            "attacker": sorted(b.attacker), "owner": b.owner,
            "n_units": {F: len(b.init[F]) for F in b.order},
            "soak": {F: int(b.soak[F]) for F in b.order},
            "p_win_dp": pw_dp, "p_win_mc": pw_mc,
            "p_lose_dp": pl_dp, "p_lose_mc": pl_mc,
            "p_draw_dp": o.p_draw, "p_draw_mc": m["occ"].get(frozenset(), 0) / n,
            "tv": tv, "tv_floor": tvf,
            "i": i, "has_retreat": bool(m["has_retreat"]),
            "e_loss_dp": e_loss_dp,                # ★ DP 口径：**未夹 0 的伤害总量**
            "e_raw_mc": float(m["raw"][F0].mean()), "sd_raw_mc": float(m["raw"][F0].std()),
            "e_loss_mc": e_loss_mc, "sd_loss_mc": sig,   # MC 口径：**实际掉的血**
            "e_rounds_dp": e_r_dp, "e_rounds_mc": e_r_mc,
            "truncated": o.truncated, "_trials": n,
            "per": {F: {"dsc": m["dsc"][F].tolist(), "left": m["left"][F].tolist(),
                        "p_win_dp": o.p_win.get(F, 0.0), "p_win_mc": float(
                            sum(v for s, v in m["occ"].items()
                                if F in s and not (set(b.enemies[F]) & s)) / n),
                        "n_units": len(b.init[F]), "hp0": m["hp0"][F],
                        "e_loss_dp": o.e_loss.get(F, 0.0),
                        "e_raw_mc": float(m["raw"][F].mean()),
                        "e_clamp_mc": float(m["loss"][F].mean())}
                    for F in sides},
        })
        A = rec["attacker"]
        _c = f"({rec['cell'][0] + 1},{rec['cell'][1] + 1})"
        print(f"{i:>3} {_c:>7} "
              f"{('+' if F0 in A else '·'):>4} {len(b.init[F0]):>4} {b.soak[F0]:>4} │ "
              f"{pw_dp:>6.3f} {pw_mc:>6.3f} {pl_dp:>6.3f} {pl_mc:>6.3f} "
              f"{o.p_draw:>5.3f} │ {tv:>6.3f} {tvf:>6.3f} │ "
              f"{e_loss_dp:>6.1f} {e_raw_mc:>6.1f} {e_loss_mc:>6.1f} │ "
              f"{e_r_dp:>5.2f} {e_r_mc:>5.2f} "
              + ("  ← ★不自洽" if _inconsistent(rows[-1]) else ""))
    print(f"（共 {len(rows)} 场）")

    bad = report(rows)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"args": vars(args), "rows": rows},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n★ 逐场明细已写 {out}（含每场每方的逐次 Δscore，可以另做分析）")
    return 10 if bad else 0


def _inconsistent(r: dict) -> str:
    """这一场 DP 与 MC 是否**统计上不自洽**（空串 = 自洽）。

    ★★ 判据为什么长这样（2026-09-30，第一版闸门**没响**，我当场改的）：
      第一版用**中位数**（`median(TV) > 0.15`）—— `--sabotage` 下 6 场里只有 1 场被下毒，
      中位数照样 0.000 ⇒ **闸门沉默**。这跟 `watch_furnace` 那两次是同一个形状：
      **判据坐在"总体水平"上，而病灶是"个别样本"** ⇒ 该看的是**逐场**，不是均值。
    ★ 两个判据都**自带尺度**，不是拍的数：
      · TV 对**它自己的解析噪声底**（`tv_floor`）之比 —— 20× 是"绝无可能来自抽样"的量级；
      · 期望掉血对**MC 自己的标准误**（`σ/√M`）—— 5σ 之外再加 1hp 绝对余量。
    """
    if r.get("has_retreat"):                # 原始伤害统计不全 ⇒ 这条判据不适用
        return ""
    se = r["sd_raw_mc"] / np.sqrt(max(1, r["_trials"]))
    if abs(r["e_loss_dp"] - r["e_raw_mc"]) > 5 * se + 1.0:
        return (f"原始伤害 DP {r['e_loss_dp']:.1f} vs MC {r['e_raw_mc']:.1f}"
                f"（差 {abs(r['e_loss_dp'] - r['e_raw_mc']):.1f} hp ≫ 5σ+1）")
    if r["tv"] > max(0.10, 20 * r["tv_floor"]):
        return f"TV {r['tv']:.3f}（噪声底 {r['tv_floor']:.3f}，{r['tv'] / max(r['tv_floor'], 1e-9):.0f}×）"
    return ""


def report(rows: list[dict]) -> bool:
    """打汇总；返回 **`True` = 对拍台本身失效**（不是 DP 错，是预言机错）。"""
    if not rows:
        return False
    tv = np.array([r["tv"] for r in rows])
    tvf = np.array([r["tv_floor"] for r in rows])
    pw = np.array([r["p_win_dp"] for r in rows])
    pl = np.array([r["p_lose_dp"] for r in rows])
    decided = np.maximum(pw, pl)                     # 最大的"某一方干脆利落地赢"概率
    el_dp = np.array([r["e_loss_dp"] for r in rows])
    er_mc = np.array([r["e_raw_mc"] for r in rows])       # ★ 同口径（未夹）
    el_mc = np.array([r["e_loss_mc"] for r in rows])      # 实际掉的血（另一回事）
    rd = np.array([r["e_rounds_dp"] for r in rows])
    rm = np.array([r["e_rounds_mc"] for r in rows])

    print("\n" + "=" * 100)
    print("【③ 汇总】")
    print("\n── A. DP 有没有说谎（分布偏差 vs 抽样噪声底）──")
    over = int((tv > 2 * tvf).sum())
    print(f"  TV        中位 {np.median(tv):.3f}   均值 {tv.mean():.3f}   "
          f"最大 {tv.max():.3f}")
    print(f"  TV 噪声底 中位 {np.median(tvf):.3f}   均值 {tvf.mean():.3f}")
    print(f"  ⇒ TV / 噪声底 中位 {np.median(tv / np.maximum(tvf, 1e-9)):.2f}×；"
          f"超过 2× 的场次 **{over}/{len(rows)}**"
          f"（{over / len(rows) * 100:.0f}%）")
    print(f"  期望伤害（DP 口径：**未夹 0**）  |DP − MC| 中位 "
          f"{np.median(np.abs(el_dp - er_mc)):.2f} hp "
          f"（DP {el_dp.mean():.1f} / MC {er_mc.mean():.1f}）")
    ov = float(np.median(er_mc / np.maximum(el_mc, 1e-9)))
    print(f"  ★ **口径差（不是 DP 的错）**：DP 的 `e_loss` 是**承受的伤害总量**"
          f"（`u[1] − nh`，`nh` 不夹 0），实际掉的血只有 {el_mc.mean():.1f}"
          f" ⇒ 伤害总量是掉血的 **{ov:.2f}×**（多出来的是**过量击杀**）。"
          f"两个数不是同一个量，喂网络的是前者。")
    print(f"  期望轮数   |DP − MC| 中位 {np.median(np.abs(rd - rm)):.2f} 轮 "
          f"（DP {rd.mean():.2f} / MC {rm.mean():.2f}）")

    # ★★ 闸门：**对拍台自己失效** vs **DP 说谎**，必须分得开
    #   （2026-09-30 我为这个栽过一次：只复原 armies+tiles ⇒ 攻下核心领地让失主亡国
    #     ⇒ 之后双方"未交战" ⇒ TV 冲到 0.995，我差点当成 DP 的 bug 报上去）
    bad = [(r["i"], _inconsistent(r)) for r in rows if _inconsistent(r)]
    if bad:
        print(f"\n  ★★★ **对拍台失效**（不是 DP 错）—— {len(bad)}/{len(rows)} 场不自洽：")
        for i, why in bad[:10]:
            print(f"      · 第 {i} 场  {why}")
        if len(bad) > 10:
            print(f"      …… 还有 {len(bad) - 10} 场")
        print("      最可能的病灶：MC 没有把**整个世界**复原（攻下核心领地 ⇒ 失主亡国"
              " ⇒ 战争结束 ⇒ 之后一轮都不掷骰）。")
        print("      用 `--sabotage` 复现这条；正常路径每个 trial 都整世界 `deepcopy`。")
    else:
        print("  ⇒ **对拍台自检通过**：逐场 TV 都在噪声底内、掉血都在 5σ 内"
              " ⇒ 上面这些偏差**可以算在 DP 头上**（DP 没说谎）")

    print("\n── B. 这些仗本身有多「悬」──")
    print(f"  最大 p_win 的分布   中位 {np.median(decided):.3f}   "
          f"<0.65 的占 {np.mean(decided < 0.65) * 100:.0f}%   "
          f"<0.55 的占 {np.mean(decided < 0.55) * 100:.0f}%")
    ent = np.array([-sum(p * np.log2(p) for p in (a, b, 1 - a - b) if p > 0)
                    for a, b in zip(pw, pl)])
    print(f"  结局熵（bit，log2 3 = 1.585 是上限）  中位 {np.median(ent):.2f}  "
          f"均值 {ent.mean():.2f}")

    print("\n── C. ★ 噪声 vs 信号（按打「打分器分」这一项算）──")
    print("  Δscore = 这一格打完 − 打之前（`evaluate.score`，全知）；"
          "「期望收益」= 逐次 Δscore 的均值")
    d, s, snr, flip = [], [], [], []
    _samples: list = []
    n_ended = 0
    for r in rows:
        for F, q in r["per"].items():
            v = np.array(q["dsc"])
            # ★ `evaluate.score` 在**已定局**时返回 ±1e9（`terminal`）⇒ 这一仗直接
            #   把一方打亡国了。它是真事件，但会把 SNR / tanh 全冲掉 ⇒ 单列。
            if np.any(np.abs(v) > 1e6):
                n_ended += 1
                continue
            _fl = (float(np.mean(np.sign(v) != np.sign(v.mean())))
                   if v.mean() else 0.0)
            d.append(v.mean())
            s.append(v.std())
            snr.append(abs(v.mean()) / max(v.std(), 1e-9))
            flip.append(_fl)
            _samples.append((r, abs(v.mean()), v.std(), _fl))
    if d:
        d, s, snr, flip = map(np.array, (d, s, snr, flip))
        print(f"  样本 {len(d)} 个（势力×战斗）"
              + (f"；另有 **{n_ended} 个直接把一方打亡国**（Δscore=±1e9，已剔除）"
                 if n_ended else ""))
        print(f"    |期望收益| 中位 {np.median(np.abs(d)):.2f} 分   "
              f"（最大 {np.abs(d).max():.1f}）")
        print(f"    骰子噪声 σ  中位 {np.median(s):.2f} 分   （最大 {s.max():.1f}）")
        print(f"    **信噪比 |ΔE|/σ 中位 {np.median(snr):.2f}**   "
              f"<1 的占 {np.mean(snr < 1) * 100:.0f}%")
        print(f"    **符号翻转率 中位 {np.median(flip) * 100:.0f}%**"
              f"（= 单次采样里结果与期望反号的概率；50% ⇒ 纯抛硬币）")
        print(f"    折成势函数奖励：tanh(σ/{S.REWARD_TANH_SCALE:g}) 中位 "
              f"{np.median(np.tanh(s / S.REWARD_TANH_SCALE)):.3f}，"
              f"tanh(|ΔE|/{S.REWARD_TANH_SCALE:g}) 中位 "
              f"{np.median(np.tanh(np.abs(d) / S.REWARD_TANH_SCALE)):.3f}")

        # ---- ★ 分箱：噪声只在「悬」的仗里咬人 ----
        #   ★ 这个分箱是**从数据里长出来的**，不是拍的：汇总一跑就看见规则 AI 的仗
        #     `p_win` 几乎全是 1.000（它只打必胜的仗）⇒ 不分箱的话，
        #     "骰子噪声小"这个结论会被**必胜仗**稀释掉，而模型面对的恰恰是悬的仗。
        print("    ── 按「这场仗有多悬」分箱（dec = max(p赢, p负)）──")
        print(f"      {'档':<10}{'n':>5}{'σ中位':>9}{'|ΔE|中位':>10}"
              f"{'SNR中位':>9}{'翻转率':>9}")
        for name, lo, hi in (("果断 ≥0.90", 0.90, 1.01), ("有把握 0.65~0.90", 0.65, 0.90),
                             ("★硬币 <0.65", 0.0, 0.65)):
            sel = [(dd, ss, ff) for r, dd, ss, ff in _samples
                   if lo <= max(r["p_win_dp"], r["p_lose_dp"]) < hi]
            if not sel:
                continue
            dd = np.array([x[0] for x in sel]); ss = np.array([x[1] for x in sel])
            ff = np.array([x[2] for x in sel])
            print(f"      {name:<10}{len(sel):>5}{np.median(ss):>9.2f}"
                  f"{np.median(np.abs(dd)):>10.2f}"
                  f"{np.median(np.abs(dd) / np.maximum(ss, 1e-9)):>9.2f}"
                  f"{np.median(ff) * 100:>8.0f}%")

    print("\n── D. 阵亡：一场仗到底吃掉几支军 ──")
    left, tot, gone = [], [], []
    for r in rows:
        for F, q in r["per"].items():
            a = np.array(q["left"])
            left.append(a.mean())
            tot.append(q["n_units"])
            gone.append(q["n_units"] - a.mean())
    if left:
        left, tot, gone = map(np.array, (left, tot, gone))
        print(f"  参战支数 中位 {np.median(tot):.1f} · "
              f"平均阵亡 {gone.mean():.2f} 支 · 一场仗打光全部兵力的场次占 "
              f"{np.mean(left < 0.5) * 100:.0f}%")
    print("\n── E. 抽查（DP 该不该被信）──")
    print(f"  DP 被顶到上限（`truncated`）的场次："
          f"{sum(1 for r in rows if r['truncated'])}/{len(rows)}")
    print("=" * 100)
    return bool(bad)


if __name__ == "__main__":
    sys.exit(main())
