# -*- coding: utf-8 -*-
"""规则表 → **模型输入的内容向量**（2026-09-12 新增，见 `TOKEN_DESIGN.md` §10.2）。

为什么要有这一层
----------------
在这之前，模型的候选嵌入是 `nn.Embedding(sub_idx)` —— **纯下标**，语义是训练时拟合
进去的。所以「石油能源厂降价 240→180」这种事模型**看不见**：`π(a|s)` 只能依赖 `s` 里
有的东西，而造价不在 `s` 里，它在同一个盘面上给出的决策一模一样，而最优决策已经变了。
（这是**表达能力**问题，不是拟合精度问题 —— `PLAN.md` 〇.5 有完整推理。）

这一层把引擎表的数值变成**每次现算的向量**，接到两处：窗口侧的 `b`/`u` token 组
（给价值头与注意力）与候选嵌入 `se`（给策略）。配合训练期的域随机化（§10.4），
模型学到的才是「**给定这组数值该怎么打**」，而不是「这一个价格点的答案」。

两条纪律
--------
1. **每次现算，不许做模块级缓存**。域随机化会在每局开始时**就地改 `game.*` 的表**
   （`mp.py` 是 `from game import BUILDINGS`，同一个 dict 对象），缓存会把"上一局的数值"
   烤进这一局 —— 而那种错**不报错**。
2. **归一化尺度只写在这个文件里**。调用点不许再除一次、也不许"看着办"：
   训练分布、随机化幅度、模型输入的可比性全依赖它。
"""
from __future__ import annotations

import numpy as np

import game
from rl import vocab as V

# ===========================================================================
# 1. 冻结的枚举与宽度（改这些 = 改模型输入宽度 = 旧 ckpt 全废，见 §10.6）
# ===========================================================================
# 建筑的 `kind` one-hot。**11 项，与 `game.BUILDINGS` 现值一一对应**：
#   academy/barracks/castle/diplomat/energy/extract/factory/gold/militia_camp/tower/townhall
# ⚠ 加一种新 kind（比如地形改版计划里的"港口"）会**加宽这个 one-hot** ——
#   那是又一次"宽度变更 + 重炼"。要么现在预留几个 kind 槽，要么认下这一条。
#   （待定项，记在 §10.9 之后；本文件先按 11 项实现。）
BUILD_KINDS = ("academy", "barracks", "castle", "diplomat", "energy", "extract",
               "factory", "gold", "militia_camp", "tower", "townhall")

# `cap_resource` 的 one-hot：5 种地块资源 + "无" = 6
CAP_RES_SLOTS = V.TILE_RES + ("",)

# 宽度（这三行是模型的输入契约，别在别处再算一遍）
F_B = len(BUILD_KINDS) + 1 + 1 + len(CAP_RES_SLOTS) + 3 * len(V.STOCK) + 5   # = 45
F_U = 1 + 1 + 1 + 1 + len(V.STOCK)                                           # = 11
F_G = 2                                                                      # 基准价 + 深度

# ===========================================================================
# 2. 归一化尺度（唯一来源）
# ===========================================================================
# 建筑：cost÷2000（城堡满级 1600 是量级上限）、wood÷100（市政厅 40）、
#       outputs/inputs/fuel 各按 STOCK 7 维、量级 ÷4（典型 1~2）、
#       energy_out÷8（石油能源厂 8）、energy÷4、limit÷8、min_slots÷20（MAX_SLOTS）、
#       max_level÷5（城堡 5 级）
_COST_DIV, _WOOD_DIV, _GOODS_DIV = 2000.0, 100.0, 4.0
_ENERGY_OUT_DIV, _ENERGY_DIV = 8.0, 4.0
_LIMIT_DIV, _SLOTS_DIV, _LEVEL_DIV = 8.0, 20.0, 5.0
# 兵种：hp÷200、speed÷2、supply÷2、atk÷100、recruit 7 维 ÷10
_HP_DIV, _SPEED_DIV, _SUPPLY_DIV, _ATK_DIV, _RECRUIT_DIV = 200.0, 2.0, 2.0, 100.0, 10.0
# 物资：基准价÷10（黄金 10 是量级上限）、深度÷24（粮食/木头 24 是上限）
_PRICE_DIV, _DEPTH_DIV = 10.0, 24.0


def _stock_vec(d: dict | None) -> list[float]:
    """按 `V.STOCK` 的固定顺序把一个 {物资: 数量} 摊成 7 维（缺的填 0）。"""
    d = d or {}
    return [float(d.get(g, 0)) / _GOODS_DIV for g in V.STOCK]


# ===========================================================================
# 3. 单项向量（**每次现算**，读的是当下的 game 表）
# ===========================================================================
def building_vector(name: str) -> np.ndarray:
    """一座建筑的内容向量 `(F_B,)`。**不在引擎表里的项（留位槽）→ 全零。**

    零向量是有意义的：留位槽 `mask=0`，模型不会去看它；等它被填成真建筑时，
    向量自然长出内容，**宽度不变**。
    """
    out = np.zeros(F_B, np.float32)
    b = game.BUILDINGS.get(name)
    if b is None:
        return out
    i = 0
    kind = b.get("kind", "")
    if kind in BUILD_KINDS:
        out[i + BUILD_KINDS.index(kind)] = 1.0
    i += len(BUILD_KINDS)
    out[i] = float(b.get("cost", 0) if not isinstance(b.get("cost"), list)
                   else b["cost"][0]) / _COST_DIV          # 城堡取 L1 造价
    i += 1
    out[i] = float(b.get("wood", 0)) / _WOOD_DIV
    i += 1
    cr = b.get("cap_resource") or ""
    if cr in CAP_RES_SLOTS:
        out[i + CAP_RES_SLOTS.index(cr)] = 1.0
    i += len(CAP_RES_SLOTS)
    out[i:i + len(V.STOCK)] = _stock_vec(b.get("outputs"))
    i += len(V.STOCK)
    out[i:i + len(V.STOCK)] = _stock_vec(b.get("inputs"))
    i += len(V.STOCK)
    out[i:i + len(V.STOCK)] = _stock_vec(b.get("fuel"))
    i += len(V.STOCK)
    out[i] = float(b.get("energy_out", 0)) / _ENERGY_OUT_DIV
    out[i + 1] = float(b.get("energy", 0)) / _ENERGY_DIV
    out[i + 2] = float(b.get("limit", 0)) / _LIMIT_DIV
    out[i + 3] = float(b.get("min_slots", 0)) / _SLOTS_DIV
    out[i + 4] = float(b.get("max_level", 0)) / _LEVEL_DIV
    return out


def unit_vector(kind: str) -> np.ndarray:
    """一个兵种的内容向量 `(F_U,)`（不在引擎表里的留位槽 → 全零）。"""
    out = np.zeros(F_U, np.float32)
    u = game.UNIT_TYPES.get(kind)
    if u is None:
        return out
    out[0] = float(u.get("hp", 0)) / _HP_DIV
    out[1] = float(u.get("speed", 0)) / _SPEED_DIV
    out[2] = float(u.get("supply", 0)) / _SUPPLY_DIV
    out[3] = float(u.get("atk", 0)) / _ATK_DIV
    out[4:4 + len(V.STOCK)] = [float(u.get("recruit", {}).get(g, 0)) / _RECRUIT_DIV
                               for g in V.STOCK]
    return out


def good_vector(good: str) -> np.ndarray:
    """一种物资的内容向量 `(F_G,)`（留位槽 → 全零）。"""
    out = np.zeros(F_G, np.float32)
    if good not in game.MARKET:
        return out
    out[0] = float(game.MARKET[good]) / _PRICE_DIV
    out[1] = float(game.MARKET_DEPTH.get(good, 0)) / _DEPTH_DIV
    return out


# ===========================================================================
# 4. 整表（观测/模型按子表整体取用；顺序 = vocab 的冻结顺序）
# ===========================================================================
def building_table() -> np.ndarray:
    """`(len(V.OBS_BUILDING), F_B)` —— 顺序即 `sub_idx`（含留位槽，内容为零）。"""
    return np.stack([building_vector(b) for b in V.OBS_BUILDING]) if V.OBS_BUILDING \
        else np.zeros((0, F_B), np.float32)


def unit_table() -> np.ndarray:
    return np.stack([unit_vector(u) for u in V.UNIT]) if V.UNIT \
        else np.zeros((0, F_U), np.float32)


def good_table() -> np.ndarray:
    return np.stack([good_vector(g) for g in V.TRADEABLE]) if V.TRADEABLE \
        else np.zeros((0, F_G), np.float32)


# 每个动作 kind 的 sub 表内容宽度（`KINDS` 顺序，见 `rl/env.py`）——模型按它建投影层。
# 0 = 这个 kind 的 sub 没有内容（move/attack/retreat/end_turn，或外交目标槽）。
CONTENT_DIM_OF_KIND = {
    "build": F_B, "recruit": F_U, "buy": F_G, "sell": F_G,
    "move": 0, "attack": 0, "retreat": 0, "end_turn": 0,
}


def content_table_for(kind: str) -> np.ndarray:
    """某个 kind 的整张内容表（按 `env.sub_tables[kind]` 的顺序）。"""
    if kind == "build":
        return building_table()
    if kind == "recruit":
        return unit_table()
    if kind in ("buy", "sell"):
        return good_table()
    return np.zeros((0, 0), np.float32)
