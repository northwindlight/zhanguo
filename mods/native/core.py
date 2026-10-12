# -*- coding: utf-8 -*-
"""多国引擎（MP）。

多个国家共存于同一张地图，各自经营（规则对所有国家一致，无作弊入口）：
  - 每国独立 国库(黄金)/战略储备/电网(不存储)/军队/国土/信箱；
  - 无人地带的视野内地块由「野人」把守，打赢即拓疆；国家间打赢即夺地，
    空城被敌军队踏入即陷（无防即失）；
  - 移动/攻击受国家关系约束：中立（非同盟非交战）不能入境、不能攻击；
  - 外交：联盟(起名结盟·全体创始成员同意·单方面退盟·共享视野·成员间外交免费·
    进攻战争须投票·议和由盟主投票·同战线自动归还核心领土)/保障独立/共同防御/
    宣战(对方必须接受，其**直接**保障国/直接共同防御伙伴自动参战——一对实体只走一跳；
         唯一不限跳数的是联盟：打盟员=全盟一起上)/求和(可赔款/索款/白和)；
  - 信箱：任何内容，下回合到信。
  - 看海：world.history 记下每个行动/每封信/每场战/每桩外交，Observer 全可见；
    各国 agent 只见「自己该知道」的（自己的面板/信箱/视野内事件）。

坐标 0-based；对外（面板/tool 入参）用 1-based。
"""

from __future__ import annotations

import copy
import json
import math
import random
from pathlib import Path

from .rules import (
    ARMY_HEAL_PER_TURN,
    HEAL_EQUIP_COST,
    BANK_LOAN_GDP_MULT,
    BANK_LOAN_TURNS,
    BANK_RATE_MAX,
    BANK_RATE_MIN,
    BANK_SPREAD,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    building_effect,
    COMBAT_DIE_MOD,
    DIPLO_CENTER_MIN_COST,
    MARKET,
    MARKET_DEPTH,
    MARKET_EQ_MAX_RATIO,
    MARKET_EQ_MIN_RATIO,
    MARKET_GAP_ONE_SIDE,
    MARKET_SENS,
    MARKET_SPREAD,
    MAX_SLOTS,
    PRICE_IMPACT,
    PRICE_MAX_RATIO,
    PRICE_MIN_ABS,
    PRICE_MIN_RATIO,
    PRICE_REVERT,
    RETREAT_ATK_PENALTY,
    RETREAT_RANGE,
    TERRAIN_CHARS,
    TERRAIN_STATS,
    TRADEABLE,
    UNIT_TYPES,
    army_name,
    roll_tile_name,
    unit_atk,
    unit_kind,
    unit_max_hp,
    unit_move_cost,
    unit_speed,
    unit_supply,
)

# 数值由各领域 parameters.py 拥有；这里仅提供历史公共常量转口。
from .balance import (
    ARMY_GID_BASE,
    START_RING_RATIO,
    ARRIVE_ATTEMPTS,
    ARRIVE_MARGIN_DIV,
    ARRIVE_MARGIN_MIN,
    BLOC_NAME_MAX,
    BUY_REPORT_COST,
    DIPLO_COST,
    EXTRA_PROMPT_TURNS,
    FALL_TRUCE_TURNS,
    MARKET_DEPTH_NATIONS_DIV,
    MOVE_COST,
    PLAN_MAX_TURNS,
    REPORT_EVERY,
    RES_KEYS,
    RES_LABEL,
    RETREAT_DEF_COVER,
    SPY_COST,
    SPY_TURNS,
    START_RES,
    SUMMARY_MIN_CHARS,
    ACADEMY_AMORT_TURNS,
    TOWNHALL_SLOT_BONUS,
)


CROSS = [(0, 0), (0, -1), (0, 1), (-1, 0), (1, 0)]

# 引擎机制常量（非数值表，就近放在引擎里，杜绝魔法数）
KEEP_MAPS = 3                 # 各国地图快照只留最近 N 份（控体积）
KEEP_SAVES = 2                # 间谍经济情报快照只留最近 N 份（控体积）

# 总消费（累计，按当时市价折金）——终局结算按它排名。只计「被消耗掉的资源」，
# 市场买卖/馈赠不计（买来的物资在真正被消耗时才入账），避免重复计数。
SPEND_FIELDS = ("build",    # 建造实付金 + 木×市价（含城堡升级）
                "recruit",  # 征兵消耗（粮/装备/金）× 市价
                "supply")   # 军费：军队吃掉的补给 × 市价
# 本期经济账本字段（按国累计，结完报表清零；全部按当时市价折金）
LEDGER_FIELDS = ("prod_value",      # 采集/工厂 产出 × 市价（军屯 2026-09-18 起不产，不在此列）
                 "mid_value",       # 工厂中间投入 × 市价
                 "fuel_value",      # 能源厂燃料 × 市价
                 "gold_in",         # 金矿 + 市政厅 入国库的金
                 "supply_eaten",    # 军队实际吃掉的补给（单位）——军费口径，不看来源
                 "heal_value",      # 军队回血消耗的装备 × 市价（HEAL_EQUIP_COST）
                 "import_gold",     # 市场买入总额
                 "export_gold",     # 市场卖出总额
                 "invest_gold",     # 建造实付金
                 "invest_wood_value")  # 建造耗木 × 当时市价

# ---- 存档契约：**追加式**（只加字段、不删字段）----
# 2026-09-19 政策改动（用户口径：「这个档允许追加，而不是重开，只加银行不能删硬件，
# 就是为了增量更新存档」）。此前是"版本锁死、零旧格式兼容"，现在改成：
#   · **加字段**：登记 SAVE_KEYS + 在 SAVE_DEFAULTS 给默认（save/load 各一行搬运）。
#     老档缺这个键 ⇒ **按默认补齐**，整档不作废、不用重开。⇒ **加字段不需要 bump 版本**。
#   · **不许删字段、不许改已有字段的语义**（那才需要 SAVE_VERSION +1 让旧档作废，
#     并把 SAVE_MIN_VERSION 抬到新版本）。
#   · 版本闸门：`SAVE_MIN_VERSION ≤ 档内版本 ≤ SAVE_VERSION` 才放行——
#     **比代码新的档依旧拒载**（未来格式我们读不懂），无版本号的古董档也拒。
#   · 覆盖不到的区域仍旧拒载：缺"非追加字段"（截断/手改坏）直接报错，不带病续局。
# save() 写前仍断言键集合与 SAVE_KEYS 严丝合缝——新增字段忘了登记，第一次存档就炸，
# 而不是静默丢字段（历史上"幽灵地块"就是 save/load 人肉对齐 50 键漏出来的病）。
# v2（2026-09-15）：地图生成换成蓝噪声 + 密度图调制（`mapgen.py`）。**必须**bump：
#   已物化格子的 terrain/resources 是**字面量存在档里**的，不拒载就会出现
#   "已占格=旧算法、新占格=新算法"的混图（比整档作废更糟——它不报错）。
# v3（2026-09-18）：外交主体上移到「外交实体」（独立国家 | 联盟）——保障/共同防御/
#   宣战/议和一律以实体为签约方，在盟国家不再持有个人条约。**必须**bump：旧的
#   alliances/defense_pacts/guarantees 是**国家级**的，读进来就会出现"成员国还留着
#   个人条约"这种新规则下不可能存在的状态（比整档作废更糟——它不报错）。
# v4（2026-09-19）：加「世界央行」（`bank`：开关/储蓄利率/未还贷款）。
#   ★ 按新的追加式政策，这一笔**本来不必 bump**（旧档缺 bank 就按默认补齐）；
#     留 4 只是记录"bank 是哪一版进来的"。**v2/v3 那两次是真破坏性变更**（删过字段、
#     改过语义）⇒ SAVE_MIN_VERSION 卡在 3：比它老的档不认（读进来会静默丢条约）。
SAVE_VERSION = 5
SAVE_MIN_VERSION = 5

# 追加式存档的**默认值表**：老档缺这些键时就按这里补齐（只列"追加进来的"字段）。
# 加字段时：SAVE_KEYS 登记 + 这里给默认 + save/load 各一行搬运 —— 三处齐了就不用 bump。
SAVE_DEFAULTS: dict = {
    "pacts": [],                                      # v3 起：实体级条约表
    "bank": {"on": False, "rate": 0.0, "loans": {}},  # v4 起：世界央行
    # 《开局指南》开关（玩家接口.opening_guide）。旧档缺它 → 按 True 补齐：实际无影响，
    # 因为挂载窗口是"前 EXTRA_PROMPT_TURNS 回合"，而现存老档早已远超这个回合数。
    "opening_guide": True,
    # v4 起：每回合的 GDP run-rate（央行授信按它算）。旧档缺 → 空表，
    # 由 `nation_gdp` 退回"最新一期经济报表的 GDP"，不会把授信算成 0。
    "gdp_run": {},
    # 全期累计 token 用量（**纯统计**）。旧档缺 → 空表；load 时按存档里的国家补零起步。
    # 默认必须是**空**的表，不能写成含"秦楚齐"的模板：八国档读进来会多出三个不存在的国家。
    "token_usage": {},
    # 市场找零余额（不足 1 金的小数，见 `World._settle_gold`）。旧档缺 → 空表，
    # 从头攒零钱即可：影响仅仅是"跨国界续档时那不到 1 金的零头"，不是语义变更。
    "gold_carry": {},
}
# `World.token_usage` 每国一格的字段清单。**save 与 load 共用这一份**：
# save 按它补齐国家键，load 按它重建骨架 ⇒ 两边顺序、字段名必然一致，
# 存档往返（save→load→save）才可能字节级相等（见 test_save_load_save_is_byte_identical）。
USAGE_FIELDS = ("calls", "prompt", "hit", "miss", "out", "reason", "est_calls", "real_calls")
SAVE_KEYS = ("version", "size", "seed", "turn", "rng_state", "nations", "order",
             "tiles", "armies", "next_army_seq", "diplo_built", "nation_code",
             "guard_once", "wars", "war_id", "truce", "blocs",
             "votes", "vote_id", "pacts", "bank", "mail_pending",
             "mailbox", "summaries", "summary_blocks", "long_memory", "turn_memory", "gift_pending",
             "map_pending", "maps", "spy_pending", "econ_intel", "plans", "polity",
             "extra_prompt", "opening_guide", "peace_offers", "proposals", "offer_id", "prices",
             "equilibrium", "flow_in", "flow_out", "grid_short", "energy_report",
             "econ_summary", "econ_reports", "ledger", "spend", "gdp_run", "history",
             "history_seen", "token_usage", "gold_carry", "mod_dependencies",
             "diplomacy_queue", "command_results", "command_id", "approved_votes", "state_version", "phase", "mod_state")


class SaveFormatError(Exception):
    """存档版本/键集合不符：拒载，提示重开（不做任何回溯迁移）。"""


def _truce_key_id(x: str) -> str:
    """休战表键的规范化：裸国名 → 国家实体 id（`国:x`）；已经是实体 id 的原样返回。

    读档迁移用：2026-10-11 之前休战表按**裸国名**存，之后按**实体 id**存
    （联盟＝`盟:名字`、独立国＝`国:名字`，与条约表 `self.pacts` 同一口径）。
    """
    return x if ":" in x else ent_nation(x)


def _pair(a: str, b: str) -> frozenset:
    return frozenset((a, b))


# ---- 外交实体：本作**唯一**的对外签约主体（2026-09-18 外交改革）----
# 只有两种：**独立国家**（不在任何联盟中的国家）与**联盟**。
# 在盟的国家不是外交实体 —— 它的一切约束性外交（保障独立/共同防御/宣战/议和）
# 都由其联盟以实体身份出面，且须**联盟投票通过**。非约束动作（写信/馈赠/换图/间谍）
# 仍归国家自己（那是通信与物资，不是对外承诺）。
# 实体 id 是字符串（进存档、进日志、进面板）："国:<国名>" / "盟:<联盟名>"。
# 联盟改名时引用要整体同步 —— 见 World._rename_bloc_refs（votes 与 pacts 两处）。
ENT_NATION = "国:"
ENT_BLOC = "盟:"


def ent_nation(name: str) -> str:
    """国家实体的 id（独立国家才用它当签约方）。"""
    return ENT_NATION + name


def ent_bloc(name: str) -> str:
    """联盟实体的 id。"""
    return ENT_BLOC + name


def is_bloc_ent(ent: str) -> bool:
    return ent.startswith(ENT_BLOC)


def ent_key(ent: str) -> str:
    """去掉前缀的名字（国名或联盟名），供展示/查表。"""
    return ent[2:]


def retreat_note(defends: bool, cover: int) -> str:
    """撤退的攻/防档位文案 —— **一处定义，多处消费**（`retreat()` 的命令行回执、
    结算时三行落地日志、战斗面板上的 `⚑` 标记）。

    `defends`（是不是守方）与 `cover` 都由 `World.battle_sides` 的 `soak_elig`
    一次导出，不在各处重判。文案只陈述**事实**（这轮吃不吃减伤、输出打不打折），
    不替 AI 判断该不该撤——给事实不给判断。
    """
    if defends:
        return f"防御撤退，本回合结算减伤 {100 - cover}%"
    return "进攻撤退，本回合结算无减伤（输出 −80%）"


# ---- 游戏层 ROI 原语（原在 玩家接口._gval / _econ_building；提到这里免得规则 AI
#      反向依赖 LLM 层。LLM 面板与规则 AI v9 从此共用同一份数字，口径逐字一致，
#      与 feat/rl 的同名实现可互相 cherry-pick）----

def good_value(world: "World", good: str, amt: int, side: str = "mid") -> float:
    """把 amt 单位 good 折成金（黄金按 MARKET['黄金'] 折算）。

    side='mid' 用中间价；'buy'/'sell' 用含价差的实际成交单价（自用替代 / 外销口径）。
    """
    if amt <= 0:
        return 0.0
    if good == "黄金":
        return amt * MARKET["黄金"]
    if side in ("buy", "sell"):
        return amt * world.market_quote(good, 1, side)[0]
    p = world.prices.get(good)
    return amt * (p if p is not None else float(MARKET.get(good, 0)))


def build_econ(world: "World", building: str, tile=None) -> dict:
    """单个建筑的**数字版**经济核算（玩家接口 经济面板与规则 AI v9 共用这一份）。

    返回：
        {
          "building": 建筑名,
          "capex":    造价折金（金 + 木×现价；城堡取 L1）,
          "per_turn": 每回合净收益（金）—— 负 = 净支出,
          "payback":  capex / per_turn（per_turn ≤ 0 → None，永远回不了本）,
          "detail":   分项明细（给面板拼文案用）,
        }

    `kind == "townhall"`（市政厅）按**该格真实密度**算产出（`gold_base + 其他建筑位 × gold_per_slot`），
    与引擎 `resolve_turn` 那段逐字同式 —— 所以传 `tile` 与不传会得到不同答案（这就是"按格子效果选"）。

    ⚠️ **注意口径**：加工厂（补给厂/装备厂）的产出按"**自用替代**"（买价）计 ——
    即"这些产出替你去市场上买"。这个口径对**流量品**（补给：军队每回合都吃）成立，
    对**存量品**（装备：只在征兵时一次性消耗）会**高估** —— 你并不是每回合都去买装备。
    """
    info = BUILDINGS[building]
    wp = world.prices.get("木头", float(MARKET["木头"]))
    cost = info["cost"] if isinstance(info["cost"], int) else info["cost"][0]
    # ★ 传了地块就按**该格的实际造价**算（用户 2026-09-11：ROI 必须含地形成本）。
    #   公式与引擎 `World.build` 逐字对齐：地形施工惩罚只上浮**金价**（木材不变），
    #   工程院再减 25%（只认已落成的、且自己不享受自己的减免）。
    if tile is not None:
        t = world.tiles.get(tile)
        if t is not None:
            bp = TERRAIN_STATS[t["terrain"]]["build_penalty"]
            if bp:
                cost = cost * (100 + bp) // 100
            if t["buildings"].get("工程院") and building != "工程院":
                cost = cost * (100 - building_effect("工程院", "build_discount")) // 100
    capex = cost + info.get("wood", 0) * wp
    kind = info["kind"]
    detail: dict = {}

    if kind == "gold":
        per = sum(good_value(world, g, a) for g, a in info["outputs"].items())
        detail["net"] = per
    elif kind == "extract":
        per = sum(good_value(world, g, a, "sell") for g, a in info["outputs"].items())
        detail["net"] = per
    elif kind == "energy":
        fuel = sum(good_value(world, f, a, "buy") for f, a in info.get("fuel", {}).items())
        per = -fuel
        detail.update(fuel=fuel, energy_out=info["energy_out"])
    elif kind == "factory":
        inv = sum(good_value(world, f, a, "buy") for f, a in info.get("inputs", {}).items())
        out_buy = sum(good_value(world, g, a, "buy") for g, a in info.get("outputs", {}).items())
        out_sell = sum(good_value(world, g, a, "sell") for g, a in info.get("outputs", {}).items())
        # 电按"1 木发 2 电"的燃料成本估
        ec = info.get("energy", 0) * good_value(world, "木头", 1, "buy") / 2
        per = out_buy - inv - ec
        detail.update(inputs_value=inv, outputs_buy=out_buy, outputs_sell=out_sell,
                      energy_cost=ec, net=out_buy - inv)
    elif kind == "academy":
        # ★ 工程院（2026-09-16，用户：「工程院的收益会自然体现到 roi，自然密度建筑」＋
        #   「你只算了省的钱，没算未来更快的密度速度为市政厅赚来的钱，太短视了」）：
        #   效果 = **本格一切建造金价 −25%**。收益是**两笔，都要算**：
        #     ① **省下的钱**：每建一座省 `disc × 该座金价`（用本格已建楼的平均造价现读
        #        —— 采集楼 45~70、工厂/兵营/市政厅 175~500，越密越贵的格省得越多）；
        #     ② **密度红利（市政厅联动）**：楼便宜 ⇒ 这一格填得更快更满 ⇒ 本格市政厅的产出
        #        `gold_base + 本格其他建筑位 × gold_per_slot` 跟着涨 ——
        #        省下的钱折成"多买几座楼"，每多一座楼 ⇒ 厅**每回合**多 `gold_per_slot` 金
        #        （`disc × 本格建筑位 × gold_per_slot`）。本格还没厅但**够格建**时也算：
        #        那是"厅会来"。
        #   一次性那笔（①）按 `ACADEMY_AMORT_TURNS` 摊成每回合；②本来就是每回合的钱。
        disc = building_effect(building, "build_discount") / 100.0
        tt = world.tiles.get(tile) if tile is not None else None
        n_used = 0
        c_avg = 0.0
        hall_ok = False
        if tt is not None:
            n_used = sum(tt["buildings"].values())          # 不含它自己（还没建）
            costs = [BUILDINGS[b_]["cost"] if isinstance(BUILDINGS[b_]["cost"], int)
                     else BUILDINGS[b_]["cost"][0]
                     for b_, c_ in tt["buildings"].items() for _ in range(c_)]
            c_avg = (sum(costs) / len(costs)) if costs else 0.0
            hall_ok = bool(tt["buildings"].get("市政厅")) or n_used >= (
                BUILDINGS["市政厅"].get("min_slots") or 10 ** 6)
        saved = (disc * n_used * c_avg) / ACADEMY_AMORT_TURNS if c_avg else 0.0
        density_gain = (disc * n_used * building_effect("市政厅", "gold_per_slot")
                        if hall_ok else 0.0)
        per = saved + density_gain
        detail.update(discounts=disc, avg_cost=c_avg, per_turn_saved=saved,
                      per_turn_density=density_gain, hall_ok=hall_ok)
    elif kind == "townhall":
        # ★ 2026-09-16（用户）：「应该走正常的 roi 机制……有市政厅的 roi 自动高」：
        #   市政厅的产出是**该格真实密度**的函数 —— 引擎 `resolve_turn` 每回合给它
        #   `gold_base + 本格其他建筑位 × gold_per_slot` 金（见上面那段"市政厅：每座 =…"）。
        #   原先这里把它和兵营/城堡一起归到 `per = 0.0` ⇒ 回本 None ⇒ **任何按回本排序的
        #   地方都看不见它**（经济层的 ROI 榜、面板都看不见）⇒ 两代规则 AI 从未建过它。
        #   这里按**传进来的那一格**算真值：密度越高的格，回本越快、ROI 自动越高。
        base = building_effect(building, "gold_base")
        per_slot = building_effect(building, "gold_per_slot")
        tt = world.tiles.get(tile) if tile is not None else None
        others = sum(tt["buildings"].values()) if tt is not None else 1   # 不含它自己（还没建）
        others += TOWNHALL_SLOT_BONUS      # ★ 稍微乐观：那一格还会继续堆（用户 2026-09-16：+2~3）
        per = base + others * per_slot
        if info.get("energy"):
            per -= info["energy"] * good_value(world, "木头", 1, "buy") / 2   # 与工厂同款电耗估法
        detail.update(gold_base=base, gold_per_slot=per_slot, other_slots=others)
    else:                                   # castle / barracks / tower：不产出，回本无定义
        per = 0.0

    return {"building": building, "capex": capex, "per_turn": per,
            "payback": (capex / per) if per > 0 else None,
            "cost": cost, "wood": info.get("wood", 0), "wood_price": wp,
            "detail": detail}


_ADJ_CACHE: dict[int, tuple] = {}


def _adjacency(size: int) -> tuple:
    """每格的邻居（**整数下标** = `x * size + y`）：只与地图边长有关，按边长建一次全家共用。

    与 `World.neighbors` **同序**（`dx` 外层、`dy` 内层）。为什么要有它：`_reachable`
    每支军每回合都要跑，而旧内层每弹一格都要现建一个邻居列表。这张表是**纯几何**的
    （与局面无关），所以不存在"缓存陈旧"这个问题。`ruleai` 的代价场有一张同款的。
    """
    got = _ADJ_CACHE.get(size)
    if got is None:
        rows = []
        for x in range(size):
            for y in range(size):
                rows.append(tuple(
                    x2 * size + y2
                    for x2 in (x - 1, x, x + 1) for y2 in (y - 1, y, y + 1)
                    if (x2 != x or y2 != y) and 0 <= x2 < size and 0 <= y2 < size))
        got = tuple(rows)
        _ADJ_CACHE[size] = got
    return got


class Nation:
    def __init__(self, name: str, res: dict[str, int] | None = None):
        self.name = name
        # 构造即全键：传部分字典也补齐 START_RES（缺键国家首次结算 add_res 会 KeyError）
        self.res = {**START_RES, **(res or {})}

    def __deepcopy__(self, memo):
        return Nation(self.name, copy.deepcopy(self.res,memo))

    def __repr__(self):
        return f"<Nation {self.name}>"

__all__ = [n for n in globals() if not n.startswith("__")]
