#!/usr/bin/env python3
"""金矿均匀度实验：随机种子 + 随机地图大小，统计全图金矿分布。

复用游戏的抽法（game.py）：
  地形   = roll_terrain(Random(f"{seed}:{x}:{y}"))   # 与 World.tile_terrain 完全一致
  资源   = roll_resources(rng, 地形)                  # 黄金 ∈ {0,1,2}，权重随地形

注意：实际游戏里 roll_resources 用的是世界 RNG（依赖占地顺序），实验改为每格
独立抽（同一个 f"{seed}:{x}:{y}" 流接着抽）。逐格的具体值因此和某次真实对局
对不上，但分布完全相同——对统计均匀度没有影响。

指标（每张图）：
  density      有矿格占比（黄金≥1 的格 / 总格数）
  total        全图黄金总量（= 可建黄金矿场上限之和）
  sector_cv    4×4 分区黄金量的变异系数（std/mean，越小越均匀）
  sector_max_ratio  最富分区 / 平均分区（"某角独肥"程度，1=绝对均匀）
  gini         逐格黄金的基尼系数（含大量 0 格，衡量贫富集中）
  near_mean / near_max   每格到最近金矿格的国际象棋距离：平均 / 最远
                         （"离矿最远的格子有多惨"，越小越均匀）

用法：
  python3 gold_uniformity.py                 # 默认 200 张随机图
  python3 gold_uniformity.py --trials 500 --size-min 30 --size-max 100
输出：逐图明细 CSV + 汇总表（stdout）。
"""
from __future__ import annotations

import argparse
import csv
import random
import statistics
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from game import roll_resources, roll_terrain  # noqa: E402

SECTORS = 4  # 空间均匀度按 4×4 分区统计


def map_gold(seed: int, size: int) -> dict[tuple[int, int], int]:
    """整张图的每格黄金值（模拟 World.tile_terrain + roll_resources 的分布）。"""
    gold = {}
    for x in range(size):
        for y in range(size):
            rng = random.Random(f"{seed}:{x}:{y}")
            res = roll_resources(rng, roll_terrain(rng))
            gold[(x, y)] = res["黄金"]
    return gold


def chebyshev_dist_to_gold(gold: dict[tuple[int, int], int], size: int) -> list[int]:
    """多源 BFS（8 邻域）→ 每格到最近金矿格的切比雪夫距离。"""
    src = [p for p, g in gold.items() if g >= 1]
    dist = {p: 0 for p in src}
    q = deque(src)
    while q:
        x, y = q.popleft()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < size and 0 <= ny < size and (nx, ny) not in dist:
                    dist[(nx, ny)] = dist[(x, y)] + 1
                    q.append((nx, ny))
    return [dist[p] for p in gold]


def gini(values: list[int]) -> float:
    v = sorted(values)
    n = len(v)
    s = sum(v)
    if s == 0:
        return 0.0
    return (2 * sum((i + 1) * x for i, x in enumerate(v))) / (n * s) - (n + 1) / n


def metrics(seed: int, size: int) -> dict:
    gold = map_gold(seed, size)
    per_tile = list(gold.values())
    total = sum(per_tile)
    n = len(per_tile)

    # 4×4 分区黄金量 → 空间均匀度
    sec = [0] * (SECTORS * SECTORS)
    cell = size / SECTORS
    for (x, y), g in gold.items():
        sec[min(int(x / cell), SECTORS - 1) * SECTORS + min(int(y / cell), SECTORS - 1)] += g
    m = total / len(sec)
    sec_cv = (statistics.pstdev(sec) / m) if m > 0 else float("nan")
    sec_max = max(sec) / m if m > 0 else float("nan")

    near = chebyshev_dist_to_gold(gold, size)

    return {
        "seed": seed,
        "size": size,
        "tiles": n,
        "gold_tiles": sum(1 for g in per_tile if g >= 1),
        "density": sum(1 for g in per_tile if g >= 1) / n,
        "total": total,
        "sector_cv": sec_cv,
        "sector_max_ratio": sec_max,
        "gini": gini(per_tile),
        "near_mean": statistics.mean(near),
        "near_max": max(near),
    }


def fmt_row(key: str, vals: list[float], unit: str = "") -> str:
    return (f"  {key:<16} mean={statistics.mean(vals):8.4f}  "
            f"median={statistics.median(vals):8.4f}  "
            f"p10={sorted(vals)[len(vals)//10]:8.4f}  "
            f"p90={sorted(vals)[(len(vals)*9)//10]:8.4f} {unit}")


def main() -> None:
    ap = argparse.ArgumentParser(description="金矿均匀度实验")
    ap.add_argument("--trials", type=int, default=200, help="随机图张数")
    ap.add_argument("--size-min", type=int, default=30, help="地图边长下限")
    ap.add_argument("--size-max", type=int, default=100, help="地图边长上限")
    ap.add_argument("--seed", type=int, default=None, help="实验本身的随机种子（默认随机）")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    print(f"实验种子={rng.getrandbits(31) if args.seed is None else args.seed} "
          f"张数={args.trials} 边长={args.size_min}..{args.size_max}\n")

    rows = [metrics(rng.randrange(1 << 31), rng.randint(args.size_min, args.size_max))
            for _ in range(args.trials)]

    out = Path(__file__).resolve().parent / "out"
    out.mkdir(exist_ok=True)
    csv_path = out / "gold_uniformity.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    print(f"逐图明细已写入 {csv_path}\n")
    print("== 全部汇总 ==")
    print(fmt_row("density", [r["density"] for r in rows], "(有矿格占比)"))
    print(fmt_row("total", [r["total"] for r in rows], "(全图金量)"))
    print(fmt_row("sector_cv", [r["sector_cv"] for r in rows], "(4×4分区CV↓)"))
    print(fmt_row("sector_max_ratio", [r["sector_max_ratio"] for r in rows], "(最富/平均↓)"))
    print(fmt_row("gini", [r["gini"] for r in rows], "(基尼↓)"))
    print(fmt_row("near_mean", [r["near_mean"] for r in rows], "(距矿均值↓)"))
    print(fmt_row("near_max", [r["near_max"] for r in rows], "(距矿最远↓)"))

    # 按边长分桶看趋势：均匀度是否随地图大小变化
    buckets = [("小(<50)", lambda s: s < 50), ("中(50-79)", lambda s: 50 <= s < 80),
               ("大(≥80)", lambda s: s >= 80)]
    print("\n== 按地图大小分桶（4×4 分区 CV 越小越均匀）==")
    for label, pred in buckets:
        sub = [r for r in rows if pred(r["size"])]
        if not sub:
            continue
        print(f"  {label:<10} n={len(sub):3d}  sector_cv={statistics.mean(r['sector_cv'] for r in sub):.4f}  "
              f"near_mean={statistics.mean(r['near_mean'] for r in sub):.3f}  "
              f"density={statistics.mean(r['density'] for r in sub):.4f}")


if __name__ == "__main__":
    main()
