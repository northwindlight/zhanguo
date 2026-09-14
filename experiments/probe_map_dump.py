# -*- coding: utf-8 -*-
"""dump 一张图的开局地形与资源分布 —— 用于诊断"为什么这张图老师/学生都打不好"。

## 为什么能直接 dump 整张图

`world.tile_terrain(x,y)` 与 `world.tile_resources(x,y)` 都是 **`(seed,x,y)` 的纯函数**
（见 `mp.py:289/293`）⇒ 地形与资源**开局就排布好，不由开图决定**，
所以可以**不看迷雾**把整张图打出来。**这是诊断用，不是智能体的观测** ——
智能体在 `rl/env.py` 里仍然只看得到视野内的格子（`visible_to` 门控）。

## 用法

    python experiments/probe_map_dump.py <seed> [<seed>...] [--turns 200] [--agent 秦] [--radius 4]
"""
from __future__ import annotations

import sys
from collections import Counter

from rl.env import ZhanguoEnv

SEEDS: list[int] = []
TURNS, AGENT, RADIUS = 200, "秦", 4
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--agent":
        i += 1; AGENT = sys.argv[i]
    elif a == "--radius":
        i += 1; RADIUS = int(sys.argv[i])
    else:
        SEEDS.append(int(a))
    i += 1
if not SEEDS:
    print(__doc__); sys.exit(2)

for seed in SEEDS:
    env = ZhanguoEnv(map_size=16, max_turns=TURNS, agent=AGENT)
    env.reset(seed)
    w = env.world
    n = w.size
    own = w.own_tiles(AGENT)
    home = own[0] if own else (n // 2, n // 2)
    hx, hy = home

    print("=" * 72)
    print(f"seed {seed}  地图 {n}×{n}  智能体 {AGENT}  起始格 (x={hx+1}, y={hy+1})  "
          f"开局领土 {len(own)} 格")
    print("=" * 72)

    ter_all, res_all = Counter(), Counter()
    rows = []
    for y in range(n):
        row = []
        for x in range(n):
            t = w.tile_terrain(x, y)
            ter_all[t] += 1
            for k, v in w.tile_resources(x, y).items():
                res_all[k] += v
            row.append(t)
        rows.append(row)

    # ---- 地形图（起始格标 @；联盟/自己领土标 . ）----
    print("\n【地形图】  @ = 起始格   * = 起始格半径内   · = 自己领土")
    for y in range(n):
        line = []
        for x in range(n):
            ch = rows[y][x][0] if rows[y][x] else "?"
            if max(abs(x - hx), abs(y - hy)) <= RADIUS:
                ch = ch.upper()
            if (x, y) == (hx, hy):
                ch = "@"
            line.append(ch)
        print("  " + " ".join(line))
    print(f"  ★ 大写 = 起始格 {RADIUS} 格（切比雪夫）内")

    # ---- 起始格周边 ----
    near = [(x, y) for y in range(n) for x in range(n)
            if max(abs(x - hx), abs(y - hy)) <= RADIUS]
    ter_near = Counter(w.tile_terrain(x, y) for x, y in near)
    res_near = Counter()
    for x, y in near:
        for k, v in w.tile_resources(x, y).items():
            res_near[k] += v

    print(f"\n【地形分布】")
    print(f"  全图 {n*n} 格：" + "  ".join(f"{k}={v}" for k, v in ter_all.most_common()))
    print(f"  起始{RADIUS}格 {len(near)} 格：" + "  ".join(f"{k}={v}" for k, v in ter_near.most_common()))

    print(f"\n【资源总量】")
    print(f"  全图：" + "  ".join(f"{k}={v:,}" for k, v in sorted(res_all.items(), key=lambda t: -t[1])))
    print(f"  起始{RADIUS}格：" + "  ".join(f"{k}={v:,}" for k, v in sorted(res_near.items(), key=lambda t: -t[1])))

    # ---- 起始格半径内每一格有什么资源 ----
    print(f"\n【起始格 {RADIUS} 格内的资源格】(dx,dy) 为相对起始格偏移")
    any_res = False
    for x, y in sorted(near, key=lambda p: max(abs(p[0]-hx), abs(p[1]-hy))):
        r = {k: v for k, v in w.tile_resources(x, y).items() if v}
        if not r:
            continue
        any_res = True
        dist = max(abs(x - hx), abs(y - hy))
        print(f"  ({x-hx:+d},{y-hy:+d}) 距离{dist}  {w.tile_terrain(x,y):<4}  "
              + " ".join(f"{k}={v}" for k, v in sorted(r.items())))
    if not any_res:
        print("  （半径内没有任何资源格）")

    # ---- 最近的若干资源格（不限半径），看"要跑多远才有矿"----
    print(f"\n【全图离起始格最近的 12 个资源格】")
    cand = []
    for y in range(n):
        for x in range(n):
            r = {k: v for k, v in w.tile_resources(x, y).items() if v}
            if r:
                cand.append((max(abs(x-hx), abs(y-hy)), x, y, r))
    cand.sort()
    for d, x, y, r in cand[:12]:
        print(f"  距离{d:>2}  ({x+1},{y+1})  {w.tile_terrain(x,y):<4}  "
              + " ".join(f"{k}={v}" for k, v in sorted(r.items())))
    print()
