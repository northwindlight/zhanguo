# -*- coding: utf-8 -*-
"""筛地图：跑老师，按难度挑出「中等」的一批，供训练/评估使用。

**为什么**（用户 2026-09-13）：图间 σ ≈ 均值的 **45%** —— 同一套打法在好图
20000、差图 3000。PPO 只看绝对回报，就会把「这局抽到差图」读成「我这个打法错了」，
于是去修正一个本来正确的行为。与其每局跑一遍老师基准**事后校正**，不如**事前**
把难度极端的图剔掉：只留中等的一批，训练和评估都用它。

**难度口径**：老师在该图跑 `turns` 回合的 `spend_total`。
★只取**前 100 回合**：图难度是**开局条件**的差异，而复利是**指数增长** ——
跑到 200 回合绝对差在拉大但**相对差在缩小**（8000/3000=2.67 → 25000/10000=2.5），
前 100 回合才是难度信号最纯的窗口。

**只剔两端、保留中间**（默认 25%~75% 分位）：剔掉的是"极端好/极端差"的图，
池子里仍有近一半，多样性够 —— 不是只留"简单图"。

用法：python experiments/screen_maps.py [n_seeds] [turns] [lo_pct] [hi_pct]
输出：rl/maps/medium_pool.json（含每个 seed 的难度，便于换分位重新切）
"""
import json
import random
import sys
from pathlib import Path

from rl.env import ZhanguoEnv
import rl.bc as bc

N = int(sys.argv[1]) if len(sys.argv) > 1 else 500
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 100
LO = float(sys.argv[3]) if len(sys.argv) > 3 else 0.25
HI = float(sys.argv[4]) if len(sys.argv) > 4 else 0.75
OUT = Path("rl/maps/medium_pool.json")
OUT.parent.mkdir(parents=True, exist_ok=True)

teacher = bc.get_teacher("v10", turns=TURNS)
bc.set_horizon(teacher, TURNS)      # ★ROI 窗口必须按本局回合数设（漏过这行会少扩张）

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
rng = random.Random(0xB4BE)         # 老师自己的 RNG：固定，保证同图必得同难度
spend: dict[int, float] = {}

print(f"筛地图：seed 0..{N - 1}，各跑老师 {TURNS} 回合（16×16）", flush=True)
for seed in range(N):
    env.reset(seed)                 # seed 同时决定地形与 RNG
    for t in range(TURNS):
        teacher(env.world, env.agent, rng, max_actions=10 ** 9,
                on_action=lambda *a: None)
        env.world.resolve_turn()
        if t + 1 < TURNS:
            env.world.begin_turn()
    spend[seed] = env.world.spend_total(env.agent)
    if seed % 25 == 0:
        print(f"  {seed:>4}/{N}  老师消费 {spend[seed]:>8.0f}", flush=True)

vals = sorted(spend.values())
cut_lo, cut_hi = vals[int(len(vals) * LO)], vals[int(len(vals) * HI)]
pool = sorted(s for s, v in spend.items() if cut_lo <= v <= cut_hi)

OUT.write_text(json.dumps({
    "turns": TURNS, "n": N, "map_size": 16,
    "lo_pct": LO, "hi_pct": HI, "cut_lo": cut_lo, "cut_hi": cut_hi,
    "spend": {str(k): v for k, v in sorted(spend.items())},
    "pool": pool,
}, ensure_ascii=False, indent=1), encoding="utf-8")

lo_all, hi_all = vals[0], vals[-1]
print(f"\n全池：{lo_all:.0f} ~ {hi_all:.0f}  中位 {vals[len(vals) // 2]:.0f}"
      f"   （极端比 {hi_all / max(1, lo_all):.1f}×）")
print(f"保留 [{LO:.0%}, {HI:.0%}] 分位：{cut_lo:.0f} ~ {cut_hi:.0f}"
      f"  ⇒ {len(pool)} 张（{len(pool) / N:.0%}）  窄了 {hi_all / max(1, lo_all):.1f}× → "
      f"{cut_hi / max(1, cut_lo):.1f}×")
print(f"已写 {OUT}")
