# -*- coding: utf-8 -*-
"""**规则表的取用口** —— 读主线引擎的数值表，**每次现算、不缓存**。

    为什么有这一层
    ──────────────
    旧线上这些表住在 `game.py` / `balance.py`（已被主线删除）。现在它们住在
    主线的模块里，而且**键换了**：引擎讲**内容 ID**（`native:infantry`、
    `native:plain`），RL 线的观测词表讲**中文短名**（`步`、`平原`）。

    ⇒ 本模块只做两件事：**定位表** + **翻译键**。**不复制数值** ——
    复制就会与主线脱节（主线改了平衡，RL 线还按老数值训练），而 RL 线的
    整个卖点就是"规则变了模型看得见"（见 `rl/features.py` 的开篇）。

    ★ **纪律：每次现算，不许做模块级缓存**（`rl/features.py` 第一节）。
      域随机化会**就地改**引擎的表 ⇒ 缓存会把"上一局的数值"烤进这一局，
      而那种错**不报错**。本模块的取值函数因此都是**函数**而不是**常量**。

    ★ 数值表的位置（主线的**唯一出处**）：
      · 兵种 / 移动代价 / 骰子 / 撤退 —— `mods/native/army/parameters.py`
      · 地形减伤 —— `mods/native/territory/mod.py`（`Terrain.defense`）
"""
from __future__ import annotations

from mods.native.army import parameters as _army
from mods.native.territory import mod as _territory_mod

from .tokens import KIND_OF, TERRAIN_OF, TERRAINS, UNIT_KINDS

# ★ 野人：引擎**没有内容 ID**（它是"按需合成的虚拟单位"）⇒ RL 线用短名当它的键。
BARBARIAN = "野人"

# ★ 地形**短名**清单（`TERRAINS` 的键）—— 给"遍历所有地形"用
#   （例如"看不见时取最保守的地形减伤"）。
TERRAIN_NAMES = tuple(TERRAINS)

# ---------------------------------------------------------------- 取值口
# ★ 兵种表：键是**中文短名**（RL 词表），值是引擎的 `UNIT_TYPES` 条目。
#   每次调用现取条目本身（不是副本）⇒ 域随机化就地改 `atk` 立刻生效。
def unit_entry(kind) -> dict:
    """兵种短名 → 引擎的兵种定义条目（**活对象**，别缓存）。

    ★ 入参可以是**短名**（`"步"`）**或军队 dict**（`{"type": "步"}`）——
      旧引擎的 `game.unit_max_hp(a)` 收的是军队 dict，RL 线沿用了那个调用形状。
    """
    if isinstance(kind, dict):
        kind = kind.get("type", "步")
    kind_id = UNIT_KINDS.get(kind, kind)
    entry = _army.UNIT_TYPES.get(kind_id)
    if entry is None:
        # ★★ **不许"取不到就悄悄用默认值"**（本线自己的纪律）。
        #   只有**野人**是明确按步兵兜底的（旧引擎逐字如此：野人只有 hp，
        #   没有兵种键 ⇒ `game.unit_kind` 按步兵兜底）。
        #   其余**不认识**的兵种 ⇒ **响亮地炸**：
        #   引擎将来加了新兵种而 RL 线没跟上时，这里必须**立刻暴露**，
        #   而不是"悄悄按步兵算" ⇒ 模型在学一条**不存在的规则**。
        if kind in (BARBARIAN, "野人"):
            entry = _army.UNIT_TYPES["native:infantry"]
        else:
            raise KeyError(
                f"不认识的兵种 {kind!r}（内容 ID {kind_id!r}）—— "
                f"引擎的兵种表是 {sorted(_army.UNIT_TYPES)}；"
                f"RL 线要认它，得先在 rl/tokens.py 里定下短名并加进 rl/vocab.py 的 UNIT")
    return entry


def unit_kind(a: dict) -> str:
    """军队 dict → **中文短名**（旧 `game.unit_kind` 的口径）。

    ⚠ 野人没有兵种键 ⇒ 按步兵兜底（与旧引擎逐字一致）。
    """
    return a.get("type", "步")


def unit_max_hp(kind: str = "步") -> int:
    """该兵种的满血上限。"""
    return int(unit_entry(kind).get("hp", _army.ARMY_MAX_HP))


def unit_atk(kind: str = "步") -> int:
    """该兵种的每战斗回合基础伤害。"""
    return int(unit_entry(kind).get("atk", _army.ARMY_ATTACK_DAMAGE))


def unit_speed(kind: str = "步") -> int:
    """该兵种每回合的移动力。"""
    return int(unit_entry(kind).get("speed", 1))


def unit_supply(kind: str = "步") -> int:
    """该兵种每回合的补给消耗。"""
    return int(unit_entry(kind).get("supply", 1))


def unit_move_cost(kind: str, terrain: str) -> int:
    """走进 `terrain` 这一格要花几点移动力（表里没写的地形按 1 = 不受阻）。

    ★ 入参都是**中文短名** ⇒ 两个都要翻成内容 ID 再查表
      （`TERRAIN_OF` 是"内容 ID → 短名"，方向相反，别用错）。
    """
    kind_id = UNIT_KINDS.get(kind, kind)
    terrain_id = TERRAINS.get(terrain, terrain)
    return int(_army.MOVE_COST.get(kind_id, {}).get(terrain_id, 1))


def move_cost_table(kind: str) -> dict:
    """该兵种的**逐地形移动代价表**（**中文短名 → 代价**）。

    ★ 这是**现算的副本**，不是引擎表本身 —— 调用方（寻路）要拿它做
      "是不是所有地形都 1" 的快路判断，并且**按中文地形名**索引。
      ⇒ 每次调用重建一张小表（5 项），代价可忽略；换来的是**键名统一**。
    """
    kind_id = UNIT_KINDS.get(kind, kind)
    table = _army.MOVE_COST.get(kind_id, {})
    return {short: int(table.get(content, 1)) for short, content in TERRAINS.items()}


def terrain_defense(terrain: str) -> int:
    """地形的减伤%（`native:plain` 0 … `native:mountain` 50）。

    ★ 入参是**中文短名**（`平原`…）⇒ 要先翻成内容 ID 再查表
      （`TERRAIN_OF` 是"内容 ID → 短名"，方向相反，别用错）。
    """
    terrain_id = TERRAINS.get(terrain, terrain)
    override = _TERRAIN_DEFENSE_OVERRIDE.get(terrain_id)
    if override is not None:
        return int(override)
    for entry in _territory_mod.TERRAINS:
        if entry.id == terrain_id:
            return int(entry.defense)
    return 0


# ★★ **地形减伤的"可变副本"** —— 域随机化要能**就地改**地形减伤
#   （旧线改 `balance.TERRAIN_STATS["丘陵"]["defense"]`）。
#   ⚠ 主线的 `Terrain` 是 **frozen dataclass**（改不动）⇒ 这里维护一张
#     **按内容 ID 索引的覆盖表**：有覆盖用覆盖，没有用引擎的原生值。
#     域随机化改这张表 ⇒ `terrain_defense` 立刻跟着变（**现取，不缓存**）。
_TERRAIN_DEFENSE_OVERRIDE: dict[str, int] = {}


def set_terrain_defense(terrain: str, defense: int) -> None:
    """★ **就地改**某地形的减伤（域随机化用）。

    ★ 入参可给**中文短名**或**内容 ID**。
    """
    terrain_id = TERRAINS.get(terrain, terrain)
    _TERRAIN_DEFENSE_OVERRIDE[terrain_id] = int(defense)


def clear_terrain_defense(terrain: str | None = None) -> None:
    """撤销地形减伤的覆盖（`terrain=None` ⇒ 全撤）。"""
    if terrain is None:
        _TERRAIN_DEFENSE_OVERRIDE.clear()
        return
    _TERRAIN_DEFENSE_OVERRIDE.pop(TERRAINS.get(terrain, terrain), None)


def combat_die_mod() -> dict:
    """战斗骰表（**活对象**）：`{骰面: 伤害修正%}`。"""
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
# ★ 旧线从 `balance.py` 取 `V11_*`；主线把这些搬到了 `ruleai/v11plus/parameters.py`
#   （名字也变了一部分）。这里把**两边合并成一份**：主线有的**现取**，
#   主线没有的（旧线自己抄 v11plus 时带出来的私有参数）**按旧值保留**并注明来源。
#   ⇒ 别让"取不到就悄悄用默认值"发生。
from ruleai.v11plus import parameters as _v11   # noqa: E402

V11_MAX_GROUP = _v11.V11_MAX_GROUP              # 单组人数的枚举上界
V11_SOLVER_NODE_CAP = _v11.V11_SOLVER_NODE_CAP  # 全局编组的搜索节点预算
V11_SHORTFALL_PENALTY = _v11.V11_SHORTFALL_PENALTY  # 缺员罚
V11_NEAR_TARGETS = _v11.V11_NEAR_TARGETS        # 每支军只看最近的几个候选
V11_MAX_CANDIDATES = _v11.MAX_CANDIDATES        # 候选目标格上限

# ★★ 旧 `balance.py` 里有、主线 `ruleai/v11plus/parameters.py` **没有**的那几个：
#   `V11_FIELD_MAX_COST` / `V11_NEED_CAP` / `V11_ROUNDS_CAP` / `V11_SCAN_RADIUS`
#   —— 那是**旧线自己抄 v11plus 时带出来的私有参数**，主线把它们删了。
#   ⇒ 这里按**旧值**保留，并注明来源，免得"找不到就换个数"变成静默的行为漂移。
#   （旧值逐字取自 `origin/feat/rl-tactics:balance.py`。）
V11_FIELD_MAX_COST = 12    # 代价场的截断半径（移动力点数）
V11_NEED_CAP = 12          # 难度评估里"最多算到几支"
V11_ROUNDS_CAP = 12        # 战斗模拟的最多轮数（轮 = 回合）
V11_SCAN_RADIUS = 10       # 候选池的搜索半径（切比雪夫）
