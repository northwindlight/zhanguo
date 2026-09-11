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

# ★ 复合建筑的**效果**（2026-09-12 起 `game.BUILDINGS[*]["effects"]` 是数据，见 §10.10）。
#   在这之前它们是 `mp.py` 的逻辑 + 模块级常量 —— 模型无从表达，只能从"建了之后
#   发生了什么"反推。这 9 个键是**语义槽**（缺省 0 = 该建筑没有这项效果）。
#   ⚠ 加一个新效果键 = 加宽这个块 = 又一次宽度变更；要么预留，要么认下。
EFFECT_KEYS = ("defense_per_level",   # 城堡：每级防御加成 %
               "gold_base",           # 市政厅：每座每回合基础产金
               "gold_per_slot",       # 市政厅：该格每座建筑额外产金
               "build_discount",      # 工程院：本地块建造金价减免 %
               "vision_radius",       # 瞭望塔：事件视野半径
               "recruit_cap",         # 兵营：每座每回合可征支数
               "militia_cap",         # 军屯：每座每回合可征民兵数
               "letter_discount",     # 外交中心：写信起步价每座减免
               "diplo_cost_min")      # 外交中心：外交费下限

# 城堡的**逐级造价表**（5 级）。其它建筑的 cost 是标量，只有首项有意义。
MAX_LEVELS = 5

# 地形一行：defense / build_penalty + 2 个预留维
# （地形改版计划里有「崎岖降速」「通行性」——先把位占住，见 §10.10 的岔路 B 后续）
TERRAIN_FIELDS = ("defense", "build_penalty")
F_T = len(TERRAIN_FIELDS) + 2                                                # = 4

# 宽度（这几行是模型的输入契约，别在别处再算一遍）
F_B = (len(BUILD_KINDS) + 1 + 1 + len(CAP_RES_SLOTS) + 3 * len(V.STOCK) + 5
       + MAX_LEVELS + len(EFFECT_KEYS))                                      # = 45+5+9 = 59
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
# 城堡逐级造价：与 cost 同尺度（÷2000），逐级各占一维
# 效果（每个键一个尺度；量级上限都按现值留了余量，抖动时不会顶到 1）
_EFFECT_DIV = {"defense_per_level": 50.0, "gold_base": 20.0, "gold_per_slot": 10.0,
               "build_discount": 50.0, "vision_radius": 8.0, "recruit_cap": 4.0,
               "militia_cap": 4.0, "letter_discount": 10.0, "diplo_cost_min": 5.0}
# 地形：defense ÷100（±50 是现值极值）、build_penalty ÷100（50 是上限）
_TERRAIN_DIV = {"defense": 100.0, "build_penalty": 100.0}
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
    i += 5
    # 城堡的**逐级造价**（其它建筑只有标量 cost，这里全 0 —— 位置留着）
    lv = b.get("cost")
    if isinstance(lv, (list, tuple)):
        for j in range(min(MAX_LEVELS, len(lv))):
            out[i + j] = float(lv[j]) / _COST_DIV
    i += MAX_LEVELS
    # ★效果块：读 `effects`（**数据**，不是本文件镜像的常量 —— 所以引擎改了、
    #   域随机化抖了，它都跟着变；镜像表那种"第二份真相"这里不存在）
    eff = b.get("effects") or {}
    for j, k in enumerate(EFFECT_KEYS):
        out[i + j] = float(eff.get(k, 0)) / _EFFECT_DIV[k]
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


def terrain_vector(name: str) -> np.ndarray:
    """一种地形的内容向量 `(F_T,)` = `[defense/100, build_penalty/100, 0, 0]`。

    ★ 为什么地形也要有内容向量：模型此前只能从网格里看到"这格是山地"（one-hot 一个名字），
    看不到"山地 = 减伤 50% / 造价 +50%" —— 又是那类"名字给了、数值没给"的问题
    （与建筑同源，见 §10.10）。后两维是给地形改版计划里的「崎岖降速/通行性」留的位。
    """
    out = np.zeros(F_T, np.float32)
    st = game.TERRAIN_STATS.get(name)
    if st is None:
        return out
    for j, k in enumerate(TERRAIN_FIELDS):
        out[j] = float(st.get(k, 0)) / _TERRAIN_DIV[k]
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


def terrain_table() -> np.ndarray:
    """`(len(V.TERRAIN) + 留位, F_T)` —— 顺序即 `vocab.TERRAIN` 的冻结顺序。

    地形**目前没有留位**（`TERRAIN` 是 5 项，没有 `RESERVED_TERRAIN`）。地形改版
    计划会加"海洋/河流"，那时要按留位纪律补 —— 见 §10.10。
    """
    return np.stack([terrain_vector(t) for t in V.TERRAIN]) if V.TERRAIN \
        else np.zeros((0, F_T), np.float32)


def build_cost_factor(terrain: str, has_academy: bool) -> float:
    """这一格的**实际建造金价倍率**（相对基础造价）：地形惩罚 × 工程院减免。

    公式与引擎 `mp.py` 的 `build()` 逐字对齐（地形只上浮金价、工程院再减 25%，
    两者**乘算**）。放进网格作为一条通道，模型就不必自己把"地形 + 有没有工程院"
    合成出这个乘数 —— 那正是它最容易学错的地方（两者量级差 3 倍）。
    """
    bp = float(game.TERRAIN_STATS.get(terrain, {}).get("build_penalty", 0))
    f = 1.0 + bp / 100.0
    if has_academy:
        f *= (100.0 - float(game.building_effect("工程院", "build_discount"))) / 100.0
    return f


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
