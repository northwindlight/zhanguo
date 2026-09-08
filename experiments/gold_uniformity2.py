#!/usr/bin/env python3
"""对比实验：金矿生成方案 A(现状 iid) vs B(分区配额) vs C(蓝噪声)。

背景：MT19937 数列均匀 ≠ 空间均匀。逐格独立抽样是泊松点过程，天然成簇
（上次实验实测分区 CV ≈ 泊松预测 1/√λ，证明随机数没问题）。想要"看起来/
事实上更均匀"，只能在生成设计上做文章。本实验三种方案同种子同图对比：

  A iid        现状：每格独立抽（gold_uniformity.py 的基线）
  B quota      分区配额：全图金量目标按 4×4 块均分（largest-remainder，
               每块只差 ±1），块内随机落点、随机 1/2 值（P(2)≈21% 与现状同分布）
  C bluenoise  蓝噪声：按目标密度随机撒点，但任意两金矿格切比雪夫距离 ≥2
               （直接消灭"一地金子"）

B/C 都把金矿与地形解耦——游戏里黄金矿场只看地块"黄金"资源值，不看地形，
所以地形-金矿相关性纯属风味，解耦不影响玩法。

指标除 gold_uniformity.py 的之外新增簇指标：
  nbr_ratio    有矿格中"隔壁也是矿"的比例（视觉成簇的直接来源）
  cluster_max  最大连片金矿格数（8 邻域连通块）

用法：python3 gold_uniformity2.py [--trials 100]
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import sys
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from game import TERRAINS, TERRAIN_WEIGHTS  # noqa: E402
from gold_uniformity import chebyshev_dist_to_gold, gini, map_gold  # noqa: E402

BLOCK = 4  # 配额分区边长

# 从 game.py 权重表现算当前分布的特征值，不硬编码
_TW = sum(TERRAIN_WEIGHTS.values())


def _p_ter(t: str) -> float:
    return TERRAIN_WEIGHTS[t] / _TW


def _w(t: str) -> list[int]:
    return TERRAINS[t]["黄金"]


_P2 = sum(_p_ter(t) * (_w(t)[2] / sum(_w(t)) if len(_w(t)) > 2 else 0)
          for t in TERRAIN_WEIGHTS)
DENSITY = sum(_p_ter(t) * sum(_w(t)[1:]) / sum(_w(t)) for t in TERRAIN_WEIGHTS)
P2_GIVEN_GOLD = _P2 / DENSITY  # 有矿格里值为 2 的占比


def gen_quota(seed: int, size: int) -> dict[tuple[int, int], int]:
    """B：4×4 块配额均分，块内随机落点。返回整张网格（无矿格=0）。"""
    rng = random.Random(seed)
    gold = {(x, y): 0 for x in range(size) for y in range(size)}
    nb = math.ceil(size / BLOCK)
    nblock = nb * nb
    total_target = DENSITY * size * size  # 矿点数配额（与现状密度对齐）
    base = int(total_target // nblock)
    rem = round(total_target - base * nblock)
    quotas = [base + 1 if i < rem else base for i in range(nblock)]
    rng.shuffle(quotas)
    for bi in range(nb):
        for bj in range(nb):
            q = quotas[bi * nb + bj]
            cells = [(x, y) for x in range(bi * BLOCK, min((bi + 1) * BLOCK, size))
                     for y in range(bj * BLOCK, min((bj + 1) * BLOCK, size))]
            rng.shuffle(cells)
            for x, y in cells[:min(q, len(cells))]:
                gold[(x, y)] = 2 if rng.random() < P2_GIVEN_GOLD else 1
    return gold


def gen_bluenoise(seed: int, size: int, min_d: int = 2) -> dict[tuple[int, int], int]:
    """C：目标密度撒点 + 最小间距（拒绝采样），值分布同现状。"""
    rng = random.Random(seed)
    want = round(DENSITY * size * size)
    cells = [(x, y) for x in range(size) for y in range(size)]
    rng.shuffle(cells)
    pts: list[tuple[int, int]] = []
    for x, y in cells:
        if len(pts) >= want:
            break
        if all(max(abs(x - px), abs(y - py)) >= min_d for px, py in pts):
            pts.append((x, y))
    gold = {(x, y): 0 for x in range(size) for y in range(size)}
    for p in pts:
        gold[p] = 2 if rng.random() < P2_GIVEN_GOLD else 1
    return gold


def gold_metrics(gold: dict[tuple[int, int], int], size: int) -> dict:
    per_tile = list(gold.values())
    total = sum(per_tile)
    n = len(per_tile)
    gset = {p for p, g in gold.items() if g >= 1}

    # 连通簇（8 邻域）
    seen: set = set()
    comp_sizes = []
    for p in gset:
        if p in seen:
            continue
        stack, c = [p], 0
        seen.add(p)
        while stack:
            x, y = stack.pop()
            c += 1
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if (dx or dy) and q in gset and q not in seen:
                        seen.add(q)
                        stack.append(q)
        comp_sizes.append(c)
    nbr = sum(1 for (x, y) in gset
              if any((x + dx, y + dy) in gset
                     for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy))

    # 4×4 分区
    sec = [0] * 16
    cell = size / 4
    for (x, y), g in gold.items():
        sec[min(int(x / cell), 3) * 4 + min(int(y / cell), 3)] += g
    m = total / 16

    near = chebyshev_dist_to_gold(gold, size) if gset else [size] * n
    return {
        "density": len(gset) / n,
        "total": total,
        "sector_cv": statistics.pstdev(sec) / m if m > 0 else float("nan"),
        "sector_max_ratio": max(sec) / m if m > 0 else float("nan"),
        "gini": gini(per_tile),
        "near_mean": statistics.mean(near),
        "near_max": max(near),
        "nbr_ratio": nbr / len(gset) if gset else 0.0,
        "cluster_max": max(comp_sizes) if comp_sizes else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trials", type=int, default=100)
    ap.add_argument("--size-min", type=int, default=50)
    ap.add_argument("--size-max", type=int, default=100)
    args = ap.parse_args()

    rng = random.Random(20260909)
    variants = {"A_iid": map_gold, "B_quota": gen_quota, "C_bluenoise": gen_bluenoise}
    rows = {k: [] for k in variants}
    for _ in range(args.trials):
        seed, size = rng.randrange(1 << 31), rng.randint(args.size_min, args.size_max)
        for name, gen in variants.items():
            rows[name].append(gold_metrics(gen(seed, size), size))

    keys = [("density", "有矿格占比"), ("sector_cv", "分区CV↓"), ("sector_max_ratio", "最富/均↓"),
            ("near_mean", "距矿均↓"), ("near_max", "距矿最远↓"),
            ("nbr_ratio", "矿贴矿比↓"), ("cluster_max", "最大矿簇↓"), ("gini", "基尼")]
    name_w = max(len(k) for k in variants)
    print(f"trials={args.trials} size={args.size_min}..{args.size_max} "
          f"(目标密度 {DENSITY:.4f}，P(2|矿)={P2_GIVEN_GOLD:.3f})\n")
    print(f"{'指标':<10}" + "".join(f"{k:>{name_w+14}}" for k in variants))
    for key, label in keys:
        line = f"{label:<10}"
        for name in variants:
            vals = [r[key] for r in rows[name]]
            line += f"  mean={statistics.mean(vals):7.3f} p90={sorted(vals)[(len(vals)*9)//10]:7.3f}"
        print(line)


if __name__ == "__main__":
    main()
