# -*- coding: utf-8 -*-
"""地形改版原型：柏林噪声（opensimplex）+ 河流 + 渡口 的渲染/扫线工具。

设计文档见《地形改版设计.md》；本文件所有参数与文档一致，可复现全部实验结果。

用法：
    python3 tools/terrain_preview.py --seed 42            # 渲染一张 50x50 地图
    python3 tools/terrain_preview.py --seed 11 --size 80  # 自定义尺寸
    python3 tools/terrain_preview.py --sweep              # 海平面-陆地占比扫线表

依赖：opensimplex（numpy）；venv 里 ~/.venv/bin/python 直接可跑。
"""

from __future__ import annotations

import argparse

import numpy as np
from opensimplex import OpenSimplex

# ---- 定稿参数（改这里前先更新设计文档）----
SCALE = 0.09      # 噪声频率：特征约 11 格
E_OCT = 4         # 海洋 fBm octave 数
M_OCT = 3         # 湿度 fBm octave 数
SEA = 0.30        # 海平面线（归一化海拔）；四种子实测陆地 79-83%
MTN = 0.78        # 山地线
DRY = 0.33        # 湿度低于此 → 沙漠
WET = 0.62        # 湿度高于此 → 森林
HILL = 0.58       # 其余海拔高于此 → 丘陵
RIV_PER_SIZE = 12 # 河流条数上限 = size / 此值
RIV_MIN_LEN = 8   # 路径短于此不成河
FORD_EVERY = 7    # 河上每 N 格 1 个渡口（河尾另保底 1 个）

CHAR = {"~": "海/湖", "M": "山地", "F": "森林", "H": "丘陵", "D": "沙漠", "P": "平原",
        "≈": "河流", "▲": "渡口"}


def gen_fields(seed: int, size: int) -> tuple[np.ndarray, np.ndarray]:
    """双 fBm：海拔 e 与湿度 m，各自归一化到 0..1。纯函数（seed+坐标决定）。"""
    x = np.arange(size) * SCALE
    y = np.arange(size) * SCALE
    e = sum(OpenSimplex(seed + i * 101).noise2array(x * (2 ** i), y * (2 ** i)) * (0.5 ** i)
            for i in range(E_OCT))
    m = sum(OpenSimplex(seed + 7777 + i * 101).noise2array(x * (2 ** i), y * (2 ** i)) * (0.5 ** i)
            for i in range(M_OCT))
    e = (e - e.min()) / (e.max() - e.min())
    m = (m - m.min()) / (m.max() - m.min())
    return e, m


def terrain_char(ev: float, mv: float) -> str:
    if ev < SEA:
        return "~"
    if ev > MTN:
        return "M"
    if mv < DRY:
        return "D"
    if mv > WET:
        return "F"
    return "H" if ev > HILL else "P"


def trace_rivers(e: np.ndarray, size: int, n_riv: int | None = None) -> list[list[tuple[int, int]]]:
    """从高处源头走最低下坡刻河，入海或内流盆地止。返回路径列表（含海中末格）。"""
    n_riv = n_riv or max(2, size // RIV_PER_SIZE)
    src_hi = max(SEA + 0.3, float(np.quantile(e, 0.92)))
    rivers: list[list[tuple[int, int]]] = []
    cands = sorted(((float(e[y, x]), x, y) for y in range(size) for x in range(size)
                    if e[y, x] > src_hi), reverse=True)
    for _, sx, sy in cands:
        if len(rivers) >= n_riv:
            break
        if any(abs(sx - p[0][0]) <= 5 and abs(sy - p[0][1]) <= 5 for p in rivers):
            continue  # 源头间隔 ≥5 格
        path: list[tuple[int, int]] = []
        seen: set[tuple[int, int]] = set()
        cx, cy = sx, sy
        while True:
            seen.add((cx, cy))
            path.append((cx, cy))
            if e[cy, cx] < SEA:
                break  # 入海
            be, best = float(e[cy, cx]), None
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    nx, ny = cx + dx, cy + dy
                    if 0 <= nx < size and 0 <= ny < size and (nx, ny) not in seen and e[ny, nx] < be:
                        be, best = float(e[ny, nx]), (nx, ny)
            if best is None:
                break  # 内流盆地
            cx, cy = best
        if len(path) >= RIV_MIN_LEN:
            rivers.append(path)
    return rivers


def render_map(seed: int, size: int) -> list[list[str]]:
    """生成整张地图的字符矩阵（含河流/渡口）。"""
    e, m = gen_fields(seed, size)
    grid = [[terrain_char(float(e[y, x]), float(m[y, x])) for x in range(size)] for y in range(size)]
    for path in trace_rivers(e, size):
        for i, (rx, ry) in enumerate(path):
            if e[ry, rx] < SEA:
                continue
            grid[ry][rx] = "▲" if (i % FORD_EVERY == 3 or i == len(path) - 2) else "≈"
    return grid


def show(seed: int, size: int) -> None:
    grid = render_map(seed, size)
    for row in grid:
        print("".join(row))
    cnt = {c: sum(r.count(c) for r in grid) for c in CHAR}
    land = 100 - cnt["~"] * 100 / (size * size)
    stats = " ".join(f"{CHAR[c]}{cnt[c]}" for c in CHAR)
    print(f"seed={seed} 陆地{land:.0f}% | {stats}\n")


def sweep(size: int) -> None:
    print(f"海平面线 → 陆地占比（size={size}）")
    for sea in (0.36, 0.33, 0.30, 0.27, 0.24):
        row = []
        for s in (11, 42, 2026, 7):
            e, _ = gen_fields(s, size)
            row.append(f"s{s}={(e >= sea).mean() * 100:.0f}%")
        print(f"  sea<{sea}: " + "  ".join(row))


def main() -> None:
    ap = argparse.ArgumentParser(description="地形改版原型（噪声+河流+渡口）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--size", type=int, default=50)
    ap.add_argument("--sweep", action="store_true", help="海平面-陆地占比扫线表")
    args = ap.parse_args()
    if args.sweep:
        sweep(args.size)
    else:
        show(args.seed, args.size)


if __name__ == "__main__":
    main()
