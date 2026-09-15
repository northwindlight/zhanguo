# -*- coding: utf-8 -*-
"""老师逐回合行为轨迹 —— 直接看它在"好图/差图"上**干了什么**，而不是猜地图特征。

## 为什么换这个办法（2026-09-14）

为解释老师 **4.45×** 的地图间差异，已经吃掉三个**空结果**：
1. 开局地形直方图 —— 不可区分
2. 资源 Σ品位（**正确口径**：品位=该格最多能盖几座）逐环 + 全图 —— 不可区分（102 vs 99；1064 vs 1074）
3. 战斗运气（换 `world.rng` 跑 6 条流）—— 只占 **7%**（组内 std 419 vs 跨图 5,744）

⇒ **别再猜地图特征了，直接看行为在哪个回合分岔。**

## 用法

    python experiments/probe_teacher_trace.py <seed> [<seed>...] [--turns 200] [--every 20]
"""
from __future__ import annotations

import copy
import random
import sys
from collections import Counter

from rl.bc import get_teacher
from rl.env import ZhanguoEnv

SEEDS: list[int] = []
TURNS, EVERY = 200, 20
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--every":
        i += 1; EVERY = int(sys.argv[i])
    else:
        SEEDS.append(int(a))
    i += 1
if not SEEDS:
    print(__doc__); sys.exit(2)

teacher = get_teacher("v10")
base = ZhanguoEnv(map_size=16, max_turns=TURNS)

for sd in SEEDS:
    base.reset(sd)
    world = copy.deepcopy(base.world)
    agent = base.agent
    rng = random.Random(0xB4BE)          # 与 teacher_baseline 同款固定流
    print("=" * 88)
    print(f"seed {sd}  老师逐 {EVERY} 回合轨迹")
    print("=" * 88)
    print(f"{'回合':>5} {'累计消费':>11} {'领土':>5} {'建筑总数':>8} {'采集建筑':>9} "
          f"{'部队':>5}  城/市政厅/工程院")
    for t in range(TURNS):
        teacher(world, agent, rng, max_actions=10 ** 9, on_action=lambda *a: None)
        world.resolve_turn()
        if t + 1 < TURNS:
            world.begin_turn()
        if (t + 1) % EVERY == 0 or t == 0:
            nz = world.nations.get(agent)
            own = world.own_tiles(agent)
            bld = Counter()
            for (x, y) in own:
                for b, c in world.tiles[(x, y)].get("buildings", {}).items():
                    bld[b] += c
            ext = sum(v for k, v in bld.items()
                      if k in ("林场", "农场", "矿场", "石油厂", "黄金矿场"))
            armies = world.nation_armies(agent) if hasattr(world, "nation_armies") else []
            print(f"{t+1:>5} {world.spend_total(agent):>11,.0f} {len(own):>5} "
                  f"{sum(bld.values()):>8} {ext:>9} {len(armies):>5}  "
                  f"{bld.get('城堡',0)}/{bld.get('市政厅',0)}/{bld.get('工程院',0)}")
    print()
