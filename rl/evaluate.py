# -*- coding: utf-8 -*-
"""**打分器**：给一个局面打分（用户 2026-09-24：「你先写搜索逻辑和打分器」）。

    它是 MCTS 的**叶子评估**，也是将来价值网络的**老师/对照**
    —— 网络的价值头可以从它的评分里学（或直接蒸馏），所以先把它写成**规则版**：
    不依赖任何训练、当场可算、可解释。

    用户 2026-09-24 补的一条设计（决定了本文件为什么这么写）
    ────────────────────────────────────────────────────
    「我让你抄的是 v11plus 的**目标评估**、**编组逻辑**，谁让你一个字都不写了，
      直接拿一个半成品用?」⇒ 手写"守家/侦察"那种**策略**层是走不通的
      （实测：把"自家核心"当候选扔进 `grouping._solve`，它 `reach=0/spread=0`
      ⇒ 代价恒 0 ⇒ 求解器把全军派回家、谁也不动）。
    ⇒ **策略交给搜索**（MCTS 能搜出"家里空了会输"），本文件只管**评估一个静态局面**。

    打分构成（量纲：1 分 ≈ 1 格国土；权重都可调）
    ─────────────────────────────────────────────
      · **国祚**：自己没了 = `-INF`，对手没了 = `+INF`（终局信号，压过一切）
      · **国土差 / 兵力差 / 血量差**
      · **逼近**：我军离对手核心越近越好，敌军离我核心越近越糟
      · **迷雾纪律**：只数**看得见**的敌军（看不见的不许猜 —— 那是 v9 堵掉的越权之一）
"""
from __future__ import annotations

INF = 1e9

# 权重 —— 按用户 2026-09-24 的口径定：
#   「**越近敌方分越高，越多地分越高，丢地扣小分，损兵扣大分，丢家直接输**」
W_TILE = 1.0      # 国土：多一格 +1、丢一格 −1（**小分**）
W_ARMY = 10.0     # ★ 我的兵：**损兵是大分** —— 一支兵压过十格地
W_KILL = 6.0      # ★ 敌的兵：**消灭敌军也加分**（略低于自己的兵 —— 造兵要回合，别人的不用）
W_HP = 0.02       # 每点 hp：伤而不死只是小账（100 hp 全损 = 2 分）
W_NEAR = 1.5      # ★ 逼近：我军离敌核每近一格 +1.5，被逼近对称地扣


def score(world, me: str, enemy: str) -> float:
    """从 `me` 的视角打分：**正 = 我占优**。

    ★ **对称性**：`score(w, a, b) == -score(w, b, a)`（除了 ±INF 那两档也一样对称）
      —— MCTS 自对弈时两边共用同一个评估函数，这条必须成立。
    """
    if not world.has_townhall(me):
        return -INF
    if not world.has_townhall(enemy):
        return +INF

    s = W_TILE * (tiles(world, me) - tiles(world, enemy))
    # ★ 兵分两项写（用户：「损兵扣大分」+「**消灭敌军也要加分**」）：
    #   若合成"兵力差"，1 换 1 会抵消成 0 分 ⇒ "消灭敌军"等于白干。
    #   分开写 ⇒ 灭敌**总是**加分（哪怕自己也在损），而损兵**总是**扣分。
    s += W_ARMY * len(armies(world, me))
    s -= W_KILL * len(armies(world, enemy))
    s += W_HP * (hp_total(world, me) - hp_total(world, enemy))

    mc, ec = core_of(world, me), core_of(world, enemy)
    if mc and ec:
        # 离对手核心越近 ⇒ `-dist` 越大 ⇒ 分越高；被逼近则相反
        s += W_NEAR * (-min_dist(world, me, ec) + min_dist(world, enemy, mc))
    return s


def terminal(world, me: str, enemy: str) -> float | None:
    """终局分（一方国祚尽失）。没结束 ⇒ `None`。"""
    a, b = world.has_townhall(me), world.has_townhall(enemy)
    if a and b:
        return None
    if not a and not b:
        return 0.0
    return INF if a else -INF


# ---------------------------------------------------------------- 小工具
def tiles(world, name: str) -> int:
    return sum(1 for t in world.tiles.values() if t["owner"] == name)


def armies(world, name: str) -> list[dict]:
    return [a for a in world.armies if a["owner"] == name and a.get("hp", 0) > 0]


def hp_total(world, name: str) -> int:
    return sum(a.get("hp", 0) for a in armies(world, name))


def core_of(world, name: str) -> tuple[int, int] | None:
    """该国的市政厅格 = 核心 = 国祚。"""
    for cell, t in sorted(world.tiles.items()):
        if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0:
            return cell
    return None


def min_dist(world, name: str, cell, *, visible_only: bool = False,
             mask=None) -> int:
    """`name` 的军队到 `cell` 的**最近切比雪夫距离**（没有军 ⇒ 返回一个大数）。"""
    if visible_only and mask is not None:
        pool = [a for a in armies(world, name) if (a["x"], a["y"]) in mask]
    else:
        pool = armies(world, name)
    if not pool:
        return 99
    return min(max(abs(a["x"] - cell[0]), abs(a["y"] - cell[1])) for a in pool)