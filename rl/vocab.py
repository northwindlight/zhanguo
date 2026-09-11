# -*- coding: utf-8 -*-
"""冻结词表：一切「编成下标」的枚举都从这里取，**顺序永远不许改**。

为什么要有这一层
----------------
本分支（`feat/rl`）= main **减去外交**。但两边的枚举空间已经不同了：

    main   : BUILDINGS 16 项（含「外交中心」）
    feat/rl: BUILDINGS 15 项（外交中心 已删）

只要观测/tokenizer 把建筑编成下标（`list(BUILDINGS).index(b)`），外交一回来、
外交中心一插回去，**所有下标全体平移**——地块通道、候选的 `sub_idx`、embedding
的语义一起错位，而且**不报错**。这类错位训练几百局都未必看得出来。

所以：下标一律取本文件的表。表**从 main 冻结**，外交项「在位不用」
（现在永远不会有东西指到它，但位置留着）。加外交时只改「哪些项会产生」，
**不碰任何下标**。

两条硬规则
----------
1. **下标 = 本文件里的位置。** 永远不许 `list(dict)` / `sorted(...)` 顺序。
2. **只许追加，不许插队、不许删。** 删一项 = 所有旧 ckpt 全部作废。

对照测试：`tests/test_vocab_main_parity.py`（直接读 `git show main:game.py`
比对，不是比对本分支的 `game.py`——那正是要防的东西）。

设计文档：`rl/TOKEN_DESIGN.md`
"""
from __future__ import annotations

# ===========================================================================
# 1. 实体枚举（下标即身份）
# ===========================================================================

# 地形 5——顺序取自 main 的 game.TERRAINS
TERRAIN = ("平原", "森林", "丘陵", "山地", "沙漠")

# 建筑 **16**——逐字逐序取自 main 的 game.BUILDINGS（含「外交中心」）。
# ⚠ 本分支的 game.BUILDINGS 只有 15 项（外交中心已删）。**不要在代码里
#   `len(game.BUILDINGS)` 当维度**，那会让观测形状随分支变化。
# ⚠ 顺序不能凭印象写：外交中心在 main 里落在「瞭望塔」与「工程院」**之间**，
#   不是表尾。`test_vocab_main_parity` 就是为这条存在的（第一版手写就写错了）。
BUILDING = (
    "城堡", "林场", "农场", "矿场", "石油厂", "黄金矿场",
    "木材能源厂", "石油能源厂", "补给厂", "装备厂", "兵营",
    "市政厅", "瞭望塔",
    "外交中心",          # ← 外交项：main 有、本分支无；位置留着
    "工程院", "军屯",
)

# 兵种 3
UNIT = ("步", "骑", "民")

# 可交易物资 6（不含黄金）
TRADEABLE = ("粮食", "木头", "矿石", "石油", "装备", "补给")

# 地块资源 5（建采集建筑看的就是它）
TILE_RES = ("矿石", "黄金", "耕地", "石油", "木头")

# 国家库存 7（= TILE_RES 去掉「耕地」+ 黄金…按 main 的 res 键序）
STOCK = ("黄金", "粮食", "木头", "矿石", "石油", "装备", "补给")

# 国家槽：固定 8。归属通道、外交目标、势力 token 都用它，
# **加国家不改任何维度**（槽位由 `World.nation_code` 绑定）。
NATION_SLOTS = 8


# ===========================================================================
# 2. 动作枚举 KIND（32）——下标即 type_emb 的身份
# ===========================================================================
# 前 8 项**必须与 rl/env.py 的 KINDS 逐字逐序相同**：那是已经训过的
# `type_emb` 下标，动一下等于把旧 ckpt 的语义改掉。
ACTIVE_KINDS = ("build", "recruit", "move", "attack", "retreat", "buy", "sell", "end_turn")

# 外交动作 20——**现在就占位**。动作类型嵌入的维度一变、ckpt 全废。
# 来源：main 的 mp_ai 工具集里去掉只读工具（query/report/rules/econ/countries）
# 与 LLM 专属（plan）之后的全部。
DIPLO_KINDS = (
    "send_letter", "gift", "share_map", "spy",
    "propose", "respond_proposal", "break_defense", "guarantee", "cancel_guarantee",
    "declare_war", "offer_peace", "accept_peace", "reject_peace",
    "bloc_found", "bloc_join", "bloc_leave", "bloc_rename", "bloc_transfer",
    "bloc_dissolve", "vote",
)

assert len(ACTIVE_KINDS) == 8 and len(DIPLO_KINDS) == 20
KIND = ACTIVE_KINDS + DIPLO_KINDS + ("_reserved_1", "_reserved_2",
                                     "_reserved_3", "_reserved_4")
KIND_INDEX = {k: i for i, k in enumerate(KIND)}
assert len(KIND) == 32 and len(KIND_INDEX) == 32


# ===========================================================================
# 3. 子项表（每个 kind 的 sub 从哪张表取）
# ===========================================================================
# 外交类动作的 sub = **目标国槽位**（0..NATION_SLOTS-1），不是名字字符串。
NATION_SUB = tuple(f"n{i}" for i in range(NATION_SLOTS))

SUB_TABLE_OF = {
    "build": BUILDING,
    "recruit": UNIT,
    "buy": TRADEABLE,
    "sell": TRADEABLE,
    "move": ("",), "attack": ("",), "retreat": ("",), "end_turn": ("",),
}
for _k in DIPLO_KINDS:
    SUB_TABLE_OF[_k] = NATION_SUB
for _k in ("_reserved_1", "_reserved_2", "_reserved_3", "_reserved_4"):
    SUB_TABLE_OF[_k] = ("",)

# 子项嵌入的维度（固定，不随分支变）
SUB_SIZES = tuple(len(SUB_TABLE_OF[k]) for k in KIND)


# ===========================================================================
# 4. 观测网格的通道布局（拼在图上的顺序，也是冻结的）
# ===========================================================================
OWNER_CHANNELS = ("self",) + tuple(f"rival{i}" for i in range(NATION_SLOTS)) + \
                 ("neutral", "barbarian")           # = 11
PATCH_SCALARS = ("slots", "pending", "built_this_turn", "my_hp", "foe_hp",
                 "barb_hp", "frontier", "explored_ever")   # = 8

GRID_CHANNELS = (len(TERRAIN)        # 5
                 + len(TILE_RES)     # 5
                 + len(OWNER_CHANNELS)   # 11（固定 8 国槽，加国家不改维度）
                 + len(BUILDING)     # 16（含外交中心）
                 + len(PATCH_SCALARS))   # 8
assert GRID_CHANNELS == 45


# ===========================================================================
# 5. 事件与关系（外交/战争预留，现在空）
# ===========================================================================
EVENT = (
    # 战争
    "declare_war", "offer_peace", "accept_peace", "reject_peace", "truce_expired",
    # 联盟
    "bloc_found", "bloc_join", "bloc_leave", "bloc_rename", "bloc_transfer",
    "bloc_dissolve", "vote_cast",
    # 双边
    "propose_pact", "accept_pact", "reject_pact", "break_pact",
    "guarantee", "cancel_guarantee",
    # 信箱 / 情报
    "letter", "gift", "share_map", "spy_report",
    # 战场（现在就有：打野人也会产生，但第 1 阶段先不产生 token）
    "battle", "tile_captured",
)
assert len(EVENT) == 24

RELATION = ("neutral", "defense_pact", "alliance", "guarantee", "war", "truce")


# ===========================================================================
# 6. 窗口预算（token 数上限，见 rl/TOKEN_DESIGN.md）
# ===========================================================================
TOKEN_BUDGET = 512
GROUP_CAP = {
    "global": 1,
    "map": 64,        # 自适应 patch 尺寸，钉在这个带内
    "army": 192,      # 实测峰值 166 支
    "nation": NATION_SLOTS,
    "event": 32,
    "memory": 8,      # 每回合池化 2 个 × 近 4 回合
    "keysite": 16,
}
PATCH_CHOICES = (2, 4, 8, 16)     # 自适应 patch 边长（取能塞进 map 上限的最小值）
POS_SCALE = 32.0                  # 与 rl/env.py 一致：相对家的绝对尺度，不按地图归一


# ===========================================================================
# 7. 只读工具（main 的 mp_ai 工具集里不产生状态变更的那些）
# ===========================================================================
# 它们不是动作：RL 的观测等价于「随时可查的面板」，所以不进 KIND。
LLM_ONLY_TOOLS = ("plan",)                                   # 国策：文本层，RL 没有
QUERY_TOOLS = ("query", "report", "rules", "econ", "countries")


# ===========================================================================
# 8. 本分支缺、main 有的项（只应该是外交那一项）
# ===========================================================================
# `test_vocab_main_parity` 会核对：**这张表就是两分支枚举的全部差异**。
# 多出任何一项 = 除了外交还漂了别的，测试会红。
MAIN_ONLY = ("外交中心",)
