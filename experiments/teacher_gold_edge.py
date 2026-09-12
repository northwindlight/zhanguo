# -*- coding: utf-8 -*-
"""老师的黄金轨迹：它到底是在"攒够再花"，还是**整局贴着 0 金跑**（资本受限）？

若是后者 ⇒ 攒钱与复利是**边界控制**问题 —— 任何流量误差立刻改变"买得起什么"，
这正是复合误差在这个问题上特别致命的原因。
"""
import sys, random, statistics as st
from rl.env import ZhanguoEnv
import rl.bc as bc
N, TURNS, JIT = (int(sys.argv[1]) if len(sys.argv) > 1 else 20), 70, 0.1
env = ZhanguoEnv(map_size=16, max_turns=TURNS); env.rules_jitter = JIT
teacher = bc.get_teacher("v10", turns=TURNS)

paths, tiles_end = [], []
for s in range(700000, 700000 + N):
    env.reset(s, map_seed=s)
    w, me = env.world, env.agent
    rng = random.Random(s)
    golds = []
    for t in range(TURNS):
        w.resolve_turn() if False else None
        teacher(w, me, rng, max_actions=10**9)
        w.resolve_turn()
        if t + 1 < TURNS: w.begin_turn()
        golds.append(w.nations[me].res.get("黄金", 0))
    paths.append(golds); tiles_end.append(len(w.own_tiles(me)))

print(f"{N} 张图 × {TURNS} 回合，抖动 {JIT:.0%}；老师终局地数 中位 {int(st.median(tiles_end))}")
print(f"\n{'回合段':>8} {'黄金中位':>9} {'黄金均值':>9} {'金<350 的图占比':>15}")
for a in range(0, TURNS, 10):
    seg = [p[a:a+10] for p in paths]
    flat = [g for p in seg for g in p]
    under = sum(1 for p in seg for g in p if g < 350) / len(flat)
    print(f"{a+1:>4}-{a+10:<3} {st.median(flat):>9,.0f} {st.mean(flat):>9,.0f} {under:>14.0%}")
allg = [g for p in paths for g in p]
print(f"\n全局：中位 {st.median(allg):,.0f}   金<350 的回合占比 {sum(1 for g in allg if g<350)/len(allg):.0%}"
      f"   金<100 的占比 {sum(1 for g in allg if g<100)/len(allg):.0%}")
