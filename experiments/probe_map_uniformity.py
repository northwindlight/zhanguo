# -*- coding: utf-8 -*-
"""地图生成的**均匀性**检验 —— 用户 2026-09-14：「一维随机数列生成的二维地图其实是不均匀的」。

## 引擎的生成方式（读码）

```python
tile_terrain(x,y)  = roll_terrain(random.Random(f"{seed}:{x}:{y}"))
tile_resources(x,y)= roll_resources(random.Random(f"{seed}:{x}:{y}:res"), terrain)
```
⇒ **每格独立播种、i.i.d. 抽**，没有空间相关结构（不是柏林噪声那类）。
地形与资源都是 `(seed,x,y)` 的**纯函数** ⇒ **不用建环境就能抽几百万次**。

## 三层检验（判据事先写死）

1. **全局**：地形比例是否等于 `TERRAIN_WEIGHTS`（平原30/森林25/丘陵20/山地15/沙漠10）
   判据：χ² 检验 p > 0.01 ⇒ 均匀
2. ★**逐坐标**：把每个坐标 (x,y) 的分布与全局比（同一套种子）
   判据：若某些坐标显著偏离 ⇒ **坐标本身带偏**（播种字符串的伪影）
3. ★★**起始 5 格**（引擎固定给的那 5 格）：`Σ品位` 随种子的分布
   判据：**与"随机 5 格"的分布比** —— 若显著不同 ⇒ **整池的"家门口抽奖"是系统性偏的**，
   不是随机噪声。**这一条直接决定我们 251 张图池和一切评估能不能用。**

4. 资源品位：给定地形，各资源品位是否等于 `TERRAINS[地形][资源]` 的权重
   （★注意：`roll_resources` 对**同一个 rng** 连续抽多个资源，字典序固定 ⇒
     "排在前面的资源永远拿第 1 个随机数" —— 若首抽有偏，会**系统性地落在某一个资源上**）

用法：python experiments/probe_map_uniformity.py [--seeds 400] [--mapsize 16]
"""
from __future__ import annotations

import random
import sys
from collections import Counter, defaultdict

from game import TERRAINS, TERRAIN_WEIGHTS, roll_resources, roll_terrain

NSEED, SIZE = 400, 16
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--seeds":
        i += 1; NSEED = int(sys.argv[i])
    elif a == "--mapsize":
        i += 1; SIZE = int(sys.argv[i])
    i += 1

TERS = list(TERRAIN_WEIGHTS)
W = [TERRAIN_WEIGHTS[t] for t in TERS]
TOT = sum(W)
HOME = [(7, 3), (8, 2), (8, 3), (8, 4), (9, 3)]      # 0-indexed，引擎固定给的开局十字
RES = ("木头", "耕地", "矿石", "石油", "黄金")


def chi2(obs: Counter, exp_p, keys, n):
    """卡方统计量与自由度（不查表，只报统计量 + 临界值参照）。"""
    s = 0.0
    for k in keys:
        e = exp_p[k] * n
        if e > 0:
            s += (obs.get(k, 0) - e) ** 2 / e
    return s


print(f"抽 {NSEED} 个种子 × {SIZE}×{SIZE} 格 = {NSEED * SIZE * SIZE:,} 格\n")

# ---------- 1. 全局地形 ----------
g = Counter()
for sd in range(NSEED):
    for x in range(SIZE):
        for y in range(SIZE):
            g[roll_terrain(random.Random(f"{sd}:{x}:{y}"))] += 1
n = sum(g.values())
print("【1 全局地形】")
for t, w in zip(TERS, W):
    print(f"  {t}: 实测 {g[t]/n:6.2%}   期望 {w/TOT:6.2%}   差 {g[t]/n - w/TOT:+.2%}")
print(f"  χ² = {chi2(g, {t: w/TOT for t, w in zip(TERS, W)}, TERS, n):.1f}  (df=4, 0.01 临界 13.3)")

# ---------- 2. 逐坐标 ----------
print("\n【2 逐坐标偏离】(同一套种子；每坐标 n = %d)" % NSEED)
per = defaultdict(Counter)
for sd in range(NSEED):
    for x in range(SIZE):
        for y in range(SIZE):
            per[(x, y)][roll_terrain(random.Random(f"{sd}:{x}:{y}"))] += 1
worst = []
for (x, y), c in per.items():
    s = chi2(c, {t: w/TOT for t, w in zip(TERS, W)}, TERS, NSEED)
    worst.append((s, x, y))
worst.sort(reverse=True)
print(f"  256 个坐标的 χ²（df=4，0.01 临界 13.3）："
      f"最大 {worst[0][0]:.1f} @({worst[0][1]},{worst[0][2]})，"
      f"中位 {worst[len(worst)//2][0]:.1f}，最小 {worst[-1][0]:.1f}")
over = [w for w in worst if w[0] > 13.3]
print(f"  超临界（>13.3）的坐标数：**{len(over)}/256** （期望 ~2.6 个，即 1%）")
for s, x, y in worst[:5]:
    c = per[(x, y)]
    print(f"    ({x},{y}) χ²={s:5.1f}  " + " ".join(f"{t}={c[t]}" for t in TERS))

# ---------- 3. 起始 5 格 vs 随机 5 格 ----------
print("\n【3 ★起始 5 格 Σ品位 vs 随机 5 格】(各 %d 个种子)" % NSEED)


def home_sum(sd, cells):
    tot = 0
    for (x, y) in cells:
        r = roll_resources(random.Random(f"{sd}:{x}:{y}:res"),
                           roll_terrain(random.Random(f"{sd}:{x}:{y}")))
        tot += sum(r.values())
    return tot


h = [home_sum(sd, HOME) for sd in range(NSEED)]
rnd = []
for sd in range(NSEED):
    cells = set()
    while len(cells) < 5:
        cells.add((random.Random(f"pick:{sd}:{len(cells)}").randrange(SIZE),
                   random.Random(f"pick2:{sd}:{len(cells)}").randrange(SIZE)))
    rnd.append(home_sum(sd, list(cells)))


def desc(v, lab):
    import statistics as st
    v = sorted(v)
    print(f"  {lab}: 均值 {st.mean(v):5.1f}  std {st.pstdev(v):4.1f}  "
          f"p5 {v[len(v)//20]}  p50 {v[len(v)//2]}  p95 {v[len(v)*19//20]}  "
          f"min {v[0]}  max {v[-1]}")


desc(h, "起始 5 格")
desc(rnd, "随机 5 格")
import statistics as st
print(f"  ⇒ 均值差 {st.mean(h) - st.mean(rnd):+.1f}，std 比 {st.pstdev(h)/st.pstdev(rnd):.2f}")

# ---------- 4. 资源品位分布 ----------
print("\n【4 资源品位分布】(只在对应地形上抽)")
for ter in ("平原", "森林"):
    print(f"  --- {ter} ---")
    for res, wts in TERRAINS[ter].items():
        c = Counter()
        for sd in range(NSEED):
            for x in range(SIZE):
                for y in range(SIZE):
                    if roll_terrain(random.Random(f"{sd}:{x}:{y}")) != ter:
                        continue
                    c[roll_resources(random.Random(f"{sd}:{x}:{y}:res"), ter)[res]] += 1
        nn = sum(c.values())
        if not nn:
            continue
        tw = sum(wts)
        obs = " ".join(f"{c[k]/nn:.1%}" for k in range(len(wts)))
        exp = " ".join(f"{w/tw:.1%}" for w in wts)
        print(f"    {res:<3} 实测 {obs}   期望 {exp}")
