# -*- coding: utf-8 -*-
"""开局经济对比（**正确口径**）—— 2026-09-14 修正。

## 我上一版错在哪（用户 2026-09-14 当场指出：「哪有那么多资源」）

`game.py:124` 写明 `cap_resource` = **该地块此项资源量即上限**，
`mp.py:674` 用它判「已达上限」。所以 `tile_resources` 返回的是
**品位等级 0~5 = 这块地最多能盖几座采集建筑**（每座产 1/回合），
**不是产量**。我上一版把 256 格的品位**求和**报成"全图木头=362" —— 那是
"最多 362 座林场"，不是 362 木头。**量纲错了。**

## 本脚本的正确口径

- 逐格报：地形 / 各资源**品位** / 建造金价倍率（地形惩罚，`game.py:66`）
- 「起始 5 格」= 引擎给的**开局领土**（`own_tiles(agent)`），这才是玩家真正拿到手的
- 邻环 = 起始 5 格的 8 邻域里**尚未拥有**的格
- 合计用 **Σ 品位 = 该范围内最多能盖的采集建筑数**（这才是可比的量）
- 另给「**成本加权容量**」= Σ 品位 / 建造倍率（地形越差，同样品位越不值）

用法：python map_cmp.py <seed> [<seed>...]
"""
from __future__ import annotations

import sys
from collections import Counter

from game import TERRAIN_STATS
from rl.env import ZhanguoEnv
from rl.features import build_cost_factor

RES = ("木头", "耕地", "矿石", "石油", "黄金")
SEEDS = [int(a) for a in sys.argv[1:]] or [900062, 900121]

env = ZhanguoEnv(map_size=16, max_turns=200)
for sd in SEEDS:
    env.reset(sd)
    w = env.world
    n = w.size
    own = w.own_tiles(env.agent)
    ownset = set(own)
    ring = set()
    for (x, y) in own:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                p = (x + dx, y + dy)
                if p != (x, y) and 0 <= p[0] < n and 0 <= p[1] < n and p not in ownset:
                    ring.add(p)

    def stat(cells):
        g = Counter(); costw = 0.0; ter = Counter()
        for (x, y) in cells:
            t = w.tile_terrain(x, y)
            ter[t] += 1
            r = w.tile_resources(x, y)
            f = build_cost_factor(t, False)
            for k in RES:
                g[k] += r.get(k, 0)
                costw += r.get(k, 0) / f
        return g, costw, ter

    g5, cw5, ter5 = stat(own)
    gr, cwr, terr = stat(ring)

    print("=" * 78)
    print(f"seed {sd}   起始领土 {len(own)} 格 @ "
          + " ".join(f"({x+1},{y+1})" for x, y in own))
    print("=" * 78)

    print("\n【起始 5 格 · 逐格】品位=该格最多能盖几座；倍率=建造金价")
    print(f"  {'坐标':>8} {'地形':<4} {'倍率':>5}  " + "  ".join(f"{r:>4}" for r in RES))
    for (x, y) in own:
        t = w.tile_terrain(x, y)
        r = w.tile_resources(x, y)
        f = build_cost_factor(t, False)
        print(f"  ({x+1:>2},{y+1:>2}) {t:<4} {f:>5.2f}  "
              + "  ".join(f"{r.get(k,0):>4}" for k in RES))

    print(f"\n【合计】Σ品位 = 该范围内最多能盖的采集建筑数")
    print(f"  起始 5 格     : " + "  ".join(f"{k}={g5[k]}" for k in RES)
          + f"   Σ={sum(g5.values())}   成本加权={cw5:.1f}   地形={dict(ter5)}")
    print(f"  邻环 {len(ring)} 格    : " + "  ".join(f"{k}={gr[k]}" for k in RES)
          + f"   Σ={sum(gr.values())}   成本加权={cwr:.1f}")
    print(f"  起始+邻环     : Σ={sum(g5.values()) + sum(gr.values())}   "
          f"成本加权={cw5 + cwr:.1f}")

    # 全图（到多远才有地可扩）——按距离分环
    print(f"\n【按距离分环】Σ品位（离最近起始格的距离，切比雪夫）")
    for d in range(1, 7):
        cells = [(x, y) for y in range(n) for x in range(n)
                 if (x, y) not in ownset
                 and min(max(abs(x - ox), abs(y - oy)) for ox, oy in own) == d]
        if not cells:
            continue
        g, cw, _ = stat(cells)
        print(f"  距离 {d}: {len(cells):>3} 格   Σ={sum(g.values()):>4}   "
              f"成本加权={cw:>7.1f}   " + " ".join(f"{k}={g[k]}" for k in RES))
    print()
