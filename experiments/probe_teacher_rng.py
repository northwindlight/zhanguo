# -*- coding: utf-8 -*-
"""老师的**地图间差异**是「地形/资源」造成的，还是「战斗掷骰」造成的？

## 为什么要这个（2026-09-14，用户的"隐藏癖好"）

实测老师 128 图的分数 **std 5,744 / 均值 21,843（26%）、极差 4.45×**。
但 dump 出**最差图 `900062`（7,709）**与**最好图 `900121`（34,298）**的开局：
起始格坐标相同 (8,4)、开局领土都是 5 格、**全图与近处的地形/资源统计上不可区分**，
而且 **900062 的近处资源还更多** ⇒ **穷富解释不了那 4.45×**。

## 关键机制（读码得来，不是猜的）

- `mp.py:211` `self.rng = random.Random(self.seed)` —— **战斗掷骰走这条流**
  （`_die` → `mp.py:1039` `self.rng.randint(1,6)`）；`mp.py` 注释明说
  「现在 `self.rng` **只剩战斗在用**（外加开局布点）」
- `rl/train.py:62` 老师基准里的 `rng = random.Random(0xB4BE)` 是**另一条、固定的**流

⇒ **地图与战斗运气被同一个 `seed` 缠住了**：换 seed 同时换地图**和**换战斗流。
⇒ **干净的切法：地图不变，只换 `world.rng`。**

## 判据（事先写死）

对同一张图跑 N 条不同的战斗 RNG 流：
- **组内 std ≈ 组间 std（5,744）** ⇒ 老师的差异**主要是战斗运气**，"地图癖好"是假象
- **组内 std ≪ 组间 std** ⇒ 差异**确实是地图的**，但不在我 dump 的那些字段里
  （⇒ 下一步查几何/野怪/中立城）

用法：python experiments/probe_teacher_rng.py <map_seed> [map_seed...] [--n 6] [--turns 200]
"""
from __future__ import annotations

import copy
import random
import statistics as st
import sys

from rl.bc import get_teacher
from rl.env import ZhanguoEnv
from rl.train import teacher_baseline

SEEDS: list[int] = []
N, TURNS = 6, 200
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--n":
        i += 1; N = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    else:
        SEEDS.append(int(a))
    i += 1
if not SEEDS:
    print(__doc__); sys.exit(2)

teacher = get_teacher("v10")
env = ZhanguoEnv(map_size=16, max_turns=TURNS)

CROSS_STD = 5744      # 老师 128 图（900000+）的组间 std，硬编码作参照
print(f"每张图跑 {N} 条不同战斗 RNG 流 × {TURNS} 回合；"
      f"参照：老师**跨图** std = {CROSS_STD:,}\n")

all_within = []
for sd in SEEDS:
    vals = []
    for k in range(N):
        env.reset(sd)
        # ★地图（地形+资源）是 (seed,x,y) 的纯函数、reset 后就定死了；
        #   这里只换掉**战斗**那条流（`mp.py` 说 reset 之后 rng 只剩战斗在用）。
        env.world.rng = random.Random(0x5EED_0000 + k)
        v = teacher_baseline(copy.deepcopy(env.world), env.agent, TURNS, teacher)
        vals.append(v)
    m, s = st.mean(vals), (st.pstdev(vals) if N > 1 else 0.0)
    all_within.append(s)
    print(f"图 {sd}：均值 {m:>9,.0f}  std {s:>8,.0f}   逐条 "
          + " ".join(f"{x:,.0f}" for x in vals))

print()
if len(all_within) > 1:
    w = st.mean(all_within)
    print(f"组内 std 均值 = {w:,.0f}   跨图 std（128 图参照）= {CROSS_STD:,}"
          f"   比值 = {w / CROSS_STD:.2f}")
    print("判据：比值 ≈1 ⇒ 主要是战斗运气；≈0 ⇒ 是地图（但不在我 dump 的字段里）")
