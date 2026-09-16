# -*- coding: utf-8 -*-
"""候选池：**哪些格是「可以去占」的**（编组的前置一步，不再负责排序）。

用户 2026-09-15 定的模型（`docs/v11编组模型.md`）里，**没有"榜"** —— 目标不是被打分排序后
逐个配兵，而是与军队一起做**全局编组**（`grouping.py`）。所以本模块只剩一件事：
把"可攻且看得见"的格铺出来，交给编组去认领。

★ 候选池必须是**全局**的（用户：「任何军队一定会找到目标，因为是全局编组」）：
  不是"自家军队旁边那一圈"，而是视野内**全部**可攻格 —— 边境永远非空（有一块地就有
  一圈无主邻格），所以无目标的军总能找到认领对象，"待命"这个状态才不存在。

三源（v10 只有第一源的一部分）：

  ① `world.frontier_of(name)` —— 自家地块外那一圈**未物化**的格（天生可见、必然无主）；
  ② 看得见的**野人驻军**格（有东西要打，得先打赢）；
  ③ 看得见的、**与我在交战**的国家领土 —— 战役层，v10 完全没有这一源。

★ 两个坑：
  · **中立国的领土一格都不收**：引擎里对中立地 `atk` 会撞墙，而撞墙会**烧掉整队的
    移动额度**（`_blind_cost`），白亏一回合。要打先宣战。
  · 候选**不能**用 `world._atk_target_ok` 过滤 —— 那个谓词问的是"这格有没有东西可打"，
    而**空野地是合法的 atk 目标**（进驻占领），用它会把最该占的空地全筛掉。

★ 迷雾纪律：本模块**一格资源都不读**（排序已经不估值了，也不需要读）。
  未物化格的资源是不可知的（占地时才由 `mapgen` 掷，玩家永远看不到；
  v9 当年堵掉的四处越权之一就是偷看它）。
"""
from __future__ import annotations

from .pathfind import chebyshev


def candidates(world, name: str, mask, *, radius: int, limit: int) -> list[tuple]:
    """候选格（**有界、便宜、只看可见**），按坐标排序返回。

    排序是**确定**的（哨兵：`frontier_of` 是裸 set，必须先 `sorted`），
    因为下游的编组求解器按坐标顺序枚举候选 —— 顺序不稳就是结果不稳。
    """
    bloc = world.bloc_of(name)
    members = set(bloc["members"]) if bloc else set()
    anchors = [(x, y) for (x, y), t in sorted(world.tiles.items())
               if t["owner"] == name or t["owner"] in members]
    anchors += [(a["x"], a["y"]) for a in world.nation_armies(name)]

    def near(cell: tuple) -> int:
        """到最近锚点的切比雪夫距离 —— **只算 `radius` 以内的锚点**。

        ★ 2026-09-16：原先每个候选格都 `min(chebyshev(c, p) for p in anchors)`，
          而锚点 = 自家/盟国地块 + 自家军队（后期三四百个），候选 40 个，还**算两遍**
          （筛一遍、排序一遍）⇒ 一回合上万次函数调用 + 九百万次 `abs`（实测占整局 16%）。
        ★ 剪枝为什么**逐值等价**（这两处用途各自都不受影响）：
          · **筛**用的是 `near <= radius`：`chebyshev > radius` 的锚点不可能给出更小的值，
            全被剪掉时"真值 > radius" ⇒ 判否，与 `radius + 1` 同效；
          · **排序**只在"筛过了的格"之间比，那些格的 near 都 ≤ radius ⇒ 剪掉的锚点
            本来也参与不进最小值。
        """
        cx, cy = cell
        best = radius + 1
        for px, py in anchors:
            dx = cx - px
            if dx < 0:
                dx = -dx
            if dx > radius:
                continue
            dy = cy - py
            if dy < 0:
                dy = -dy
            if dy > radius:
                continue
            d = dx if dx > dy else dy
            if d < best:
                best = d
        return best if anchors else 0            # 锚点为空时 `min(..., default=0)` 的原样

    out: set = set()
    for cell in sorted(world.frontier_of(name)):          # ★ frontier 是裸 set ⇒ 必须排序
        if cell in mask:
            out.add(cell)
    for a in sorted(world.armies, key=lambda a: a["id"]):
        if a["owner"] == "野人" and a.get("hp", 0) > 0 and (a["x"], a["y"]) in mask:
            out.add((a["x"], a["y"]))
    for cell, t in sorted(world.tiles.items()):
        if cell in mask and world.war_between(name, t["owner"]):
            out.add(cell)

    # 半径粗筛 + 按 (距离, 坐标) 排序 —— **每格只算一次 near**（原先算两遍）
    scored = sorted((near(c), c) for c in sorted(out))
    kept = [c for n, c in scored if n <= radius]
    return sorted(kept[:limit])