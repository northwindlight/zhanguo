# -*- coding: utf-8 -*-
"""规则表 → **模型输入的内容向量**（沙盒版；框架与纪律承自旧线 `feat/rl` 的同名文件）。

为什么要有这一层
----------------
候选/军队的嵌入如果只有 `nn.Embedding(idx)`，语义就是**训练时拟合进去的常数**。
于是「民兵攻击 20 → 30」这种事模型**看不见**：`π(a|s)` 只能依赖 `s` 里有的东西，
而战力不在 `s` 里 ⇒ 同一个盘面给出的决策一模一样，**而最优决策已经变了**。
（旧线实锤：石油厂造价 240→180，模型动作一字不变。这是**表达能力**问题，
不是拟合精度问题。）

这一层把引擎表的数值变成**每次现算**的向量，接到三处：
  · 候选侧 —— 执行军的兵种数值 ⊕ 目标格的地形数值
  · 军队 token —— 该军的兵种数值（**这同时就是用户要的「各单位战斗力」**）
  · 全局 token —— 骰子表 ⊕ 撤退常数 ⊕ 续战常数

两条纪律（承自旧线，都踩过）
----------------------------
1. ★ **每次现算，不许做模块级缓存**。域随机化会在每局开始时**就地改**
   `game.UNIT_TYPES` / `balance.TERRAIN_STATS`（`mp.py` 是 `from game import …`，
   同一个 dict 对象）—— 缓存会把"上一局的数值"烤进这一局，而那种错**不报错**。
2. ★ **归一化尺度只写在这个文件里**。调用点不许再除一次、也不许"看着办"：
   训练分布与模型输入的可比性全依赖它。

★ 沙盒砍掉了什么（相对旧线）：`BUILD_KINDS`(14) / `STOCK` / `TILE_RES` / `recruit`
  造价 / `EFFECT_KEYS` —— 沙盒**不进经济**（不建、不征、不买卖），军事上只值
  「好不好守」「走得快不快」「打得疼不疼」「还能撑几回合」。
"""
from __future__ import annotations

import numpy as np

import balance
import game
from rl import vocab as V

# ===========================================================================
# 1. 冻结的宽度（改这些 = 改模型输入宽度 = 旧 ckpt 全废）
# ===========================================================================
# 兵种数值：hp / atk / speed / supply
F_U = 4
# 地形数值：defense / 步-移动代价 / 骑-移动代价 / 民-移动代价
F_T = 4
# 骰子表：6 面各自的伤害修正%
F_D = 6
# 撤退常数：输出惩罚 / 守方减伤
F_R = 2
# 续战常数：满血 / 每回合回复 / 断粮扣血  —— "这支军还能撑几回合"靠它
F_S = 3

F_CAND = F_U + F_T          # 候选的内容段（执行军 ⊕ 目标格）
F_GLOB = F_D + F_R + F_S    # 全局的内容段

# 归一化尺度（**只写在这里**）
_HP_DIV = 100.0
_ATK_DIV = 100.0             # 步/骑 50、民兵 20 ⇒ 0.5 / 0.2
_SPEED_DIV = 2.0             # 步/民 1、骑 2
_SUPPLY_DIV = 2.0            # 步/民 1、骑 2
_DEF_DIV = 100.0             # 地形防御% → −0.10 ~ +0.50
_MOVE_DIV = 2.0              # 移动代价 1~2
_DIE_DIV = 25.0              # 骰修正 −25% ~ +25% ⇒ −1.0 ~ +1.0
_PCT_DIV = 100.0             # 撤退/续战那几个百分比


# ===========================================================================
# 2. 现算的内容向量（**每次调用都读引擎表**）
# ===========================================================================
def unit_vector(kind: str) -> np.ndarray:
    """兵种 → `(F_U,)`。表里没有的 kind（野人等无 `type` 的旧军）按步兵兜底。"""
    t = game.UNIT_TYPES.get(kind)
    if t is None:
        t = game.UNIT_TYPES["步"]
    return np.array([t.get("hp", game.ARMY_MAX_HP) / _HP_DIV,
                     t.get("atk", game.ARMY_ATTACK_DAMAGE) / _ATK_DIV,
                     t.get("speed", 1) / _SPEED_DIV,
                     t.get("supply", 1) / _SUPPLY_DIV], dtype=np.float32)


def terrain_vector(terrain: str) -> np.ndarray:
    """地形 → `(F_T,)` = 减伤 + 三个兵种的移动代价。

    ★ 三列移动代价**都留着**（步/民 现在同值 ⇒ 两列冗余）：用户改 `MOVE_COST` 时
      「让民兵也怕森林」这类改动必须**看得见**，宁可现在多一列常数。
    """
    d = balance.TERRAIN_STATS.get(terrain, {}).get("defense", 0)
    mv = [balance.MOVE_COST.get(k, {}).get(terrain, 1) for k in V.UNIT]
    return np.array([d / _DEF_DIV] + [m / _MOVE_DIV for m in mv], dtype=np.float32)


def die_vector() -> np.ndarray:
    """骰子表 → `(F_D,)`，按**骰面 1..6 的顺序**（不是 dict 迭代顺序）。"""
    return np.array([balance.COMBAT_DIE_MOD[d] / _DIE_DIV
                     for d in range(1, F_D + 1)], dtype=np.float32)


def retreat_vector() -> np.ndarray:
    """撤退经济 → `(F_R,)` = (输出惩罚, 守方减伤)。"""
    return np.array([balance.RETREAT_ATK_PENALTY / _PCT_DIV,
                     balance.RETREAT_DEF_COVER / _PCT_DIV], dtype=np.float32)


def sustain_vector() -> np.ndarray:
    """续战 → `(F_S,)` = (满血, 每回合回复, 断粮扣血)。"""
    return np.array([game.ARMY_MAX_HP / _HP_DIV,
                     game.ARMY_HEAL_PER_TURN / _HP_DIV,
                     game.ARMY_STARVE_DAMAGE / _HP_DIV], dtype=np.float32)


def glob_rule_vector() -> np.ndarray:
    """全局内容段 `(F_GLOB,)` = 骰子 ⊕ 撤退 ⊕ 续战。"""
    return np.concatenate([die_vector(), retreat_vector(), sustain_vector()])


def cand_content(kind: str, terrain: str) -> np.ndarray:
    """候选的内容段 `(F_CAND,)` = 执行军的兵种数值 ⊕ 目标格的地形数值。"""
    return np.concatenate([unit_vector(kind), terrain_vector(terrain)])


# ===========================================================================
# 3. 批量表（拼张量用；**同样是现算**，别缓存）
# ===========================================================================
def unit_table() -> np.ndarray:
    """`(len(vocab.UNIT), F_U)` —— 按 `vocab.UNIT` 的**冻结顺序**（下标即身份）。"""
    return np.stack([unit_vector(k) for k in V.UNIT])


def terrain_table() -> np.ndarray:
    """`(len(vocab.TERRAIN), F_T)` —— 按 `vocab.TERRAIN` 的冻结顺序。"""
    return np.stack([terrain_vector(t) for t in V.TERRAIN])