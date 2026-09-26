# -*- coding: utf-8 -*-
"""**跨臂对打**：甲臂的网 vs 乙臂的网，同一批固定种子，判"谁更强"。

    python -m rl.head2head --a rl/runs/par_mem/mem01.pt --b rl/runs/par_base/base01.pt \
        --seeds 40 --size 12 --t-max 400

★ 为什么必须有它（2026-09-26 查出来的缺口）：
  `rl/eval_fixed.py` 取**一个** `--ckpt`，然后把池子里的网**按座位轮着发**
  ⇒ 那是**同一臂自对弈**。它能回答"这一臂在固定种子上打成什么样"（回合、集中度、
  谁赢），**但回答不了"记忆臂和基线臂哪个强"** —— 而后者正是这条线唯一的判据
  （用户 09-18：「判据只看**采样臂**」，09-24：「最终**只看胜场**」）。
  ⇒ 这个脚本把两臂的网**放进同一局**，谁赢算谁的。

★★ 座位与先后手怎么摆（三条，都是为了"别把座位偏差算成实力差"）：
  ① **交替配比**：k 个座位上甲占 `k//2` 或 `k−k//2`（3 国局 ⇒ 1 个 vs 2 个，
     两局一循环）。★ 不交替就**必错**：3 国局里"甲占 2 座"时甲只要**任一**国赢
     就算甲赢 ⇒ 对称局面下甲胜率天然 ≈ 2/3，那是座位给的，不是实力。
  ② **哪几个座位归甲随机抽**（按种子派生）⇒ 甲不会永远坐"先手位"或某个角。
  ③ **先手在 k 国之间轮换**（与训练里 `first = players[(it+e) % k]` 同一口径）。
  ⇒ 自检：**同一个 ckpt 当甲乙两方**时，总胜率必须 ≈ 50%（见
     `tests/test_rl_head2head.py` 的那条对照 —— 座位/先后手有偏差它会当场红）。

★ 报数要带范围（用户 09-18 的纪律：报评估数要说清只覆盖到哪一段）：
  这批数只覆盖"**这组种子 × 这个图幅 × 这个地平线 × 采样臂**"，不是"更强的模型"。
"""
from __future__ import annotations

import argparse
import json

import numpy as np
import torch

from . import vocab as V
from .eval_fixed import load_pool, play
from .sandbox import Sandbox, n_nations_for


def seats_for_a(k: int, i: int) -> int:
    """第 `i` 局甲占几个座位（**交替配比**，见模块 docstring 的 ①）。

    k 座位两局一循环地分成 `k//2` 与 `k−k//2` ⇒ 甲的平均座位数 = k/2 ，
    于是"座位多的那一方更容易赢"这件事在总胜率里**抵消掉**。
    ★ k 为偶数时两边恒等（k=2 ⇒ 永远 1:1），本来就不需要交替。
    """
    return (k // 2) if (i % 2 == 0) else (k - k // 2)


def assign(nations: list[str], i: int, k: int, seed0: int) -> tuple[set, set]:
    """第 `i` 局哪几个国名归甲 / 归乙（按 `seed0+i` 派生 ⇒ 可复现、且不总在同一座位）。"""
    rng = np.random.default_rng(seed0 + i)
    seats = list(rng.permutation(k)[:seats_for_a(k, i)])
    a = {nations[j] for j in seats}
    return a, set(nations) - a


def game_setup(nations: list[str], i: int, seed0: int) -> tuple[set, str]:
    """第 `i` 局的**座位归属**与**先手** —— 抽出来是为了能**纯逻辑地**验公平性。

    ★★ 为什么要抽出来（而不是塞在 `main()` 里）：公平性要做成一条**真闸门**，
      就必须能跑**几百上千局**去量"甲侧胜率是不是 50%"。走真的沙盒 + 前向，
      一局要十几秒（实测 24 局 494 秒）⇒ 只能跑几十局 ⇒ 噪声 ±15%
      ⇒ 那条闸门**根本响不起来**（假绿）。抽成纯函数之后，同样几百局是**毫秒级**，
      而且还能注入"已知偏差"（比如"先手必赢"）去确认它**真的会响**。
    """
    a_side, _ = assign(nations, i, len(nations), seed0)
    return a_side, nations[i % len(nations)]


def side_of(won: set, a_side: set) -> str:
    """胜方归哪一侧：`"A"` / `"B"` / `"平"`（平 = 没人赢）。"""
    if not won:
        return "平"
    return "A" if (won & a_side) else "B"


def nets_for(nations, a_side, pool_a, pool_b, i, seed0) -> dict:
    """把两池的网发到座位上（**同侧不重复**；一侧座位多于该池份数时循环发）。"""
    rng = np.random.default_rng(seed0 * 7 + i)
    out = {}
    for side, pool in ((True, pool_a), (False, pool_b)):
        names = [n for n in nations if ((n in a_side) == side)]
        if not names:
            continue
        order = list(rng.permutation(len(pool)))
        for j, n in enumerate(names):
            out[n] = pool[order[j % len(pool)]]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="跨臂对打（甲 vs 乙，固定种子）")
    ap.add_argument("--a", dest="ckpt_a", default=None, help="甲臂 ckpt（不给 ⇒ 未训练）")
    ap.add_argument("--b", dest="ckpt_b", default=None, help="乙臂 ckpt（不给 ⇒ 未训练）")
    ap.add_argument("--seeds", type=int, default=40, help="固定种子个数（两臂共用）")
    ap.add_argument("--seed0", type=int, default=1000)
    ap.add_argument("--size", type=int, default=12)
    ap.add_argument("--nations", type=int, default=None, help="缺省按 `n_nations_for(size)`")
    ap.add_argument("--t-max", dest="t_max", type=int, default=400)
    ap.add_argument("--pool", type=int, default=5, help="每臂读回几份网")
    ap.add_argument("--greedy", action="store_true",
                    help="★ 缺省按策略**采样**（用户 09-18：「判据只看采样臂」）")
    ap.add_argument("--halls-known", dest="halls_known", action="store_true", default=True)
    ap.add_argument("--no-halls-known", dest="halls_known", action="store_false")
    ap.add_argument("--threads", type=int, default=0)
    ap.add_argument("--record", default=None,
                    help="★ 把每局写进这个 JSONL（供 `rl/elo.py --extra` 用）—— "
                         "**它是让两臂进同一张比较图的唯一途径**：评级只在连通分量内"
                         "有意义，各自算各自的池子两张表不可比（A/B 要的正是可比）。")
    a = ap.parse_args()
    if a.threads:
        torch.set_num_threads(a.threads)

    k = a.nations or n_nations_for(a.size)
    pool_a = load_pool(a.ckpt_a, a.pool)
    pool_b = load_pool(a.ckpt_b, a.pool)
    print(f"★ 甲 = {a.ckpt_a or '未训练'}  乙 = {a.ckpt_b or '未训练'}")
    rows = []
    rng = np.random.default_rng(0)
    for i, s in enumerate(range(a.seed0, a.seed0 + a.seeds)):
        sb = Sandbox(seed=s, size=a.size, n_nations=k, t_max=a.t_max,
                     halls_known=a.halls_known).reset()
        nations = list(sb.players)
        a_side, first = game_setup(nations, i, a.seed0)
        sb.first = first
        net_of = nets_for(nations, a_side, pool_a, pool_b, i, a.seed0)
        # ★ 座位 → 身份标签（`A2` = 甲臂第 2 份）：跨臂流水用**合成 mid**，
        #   因为 `load_pool` 只给回权重、没有池子里的 mid ⇒ 这一层命名是本工具自己的口径。
        slot_of = {}
        for side, pool in ((True, pool_a), (False, pool_b)):
            order = list(np.random.default_rng(a.seed0 * 7 + i).permutation(len(pool)))
            names = [n for n in nations if ((n in a_side) == side)]
            for j, n in enumerate(names):
                slot_of[n] = f"{'A' if side else 'B'}{order[j % len(pool)]}"
        r = play({}, sb, greedy=a.greedy, rng=rng, net_of=net_of)
        won = set(r["winner_members"])
        rows.append({"i": i, "na": len(a_side), "turns": r["turns"],
                     "side": side_of(won, a_side),
                     "ratio": r["ratio"]})
        if a.record:
            with open(a.record, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({
                    "i": i, "mids": {n: slot_of[n] for n in nations},
                    "winner": (slot_of[sorted(won)[0]] if won else None),
                    "turns": r["turns"]}, ensure_ascii=False) + "\n")

    na = sum(r["na"] for r in rows)
    nb = k * len(rows) - na
    aw = sum(1 for r in rows if r["side"] == "A")
    bw = sum(1 for r in rows if r["side"] == "B")
    dr = sum(1 for r in rows if r["side"] == "平")
    dec = aw + bw
    turns = np.array([r["turns"] for r in rows], float)
    ratio = np.array([r["ratio"] for r in rows], float)
    print(f"── {'贪心臂' if a.greedy else '采样臂'} · {a.size}×{a.size} / {k} 国 · "
          f"{a.seeds} 个固定种子 · t_max {a.t_max} ──")
    print(f"  座位：甲 {na} / 乙 {nb}（交替配比 {k // 2}:{k - k // 2} ⇒ 抵消座位偏差）")
    print(f"  **甲胜 {aw} · 乙胜 {bw} · 平 {dr}**"
          + (f" ⇒ 甲在有胜负的局里胜率 **{aw / dec:.0%}**（{dec} 局有胜负）"
             if dec else " ⇒ **全是平局**，判不出强弱（换地平线或加种子）"))
    print(f"  平均回合 {turns.mean():.0f}（中位 {np.median(turns):.0f}，"
          f"打到上限 {int((turns >= a.t_max).sum())} 局）")
    print(f"  两方合计 策略集中度 ent/logK = {ratio.mean():.3f}（1.0=纯均匀）")
    print("  ★ 覆盖范围：只说明「这组种子 × 这个图幅 × 这个地平线 × 采样臂」下的强弱")


if __name__ == "__main__":
    main()