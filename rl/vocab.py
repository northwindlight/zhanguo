# -*- coding: utf-8 -*-
"""冻结词表：一切「编成下标」的枚举都从这里取，**顺序永远不许改**。

为什么要有这一层
----------------
本分支（`feat/rl`）的引擎**与 main 逐字相同**（2026-09-12 变基起），但
**观测空间必须冻住**：观测/tokenizer 把建筑编成下标，下标一变，
地块通道、候选的 `sub_idx`、embedding 的语义一起错位，而且**不报错**——
这类错位训练几百局都未必看得出来。引擎加一项建筑（比如外交中心回到
`game.BUILDINGS`）、或者 main 删一项，都会把下标整体平移。

    main   : BUILDINGS 16 项（含「外交中心」）
    RL 观测: 本文件的 BUILDING **减去 MAIN_ONLY** = 15 项（外交中心不进观测）

所以：下标一律取本文件的表。表**从 main 冻结**，`MAIN_ONLY` 的项
「在位不用」（观测/动作空间里没有它，但位置留着）。要加东西时只改
「哪些项会产生」，**不碰任何下标**。

⚠ **当初表尾没预留空位**，所以「引擎多一项建筑」这件事没有免费的余地：
要么进观测（宽度变、旧 ckpt 全废），要么像外交中心这样挂进 `MAIN_ONLY`。
想彻底解决，得在**下一次观测改动**里一并预留空位（那时本来就要重炼）。

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
# ⚠ 引擎侧现在与 main 逐字相同（`game.BUILDINGS` 也是 16 项），但**维度不许取
#   `len(game.BUILDINGS)`**：观测维度取「本表减去 MAIN_ONLY」= 15
#   （`rl/env.py` 的 `bnames`）。引擎加一项建筑不该动观测，反之亦然。
# ⚠ 顺序不能凭印象写：外交中心在 main 里落在「瞭望塔」与「工程院」**之间**，
#   不是表尾。`test_vocab_main_parity` 就是为这条存在的（第一版手写就写错了）。
BUILDING = (
    "城堡", "林场", "农场", "矿场", "石油厂", "黄金矿场",
    "木材能源厂", "石油能源厂", "补给厂", "装备厂", "兵营",
    "市政厅", "瞭望塔",
    "外交中心",          # ← MAIN_ONLY：引擎有、**观测/动作空间里没有**；位置留着
    "工程院", "军屯",
    # ---- 留位（2026-09-12 用户拍板：三类都留、都从紧，见 §10.3）----
    # 引擎里**没有**这几项，它们只是把下标先占住：将来加建筑时填进来，
    # **下标不动、宽度不变、ckpt 不废**。约定见文件末尾 §9。
    "_reserved_b1", "_reserved_b2", "_reserved_b3", "_reserved_b4",
)

# 兵种 3 → 5（+2 留位）
UNIT = ("步", "骑", "民", "_reserved_u1", "_reserved_u2")

# 可交易物资 6（不含黄金）→ 8（+2 留位）
TRADEABLE = ("粮食", "木头", "矿石", "石油", "装备", "补给",
             "_reserved_t1", "_reserved_t2")

# 地块资源 5（建采集建筑看的就是它）
TILE_RES = ("矿石", "黄金", "耕地", "石油", "木头")

# 国家库存 7（= TILE_RES 去掉「耕地」+ 黄金…按 main 的 res 键序）
STOCK = ("黄金", "粮食", "木头", "矿石", "石油", "装备", "补给")

# 国家槽：固定 8。归属通道、外交目标、势力 token 都用它，
# **加国家不改任何维度**（槽位由 `World.nation_code` 绑定）。
NATION_SLOTS = 8


# ---------------------------------------------------------------------------
# 1b. 「观测子表」= 上表减去引擎有但 RL 不看的项；再减去留位 = 真正能产生的项
# ---------------------------------------------------------------------------
# 判据（三层，全都写在这里，别在别处再各写一份）：
#   MAIN_ONLY  ：**引擎里有**，但独局下造不出来/用不上 → 不进观测、不进动作空间。
#   RESERVED_* ：**引擎里没有**，只是把下标占住 → 不进候选，但**占宽度**（网格通道 /
#                glob / token 组都给它留了一格）。将来加实体时把它填成真项即可。
#   *_REAL     ：引擎里真有、且能被产生（候选枚举只走这些）。
MAIN_ONLY = ("外交中心",)
RESERVED_BUILDING = ("_reserved_b1", "_reserved_b2", "_reserved_b3", "_reserved_b4")
RESERVED_UNIT = ("_reserved_u1", "_reserved_u2")
RESERVED_TRADEABLE = ("_reserved_t1", "_reserved_t2")
RESERVED = frozenset(RESERVED_BUILDING + RESERVED_UNIT + RESERVED_TRADEABLE)

OBS_BUILDING = tuple(b for b in BUILDING if b not in MAIN_ONLY)        # 19（观测子表）
BUILDABLE = tuple(b for b in OBS_BUILDING if b not in RESERVED)        # 15（能建）
RECRUITABLE = tuple(u for u in UNIT if u not in RESERVED)              # 3（能征）
TRADEABLE_REAL = tuple(g for g in TRADEABLE if g not in RESERVED)      # 6（能买卖）


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
    "build": OBS_BUILDING,          # 19 = 15 真建筑 + 4 留位（**不含**外交中心）
    "recruit": UNIT,                # 5 = 3 + 2 留位
    "buy": TRADEABLE,               # 8 = 6 + 2 留位
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
# ⚠ 归属段的**计划**是「固定 8 国槽」（加国家不改维度），但**实现是动态的**：
#   `rl/env.py` 用 `1 + len(rivals) + 1`（`rivals=()` 时 2 条）。两者不一致这事
#   记在 `rl/TOKEN_DESIGN.md` §10.3（"加对手"那一步必须先把它钉成 8 槽，
#   否则加对手就会改宽度 → 又一次重炼）。这里保留 8 槽作为**上限定义**，
#   并由 `rl/env.py` 断言 `len(rivals) <= NATION_SLOTS`。
OWNER_CHANNELS = ("self",) + tuple(f"rival{i}" for i in range(NATION_SLOTS)) + \
                 ("neutral", "barbarian")           # = 11（上限口径）
OWNER_CHANNELS_DYNAMIC = 1 + 1                         # 实测：self + neutral
PATCH_SCALARS = ("slots", "pending", "built_this_turn", "my_hp", "foe_hp",
                 "barb_hp", "frontier", "explored_ever")   # = 8
# ⚠ 删除 `GRID_CHANNELS = 45` / `GROUP_CAP` / `PATCH_CHOICES`：它们是从没接线的
#   **计划值**，且 45 与实测 36 口径不同（§10.3 坑 6）。网格宽度的唯一来源是
#   `len(env.obs_channels())`；token 上限的唯一来源是 `rl/tokenize.py` 的 `CAP`。


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
# ⚠ 这里曾有 `GROUP_CAP` / `PATCH_CHOICES` 两份"计划表"，从没接线（真正的上限在
#   `rl/tokenize.py` 的 `CAP`，M 组也已改成固定 8×8 槽位）—— 2026-09-12 删掉，
#   避免两套口径各自漂。窗口预算的唯一来源 = `rl/tokenize.py` 的 `CAP` 合计。
POS_SCALE = 32.0                  # 与 rl/env.py 一致：相对家的绝对尺度，不按地图归一


# ===========================================================================
# 7. 只读工具（main 的 mp_ai 工具集里不产生状态变更的那些）
# ===========================================================================
# 它们不是动作：RL 的观测等价于「随时可查的面板」，所以不进 KIND。
LLM_ONLY_TOOLS = ("plan",)                                   # 国策：文本层，RL 没有
QUERY_TOOLS = ("query", "report", "rules", "econ", "countries")


# ===========================================================================
# 8. 留位纪律（2026-09-12 起）
# ===========================================================================
# `MAIN_ONLY` / `RESERVED_*` / `OBS_*` / `*_REAL` 全部定义在 §1b —— **只此一份**。
# 三条纪律：
#   1. 留位项**只许追加在表尾**，永远不许插队、不许删、不许改名（改名 = 换身份）。
#   2. 留位项**不产生候选**、也不参与"引擎里有没有这一项"的判断；
#      候选枚举只走 `BUILDABLE` / `RECRUITABLE` / `TRADEABLE_REAL`。
#   3. 新实体上线时：**填一个留位槽**（改名字、把引擎的表也加上），
#      宽度不动、下标不动、旧 ckpt 不作废 —— 这正是留位的目的。
# 逐项宽度对照与决策记在 `rl/TOKEN_DESIGN.md` §10.3。
