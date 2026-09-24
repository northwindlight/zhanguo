# -*- coding: utf-8 -*-
"""**打分器**：给一个局面打分（用户 2026-09-24：「你先写搜索逻辑和打分器」）。

    它是 MCTS 的**叶子评估**、PPO 的**势函数**（奖励取其差分），也是将来价值网络的老师。

    ★★★ 铁律：**只对可见视野打分**（用户 2026-09-24）
    ────────────────────────────────────────────────
    「你怎么知道敌军离你多远，**这不是暴露信息了吗**，**打分只对可见视野打分**」。

    旧版（第一稿）用了全图：`min_dist(world, enemy, mc)`（敌军到我核的距离）、
    `len(armies(world, enemy))`（敌军总数）、`tiles(world, enemy)`（敌方国土）——
    那是**打分器作弊**：它会奖励"朝一个我根本看不见的敌人走过去"，而那个信号在真实
    对局里**不存在**。模型照着它学，学到的是**上帝视角策略**，一进有迷雾的对局就废。

    这与旧线记过的"**四处越权偷看**"（`ruleai/v9.py`）是同一类 ——
    v9 当年就是靠 `visible_to` 门控把那些堵掉的（`zhanguo_ruleai_v9`）。

    ⇒ 本文件所有涉及**敌方**的量（军队、国土、核心、血量）**一律过 `mask`**；
      **自己**的量全知（那是应该的）。
      `mask=None` ⇒ 全知模式，**只该用于无迷雾的诊断/对照，别进训练**。

    打分构成（量纲：1 分 ≈ 1 格国土；权重都可调）
    ─────────────────────────────────────────────
      · **国祚**：自己没了 = `-INF`，对手没了 = `+INF`（亡国是**公开事件**，不算偷看）
      · **国土差 / 兵力差 / 血量差**（敌方那半边过 mask）
      · **逼近**：我军离敌核越近越好、敌军离我核越近越糟（**两边都得看得见才算**）
      · ★ **安全 / 防御**（用户：「怎么不可能回防，5 支军队赖着主城不动压根输不了，
        是目前的打分模型**没有奖励防御，没有安全扣分机制**」）
"""
from __future__ import annotations

INF = 1e9

# 权重 —— 按用户口径定：「越近敌方分越高，越多地分越高，丢地扣小分，损兵扣大分，丢家直接输」
W_TILE = 1.0      # 国土：多一格 +1、丢一格 −1（**小分**）
W_ARMY = 10.0     # ★ 我的兵：**损兵是大分** —— 一支兵压过十格地
W_KILL = 6.0      # ★ 敌的兵：**消灭敌军也加分**（略低于自己的兵 —— 造兵要回合，别人的不用）
W_HP = 0.02       # 每点 hp：伤而不死只是小账
W_NEAR = 1.5      # ★ 逼近：我军离敌核每近一格 +1.5，被逼近对称地扣

# ★★ 安全 / 防御（用户 2026-09-24 指出打分器缺这两样）
#   旧版只有 `W_NEAR`，而它对攻守**名义上对称、实际上只奖励进攻**：
#   守家不会让"我离敌核"变近 ⇒ 守家**白守**，模型当然一路冲出去。
THREAT_R = 6      # 威胁判定半径（敌军进到离我核这么近才算"家有事"）
GUARD_R = 3       # "守在家附近"的半径
W_THREAT = 3.0    # ★ 被逼近：按逼近强度扣分
W_GUARD = 5.0     # ★ 守家：按逼近强度 × 守家军数**加分**（只在有威胁时生效）


def score(world, me: str, enemy: str, mask=None) -> float:
    """从 `me` 视角打分（正 = 我占优）。★ **敌方的一切都过 `mask`**（见文件头）。

    `mask` = `pathfind.vision_mask(world, me)` 的集合；`None` ⇒ 全知（只给诊断用）。
    """
    if not world.has_townhall(me):
        return -INF
    if not world.has_townhall(enemy):
        return +INF                       # 亡国是**公开事件**，不算偷看

    s = W_TILE * (tiles(world, me) - tiles(world, enemy, mask))
    s += W_ARMY * len(armies(world, me))               # 我方：全知
    s -= W_KILL * len(armies(world, enemy, mask))      # ★ 敌方：**只数看得见的**
    s += W_HP * (hp_total(world, me) - hp_total(world, enemy, mask))

    mc = core_of(world, me)
    ec = core_of(world, enemy, mask)                   # ★ 看不见敌核 ⇒ None
    if mc is not None and ec is not None:
        my_d = min_dist(world, me, ec)                 # 我离敌核（我方位置全知）
        foe_d = min_dist(world, enemy, mc, mask)       # ★ 敌离我核：**只数看得见的敌军**
        s += W_NEAR * (-my_d + foe_d)

        # ★★ 安全 / 防御 —— **只在真有威胁时生效**：
        #   没威胁时守家**不加分**，否则模型会永远缩在核心格不动（另一个极端）。
        #   有威胁时：被逼近扣分 + **守家的军按逼近强度加分** ⇒ "赖在主城"第一次有了收益，
        #   "回来拦"也有了收益 —— 攻守这才对称。
        if foe_d <= THREAT_R:
            intensity = (THREAT_R - foe_d + 1) / float(THREAT_R)
            s -= W_THREAT * intensity
            guards = sum(1 for a in armies(world, me)
                         if max(abs(a["x"] - mc[0]), abs(a["y"] - mc[1])) <= GUARD_R)
            s += W_GUARD * intensity * guards
    return s


def terminal(world, me: str, enemy: str) -> float | None:
    """终局分（一方国祚尽失）。没结束 ⇒ `None`。**亡国是公开事件，不需要视野。**"""
    a, b = world.has_townhall(me), world.has_townhall(enemy)
    if a and b:
        return None
    if not a and not b:
        return 0.0
    return INF if a else -INF


# ---------------------------------------------------------------- 小工具（★都可过 mask）
def _vis(cell, mask) -> bool:
    return mask is None or cell in mask


def tiles(world, name: str, mask=None) -> int:
    """国土数。★ 数**敌方**时必须传 `mask`（视野外看不见谁占了哪）。"""
    return sum(1 for cell, t in world.tiles.items()
               if t["owner"] == name and _vis(cell, mask))


def armies(world, name: str, mask=None) -> list[dict]:
    """军队。★ 数**敌方**时必须传 `mask`（看不见的敌军不算 —— 这是本轮修的那个漏洞）。"""
    return [a for a in world.armies
            if a["owner"] == name and a.get("hp", 0) > 0 and _vis((a["x"], a["y"]), mask)]


def hp_total(world, name: str, mask=None) -> int:
    return sum(a.get("hp", 0) for a in armies(world, name, mask))


def core_of(world, name: str, mask=None):
    """该国的市政厅格 = 核心 = 国祚。★ 看不见 ⇒ `None`（**厅也要在视野内才知道在哪**，
    引擎 `_public_buildings` 原话："看不见就打不着"）。"""
    for cell, t in sorted(world.tiles.items()):
        if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0 and _vis(cell, mask):
            return cell
    return None


def min_dist(world, name: str, cell, mask=None) -> int:
    """`name` 的军队到 `cell` 的**最近切比雪夫距离**（没有军 ⇒ 返回 99）。

    ★ 传 `mask` 时只算**看得见**的军 —— 打分器要算"敌军离我核多远"就必须走这条，
      否则就是隔着迷雾点名敌军位置（用户 2026-09-24 抓到的那个漏洞）。
    """
    pool = armies(world, name, mask)
    if not pool:
        return 99
    return min(max(abs(a["x"] - cell[0]), abs(a["y"] - cell[1])) for a in pool)