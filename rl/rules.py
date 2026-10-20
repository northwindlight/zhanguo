# -*- coding: utf-8 -*-
"""★ **规则表的取用口** —— 读**主线引擎**的数值表，**每次现取、不缓存**。

    为什么单独一层
    ─────────────
    规则表住在主线的模块里（`mods/native/army/parameters.py`、
    `mods/native/territory/mod.py`），键就是**内容 ID**。RL 线要的
    兵种/地形/建筑统计（血量、攻力、移动代价、地形减伤、骰面修正…）都从那里取。

    ★ **不复制数值**：复制就会与主线脱节 —— 主线改了平衡，RL 线还按老数值
      训练，而那种错**不报错**（只是模型在学一条已经不存在的规则）。
    ★ **每次现取**（`rl/features.py` 的第一节纪律）：域随机化会**就地改**
      主线的表，RL 线必须**立刻**看得见 ⇒ 这里一律是**函数**，不是模块级常量。
    ★ **不认识就响亮报错**：不许"取不到就悄悄用默认值"。
"""
from __future__ import annotations

from mods.native.army import parameters as _army
from mods.native.territory import mod as _territory_mod

from .tokens import BARBARIAN, CASTLE, TOWN_HALL, WATCHTOWER


# ---------------------------------------------------------------- 兵种
def unit_entry(kind: str) -> dict:
    """兵种**内容 ID** → 引擎的兵种定义条目（**活对象**，别缓存）。

    ★ 入参是**内容 ID**（`native:infantry`）—— RL 线直接用引擎的标准命名空间。
    ★ 野人没有内容 ID（它是虚拟单位）⇒ 按步兵兜底（旧引擎逐字如此：
      野人只有 hp、没有兵种键 ⇒ `game.unit_kind` 按步兵兜底）。
    ★ 其余**不认识**的兵种 ⇒ **响亮地炸**：引擎将来加了新兵种而 RL 线没跟上时，
      这里必须立刻暴露，而不是"悄悄按步兵算"⇒ 模型在学一条不存在的规则。
    """
    if isinstance(kind, dict):
        kind = kind.get("type", "native:infantry")
    entry = _army.UNIT_TYPES.get(kind)
    if entry is None:
        if kind == BARBARIAN:
            entry = _army.UNIT_TYPES["native:infantry"]
        else:
            raise KeyError(
                f"不认识的兵种 {kind!r} —— 引擎的兵种表是 {sorted(_army.UNIT_TYPES)}；"
                f"RL 线要认它，得先把它加进 rl/vocab.py 的 UNIT")
    return entry


def unit_kind(a: dict) -> str:
    """军队 dict → 兵种**内容 ID**。

    ⚠ 野人没有兵种键 ⇒ 按步兵兜底（与旧引擎逐字一致）。
    """
    return a.get("type", "native:infantry")


def unit_max_hp(kind: str = "native:infantry") -> int:
    """该兵种的满血上限。"""
    return int(unit_entry(kind).get("hp", _army.ARMY_MAX_HP))


def unit_atk(kind: str = "native:infantry") -> int:
    """该兵种的每战斗回合基础伤害。"""
    return int(unit_entry(kind).get("atk", _army.ARMY_ATTACK_DAMAGE))


def unit_speed(kind: str = "native:infantry") -> int:
    """该兵种每回合的移动力。"""
    return int(unit_entry(kind).get("speed", 1))


def unit_supply(kind: str = "native:infantry") -> int:
    """该兵种每回合的补给消耗。"""
    return int(unit_entry(kind).get("supply", 1))


def unit_move_cost(kind: str, terrain: str) -> int:
    """走进 `terrain` 这一格要花几点移动力（表里没写的地形按 1 = 不受阻）。"""
    return int(_army.MOVE_COST.get(kind, {}).get(terrain, 1))


def move_cost_table(kind: str) -> dict:
    """该兵种的**逐地形移动代价表**（**内容 ID → 代价**）。

    ★ 这是**现算的副本**（不是引擎表本身）—— 调用方（寻路）要拿它做
      "是不是所有地形都 1" 的快路判断。代价可忽略（5 项）；
      换来的是**键名统一**（都是内容 ID）。
    """
    table = _army.MOVE_COST.get(kind, {})
    return {terrain: int(cost) for terrain, cost in table.items()}


# ---------------------------------------------------------------- 地形
def terrain_defense(terrain: str) -> int:
    """地形的减伤%（`native:plain` 0 … `native:mountain` 50）。"""
    for entry in _territory_mod.TERRAINS:
        if entry.id == terrain:
            return int(entry.defense)
    return 0


def terrain_names() -> tuple:
    """引擎的地形**内容 ID** 全集（顺序 = 引擎自己的顺序）。"""
    return tuple(t.id for t in _territory_mod.TERRAINS)


# ---------------------------------------------------------------- 战斗 / 续战
def combat_die_mod() -> dict:
    """战斗骰表（**现取**，不缓存）：`{骰面: 伤害修正%}`。"""
    return _army.COMBAT_DIE_MOD


def retreat_atk_penalty() -> int:
    """撤退军的输出惩罚%。"""
    return int(_army.RETREAT_ATK_PENALTY)


def retreat_def_cover() -> int:
    """守方撤退时的承伤比例%（50 = 只承受一半）。"""
    return int(_army.RETREAT_DEF_COVER)


def army_max_hp() -> int:
    return int(_army.ARMY_MAX_HP)


def army_heal_per_turn() -> int:
    return int(_army.ARMY_HEAL_PER_TURN)


def army_starve_damage() -> int:
    return int(_army.ARMY_STARVE_DAMAGE)


def army_attack_damage() -> int:
    return int(_army.ARMY_ATTACK_DAMAGE)


# ---------------------------------------------------------------- 规则 AI 参数
# ★ 旧线从 `balance.py` 取 `V11_*`；主线把这些搬到了 `ruleai/v11plus/parameters.py`。
#   两边**合并成一份**：主线有的**现取**，主线没有的（旧线自己抄 v11plus 时
#   带出来的私有参数）**按旧值保留**并注明来源 ——
#   别让"取不到就悄悄用默认值"发生。
from ruleai.v11plus import parameters as _v11   # noqa: E402

V11_MAX_GROUP = _v11.V11_MAX_GROUP                  # 单组人数的枚举上界
V11_SOLVER_NODE_CAP = _v11.V11_SOLVER_NODE_CAP      # 全局编组的搜索节点预算
V11_SHORTFALL_PENALTY = _v11.V11_SHORTFALL_PENALTY  # 缺员罚
V11_NEAR_TARGETS = _v11.V11_NEAR_TARGETS            # 每支军只看最近的几个候选
V11_MAX_CANDIDATES = _v11.MAX_CANDIDATES            # 候选目标格上限

# ★★ 旧 `balance.py` 里有、主线 `ruleai/v11plus/parameters.py` **没有**的四个：
#   `V11_FIELD_MAX_COST` / `V11_NEED_CAP` / `V11_ROUNDS_CAP` / `V11_SCAN_RADIUS`
#   —— 那是**旧线自己抄 v11plus 时带出来的私有参数**，主线把它们删了。
#   ⇒ 这里按**旧值**保留（逐字取自 `origin/feat/rl-tactics:balance.py`）。
V11_FIELD_MAX_COST = 12    # 代价场的截断半径（移动力点数）
V11_NEED_CAP = 12          # 难度评估里"最多算到几支"
V11_ROUNDS_CAP = 12        # 战斗模拟的最多轮数（轮 = 回合）
V11_SCAN_RADIUS = 10       # 候选池的搜索半径（切比雪夫）
