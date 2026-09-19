# -*- coding: utf-8 -*-
"""多国引擎（MP）。

多个国家共存于同一张地图，各自经营（规则对所有国家一致，无作弊入口）：
  - 每国独立 国库(黄金)/战略储备/电网(不存储)/军队/国土/信箱；
  - 无人地带的视野内地块由「野人」把守，打赢即拓疆；国家间打赢即夺地，
    空城被敌军队踏入即陷（无防即失）；
  - 移动/攻击受国家关系约束：中立（非同盟非交战）不能入境、不能攻击；
  - 外交：联盟(起名结盟·全体创始成员同意·单方面退盟·共享视野·成员间外交免费·
    进攻战争须投票·议和由盟主投票·同战线自动归还核心领土)/保障独立/共同防御/
    宣战(对方必须接受，其保障/共同防御/联盟全体按传递闭包自动参战)/求和(可赔款/索款/白和)；
  - 信箱：任何内容，下回合到信。
  - 看海：world.history 记下每个行动/每封信/每场战/每桩外交，Observer 全可见；
    各国 agent 只见「自己该知道」的（自己的面板/信箱/视野内事件）。

坐标 0-based；对外（面板/tool 入参）用 1-based。
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

from game import (
    ARMY_HEAL_PER_TURN,
    BANK_LOAN_MAX,
    BANK_LOAN_MAX_TURNS,
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

# 数值表在 balance.py（**唯一调参入口**）；这里原样转口，`mp.X` 的老引用照旧。
from balance import (
    ARRIVE_ATTEMPTS,
    ARRIVE_MARGIN_DIV,
    ARRIVE_MARGIN_MIN,
    BLOC_NAME_MAX,
    DIPLO_COST,
    EXTRA_PROMPT_TURNS,
    FALL_TRUCE_TURNS,
    HUNS_RING_RATIO,
    MARKET_DEPTH_NATIONS_DIV,
    MOVE_COST,
    PLAN_MAX_TURNS,
    POLITY,
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
ARMY_GID_BASE = 100_000_000   # 军队全局唯一 gid = 国家码 × 该值 + 序列（对 AI 不可见）
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
                 "import_gold",     # 市场买入总额
                 "export_gold",     # 市场卖出总额
                 "invest_gold",     # 建造实付金
                 "invest_wood_value")  # 建造耗木 × 当时市价

# ---- 存档契约：版本锁死，零旧格式兼容 ----
# SAVE_VERSION + SAVE_KEYS 是唯一来源：
#   save() 写前断言键集合与 SAVE_KEYS 一致——新增状态字段忘了登记，第一次存档就炸，
#   而不是静默丢字段（历史上"幽灵地块"就是 save/load 人肉对齐 50 键漏出来的病）；
#   load() 先验版本再验键，任何不符直接抛 SaveFormatError——旧档不迁移、不猜，
#   "同 seed 同档同状态"是复现性的口径，跨版本迁移就是腐烂。
# 新增字段三步：__init__ 给默认 → SAVE_KEYS 登记 → save/load 各一行搬运。
# v2（2026-09-15）：地图生成换成蓝噪声 + 密度图调制（`mapgen.py`）。**必须**bump：
#   已物化格子的 terrain/resources 是**字面量存在档里**的，不拒载就会出现
#   "已占格=旧算法、新占格=新算法"的混图（比整档作废更糟——它不报错）。
# v3（2026-09-18）：外交主体上移到「外交实体」（独立国家 | 联盟）——保障/共同防御/
#   宣战/议和一律以实体为签约方，在盟国家不再持有个人条约。**必须**bump：旧的
#   alliances/defense_pacts/guarantees 是**国家级**的，读进来就会出现"成员国还留着
#   个人条约"这种新规则下不可能存在的状态（比整档作废更糟——它不报错）。
# v4（2026-09-19）：加「世界央行」（`bank`：开关/储蓄利率/未还贷款）。**必须**bump：
#   旧档没有 bank 键、load 的键集合校验会直接拒载（本项目零兼容，见上）。
SAVE_VERSION = 4
SAVE_KEYS = ("version", "size", "seed", "turn", "rng_state", "nations", "order",
             "tiles", "armies", "next_army_seq", "diplo_built", "nation_code",
             "guard_once", "wars", "war_id", "truce", "blocs",
             "votes", "vote_id", "pacts", "bank", "mail_pending",
             "mailbox", "summaries", "summary_blocks", "long_memory", "turn_memory", "gift_pending",
             "map_pending", "maps", "spy_pending", "econ_intel", "plans", "polity",
             "extra_prompt", "peace_offers", "proposals", "offer_id", "prices",
             "equilibrium", "flow_in", "flow_out", "grid_short", "energy_report",
             "econ_summary", "econ_reports", "ledger", "spend", "history",
             "history_seen")


class SaveFormatError(Exception):
    """存档版本/键集合不符：拒载，提示重开（不做任何回溯迁移）。"""


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


# ---- 游戏层 ROI 原语（原在 mp_ai._gval / _econ_building；提到这里免得规则 AI
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
    """单个建筑的**数字版**经济核算（mp_ai 经济面板与规则 AI v9 共用这一份）。

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


class World:
    def __init__(self, size: int = 80, seed: int | None = None, *,
                 nations: list[str] | None = None,
                 starts: dict[str, tuple[int, int]] | None = None,
                 res: dict[str, dict[str, int]] | None = None,
                 gen: bool = True,
                 max_turns: int = 200):
        self.size = size
        self.seed = seed if seed is not None else random.randrange(1 << 31)
        self.rng = random.Random(self.seed)
        self.turn = 0
        # ★**本局总回合数**（用户 2026-09-15：「v10 起，不设默认视野，恒等于回合数加 20」）。
        #   规则 AI 的规划窗口（`left = 视野 − world.turn`）从这里推，**不再有自己的
        #   `HORIZON` 常量** —— 那个常量必须由外部 `set_horizon()` 覆盖才对准，而
        #   "设了但没设上"是这个项目反复栽的坑（2026-09-15 一次：v11/v12 全程按 200 规划）。
        #   ⇒ 口径唯一：**视野 = `max_turns + 20`**。跑局的人只需把本局长度放进来
        #   （`mp_run` 按 `--turns`/配置设；`feat/rl` 分支的 `rl/env.py` 按 `max_turns` 设）。
        #   缺省 200 是**这场游戏的缺省长度**（与 `mp_config` 的 `max_turns` 同值），
        #   不是"缺省视野"。
        self.max_turns = int(max_turns)
        self._mapgen = None          # 整张图的生成器（惰性，见 mapgen 属性）
        self.tiles: dict[tuple[int, int], dict] = {}
        self.nations: dict[str, "Nation"] = {}
        self.order: list[str] = []
        self.wars: list[dict] = []  # 战争冲突：{id, atk(进攻主导), def(防御主导), followers(跟随方), turn}
        self._war_id = 0   # 计数器先自增再取值 → 首个 id 为 1
        self.truce: dict[frozenset, int] = {}  # 休战期：边→ 生效至第 N 回合（含），期内不得再宣战
        # 联盟 = {name 联盟名, chief 盟主(发起方，可移交), members 成员(加入序), turn 创立回合}
        self.blocs: list[dict] = []
        # 联盟投票：{id, kind: 宣战|议和|入盟|缔约, bloc, proposer, payload, votes{国:bool}, turn}
        self.votes: list[dict] = []
        self._vote_id = 0  # 同上：首个投票 id 为 1
        # 条约表：保障（单向）/ 共同防御（对称）。**签约方一律是外交实体**（见 ent_* 助手）：
        #   条目 = {kind: "保障"|"共同防御", a: 实体id, b: 实体id, turn: 缔结回合}
        #   共同防御按 id 排序只存一条；保障按方向存（a 保障 b）。
        # 旧的「双边同盟」alliances 与**国家级**的 defense_pacts/guarantees 已随 v3 退休：
        #   国家不再是签约主体，成员手里不可能存在个人条约（入盟即作废）。
        self.pacts: list[dict] = []
        # 世界央行（2026-09-19）：**默认关**（`mp_config.json` 的 world_bank 打开才生效）。
        #   on   —— 开关
        #   rate —— 储蓄利率（观察者设，可为负；负则每回合按现金扣钱，**扣到 0 为止**）
        #   loans —— {国名: {principal, due, turns_left, taken_turn, rate}}，一国同时只有一笔
        # 开了之后：国库现金默认就是储蓄（不用存）；贷款利率 = rate + BANK_SPREAD。
        self.bank: dict = {"on": False, "rate": 0.0, "loans": {}}
        self.mail_pending: list[dict] = []
        self.mailbox: dict[str, list[dict]] = {}
        self.summaries: dict[str, list[dict]] = {}  # 各国回合小结纪事 [{turn,text}]（私有，本国 AI 记忆；全留，供旧回合汇总）
        self.summary_blocks: dict[str, list[dict]] = {}  # 各国阶段块总结 [{from,to,text,turn}]（滑出 replay 的回合经 LLM 压成一段，覆盖其小结）
        self.long_memory: dict[str, str] = {}     # 各国递归累积的长期记忆（酒馆式：压缩时以旧记忆为基础扩写，承载长程规划/盟约/教训）
        self.turn_memory: dict[str, list[dict]] = {}  # 各国完整回合记录（含思考 reasoning_content），按 ctx_window 预算动态保留最近若干回合
        self.gift_pending: list[dict] = []         # 馈赠在途（下回合到账）
        self.map_pending: list[dict] = []          # 交换地图在途（下回合到账）
        self.maps: dict[str, list[dict]] = {}      # 各国收到的地图情报（{from,turn,text}，留最近3张）
        self.spy_pending: list[dict] = []          # 间谍在途（3回合后回报）
        self.econ_intel: dict[str, list[dict]] = {}  # 各国收到的间谍情报（{from,turn,text}，留最近2份）
        self.plans: dict[str, dict] = {}           # 各国国策规划 {text, turn}——常驻上下文，每10回合须修订
        self.polity: dict[str, str] = {}           # 政体标记（"huns"=匈奴）→ 造价/征召/外交限制
        self.extra_prompt: dict[str, dict] = {}    # 临时注入的额外上下文 {text, until}——塞入正常 system_prompt，until 后自动消失
        self.peace_offers: list[dict] = []
        self.proposals: list[dict] = []
        self._offer_id = 0  # 同上：首个邀约 id 为 1
        self.prices: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}   # 中间价 mid
        self.equilibrium: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}  # 供需均衡价（每回合末重算）
        self.flow_in: dict[str, int] = {g: 0 for g in TRADEABLE}   # 本回合世界入库流量（产出），算完均衡价清零
        self.flow_out: dict[str, int] = {g: 0 for g in TRADEABLE}  # 本回合世界出库流量（消耗）
        self.armies: list[dict] = []
        # `troops` / `guardians`（非野人名单、野人按格索引）的惰性缓存 —— 派生量，**不进存档**
        self._troops: list[dict] = []
        self._guards: dict = {}
        self._army_key: tuple | None = None
        self.next_army_seq: dict[str, int] = {}  # 各国独立军队序列：从1递增、阵亡不回收
        self.diplo_built: dict[str, int] = {}    # 各国「自建」外交中心座数（夺地抢来的不计，不影响自建限额）
        self.nation_code: dict[str, int] = {}    # 国家码：军队全局唯一id = 码×1e8+序列（野人=0，秦=1→100000001）
        self._next_code = 1
        self.guard_once: set[tuple[int, int]] = set()  # 每格至多出生一支野人：死了就没了，不重生
        self._engage_seq = 0                     # 入场序号：军队每次 atk 参战取一个递增号（野地索取顺序）
        self.grid_short: dict[str, bool] = {}
        self.energy_report: dict[str, tuple[int, int, bool]] = {}
        self.econ_summary: dict[str, str] = {}   # 上一回合结算摘要（各国 agent 看）
        # 经济报表：每 REPORT_EVERY 回合自动结一期（第 11/21/31… 回合开局可查），AI 只读、不能手动跑
        self.econ_reports: dict[str, list[dict]] = {}   # {国: [期快照…]}
        self.ledger: dict[str, dict] = {}               # 本期累计账本（结完报表清零）
        self.spend: dict[str, dict] = {}                # 总消费（全期累计，不清零）：终局排名用
        self.history: list[dict] = []
        self.history_seen = 0
        # gen=False：只为读档准备一个空壳（世界由存档整体还原）。默认 gen=True 才铺地图/野人——
        # 否则 load() 会先按默认「秦楚齐」建一遍，再逐格覆盖存档，留下存档里没有的**幽灵地块**
        # （国家数 ≠3 时必然发生：默认三国里没被覆盖的那些格子会留在图上）。
        if gen:
            names = list(nations or ["秦", "楚", "齐"])
            for nm in names:
                self.nations[nm] = Nation(nm, (res or {}).get(nm))
                self.order.append(nm)
                self.mailbox[nm] = []
                self.grid_short[nm] = False
                self._assign_code(nm)
            if starts:
                self._place_crosses(starts)
            else:
                self._place_ring(names)
            self._ensure_guardians()

    # ------------------------------------------------------------- 基建
    def alive(self) -> list[str]:
        return [n for n in self.order if n in self.nations]

    def _check(self, x: int, y: int) -> None:
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise IndexError(f"坐标越界：({x+1},{y+1}) 超出地图（1..{self.size}）")

    def tile_terrain(self, x: int, y: int) -> str:
        """这一格的地形：**已物化的地块以它自己的 `terrain` 为准**（那是权威值——
        占地后写进地块、也写进存档），未物化的才现问 `mapgen`。

        （移动代价按地形算之后，这条从"纯函数"变成"以地块为准"很关键：
        测试里就地改地形、以及将来任何地形改造，都必须让可达性看得见。）
        """
        t = self.tiles.get((x, y))
        return t["terrain"] if t is not None else self.mapgen.terrain(x, y)

    # ---- 移动可达性（2026-09-15：多格移动**逐格**判定，不再是一次跳跃）----
    def _mv_wall(self, name: str, x: int, y: int) -> str | None:
        """mv 走一步进这格的"墙"：`None` = 可通行。

        口径与旧版**目标格**判定逐条对齐（只是现在每步都要过这一关）：
        野地 / 自家 / 盟国可走；敌国与中立领土一律不可（要进占只能 atk）；
        野地上有与你交战的敌军驻守也不可（得先 atk）。
        """
        owner = self.owned_by(x, y)
        if owner is not None and owner != name and not self.allied_between(name, owner):
            return (f"({x + 1},{y + 1}) 是敌国领土，mv 不得进入；进占请用 atk（会交战/占领）"
                    if self.war_between(name, owner)
                    else f"中立不可入境：({x + 1},{y + 1}) 是「{owner}」的领土（结盟或宣战后才能进出）")
        if owner is None and any(d["owner"] != name and d["owner"] != "野人"
                                 and (d["x"], d["y"]) == (x, y)
                                 and self.war_between(name, d["owner"])
                                 for d in self.troops):    # 谓词本就排除野人 ⇒ 只扫国家军队
            return f"({x + 1},{y + 1}) 有敌军驻守，不能 mv 过去；进攻请用 atk（会交战）"
        return None

    def _reachable(self, name: str, a: dict, *, for_attack: bool = False
                   ) -> dict[tuple[int, int], int]:
        """该军本回合**走得到**的格子 → 累计代价（一致代价搜索，代价是 1/2 的小整数）。

        规则（除了"逐格走"本身，其余与旧口径一致）：

        - 每步消耗 = `max(出发格代价, 目标格代价)`（用户 2026-09-15：**对称**——
          进森林/山地减速，**待在森林/山地里的那一回合也一样慢**，不存在
          "从山上冲出来跑更快"），总预算 `unit_speed(a)`
          ⇒ 骑兵平地 2 格；涉森林/山地一步吃满 ⇒ **只能 1 格、且不可能穿过山地**；
        - **中间格**必须可通行（`_mv_wall` 为 None）：野地/自家/盟国；
        - `for_attack=True` 时**终点**额外允许敌国领土与驻军格（那是要打的），
          但中途仍必须可通行 —— 所以"隔着一座山打到纵深"不再可能；
        - 起点以代价 0 计入（原地 atk 用得上；mv 到自己格照旧是"挪了个寂寞"）。

        返回 `{格子: 代价}`（不含预算之外的格子）。

        ★ 2026-09-16 换实现，**逐值等价**（同一套判定式、同样的松弛条件与"可攻终点只记不扩"）：
          预算只有 1~2（`unit_speed`）⇒ 用**桶队列**替掉堆（省掉每回合几万次 heapq 调用），
          邻居改用按边长预建的**整数下标表**（`_adjacency`，纯几何）替掉"每步现建列表"。
          这是 v11plus 每支军每回合都要走的路径（`best_step` 与 `marchable` 的前提）。
        """
        size = self.size
        adj = _adjacency(size)
        budget = unit_speed(a)
        start = a["x"] * size + a["y"]
        best = bytearray([255]) * (size * size)      # 索引 = x * size + y；255 = 还没到过
        best[start] = 0
        buckets: list = [[] for _ in range(budget + 1)]
        buckets[0].append(start)
        # ★ 碰过的格记一份**下标名单**：收尾只把这几格还原成坐标建 dict。
        #   （别去枚举整张 `best`：那是 O(全图)，实测比旧版还慢一倍。）
        touched: list = [start]
        terrain = self.tile_terrain
        wall = self._mv_wall
        move = MOVE_COST.get(unit_kind(a), {})
        for d in range(budget + 1):
            b = buckets[d]
            while b:
                i = b.pop()
                if best[i] != d:                     # 陈旧条目（后来有更近的路）
                    continue
                x, y = divmod(i, size)
                here = move.get(terrain(x, y), 1)
                for j in adj[i]:
                    jx, jy = divmod(j, size)
                    cj = move.get(terrain(jx, jy), 1)
                    ncost = d + (here if here > cj else cj)
                    if ncost > budget or ncost >= best[j]:
                        continue
                    why = wall(name, jx, jy)
                    if why:
                        if for_attack and self._atk_target_ok(name, jx, jy):
                            best[j] = ncost          # 可攻：算**终点**，但不从它继续扩
                            touched.append(j)
                        continue                     # 走不进去 ⇒ 更不能穿过
                    best[j] = ncost
                    touched.append(j)
                    buckets[ncost].append(j)
        return {(i // size, i % size): best[i] for i in touched}

    def polity_rule(self, name: str, key: str, default):
        """取该国的政体修正项（数值全在 `balance.POLITY`；没有政体/没这一项 → `default`）。"""
        return POLITY.get(self.polity.get(name) or "", {}).get(key, default)

    def recruit_cost(self, name: str, kind: str) -> dict[str, int]:
        """该国征召一支 `kind` 实际要花的原料（**含政体特价**）。

        引擎（`recruit`）与 AI 文案（`mp_ai` 的征召工具描述）都走这一份口径 ——
        曾经两边各写一遍数字，改一处就漂一处。
        """
        return dict(self.polity_rule(name, "recruit", {}).get(kind)
                    or UNIT_TYPES[kind]["recruit"])

    def _atk_target_ok(self, name: str, x: int, y: int) -> bool:
        """这一格能不能当 atk 的**终点**（敌国领土 / 野地驻军）= "有东西可打/可占"。
        与 `attack` 里那套墙判定的"可攻"侧一致（不含"不抢别人的战斗"那条，那条在 attack 里）。"""
        owner = self.owned_by(x, y)
        if owner is not None:
            return owner != name and not self.allied_between(name, owner) \
                and self.war_between(name, owner)
        return any(d["owner"] != name and (d["x"], d["y"]) == (x, y) for d in self.armies)

    def _unreachable_msg(self, a: dict, x: int, y: int, *, for_attack: bool) -> str:
        """走不到目标格时的说法：能教人（平地几格、崎岖几格），但**不替玩家开图**
        ——地形只在视野内才点名（迷雾限眼不限手：盲推撞墙如实报"墙在视野外"）。"""
        label = UNIT_TYPES[unit_kind(a)]["label"]
        budget = unit_speed(a)
        ter = self.tile_terrain(x, y)
        cost = unit_move_cost(a, ter)
        acted = "冲不进去" if for_attack else "走不到"
        seen = self.visible_to(a["owner"], x, y)
        if seen:
            tip = (f"（{label}移动力 {budget}；进{ter}要 {cost}"
                   f"{'，一步就吃满 ⇒ 只能走 1 格' if cost >= budget else ''}）")
        else:
            tip = f"（{label}移动力 {budget}；沿路地形/敌情不明——盲推撞墙如实报告，但额度照烧）"
        return f"{a['name']} {acted} ({x + 1},{y + 1}){tip}"

    @property
    def mapgen(self):
        """整张图的生成器（`mapgen.MapGen`，**惰性**：第一次问地形/资源才建）。

        2026-09-15 起地图不再是逐格纯函数，而是**由 `(seed, size)` 决定的一整张图**
        （蓝噪声 + 密度图调制，见 `mapgen.py`）：逐格独立抽是泊松随机场，局部既结块
        又有孔洞，且"起步 5 格开什么牌"会被复利放大。对外接口没变
        （`tile_terrain` / `tile_resources` 签名与返回值照旧），调用点不用改。

        ★ **惰性**是有意的：读档（`gen=False`）连图都不用生成——存档里已物化的地块
        自带 `terrain`/`resources` 字面量，只有新占的格子才会问到这里。
        """
        if self._mapgen is None:
            from mapgen import MapGen
            self._mapgen = MapGen(self.seed, self.size)
        return self._mapgen

    def tile_resources(self, x: int, y: int) -> dict[str, int]:
        """该地块的矿藏/耕地布局（建采集建筑要看的就是它）——**静态属性**，
        跟谁先占、占之前打过几仗毫无关系（见 `mapgen`）。

        历史两坑（都已修，别再踩）：曾用**世界共享 RNG** `roll_resources(self.rng, …)`
        ⇒ 同一格摇出什么取决于轮到它时 RNG 走了多远（战斗掷骰也在那条流上），
        「种子可复现」形同虚设；命名同理（每建一格都消耗、撞名重试使消耗个数不定）。
        现在 `self.rng` 只剩战斗与开局布点在用。
        """
        return self.mapgen.resources(x, y)

    def ter_char(self, x: int, y: int) -> str:
        return TERRAIN_CHARS[self.tile_terrain(x, y)]

    def visible_buildings(self, name: str, x: int, y: int) -> dict:
        """name 在 (x,y) 上**看得见**的建筑（不含在建）——全作唯一口径，面板一律走这里。

        · **自家的地**：全部建筑（自己的工地当然清楚；在建不在此列，看 `pending`）。
        · **视野内的他国/无主地**：**只有城堡** —— 要塞从外面就看得见，而兵营/工厂/农田
          是内政底细，那是 `spy` / 换图才买得到的东西。
        · **视野外**：空 dict（与 `visible_to` 同一条纪律）。

        （2026-09-19 用户：「我想公开，因为不知道城堡很吃亏」——公开的**只有城堡**那一档，
        其余建筑与地块资源仍旧未探明。）
        """
        t = self.tiles.get((x, y))
        if not t:
            return {}
        if t["owner"] == name:
            return {b: n for b, n in t["buildings"].items() if n}
        if not self.visible_to(name, x, y):
            return {}
        n = t["buildings"].get("城堡", 0)
        return {"城堡": n} if n else {}

    def owned_by(self, x: int, y: int) -> str | None:
        t = self.tiles.get((x, y))
        return t["owner"] if t else None

    def neighbors(self, x: int, y: int) -> list[tuple[int, int]]:
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < self.size and 0 <= ny < self.size:
                    out.append((nx, ny))
        return out

    def own_tiles(self, name: str) -> list[tuple[int, int]]:
        return sorted((x, y) for (x, y), t in self.tiles.items() if t["owner"] == name)

    def frontier_of(self, name: str) -> set[tuple[int, int]]:
        fr = set()
        for (x, y), t in self.tiles.items():
            if t["owner"] != name:
                continue
            for nx, ny in self.neighbors(x, y):
                if (nx, ny) not in self.tiles:
                    fr.add((nx, ny))
        return fr

    def nation_building_count(self, name: str, building: str, *, include_pending: bool = False) -> int:
        """name 全部地块上某建筑的已落成座数（include_pending=True 含在建）。"""
        n = 0
        for t in self.tiles.values():
            if t["owner"] != name:
                continue
            n += t["buildings"].get(building, 0)
            if include_pending:
                n += (t.get("pending") or {}).get(building, 0) or 0
        return n

    def visible_to(self, name: str, x: int, y: int) -> bool:
        """name 是否看得见 (x,y)：它本身或相邻格（含对角）有自家的地。
        联盟共享视野：盟友的地块视同己方（自己+盟友地盘各带相邻一圈）。"""
        cand = [(x, y)] + self.neighbors(x, y)
        bloc = self.bloc_of(name)
        for cx, cy in cand:
            o = self.owned_by(cx, cy)
            if o == name:
                return True
            if bloc is not None and o in bloc["members"]:
                return True
        # 瞭望塔：己方/盟方任一瞭望塔半径（`effects.vision_radius`）圆（欧氏）内也可见（事件视野）
        # 半径**每次调用取一次**（效果是数据，可能被整体抖动）；这条路径调用极频繁，
        # 别把它塞进内层循环。
        _tower_r = building_effect("瞭望塔", "vision_radius")
        _tower_r2 = _tower_r * _tower_r
        for (tx, ty), t in self.tiles.items():
            if not t["buildings"].get("瞭望塔"):
                continue
            o = t["owner"]
            if o != name and not (bloc is not None and o in bloc["members"]):
                continue
            if (tx - x) ** 2 + (ty - y) ** 2 <= _tower_r2:
                return True
        return False

    # ------------------------------------------------------------- 联盟
    def bloc_of(self, name: str) -> dict | None:
        """name 所属联盟（一国同时只属一个联盟）；无则 None。"""
        for b in self.blocs:
            if name in b["members"]:
                return b
        return None

    def bloc_by_name(self, name: str) -> dict | None:
        for b in self.blocs:
            if b["name"] == name:
                return b
        return None

    def bloc_chief(self, bloc: dict | None) -> str | None:
        """联盟盟主。以 chief 字段为准；字段缺失/失效（旧档、盟主已不在）时退回最早加入者。"""
        if not bloc or not bloc.get("members"):
            return None
        c = bloc.get("chief")
        return c if c in bloc["members"] else bloc["members"][0]

    # ------------------------------------------------------------- 外交实体
    # 对外签约主体只有两种：**独立国家** 与 **联盟**。在盟国家不是实体（见模块头注释）。
    def entity_of(self, name: str) -> str:
        """该国的外交实体：在盟 → 其联盟；否则 → 它自己（独立国家）。"""
        b = self.bloc_of(name)
        return ent_bloc(b["name"]) if b is not None else ent_nation(name)

    def entity_members(self, ent: str) -> list[str]:
        """实体包含的现存国家（联盟=全体成员，加入序；独立国家=[自己]；已灭=[]）。"""
        if is_bloc_ent(ent):
            b = self.bloc_by_name(ent_key(ent))
            return [m for m in b["members"] if m in self.nations] if b else []
        n = ent_key(ent)
        return [n] if n in self.nations else []

    def entity_chief(self, ent: str) -> str | None:
        """实体的对外代表：联盟=盟主，独立国家=它自己。"""
        if is_bloc_ent(ent):
            return self.bloc_chief(self.bloc_by_name(ent_key(ent)))
        n = ent_key(ent)
        return n if n in self.nations else None

    def entity_label(self, ent: str) -> str:
        """日志/面板用的人话名。"""
        if is_bloc_ent(ent):
            b = self.bloc_by_name(ent_key(ent))
            return f"联盟「{ent_key(ent)}」" if b else f"联盟「{ent_key(ent)}」（已散）"
        return ent_key(ent)

    def entities(self) -> list[str]:
        """全部现存外交实体（独立国家 + 联盟），**sorted** —— 面板与闭包都不许裸集合迭代。"""
        out = [ent_nation(n) for n in self.alive() if self.bloc_of(n) is None]
        out += [ent_bloc(b["name"]) for b in self.blocs
                if self.entity_members(ent_bloc(b["name"]))]
        return sorted(out)

    def entity_at_war(self, ent: str) -> bool:
        """实体是否卷入战争（任一国在打就算）——战时锁死与缔约禁令都看它。"""
        return any(self.at_war(m) for m in self.entity_members(ent))

    def _war_brief_ent(self, ent: str) -> str:
        for m in self.entity_members(ent):
            if self.at_war(m):
                return self._war_brief(m)
        return f"{self.entity_label(ent)} 正在交战"

    def _same_camp(self, x: str, y: str) -> bool:
        """x、y 是否"该并肩"：同属一个实体，或有共同防御（宣战并入战线的判据）。"""
        ex, ey = self.entity_of(x), self.entity_of(y)
        return ex == ey or self.has_pact("共同防御", ex, ey)

    # ------------------------------------------------------------- 条约表（实体级）
    def has_pact(self, kind: str, a: str, b: str) -> bool:
        """a、b 都是**实体 id**。保障看方向（a 保障 b）；共同防御对称。"""
        if kind == "保障":
            return any(p["kind"] == kind and p["a"] == a and p["b"] == b for p in self.pacts)
        return any(p["kind"] == kind and {p["a"], p["b"]} == {a, b} for p in self.pacts)

    def pacts_of(self, ent: str) -> list[dict]:
        """与该实体有关的一切条约（面板/断约/亡国清理用）。"""
        return [p for p in self.pacts if ent in (p["a"], p["b"])]

    def guaranteed_by(self, ent: str) -> list[str]:
        """ent 保障的实体（**sorted**）。面板用。"""
        return sorted(p["b"] for p in self.pacts if p["kind"] == "保障" and p["a"] == ent)

    def guarantors_of(self, ent: str) -> list[str]:
        """保障 ent 的实体（**sorted**：闭包要按它迭代，裸集合序会毁复现性）。"""
        return sorted(p["a"] for p in self.pacts if p["kind"] == "保障" and p["b"] == ent)

    def defense_partners_of(self, ent: str) -> list[str]:
        """与 ent 有共同防御的实体（**sorted**，同上）。"""
        return sorted(p["b"] if p["a"] == ent else p["a"]
                      for p in self.pacts
                      if p["kind"] == "共同防御" and ent in (p["a"], p["b"]))

    def _add_pact(self, kind: str, a: str, b: str) -> None:
        x, y = (a, b) if kind == "保障" else tuple(sorted((a, b)))
        self.pacts.append({"kind": kind, "a": x, "b": y, "turn": self.turn})

    def _drop_pact(self, kind: str, a: str, b: str) -> bool:
        """删一条条约（a、b 是实体 id）。返回是否真删到。"""
        for p in list(self.pacts):
            if p["kind"] != kind:
                continue
            hit = (p["a"] == a and p["b"] == b) if kind == "保障" else ({p["a"], p["b"]} == {a, b})
            if hit:
                self.pacts.remove(p)
                return True
        return False

    def drop_pacts_of(self, ent: str) -> list[str]:
        """清掉与该实体有关的一切条约（入盟吞掉个人条约、国家灭亡）。返回人话描述。"""
        gone = []
        for p in list(self.pacts):
            if ent in (p["a"], p["b"]):
                self.pacts.remove(p)
                gone.append(f"{p['kind']}（{self.entity_label(p['a'])}—{self.entity_label(p['b'])}）")
        return gone

    def _pact_block(self, kind: str, A: str, B: str) -> str | None:
        """准入：两实体能否签这种条约。返回拒绝理由，None=可以签。"""
        if A == B:
            return (f"{self.entity_label(B)} 与你同属一个实体——盟内互通领土/互不攻击/互卫"
                    "是联盟自带效果，无须也无法单独缔约")
        if kind == "保障":
            if self.has_pact("保障", A, B):
                return f"{self.entity_label(A)} 已保障 {self.entity_label(B)}，无须重复"
            if self.has_pact("共同防御", A, B):
                return f"两实体已有共同防御（更高一档），无须再保障"
            return None
        if self.has_pact("共同防御", A, B):
            return "两实体已是共同防御"
        return None

    def _conclude_pact(self, kind: str, A: str, B: str) -> tuple[bool, str]:
        """真正落条约（双方实体都已同意）。缔结共同防御时自动解除低档的保障。"""
        low = []
        if kind == "共同防御":
            for x, y in ((A, B), (B, A)):
                if self._drop_pact("保障", x, y):
                    low.append(f"{self.entity_label(x)} 对 {self.entity_label(y)} 的保障")
        self._add_pact(kind, A, B)
        up = f"（自动解除低档：{'、'.join(low)}）" if low else ""
        self.log(f"🕊 {self.entity_label(A)} 与 {self.entity_label(B)} 缔结{kind}{up}", phase="外交")
        return True, f"{self.entity_label(A)} 与 {self.entity_label(B)} 缔结{kind}{up}"

    def _break_pacts_between(self, e1: str, e2: str) -> list[str]:
        """开战前清约束：解除两实体之间的一切保障/共同防御（含双向保障）。"""
        out = []
        for kind, x, y in (("共同防御", e1, e2), ("保障", e1, e2), ("保障", e2, e1)):
            if self._drop_pact(kind, x, y):
                out.append(f"解除 {self.entity_label(x)} 与 {self.entity_label(y)} 的{kind}")
        for line in out:
            self.log(f"💔 {line}（双方开战，自动解除）", phase="外交")
        return out

    def _pact_exit_wars(self, side: list[str], other: list[str]) -> list[dict]:
        """断约退战：跟随方不想打的退出通道——昔日盟友参与的战线，其跟随方身份随之解除。
        side/other 是**国家名单**（实体成员）；名单序来自联盟成员序，不做集合迭代。"""
        exited = []
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if not any(o in atk + dfs for o in other):
                continue
            hit = False
            for m in side:
                if m in w["followers"]:
                    w["followers"].remove(m)
                    hit = True
                if m in w.get("atk_followers", []):
                    w["atk_followers"].remove(m)
                    hit = True
            if hit:
                exited.append(w)
        for m in side:      # 已不属任何战线 → 解除交战，回合末自动遣返
            still = any(m in ([x["atk"]] + list(x.get("atk_followers", []))
                              + [x["def"]] + list(x["followers"])) for x in self.wars)
            if not still:
                for u in self.armies:
                    if u["owner"] == m and u.get("engaged"):
                        u["engaged"] = False
        return exited

    def at_war(self, name: str) -> bool:
        """该国是否正卷入任何战争（进攻/防御主导者或跟随方）。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if name in atk or name in dfs:
                return True
        return False

    def _war_brief(self, name: str) -> str:
        """给"战争期禁止外交"类错误提示用的战线简述。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if name in atk:
                return f"{name} 正与 {'、'.join(dfs)} 交战"
            if name in dfs:
                return f"{name} 正与 {'、'.join(atk)} 交战"
        return ""

    def bloc_desc(self) -> str:
        """看海/面板用：全部联盟一览。"""
        if not self.blocs:
            return "无联盟"
        parts = []
        for b in self.blocs:
            chief = self.bloc_chief(b) or "?"
            parts.append(f"「{b['name']}」（盟主 {chief}；成员：{'、'.join(b['members'])}）")
        return "  ".join(parts)

    # ------------------------------------------------------------- 看海日志
    def _stamp_seen(self, entry: dict) -> None:
        """纪事落盘时刻抓一份"谁看得见这里"的快照。events_for 按快照过滤，
        而不是按查询时刻的视野——否则你后来夺下的地上发生的**旧** Retreat/战报
        会事后凭空显形（回溯补发情报）。"""
        x, y = entry.get("x"), entry.get("y")
        if x is not None and self.nations:
            entry["seen"] = [n for n in self.nations if self.visible_to(n, x, y)]

    def broadcast(self, text: str, phase: str = "事件") -> str:
        """**全世界都看得见**的公告（央行利率这类世界新闻用它）。

        与 `log` 的区别：`log` 按坐标做视野快照，**没有坐标的条目谁也看不到**；
        这里显式把 `seen` 写成全体现存国家，`events_for` 按快照放行。
        """
        entry = {"turn": self.turn, "phase": phase, "nation": None,
                 "x": None, "y": None, "text": text,
                 "seen": list(self.nations)}
        self.history.append(entry)
        return text

    def log(self, text: str, phase: str = "事件", nation: str | None = None,
            x: int | None = None, y: int | None = None) -> str:
        entry = {
            "turn": self.turn, "phase": phase, "nation": nation,
            "x": x, "y": y, "text": text,
        }
        self._stamp_seen(entry)
        self.history.append(entry)
        return text

    def action(self, nation: str, tool: str, args: str, result: str, x=None, y=None):
        entry = {
            "turn": self.turn, "phase": "行动", "nation": nation,
            "x": x, "y": y,
            "text": f"{nation} ◇ {tool} {args} → {result}",
        }
        self._stamp_seen(entry)
        self.history.append(entry)

    def events_for(self, name: str, limit: int = 14) -> list[str]:
        """该国能看到的近期事件（自己相关，或发生在视野内）。信件走信箱，此处不重复。"""
        out = []
        for h in reversed(self.history):
            if h["phase"] == "信件":
                continue
            if h["nation"] == name:
                out.append(f"[第{h['turn']}回合] {h['text']}")
            elif "seen" in h:
                # ★ 有快照就**只认快照**（绝不退回现视野——否则夺地后旧战报会回溯显形）；
                #   无坐标的世界广播也走这条：broadcast 把 seen 写成全体现存国家。
                if name in h["seen"]:
                    out.append(f"[第{h['turn']}回合] {h['text']}")
            elif h.get("x") is not None and self.visible_to(name, h["x"], h["y"]):
                # ★ 按落盘时刻的视野快照过滤（_stamp_seen）；无快照的极少数条目退回现视野
                out.append(f"[第{h['turn']}回合] {h['text']}")
            if len(out) >= limit:
                break
        return list(reversed(out))

    def fresh_history(self) -> list[dict]:
        h = self.history[self.history_seen:]
        self.history_seen = len(self.history)
        return h

    # ------------------------------------------------------------- 开局
    def _new_tile(self, x: int, y: int, owner: str) -> dict:
        terrain = self.tile_terrain(x, y)
        used = {t["name"] for t in self.tiles.values() if t.get("name")}
        return {
            "owner": owner,
            "terrain": terrain,
            "resources": self.tile_resources(x, y),
            "buildings": {b: 0 for b in BUILDINGS},
            "pending": {b: 0 for b in BUILDINGS},  # 在建（下回合才生效）
            # ★地块命名**不能用世界共享 RNG**（2026-09-11 修）。
            #   `roll_tile_name` 撞名会重试，所以每建一格消耗的随机数**个数不定**；
            #   而 `self.rng` 同时是战斗掷骰（`_die` → `self.rng.randint(1,6)`）的流 ——
            #   于是**战斗结果取决于建地的顺序和数量**，同一 seed 两局对不上。
            #   这和 `tile_resources` 那段注释里已经修过的病是同一个（当时只修了资源，
            #   命名漏了）。改成与地形/资源同一套做法：**纯函数 of (seed, x, y)**。
            #   名字本身仍可能因撞名而不同（`used` 依赖建地顺序），但那是纯装饰；
            #   关键是**战斗的随机流从此与命名无关**，A/B 对比才干净。
            "name": roll_tile_name(random.Random(f"{self.seed}:{x}:{y}:name"), used),
            "core": owner,  # 核心领土：首任 owner；每次战争结束按参与者实占重算
            "recruited_this_turn": 0,
            "built_this_turn": 0,
        }

    def _place_crosses(self, starts: dict[str, tuple[int, int]]):
        for nm, (cx, cy) in starts.items():
            for dx, dy in CROSS:
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size and (x, y) not in self.tiles:
                    self._drop_guardians(x, y)   # 全图野人已预置：新占的格子上的守卫撤走
                    self.tiles[(x, y)] = self._new_tile(x, y, nm)

    def _place_ring(self, names: list[str]):
        """各国环状开局（3 国=三角）。"""
        n = len(names)
        c = self.size / 2
        r = self.size * HUNS_RING_RATIO   # 环半径（占地图边长比例，见 balance）
        pts = {}
        for i, nm in enumerate(names):
            ang = -math.pi / 2 + i * 2 * math.pi / n
            pts[nm] = (int(round(c + r * math.cos(ang))), int(round(c + r * math.sin(ang))))
        self._place_crosses(pts)

    def _ensure_guardians(self):
        """把**全图**无主地块都放上野人守卫（开局一次；读档时对旧档做同样的一次性补齐）。

        野人是地图的静态属性，不是"谁看见了才存在"——同一 seed 的野人布局永远相同，
        也不会因为你占了一块地就让边界外凭空冒出野人来。已死过守卫的格子（guard_once）
        不再补：杀了就是永久清空。"""
        for x in range(self.size):
            for y in range(self.size):
                if (x, y) in self.tiles or (x, y) in self.guard_once:
                    continue
                self._spawn_guardian(x, y)

    def _spawn_guardian(self, x: int, y: int):
        gid, seq = self._new_army("野人")
        self.armies.append({"id": seq, "gid": gid, "name": f"野人{seq}", "hp": ARMY_MAX_HP,
                            "x": x, "y": y, "owner": "野人", "moved_turn": -1, "engaged": False})
        self.guard_once.add((x, y))  # 出生过就算数：这格野人死了不再有

    def _drop_guardians(self, x: int, y: int):
        self.armies = [a for a in self.armies
                       if not (a["owner"] == "野人" and (a["x"], a["y"]) == (x, y))]

    # ------------------------------------------------------------- 看海中途加国
    def add_nation(self, name: str, polity: str = "", extra=None,
                   start: dict | None = None, summary=None) -> tuple[bool, str]:
        """看海中途加国：随机到距所有现有领地足够远的位置登场。polity='huns'=匈奴。
        extra=临时注入上下文(塞入正常 system_prompt，20回合后仅剩 summary 小结)；
        summary=20回合后常驻的小结(存进存档)；start=定制开局（如 {"骑":8,"黄金":2000,"补给":300}）。"""
        name = (name or "").strip()
        if not name:
            return False, "需要国名，如 add 匈奴 / add 秦"
        if name in self.nations:
            return False, f"国家 {name} 已存在"
        polity = (polity or "").strip()
        is_huns = polity in ("匈奴", "huns", "hun")
        margin = max(ARRIVE_MARGIN_MIN, self.size // ARRIVE_MARGIN_DIV)
        pos = None
        if self.tiles:
            for _ in range(ARRIVE_ATTEMPTS):
                x, y = self.rng.randrange(self.size), self.rng.randrange(self.size)
                if all(max(abs(x - px), abs(y - py)) >= margin for (px, py) in self.tiles):
                    pos = (x, y)
                    break
        else:
            pos = (self.size // 2, self.size // 2)
        if pos is None:
            pos = (self.rng.randrange(self.size), self.rng.randrange(self.size))
        self.nations[name] = Nation(name, None)
        self.order.append(name)
        self.mailbox[name] = []
        self.grid_short[name] = False
        self._assign_code(name)
        self._place_crosses({name: pos})
        if is_huns:
            self.apply_polity(name, "huns", home=pos, start=start)
        if extra:
            self.extra_prompt[name] = {"text": str(extra), "until": self.turn + EXTRA_PROMPT_TURNS,
                                       "summary": str(summary or "")}
        desc = ("匈奴" if is_huns else "国家") + f" {name} 登场（距各国至少 {margin} 格）"
        if is_huns:
            pd = POLITY["huns"]
            d0 = pd.get("start", {})
            s = start or {}
            cav = int(s.get("骑", d0.get("骑", 0)))
            gold = int(s.get("黄金", d0.get("黄金", 0)))
            sup = int(s.get("补给", d0.get("补给", 0)))
            r = pd.get("recruit", {}).get("骑", {}) or {}
            short = {"粮食": "粮", "装备": "装", "黄金": "金"}
            rc = "+".join(f"{a}{short.get(k, k)}" for k, a in r.items()) or "—"
            desc += (f"：开局 {cav} 骑兵·金{gold}·补给{sup}"
                     f"·建筑+{pd.get('build_cost_pct', 100) - 100}%惩罚·骑兵征召{rc}"
                     "·不能外交（只可勒索/宣战/逼降/求和）")
        self.log(desc, phase="事件", nation=name)
        return True, desc

    def cheat(self, name: str, **kw) -> tuple[bool, str]:
        """观海作弊补助：直接给某国加资源/骑兵（kw: 黄金/粮食/木头/矿石/石油/装备/补给/骑）。"""
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        r = self.nations[name].res
        parts = []
        home = self.own_tiles(name)[0] if self.own_tiles(name) else None
        for k, v in kw.items():
            v = int(v)
            if k == "骑" and v > 0 and home:
                for _ in range(v):
                    gid, seq = self._new_army(name)
                    self.armies.append({"id": seq, "gid": gid, "name": army_name(name, seq, "骑"), "type": "骑",
                                        "hp": ARMY_MAX_HP, "x": home[0], "y": home[1],
                                        "owner": name, "moved_turn": -1, "engaged": False})
                parts.append(f"骑+{v}")
            elif k in r:
                r[k] += v
                parts.append(f"{k}+{v}")
        if not parts:
            return False, "没补任何东西（可用 金/粮/木/矿/油/装/补/骑）"
        self.log(f"⚙ 观海补助 {name}：{'、'.join(parts)}", phase="事件")  # 观察者可见
        return True, f"已给 {name} 补助：{'、'.join(parts)}"

    def apply_polity(self, name: str, polity: str, home: tuple[int, int] | None = None,
                     start: dict | None = None) -> None:
        """给一个已存在的国家套政体（目前仅 huns=匈奴）：覆写开局资源 + 骑兵 + 标记。
        start 可定制（如 {"骑":8,"黄金":2000,"补给":300}），缺省 6骑/1000金/200补。"""
        polity = (polity or "").strip()
        if polity not in ("huns", "匈奴", "hun") or name not in self.nations:
            return
        self.polity[name] = "huns"
        start = start or {}
        _def = POLITY.get(polity if polity in POLITY else "huns", {}).get("start", {})
        gold = int(start.get("黄金", _def.get("黄金", 1000)))
        supply = int(start.get("补给", _def.get("补给", 200)))
        cav = int(start.get("骑", _def.get("骑", 6)))
        self.nations[name].res.update({"黄金": gold, "粮食": 0, "木头": 0,
                                       "矿石": 0, "石油": 0, "装备": 0, "补给": supply})
        if home is None:
            own = self.own_tiles(name)
            home = max(own, key=lambda p: sum(1 for n in self.neighbors(*p)
                                              if self.owned_by(*n) == name)) if own else None
        if home:
            cx, cy = home
            for i in range(cav):
                gid, seq = self._new_army(name)
                self.armies.append({"id": seq, "gid": gid, "name": army_name(name, seq, "骑"),
                                    "type": "骑", "hp": ARMY_MAX_HP,
                                    "x": cx, "y": cy, "owner": name,
                                    "moved_turn": -1, "engaged": False})

    # ------------------------------------------------------------- 资源
    def res(self, name: str, key: str) -> int:
        return self.nations[name].res.get(key, 0)

    def add_res(self, name: str, key: str, v: int):
        self.nations[name].res[key] += v

    # ------------------------------------------------------------- 地块建设
    @staticmethod
    def _eff(t: dict) -> dict:
        """有效建筑数 = 已建成 + 在建（用于占位/上限判断）。"""
        b = t["buildings"]
        p = t.get("pending", {})
        return {k: b.get(k, 0) + p.get(k, 0) for k in b}

    def build(self, name: str, x: int, y: int, building: str) -> tuple[bool, str]:
        """下单建造。造价即扣，但建筑先进入『在建』，回合结算时才落地，下回合起生效。"""
        t = self.tiles.get((x, y))
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        if t is None or t["owner"] != name:
            return False, "只能在自己的地块上建造"
        if building not in BUILDINGS:
            return False, f"未知建筑：{building}"
        info = BUILDINGS[building]
        b = t["buildings"]
        eff = self._eff(t)
        if t["built_this_turn"]:
            return False, "该地块本回合已下过单（每地块每回合限建 1 座）"
        if sum(eff.values()) >= MAX_SLOTS:
            return False, f"建筑位已满（{MAX_SLOTS} 格，含在建）"
        cr = info["cap_resource"]
        if cr is not None:
            have = t["resources"][cr]
            if have <= 0:
                return False, f"本地无{cr}，无法建{building}"
            if eff[building] >= have:
                return False, f"{building} 已达上限：本地 {cr}={have}"
        if info["kind"] == "castle" and eff[building] >= info["max_level"]:
            return False, f"{building} 已达上限 L{info['max_level']}"
        # 特殊约束（市政厅等）：需该地块已用建筑位达标（含在建）/ 每地块限座
        used_eff = sum(eff.values())
        if info.get("min_slots") and used_eff < info["min_slots"]:
            return False, f"{building} 需该地块已用建筑位 ≥{info['min_slots']}（现 {used_eff}），先建满再盖"
        if info.get("limit") and eff[building] >= info["limit"]:
            return False, f"{building} 已达上限（每地块 {info['limit']} 座）"
        if info.get("limit_nation") and self.diplo_built.get(name, 0) >= info["limit_nation"]:
            return False, f"{building} 自建全国限 {info['limit_nation']} 座（已自建过；再要只能从别国手里抢）"
        lv = eff[building]
        cost = info["cost"][lv] if info["kind"] == "castle" else info["cost"]
        label = f"城堡L{lv+1}" if info["kind"] == "castle" else building
        bp = TERRAIN_STATS[t["terrain"]]["build_penalty"]   # 地形施工惩罚（只上浮金价，木材不变）
        if bp:
            cost = cost * (100 + bp) // 100
        disc = ""
        if t["buildings"].get("工程院") and building != "工程院":
            _disc = building_effect("工程院", "build_discount")   # 只认**已落成**的（本格有它）
            cost = cost * (100 - _disc) // 100
            disc = f"，工程院-{_disc}%"
        if self.polity.get(name) == "huns":
            # 政体建造惩罚（数值在 balance.POLITY，乘算：不擅建设，靠抢）
            cost = cost * self.polity_rule(name, "build_cost_pct", 100) // 100
        wood = info["wood"]
        if self.res(name, "黄金") < cost:
            return False, f"黄金不足：{label} 需 {cost}，国库 {self.res(name,'黄金')}"
        if self.res(name, "木头") < wood:
            return False, f"木材不足：{label} 需 {wood}，储备 {self.res(name,'木头')}"
        self.add_res(name, "黄金", -cost)
        self.add_res(name, "木头", -wood)
        self.flow_out["木头"] += wood   # 世界流量：大兴土木推高木价
        led = self._ledger(name)        # 经济报表：投资=实付金 + 木×当时市价（含地形/工程院/匈奴修正后的真价）
        led["invest_gold"] += cost
        led["invest_wood_value"] += self._mval("木头", wood)
        self._spend(name)["build"] += cost + self._mval("木头", wood)   # 总消费：建造
        t["pending"][building] += 1  # 在建，回合末才落地
        t["built_this_turn"] = 1
        if building == "外交中心":
            self.diplo_built[name] = self.diplo_built.get(name, 0) + 1  # 自建名额记账（抢来的不占）
        tile_name = t["name"]
        note = f"，{t['terrain']}施工+{bp}%" if bp else ""
        return True, f"动工 {label}（@{tile_name}{note}{disc}，本回合在建、下回合生效），-{cost}金 -{wood}木"

    def recruit(self, name: str, x: int, y: int, n: int = 1, kind: str = "步") -> tuple[bool, str]:
        t = self.tiles.get((x, y))
        if kind not in UNIT_TYPES:
            return False, f"未知兵种：{kind}（可选：{'、'.join(UNIT_TYPES)}）"
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        if t is None or t["owner"] != name:
            return False, "只能在自己有兵营/军屯的地块征兵"
        if kind == "民":
            # 民兵走军屯征召：军屯不耗电，不受全国电网停摆影响。
            # 双限额：每座军屯每回合 1 支；且**全国民兵总数 ≤ 全国军屯总数**（军屯=民兵编制上限）
            if t["buildings"]["军屯"] <= 0:
                return False, "该地块没有军屯（民兵只能在军屯征召：50金+5粮/支，每军屯每回合1支）"
            _mcap = building_effect("军屯", "militia_cap")
            tile_cap = t["buildings"]["军屯"] * _mcap - t.get("militia_recruited_this_turn", 0)
            if tile_cap <= 0:
                return False, "本回合该地块民兵征召产能已用完（每军屯 1 支/回合）"
            quota = self.nation_building_count(name, "军屯")
            alive = sum(1 for a in self.troops if a["owner"] == name and unit_kind(a) == "民")
            cap = min(tile_cap, quota - alive)
            if cap <= 0:
                return False, (f"民兵总数已达军屯编制上限（{alive}/{quota} 座）："
                               f"军屯即民兵编制——想扩编先建军屯，阵亡后方可补员")
        else:
            if self.grid_short.get(name):
                return False, "全国电网不足，高级建筑（含兵营）停摆，无法征兵"
            if t["buildings"]["兵营"] <= 0:
                return False, "该地块没有兵营"
            cap = t["buildings"]["兵营"] * building_effect("兵营", "recruit_cap") - t["recruited_this_turn"]
            if cap <= 0:
                return False, "本回合征召产能已用完（每兵营 1 支/回合）"
        n = min(n, cap)
        cost = self.recruit_cost(name, kind)
        n = min(n, min(self.res(name, f) // amt for f, amt in cost.items()))
        if n <= 0:
            return False, "战略储备不足（每支耗 " + "、".join(f"{f}x{a}" for f, a in cost.items()) + "）"
        spent = 0.0
        for f, amt in cost.items():
            self.add_res(name, f, -amt * n)
            # ⚠ 国库的「黄金」**就是钱本身**，按面值 1:1 计；不能走 _mval ——
            # _mval 里「黄金」是**地块资源单位**的折算率（1 单位 = 10 金，见 MARKET["黄金"]，
            # 那是给黄金矿场产出用的）。曾经这里一律走 _mval，于是民兵（50 金 + 5 粮）
            # 被记成 ~510 分消费、虚高 10 倍——RL 的「爆民兵」正是吃这个 10 倍系数。
            # 对照同文件 `build`：它一直是 `cost + _mval("木头", wood)`，黄金走面值。
            # 所以这不是两种口径各有道理，是这里跟 build 不一致。
            spent += (amt * n) if f == "黄金" else self._mval(f, amt * n)
            if f in self.flow_out:
                self.flow_out[f] += amt * n   # 世界流量：征兵吃粮吃装备（黄金是货币，不计）
        self._spend(name)["recruit"] += spent
        for i in range(n):
            gid, seq = self._new_army(name)
            self.armies.append({"id": seq, "gid": gid, "name": army_name(name, seq, kind),
                                "type": kind, "hp": unit_max_hp({"type": kind}), "x": x, "y": y,
                                "owner": name, "moved_turn": -1, "engaged": False})
        if kind == "民":
            t["militia_recruited_this_turn"] = t.get("militia_recruited_this_turn", 0) + n
        else:
            t["recruited_this_turn"] += n
        return True, f"征召 {n} 支{UNIT_TYPES[kind]['label']} @{t['name']}"

    # ------------------------------------------------------------- 军队
    def _assign_code(self, name: str) -> None:
        """分配国家码（军队全局唯一 gid 的前缀；野人固定 0）。"""
        if name == "野人":
            self.nation_code.setdefault("野人", 0)
        elif name not in self.nation_code:
            self.nation_code[name] = self._next_code
            self._next_code += 1

    def _new_army(self, owner: str) -> tuple[int, int]:
        """新军队编号 → (gid, seq)。seq=本国序列，从 1 递增、阵亡不回收（AI 所见与引用即 seq）；
        gid=国家码×1e8+seq，全局唯一但**对 AI 不可见**（仅存档/内部用；野人码 0 → gid==seq）。"""
        seq = self.next_army_seq.get(owner, 0) + 1
        self.next_army_seq[owner] = seq
        return self.nation_code.get(owner, 0) * ARMY_GID_BASE + seq, seq

    def _next_engage_seq(self) -> int:
        """递增的「入场序号」：军队每次 atk 参战领一个新号，用于野地索取顺序。"""
        self._engage_seq += 1
        return self._engage_seq

    def _claim_winner(self, alive: dict[str, list[dict]], *, attackers: set[str] | None = None) -> str | None:
        """该格（野地或敌国）的归属候选：进攻方中「无活敌」者，按入场序号取最早 atk 的
        ——**索取者优先**；索取者已阵亡则顺位给最早入场的同盟者。无候选返回 None。
        attackers=本场战斗的进攻方集合（战斗结算用；此时 engaged 标记可能已被清扫）。
        attackers=None 时不再筛进攻方，只看「在场且与格上他人无交战」——供「守军尽撤」用。"""
        claim = []
        for F, fs in alive.items():
            if F == "野人":
                continue
            if attackers is not None and F not in attackers:
                continue
            # ★ 已下令撤退的军队**不索取地块**：人都要走了，把地判给一支正在脱离的军队
            #   （甚至全 faction 皆撤 → 格归一个驻军为零的国家）不合理；留下的人才配拿。
            stay = [a for a in fs if not a.get("retreat_to")]
            if not stay:
                continue
            if any(G in alive and G != F and self.war_between(F, G) for G in alive):
                continue
            claim.append(F)
        if not claim:
            return None
        return min(claim, key=lambda F: min((a.get("engage_seq", 10 ** 9) for a in alive[F]
                                             if not a.get("retreat_to")),
                                            default=10 ** 9))

    @property
    def troops(self) -> list[dict]:
        """**非野人**的军队（= 各国军队）——一张现成的名单，省掉"扫全表只为滤掉野人"。

        ★ 为什么要有它（2026-09-16 实测）：野人是地图的静态属性（每个无主格一支），
          一局里通常 1500+ 支，而**绝大多数判定里它们连候选都不是** —— `_mv_wall` 的
          "野地驻军"那条、`nation_armies`、v11 的寻路/编组…… 谓词里都写着 `!= "野人"`，
          却仍要逐条走过那 1500 支。40x40、60 回合量到：全库扫 `armies` 的元素数约 93 万，
          其中 98% 是这种**必然落空**的白扫（这笔开销全部摊在每回合/每次寻路上）。

        ★ **口径**：`[a for a in self.armies if a["owner"] != "野人"]` —— 逐条相同。
          **只许用在谓词本来就把野人排除在外的地方**：`_defs_at` 要拿野人当守军、
          `targeting` 要枚举野人驻军，那些地方不许用。
        ★ **失效判据** = `(id(self.armies), len(self.armies), sum(next_army_seq.values()))`：
          军队的 `owner` **一经创建不再改写**（引擎里没有一处写军队的 `["owner"]` ——
          `["owner"] =` 那两处改的是**地块**），所以名单只在**新建 / 阵亡 / 整表重建**时变，
          而这三件事必然动到上面三项里的至少一项：新建 ⇒ 序列号 +1；阵亡 ⇒ 长度变；
          `load()` 与过滤式重建 ⇒ 列表对象换人。
        ★ 缓存的是**引用**：调用方不许就地改这张表（要改先 `list(...)` 拷一份）。
        ★ 它是**派生量、不进存档** —— 别按"新增字段三步"往 SAVE_KEYS 里写。
        """
        self._army_split()
        return self._troops

    @property
    def guardians(self) -> dict:
        """`{(x, y): 野人军队}` —— 按格查野人，省掉"为了找一支野人扫 1500 支"。

        ★ 为什么敢缓存：**野人从不移动**（它们是地图的静态属性，`_spawn_guardian` 出生后
          只会被 `_drop_guardians` 撤走或在战斗里阵亡），所以这张表只在**野人增减**时失效 ——
          判据与 `troops` 同一把钥匙（id/长度/序列号），而"新建/阵亡/整表重建"三件事
          必然动到它。**国家军队不在这张表里**（它们会移动，缓存就会陈旧）。
        ★ 一个格最多一支野人（`guard_once`：出生过就不再补），所以是"格 → 一支"。
        ★ 用途：`_defs_at`（v11 的战斗评估每回合要问 ~10 次，每次原本都要全表扫一遍）。
        """
        self._army_split()
        return self._guards

    def _army_split(self) -> None:
        """按需重建 `troops` / `guardians`（**一次扫描**建两张表）。

        判据：`(id(self.armies), len(self.armies), sum(next_army_seq.values()))` ——
        推导见 `troops` 的文档（军队 `owner` 不可改写 ⇒ 名单只在建/亡/整表重建时变）。
        惰性：只有真被问到的那一张会付重建成本（两张一起扫，比各扫一遍便宜）。
        """
        arm = self.armies
        key = (id(arm), len(arm), sum(self.next_army_seq.values()))
        if key == self._army_key:
            return
        troops: list[dict] = []
        guards: dict = {}
        for a in arm:
            if a["owner"] == "野人":
                guards[(a["x"], a["y"])] = a
            else:
                troops.append(a)
        self._troops, self._guards, self._army_key = troops, guards, key

    def _army(self, name: str, aid: int) -> dict | None:
        src = self.armies if name == "野人" else self.troops   # 野人不是"国家军队"，见 `troops`
        return next((a for a in src if a["owner"] == name and a["id"] == aid), None)

    def nation_armies(self, name: str) -> list[dict]:
        src = self.armies if name == "野人" else self.troops   # 同上：传"野人"走全表
        return [a for a in src if a["owner"] == name]

    def _defs_at(self, name: str, x: int, y: int) -> list[dict]:
        """这一格上、对 `name` 而言算**敌人**的军队（引擎口径的唯一出处）。

        ★ 2026-09-16：不再全表扫——**野人从不移动**，所以按格查表（`guardians`）；
          国家军队那张表小（几十支），直接扫 `troops`。逐条等价，唯一要守的是**顺序**：
          原写法按 `self.armies` 的列表序产出，而野人恒为该列表的**前缀**
          （`_ensure_guardians` 在建局时一次铺完，此后只会被撤走/阵亡，不会插到国家军队之后），
          所以"先野人、后国家军队（各按列表序）"与原序**逐位相同** ——
          `tests/test_troops.py` 用一份朴素实现逐格对照钉住它。
        """
        owner = self.owned_by(x, y)
        out = []
        if owner is None:                       # 野人只守无主格（已物化的地块上没有野人）
            g = self.guardians.get((x, y))
            if g is not None:
                out.append(g)
        for a in self.troops:
            if (a["x"], a["y"]) != (x, y) or a["owner"] == name:
                continue
            if self.war_between(name, a["owner"]):
                out.append(a)
        return out

    def _blind_cost(self, name: str, armies: list[dict], x: int, y: int) -> None:
        """向**视野外**的目标格下令、撞上目标格上的规则墙（中立领土/敌境空格/
        敌军驻守/第三方打野）→ 参令军队本回合移动额度**照样烧掉**。
        报错本身如实返回——那就是斥候带回的情报，侦察付了钱就要拿得到货；
        要付的是钱：对看不见的地方滥发命令，每一发都值一支军队一回合的腿。
        视野内撞墙不额外罚（你看得见，试错是正常决策，报错免费）。"""
        if self.visible_to(name, x, y):
            return
        for a in armies:
            a["moved_turn"] = self.turn

    def move(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        if a.get("engaged"):
            return False, f"{a['name']} 交战中，先 retreat 撤出"
        # 交战地中的军队（含防守方）不能直接 mv 撤离——撤出走 retreat（回合末随战斗结算后脱离）
        if any(d["owner"] != a["owner"] and d["owner"] != "野人" and d.get("engaged")
               and (d["x"], d["y"]) == (a["x"], a["y"]) and self.war_between(a["owner"], d["owner"])
               for d in self.troops):        # 谓词排除野人 ⇒ 只扫国家军队
            return False, f"{a['name']} 所在格正在交战，不能直接 mv 撤离；撤出请用 retreat（回合末随战斗结算后脱离）"
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        if a.get("moved_turn") == self.turn:
            return False, "本回合已移动过"
        # ★ 目标格本身的墙先判（与旧口径一致：视野外撞墙 → 报错照给、**额度照烧**，侦察要付钱）。
        #   然后才是"走不到"（地形代价/中途被挡）——那一条**不烧额度**（旧版的射程不够也不罚）。
        why = self._mv_wall(name, x, y)
        if why:
            self._blind_cost(name, [a], x, y)
            # 城堡公开（2026-09-19）：这里之所以只在**调用点**补、不写进 `_mv_wall`，
            # 是因为 `_mv_wall` 在 `_reachable` 的逐格 BFS 热路径上——往里加
            # `visible_to`（O(全表)）会把寻路拖垮。
            _cl = self.visible_buildings(name, x, y).get("城堡", 0)
            return False, why + (f"（该格城L{_cl}）" if _cl else "")
        if (x, y) not in self._reachable(name, a):
            return False, self._unreachable_msg(a, x, y, for_attack=False)
        # mv 只挪位置，不占地——占地走 atk
        a["x"], a["y"] = x, y
        a["moved_turn"] = self.turn
        return True, f"军队{a['id']} 移防 ({x+1},{y+1})"

    def attack(self, name: str, aids: list[int], x: int, y: int) -> tuple[bool, str]:
        """atk = 一次『进军占地』：派军队进目标格——
        有守军(野人/敌国军)就交战（打赢后按索取顺序占地）；格上**无任何军队**才直接进驻占领（空城/无主空地），
        他国领土上有非敌军队（中立/第三方）则不能进驻；**野地例外**——和平驻守的第三方不参战、不占地，
        清场后直接进驻（驻守者回合末被遣返）。野地上有「非敌非盟」的一方正在打野时不能插足。
        多势力同时开战各打各的敌人。mv 只挪位置不占地；占地一律走 atk，没有特例。"""
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        targets = [a for a in self.armies if a["owner"] == name and a["id"] in aids]
        if not targets:
            return False, f"未找到我方军队 {aids}"
        # ★ 目标格上的"墙"与 mv 同规：视野外撞墙 → 报错如实，但突入军队本回合额度照烧。
        owner = self.owned_by(x, y)
        why = None
        if owner is not None and (owner == name or self.allied_between(name, owner)):
            why = "目标是自己或盟国的领土，不能进攻"
        elif owner is not None and not self.war_between(name, owner):
            why = "中立不可攻击他国领土"
        else:
            # 野地上别人正在打野（交战方与你既非敌也非盟）→ 不能插足抢地：
            # 这是「不抢别人的战斗」——中立打野时你不能 atk；盟友/敌人在打野则可以参战
            # （都按索取顺序占地）。想旁观仍可 mv 过去（不参战）。
            busy = [a for a in self.troops
                    if (a["x"], a["y"]) == (x, y) and a.get("engaged") and a["owner"] not in ("野人", name)]
            if owner is None and busy and not any(self.war_between(name, a["owner"]) or self.allied_between(name, a["owner"])
                                                  for a in busy):
                other = busy[0]["owner"]
                why = (f"({x+1},{y+1}) 有 {other}军正在打野（与你非交战也非盟友），"
                       f"不能插足抢地——等这场战斗打完再 atk（想插手就先向 {other} 宣战）；"
                       f"旁观可以 mv 过去（不参战）")
        if why:
            self._blind_cost(name, targets, x, y)   # 视野外撞墙：报错照给，突入军队的移动额度照烧
            return False, why
        defs = self._defs_at(name, x, y)
        for a in targets:
            if a.get("engaged") and (a["x"], a["y"]) != (x, y):
                return False, (f"{a['name']} 正在交战中，不能离开战场改攻他处；"
                               f"想脱战先 retreat 军队id 目标格（会挨守军一击）")
            # ★ atk 也**逐格**走：隔着山/隔着别人的地界就冲不进去（旧版是 5×5 直取）
            if (a["x"], a["y"]) != (x, y) and (x, y) not in self._reachable(name, a, for_attack=True):
                return False, self._unreachable_msg(a, x, y, for_attack=True)
            if (a["x"], a["y"]) != (x, y) and a.get("moved_turn") == self.turn:
                return False, f"{a['name']} 本回合已移动过"
        for a in targets:
            if (a["x"], a["y"]) != (x, y):
                a["x"], a["y"] = x, y
                a["moved_turn"] = self.turn
        ids = "、".join(f"{a['name']}" for a in targets)
        if defs:
            for a in targets:
                a["engaged"] = True
                a["engage_seq"] = self._next_engage_seq()  # 野地索取顺序：谁先 atk 谁号小
            who = "野人" if defs[0]["owner"] == "野人" else f"{defs[0]['owner']}军"
            _cl = self.visible_buildings(name, x, y).get("城堡", 0)
            _fort = f"（该格城L{_cl}，守方吃它的减伤）" if _cl else ""
            return True, (f"{ids} 冲入 ({x+1},{y+1}) 与{who}交战{_fort}，"
                          "之后每回合结算一轮；可 retreat 撤出")
        # 格上还有其他军队但不是你的敌人（中立/盟友，和平驻守）：
        #  · 他国领土 → 不能进驻（旧规：中立地不进门）；
        #  · 野地（无主）→ 和平驻守不产生任何权利、也不构成障碍：atk 只打野人/敌人，
        #    清场后直接进驻占地，驻守的第三方被挤走（回合末自动遣返）。
        squatters = sorted({a["owner"] for a in self.armies
                            if a["owner"] != name and a["hp"] > 0 and (a["x"], a["y"]) == (x, y)})
        if squatters and owner is not None:
            return False, (f"({x+1},{y+1}) 有他国军队但并非你的敌人（中立/第三方），"
                           f"不能直接进驻；只能攻击敌人或占领无任何守军的空地")
        # 格上无守军/敌人 → atk 进驻即占
        _ok, cmsg = self._conquer(x, y, name, "进驻占领", log_it=False)
        nm2 = self.tiles[(x, y)]["name"]
        self.log(f"{name} {ids} 进驻 ({x+1},{y+1})，{cmsg}", phase="领土", nation=name, x=x, y=y)
        note = (f"（{'、'.join(squatters)}军未参战，回合末自动遣返）" if squatters else "")
        return True, f"{ids} 进驻 ({x+1},{y+1})，敌人为 0，{cmsg}{note}"

    def _retreat_legal(self, name: str, x: int, y: int) -> bool:
        """撤退合法点：无人荒地 / 己方领土 / 同盟领土（中立与敌国格都不行）。
        ★ 无人荒地上有敌（交战中）军队驻守 → 也不行——与 mv 的"野地有敌军→只能 atk"同口径，
        否则能以"撤退"名义免费将军队空投进敌脚（同格不交战，下回合被围歼）。
        己方/同盟格上有混战敌军仍放行（那是增援，mv 同样允许）。"""
        o = self.owned_by(x, y)
        if o == name:
            return True
        if o is not None:
            return self.allied_between(name, o)
        return not any(a["owner"] != name and a["owner"] != "野人"
                       and (a["x"], a["y"]) == (x, y) and self.war_between(name, a["owner"])
                       for a in self.troops)

    def retreat(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        """撤出：与 mv/atk 同一个『每回合一次移动』额度。
        交战中的军队（含防守方守军）都能用；撤退固定只能退相邻 1 格（3×3，所有人，不按兵种速度）；
        目标限 己方/同盟/无人荒地（中立与敌国格都不行）；四周没有合法撤退点则不能撤退。
        撤退不立刻结算：军队留在战场参与本回合末的战斗结算（伤害全场分摊；防御方撤退减伤
        RETREAT_DEF_COVER%），结算后自动脱离到目标格——避免『每撤一支各吃一次全额』的灾难。"""
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        in_battle = bool(a.get("engaged")) or any(
            d["owner"] != a["owner"] and d["owner"] != "野人" and d.get("engaged")
            and (d["x"], d["y"]) == (a["x"], a["y"]) and self.war_between(a["owner"], d["owner"])
            for d in self.troops)
        if not in_battle:
            return False, f"{a['name']} 未在交战中，无需撤退"
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        if (a["x"], a["y"]) == (x, y):
            return False, "撤出需选一个不同的格"
        if max(abs(a["x"] - x), abs(a["y"] - y)) > RETREAT_RANGE:
            return False, "撤退固定只能退相邻 1 格（3×3），超出范围"
        # 没有合法撤退点则不能撤退（目标限 己方/同盟/无人荒地）
        if not any(self._retreat_legal(name, nx, ny)
                   for nx, ny in self.neighbors(a["x"], a["y"])):
            return False, "四周没有合法撤退点（己方/同盟/无人荒地），无法撤退"
        if a.get("moved_turn") == self.turn:
            return False, f"{a['name']} 本回合已移动/进攻过，移动额度用尽，撤不出（下回合再撤）"
        if not self._retreat_legal(name, x, y):
            return False, "不能撤到敌国或中立国的格子；只能撤向 己方/同盟/无人荒地"
        # 撤退不立刻结算：标记 retreat_to 留在原地，本回合结束时随战斗结算走正常战斗机制
        # （敌方伤害全场分摊，撤退者在场照常吃自己那份；防御方撤退减伤 RETREAT_DEF_COVER%），
        # 结算后自动脱离到目标格（见 resolve_turn 撤退落地）。
        # 防御方减伤：我方在该格「未参战」（不是进攻方）、或本身就是格主 → 守方，撤退减伤
        # RETREAT_DEF_COVER%（谁挨打谁是守方，野地和平驻军同理）；主动进攻方撤退是全额。
        holder = self.owned_by(a["x"], a["y"])
        attacking = any(m["owner"] == name and m.get("engaged") and (m["x"], m["y"]) == (a["x"], a["y"])
                        for m in self.troops)
        cover = RETREAT_DEF_COVER if (not attacking or holder == name) else 100
        a["retreat_to"] = [x, y]
        a["retreat_cover"] = cover
        a["moved_turn"] = self.turn
        note = f"（防御方撤退，结算减伤 {100 - cover}%）" if cover < 100 else ""
        return True, (f"{a['name']} 准备撤到 ({x+1},{y+1}){note}：本回合结束时随战斗结算"
                      f"（全场分摊）后自动脱离；结算期间仍在战场")

    # ------------------------------------------------------------- 战斗
    def _die(self):
        d = self.rng.randint(min(COMBAT_DIE_MOD), max(COMBAT_DIE_MOD))
        return d, COMBAT_DIE_MOD[d]

    @staticmethod
    def _combat_power(atk_total: int, def_pct: int) -> int:
        """atk_total = 该方各军兵种攻击之和（步/骑 50、民兵 20，见 unit_atk）。"""
        return max(1, atk_total * (100 - def_pct) // 100)

    @staticmethod
    def _round_damage(power: int, mod: int) -> int:
        return max(1, power * (100 + mod) // 100)

    @staticmethod
    def _spread(dmg: int, units: list[dict]):
        per, rem = divmod(dmg, len(units))
        for i, u in enumerate(units):
            u["hp"] -= per + (1 if i < rem else 0)

    def _defense_pct(self, x: int, y: int, def_owner: str | None) -> int:
        """地块总防御% = 地形与城堡**相乘**叠加。"""
        t = self.tiles.get((x, y))
        terrain = t["terrain"] if t else self.tile_terrain(x, y)
        castle = t["buildings"]["城堡"] if (t and t["owner"] == def_owner) else 0
        td = TERRAIN_STATS[terrain]["defense"]
        cd = castle * building_effect("城堡", "defense_per_level")
        return 100 - ((100 - td) * (100 - cd)) // 100

    @staticmethod
    def _modtxt(mods: dict[str, int]) -> str:
        """每方骰修正的简短文本：如「甲+5% 乙-15%」。"""
        return " ".join(f"{F}{m:+d}%" for F, m in sorted(mods.items())) or "—"

    def _resolve_battles(self) -> list[str]:
        """每格每回合的多势力交战结算：**进攻方不纯联合**——每方独立只打自己的敌人（互相宣战才互打），
        每方掷自己的骰；野人只守无主格、只打"进攻方"（不打扰和平停驻者）；地形/城堡减伤只给格主/野人；
        同格多方同时出手再统一结算阵亡；占地 = 唯一幸存且野人已清的势力。"""
        lines: list[tuple[int, int, str]] = []  # (x, y, 战报行)——带坐标才进得了事件视野
        engaged = [a for a in self.armies if a.get("engaged") and a["owner"] != "野人"]
        for (x, y) in sorted({(a["x"], a["y"]) for a in engaged}):
            # 格子标记带上**城堡等级**（2026-09-19：城堡公开——凡是报"哪格在打"的地方都带上它，
            # 否则守方靠城减伤，攻方却看不出为什么打不动）。tag 被本格所有战报行复用，
            # 所以这一处改动就让每条战报都带上。
            _t_castle = self.tiles.get((x, y))
            _cl = _t_castle["buildings"].get("城堡", 0) if _t_castle else 0
            tag = f"({x+1},{y+1}){self.ter_char(x, y)}" + (f" 城L{_cl}" if _cl else "")
            owner = self.owned_by(x, y)
            # 格上活军按势力分组（进攻方 + 守军 + 停驻者 + 无主格野人）
            forces: dict[str, list[dict]] = {}
            for a in self.armies:
                if (a["x"], a["y"]) != (x, y) or a["hp"] <= 0:
                    continue
                if a["owner"] == "野人" and owner is not None:
                    continue  # 野人只守无主格
                forces.setdefault(a["owner"], []).append(a)
            attacker = {F for F in forces if F != "野人"
                        and any(a.get("engaged") for a in forces[F])}
            if not forces or not attacker:
                continue
            # 敌人关系：非野人势力 = 格上其他交战方 + (自己是进攻方时)无主格野人；野人 = 只打进攻方
            def _enemies(F: str) -> list[str]:
                if F == "野人":
                    return [G for G in attacker if G != "野人"]
                en = [G for G in forces if G != F and G != "野人" and self.war_between(F, G)]
                if F in attacker and "野人" in forces:
                    en.append("野人")
                return en
            # 地形/城堡减伤给「守方」：未参战的驻军（含野人）挨打时吃本地地形，格主永远算守方；
            # 交战中的进攻方不吃加成（谁挨打谁是守方）。
            soak = {F: (self._defense_pct(x, y, F) if (F not in attacker or F == owner) else 0)
                    for F in forces}
            # 每方掷自己的骰，同时出手（先算全部伤害再统一施加，允许同归于尽）
            dmg: dict[str, int] = {F: 0 for F in forces}
            mods: dict[str, int] = {}
            for F in forces:
                en = _enemies(F)
                if not en:
                    continue
                _d, mod = self._die()
                mods[F] = mod
                # 撤退中的军队输出 -80%（撤离途中无心恋战），按军计入攻击总和
                atk = sum(unit_atk(a) if not a.get("retreat_to")
                          else max(1, unit_atk(a) * (100 - RETREAT_ATK_PENALTY) // 100)
                          for a in forces[F])
                power = self._round_damage(self._combat_power(atk, 0), mod)
                share = power / len(en)  # 均分给各敌人（腹背受敌则兵力分散）
                for G in en:
                    dmg[G] += max(1, round(share * (100 - soak[G]) / 100))
            for F in forces:
                if not dmg[F]:
                    continue
                units = forces[F]
                ws = [a.get("retreat_cover", 100) for a in units]
                if all(w == 100 for w in ws):
                    self._spread(dmg[F], units)
                else:
                    # 有人带撤退减伤：先按常规全场分摊算出每人该吃多少，再按 cover% 打折——
                    # 防御方撤退 cover=50 就只吃一半（单支守军撤退也真的减半），
                    # 少掉的那部分不再转嫁给同格友军。
                    per, rem = divmod(dmg[F], len(units))
                    for i, (a, w) in enumerate(zip(units, ws)):
                        share = per + (1 if i < rem else 0)
                        a["hp"] -= share * w // 100
            for a in list(self.armies):
                if (a["x"], a["y"]) == (x, y) and a["hp"] <= 0:
                    self.armies.remove(a)
            alive = {}
            for F, fs in forces.items():
                fs2 = [a for a in fs if a["hp"] > 0]
                if fs2:
                    alive[F] = fs2
            survivors = [F for F in alive if F != "野人"]
            # 清 engaged：该方在格上已无活敌人 → 脱离战斗
            for F in alive:
                if F == "野人":
                    continue
                if not _enemies(F) or not any(G in alive for G in _enemies(F)):
                    for a in alive[F]:
                        a["engaged"] = False
            # 占地 / 战报：归属候选 = 进攻方中「无活敌」者，按索取顺序取最早 atk 的（索取者优先；
            # 索取者阵亡则顺位最早入场的同盟者）。野地与敌国同规。
            win = self._claim_winner(alive, attackers=attacker)
            desc_alive = "；".join(f"{F} 余{len(alive[F])}支[{alive[F][0]['hp']}hp]" for F in survivors)
            if owner is None:
                # ---- 野地（无主）----
                # 野人清空 + 有归属候选 → 占地；和平驻守的第三方不占地也不参战（回合末被遣返）。
                if "野人" in alive:
                    if win is not None:
                        fs = alive[win]
                        lines.append((x, y, f"⚔ {win} 仍与野人交战 @{tag}"
                                     f"（余{len(fs)}支[{fs[0]['hp']}hp]，守军未清，占不得）"))
                    else:
                        lines.append((x, y, f"⚔ 攻方全灭 @{tag}，野人仍在（无主地守军未清）"))
                elif win is not None:
                    fs = alive[win]
                    info = f"{win} 余{len(fs)}支[{fs[0]['hp']}hp]"
                    _ok, msg = self._conquer(x, y, win, "进驻")
                    others = [F for F in survivors if F != win and F in attacker]
                    joint = (f"（共占，按索取顺序归 {win}；同场 {'、'.join(others)}）" if others else "")
                    sq = sorted(F for F in survivors if F not in attacker)
                    squeeze = (f"（{'、'.join(sq)}军未参战，回合末自动遣返）" if sq else "")
                    lines.append((x, y, f"⚔ 全歼守军 @{tag}，{msg}（{info}）{joint}{squeeze}"))
                elif any(F in attacker for F in survivors):
                    lines.append((x, y, f"⚔ 多方混战 @{tag}（骰 {self._modtxt(mods)}）：{desc_alive}（战局未定）"))
                elif survivors:
                    lines.append((x, y, f"⚔ 野人已清 @{tag}，但场上只剩未参战的驻军，野地维持无主"))
                else:
                    lines.append((x, y, f"⚔ 同归于尽 @{tag}——此地成无主空地，可直接占领"))
            else:
                # ---- 他国领土：同一套索取顺序（多国共同围攻时，第一个 atk 者优先接管）----
                if win is not None and win != owner:
                    fs = alive[win]
                    info = f"{win} 余{len(fs)}支[{fs[0]['hp']}hp]"
                    ok, msg = self._conquer(x, y, win, "攻陷")
                    lines.append((x, y, f"⚔ 全歼守军 @{tag}，{msg}（{info}）"))
                elif owner in alive:
                    fs = alive[owner]
                    info = f"{owner} 余{len(fs)}支[{fs[0]['hp']}hp]"
                    if any(F in attacker for F in survivors):
                        lines.append((x, y, f"⚔ 守军坚守 @{tag}：{info}，攻方未退"
                                     f"（骰 {self._modtxt(mods)}）"))
                    else:
                        dead = sum(len(v) for F, v in forces.items() if F != owner)
                        lines.append((x, y, f"⚔ 守军坚守 @{tag}：攻方{dead}支全灭，{info}"
                                     f"（骰 {self._modtxt(mods)}）"))
                elif survivors:
                    lines.append((x, y, f"⚔ 多方混战 @{tag}（骰 {self._modtxt(mods)}）：{desc_alive}（战局未定）"))
                else:
                    lines.append((x, y, f"⚔ 同归于尽 @{tag}——城仍在敌手"))
        return lines

    # ------------------------------------------------------------- 占领/灭国
    def _conquer(self, x: int, y: int, by: str, how: str, *, log_it: bool = True) -> tuple[bool, str]:
        self._check(x, y)
        old = self.owned_by(x, y)
        self.guard_once.discard((x, y))
        self._drop_guardians(x, y)
        if old is None:
            t = self._new_tile(x, y, by)
            self.tiles[(x, y)] = t
            msg = f"{by} {how}拓疆「{t['name']}」({x+1},{y+1}){t['terrain']}"
        elif old != by:
            t = self.tiles[(x, y)]
            t["owner"] = by
            msg = f"{by} {how}「{t['name']}」({x+1},{y+1})"
            # 城堡**公开**（2026-09-19）：占领后建筑原样保留，所以要报出缴获了什么要塞。
            # ★ 这条日志是**视野广播**的（同格谁看得见谁就收到），所以只能带公开信息——
            #   城堡可以；兵营/工厂/农田那些若报出去，等于向第三者泄露被占国的内政底细。
            _cl = t["buildings"].get("城堡", 0)
            if _cl:
                msg += f"（城L{_cl}）"
            extra = self._return_core(x, y, by)  # 同战线盟友核心领土 → 自动归还
            if extra:
                t = self.tiles[(x, y)]
                msg += extra
        else:
            return False, "已是自己领土"
        if log_it:
            self.log(msg, phase="领土", nation=by, x=x, y=y)
        if old and old != by:
            self._eliminate_if_dead(old)
        return True, msg

    def _eliminate_if_dead(self, name: str) -> bool:
        if name not in self.nations:
            return False
        if any(t["owner"] == name for t in self.tiles.values()):
            return False
        self.log(f"☠ {name} 亡国：领土尽失，国祚断绝！", phase="灭国", nation=name)
        del self.nations[name]
        # 军队解散
        self.armies = [a for a in self.armies if a["owner"] != name]
        # 关系与外交清场
        new_wars = []
        ended_wars = []
        for w in self.wars:
            if name in (w["atk"], w["def"]):
                ended_wars.append(w)
                continue  # 主导者亡 → 整场战争结束
            w["followers"] = [c for c in w["followers"] if c != name]  # 跟随方亡 → 仅剔出
            w["atk_followers"] = [c for c in w.get("atk_followers", []) if c != name]
            new_wars.append(w)
        self.wars = new_wars
        # 因亡国而终结的战争：余方实际持有重算为核心领土
        for w in ended_wars:
            self._snapshot_cores([w["atk"], w["def"]] + list(w["followers"])
                                 + list(w.get("atk_followers", [])))
        # 联盟清场：盟主亡 → 顺位继承（最早加入的剩余成员）；成员全亡 → 解散
        for bloc in list(self.blocs):
            if name in bloc["members"]:
                was_chief = self.bloc_chief(bloc) == name
                bloc["members"].remove(name)
                if bloc["members"]:
                    if was_chief:
                        bloc["chief"] = bloc["members"][0]
                        self.log(f"👑 {name} 亡国，盟主之位由 {bloc['chief']} 继承（联盟「{bloc['name']}」）",
                                 phase="外交")
                else:
                    self.blocs.remove(bloc)
                    self.log(f"💔 联盟「{bloc['name']}」因成员凋零而解散", phase="外交")
        self.votes = [v for v in self.votes if self.bloc_by_name(v["bloc"]) is not None]
        self.truce = {p: u for p, u in self.truce.items() if name not in p}
        # 一方灭亡 → 强制全天下休战 10 回合（防连环征服滚雪球；已有更长休战则保留）
        alive = self.alive()
        for i in range(len(alive)):
            for j in range(i + 1, len(alive)):
                p = _pair(alive[i], alive[j])
                self.truce[p] = max(self.truce.get(p, 0), self.turn + FALL_TRUCE_TURNS)
        gone = self.drop_pacts_of(ent_nation(name))
        if gone:
            self.log(f"💔 {name} 亡国，其条约随之作废（{'、'.join(gone)}）", phase="外交")
        self.mailbox.pop(name, None)
        self.summaries.pop(name, None)
        self.summary_blocks.pop(name, None)
        self.long_memory.pop(name, None)
        self.turn_memory.pop(name, None)
        self.maps.pop(name, None)
        self.gift_pending = [g for g in self.gift_pending if g["from"] != name and g["to"] != name]
        self.map_pending = [m for m in self.map_pending if m["from"] != name and m["to"] != name]
        self.spy_pending = [s for s in self.spy_pending if s["from"] != name and s["to"] != name]
        self.econ_intel.pop(name, None)
        self.ledger.pop(name, None)          # 账本是本期暂态；已出的 econ_reports 留作历史
        self.plans.pop(name, None)
        self.polity.pop(name, None)
        self.extra_prompt.pop(name, None)
        self.mail_pending = [m for m in self.mail_pending if m["to"] != name and m["from"] != name]
        self.peace_offers = [p for p in self.peace_offers if p["a"] != name and p["b"] != name]
        self.proposals = [p for p in self.proposals
                          if p.get("a") != name and p.get("b") != name
                          and p.get("A") != ent_nation(name) and p.get("B") != ent_nation(name)]
        return True

    # ------------------------------------------------------------- 经济报表
    def _ledger(self, n: str) -> dict:
        """取（或新建）该国本期经济账本。投资/贸易/资产从这里按整期出数；
        GDP/军费不读整期值——由 _close_report_period 取**结报回合的增量**
        （run-rate，见 _ledger_runrate_mark），账本怎么攒、首期缺几回合都不影响它们。"""
        led = self.ledger.get(n)
        if led is None:
            led = self.ledger[n] = {**{k: 0.0 for k in LEDGER_FIELDS},
                                    "_since": max(1, self.turn)}
        return led

    def _spend(self, n: str) -> dict:
        """取（或新建）该国总消费账（全期累计，不随报表清零）。"""
        d = self.spend.get(n)
        if d is None:
            d = self.spend[n] = {k: 0.0 for k in SPEND_FIELDS}
        return d

    def spend_total(self, n: str) -> float:
        """该国累计总消费（建造+征兵+军费）——RL 的回报就是这个数的终局值。"""
        return float(sum(self._spend(n).values()))

    def _mval(self, good: str, amt: int) -> float:
        """按当前市价把 amt 单位 good 折成金（黄金按 MARKET['黄金']）。"""
        if amt <= 0:
            return 0.0
        if good == "黄金":
            return amt * MARKET["黄金"]
        return amt * self.prices.get(good, float(MARKET.get(good, 0)))

    def nation_assets(self, n: str) -> float:
        """该国全部建筑的重置成本（造价金 + 木×现价；城堡按已升到的级数累计投入）。
        与 settlement.py 同口径，只是木头用现价——夺来的地同样计入（报表里会注明）。"""
        wp = self.prices.get("木头", float(MARKET["木头"]))
        total = 0.0
        for t in self.tiles.values():
            if t.get("owner") != n:
                continue
            for bname, cnt in t["buildings"].items():
                if not cnt:
                    continue
                info = BUILDINGS[bname]
                if info["kind"] == "castle":
                    total += sum(info["cost"][:cnt])          # 逐级累计，不是 cnt×单价
                else:
                    total += cnt * (info["cost"] + info["wood"] * wp)
        return total

    @staticmethod
    def _growth(cur: float, base: float | None) -> float | None:
        """环比增长率；无上期或上期非正 → None（报表里显示「—」）。"""
        if base is None or base <= 0:
            return None
        return (cur - base) / base

    # 报表口径（2026-09-12 用户定）：GDP / 军费**不平均、不按整期累计**——
    # 只取"结报这一回合"的实际产出增量（run-rate）。投资/贸易/资产仍是整期值。
    _RUNRATE_FIELDS = ("prod_value", "mid_value", "fuel_value", "gold_in", "supply_eaten")

    def _ledger_runrate_mark(self) -> dict:
        """结算开头为每国抓一份 GDP/军费相关字段的快照，供 _close_report_period 求本回合增量。"""
        return {n: {k: self._ledger(n).get(k, 0.0) for k in self._RUNRATE_FIELDS}
                for n in self.alive()}

    def _close_report_period(self, runrate_from: dict) -> None:
        """把本期账本结成一期快照并清零账本。只在回合结算末尾调用——AI 无手动入口。
        runrate_from：本回合结算开头（生产/军耗发生之前）的账本快照；
        GDP 与军费只反映**本回合**（报表所结算的那一回合）的实际增量。"""
        for n in self.alive():
            led = self._ledger(n)
            start = int(led.get("_since", self.turn - REPORT_EVERY + 1))
            days = max(1, min(REPORT_EVERY, self.turn - start + 1))   # 覆盖回合数（仅供展示）
            snap0 = runrate_from.get(n, {k: 0.0 for k in self._RUNRATE_FIELDS})
            d = lambda k: led.get(k, 0.0) - snap0.get(k, 0.0)          # 本回合增量
            # GDP = 本回合生产增加值（市价，不含军费）——不除天数、不攒期累计
            gdp = d("prod_value") - d("mid_value") - d("fuel_value") + d("gold_in")
            # 军费 = 本回合军队实际吃掉的补给 × 现价（不看来源：自产/外购一视同仁）
            military = d("supply_eaten") * self.prices.get("补给", float(MARKET["补给"]))
            invest = led["invest_gold"] + led["invest_wood_value"]      # 整期累计（不 run-rate）
            assets = self.nation_assets(n)
            supply_total = led["prod_value"] + led["import_gold"]
            trade = ((led["export_gold"] + led["import_gold"]) / supply_total
                     if supply_total > 0 else 0.0)
            # 上期快照：首期没有（prev={}）→ 各增长率取 None，报表显示「—」；
            # 用 .get 兜底，避免旧档/损坏数据里缺字段时把异常抛进回合结算
            prev = (self.econ_reports.get(n) or [{}])[-1]
            if not isinstance(prev, dict):
                prev = {}
            snap = {
                "period_end": self.turn, "report_turn": self.turn + 1,
                "period_start": start, "span": days,
                "gdp": round(gdp, 1), "gdp_growth": self._growth(gdp, prev.get("gdp")),
                "military": round(military, 1),
                "military_ratio": round(military / gdp, 4) if gdp > 0 else None,
                "invest": round(invest, 1),
                "invest_growth": self._growth(invest, prev.get("invest")),
                "assets": round(assets, 1),
                "assets_growth": self._growth(assets, prev.get("assets")),
                "export_gold": round(led["export_gold"], 1),
                "import_gold": round(led["import_gold"], 1),
                "supply_eaten": int(led["supply_eaten"]),           # 整期累计（供展示）
                "supply_eaten_turn": int(d("supply_eaten")),        # 本回合吃掉（军费口径）
                "trade_ratio": round(trade, 4),
            }
            self.econ_reports.setdefault(n, []).append(snap)
            # 公告一条（看海终端可见；该国在【近讯】里也能看到 → 提醒它去 report 查）
            mr = snap["military_ratio"]
            self.log(f"📊 第 {snap['report_turn']} 回合经济报表已生成："
                     f"本回合 GDP {snap['gdp']:.0f} 金、军费占 GDP "
                     f"{f'{mr * 100:.0f}%' if mr is not None else '—'}、"
                     f"总资产 {snap['assets']:.0f}（report 看明细 / report all=true 看趋势）",
                     phase="内政", nation=n)
        self.ledger = {}

    @staticmethod
    def _remove_pair(lst: list, name: str):
        lst[:] = [p for p in lst if name not in p]

    # ------------------------------------------------------------- 回合结算
    def resolve_turn(self) -> dict:
        # 快照本回合起点的账本增量字段：GDP/军费只报"本回合"的实际产出（见 _close_report_period）
        runrate_from = self._ledger_runrate_mark()
        # 0) 刷新每地块每回合配额
        for t in self.tiles.values():
            t["recruited_this_turn"] = 0
            t["built_this_turn"] = 0
            t["militia_recruited_this_turn"] = 0

        # 1) 采集
        prod = {n: {k: 0 for k in ("粮食", "木头", "矿石", "石油", "装备", "补给")} for n in self.alive()}
        plants: dict[str, list] = {n: [] for n in self.alive()}
        factories: dict[str, list] = {n: [] for n in self.alive()}
        maint = {n: 0 for n in self.alive()}
        gold_in = {n: 0 for n in self.alive()}
        for (x, y), t in self.tiles.items():
            owner = t["owner"]
            if owner not in self.nations:
                continue
            b = t["buildings"]
            for bname, cnt in b.items():
                if cnt == 0:
                    continue
                info = BUILDINGS[bname]
                kind = info["kind"]
                if kind == "extract":
                    # 采集类：无投入，按 outputs 产出入储备。
                    # （军屯曾走这条分支屯田产粮，2026-09-18 起不产——它现在是纯民兵编制，
                    #   见 balance.py；这条分支只剩采集建筑。）
                    for g, amt in info["outputs"].items():
                        self.add_res(owner, g, amt * cnt)
                        prod[owner][g] += amt * cnt
                        self.flow_in[g] += amt * cnt   # 世界流量：产出
                        self._ledger(owner)["prod_value"] += self._mval(g, amt * cnt)
                elif kind == "gold":
                    gain = info["outputs"].get("黄金", 0) * cnt * MARKET["黄金"]
                    self.add_res(owner, "黄金", gain)
                    gold_in[owner] += gain
                    self._ledger(owner)["gold_in"] += gain
                elif kind == "energy":
                    plants[owner].append((bname, info, cnt))
                elif kind == "factory":
                    factories[owner].append((bname, info, cnt))
                    maint[owner] += cnt
                elif kind in ("barracks", "townhall"):
                    maint[owner] += cnt

        # 2) 电网 + 工厂
        for n in self.alive():
            et = 0
            for bname, info, cnt in plants[n]:
                fuel = info["fuel"]
                batches = cnt
                for f, need in fuel.items():
                    batches = min(batches, self.res(n, f) // need)
                for f, need in fuel.items():
                    self.add_res(n, f, -need * batches)
                    self.flow_out[f] += need * batches   # 世界流量：能源厂烧燃料
                    self._ledger(n)["fuel_value"] += self._mval(f, need * batches)
                et += batches * info["energy_out"]
            short = et < maint[n]
            self.grid_short[n] = short
            self.energy_report[n] = (et, maint[n], short)
            if not short:
                for bname, info, cnt in factories[n]:
                    batches = cnt
                    for f, need in info["inputs"].items():
                        batches = min(batches, self.res(n, f) // need)
                    if batches == 0:
                        continue
                    for f, need in info["inputs"].items():
                        self.add_res(n, f, -need * batches)
                        self.flow_out[f] += need * batches   # 世界流量：工厂投料
                        self._ledger(n)["mid_value"] += self._mval(f, need * batches)
                    for g, amt in info["outputs"].items():
                        self.add_res(n, g, amt * batches)
                        prod[n][g] += amt * batches
                        self.flow_in[g] += amt * batches    # 世界流量：工厂产出
                        self._ledger(n)["prod_value"] += self._mval(g, amt * batches)
                # 市政厅：每座 = effects.gold_base + 该地块已占建筑位(不含自身)×effects.gold_per_slot 金；电网不足即停摆
                for (hx, hy), ht in self.tiles.items():
                    if ht["owner"] != n:
                        continue
                    h = ht["buildings"].get("市政厅", 0)
                    if h:
                        others = sum(ht["buildings"].values()) - h
                        hall_gain = (building_effect("市政厅", "gold_base")
                                     + others * building_effect("市政厅", "gold_per_slot")) * h
                        self.add_res(n, "黄金", hall_gain)
                        gold_in[n] += hall_gain
                        self._ledger(n)["gold_in"] += hall_gain

        # 3) 战争结算
        war_lines = self._resolve_battles()
        for wx, wy, ln in war_lines:
            self.log(ln, phase="战报", x=wx, y=wy)  # 带坐标 → 视野内（含瞭望塔圈）才可见
        flat_lines = [ln for _, _, ln in war_lines]

        # 3.5) 撤退落地：撤退军队已随本轮战斗结算（全场分摊），此刻脱离到目标格
        for a in [a for a in self.armies if a.get("retreat_to")]:
            tx, ty = a["retreat_to"]
            a.pop("retreat_to", None)
            a.pop("retreat_cover", None)
            if a["hp"] <= 0:
                continue  # 结算中阵亡，撤不成了（战报已记）
            if self._retreat_legal(a["owner"], tx, ty):
                a["x"], a["y"] = tx, ty
                a["engaged"] = False
                self.log(f"{a['name']} 撤到 ({tx+1},{ty+1})，脱离交战", phase="战报",
                         nation=a["owner"], x=tx, y=ty)
            else:
                alts = [(nx, ny) for nx, ny in self.neighbors(a["x"], a["y"])
                        if (nx, ny) != (a["x"], a["y"]) and self._retreat_legal(a["owner"], nx, ny)]
                if alts:
                    a["x"], a["y"] = alts[0]
                    a["engaged"] = False
                    self.log(f"{a['name']} 撤退目标格战局生变，改撤 ({alts[0][0]+1},{alts[0][1]+1})",
                             phase="战报", nation=a["owner"], x=alts[0][0], y=alts[0][1])
                else:
                    self.log(f"{a['name']} 撤退目标格已不合法且四周无可退点，原地留守", phase="战报",
                             nation=a["owner"], x=a["x"], y=a["y"])

        # 4) 军队补给 + 回复（每国吃自己的补给仓）
        famine = {}
        for n in self.alive():
            ps = self.nation_armies(n)
            need = self._supply_need(n, ps)  # 步1/骑2；民兵驻自家军屯格免费
            paid = min(need, self.res(n, "补给"))
            self.add_res(n, "补给", -paid)
            self.flow_out["补给"] += paid   # 世界流量：军队吃补给
            self._ledger(n)["supply_eaten"] += paid
            self._spend(n)["supply"] += self._mval("补给", paid)   # 总消费：军费
            short = need - paid
            if short:
                # 缺口按比例分摊：每军扣 35×缺口/需求（交战中也照扣），至少 1
                per = max(1, ARMY_STARVE_DAMAGE * short // need)
                dead = []
                for a in ps:
                    a["hp"] -= per
                    if a["hp"] <= 0:
                        dead.append(a)
                for a in dead:
                    if a in self.armies:
                        self.armies.remove(a)
                famine[n] = (short, per, len(dead))
                ps = self.nation_armies(n)
            battle_tiles = {(a["x"], a["y"]) for a in self.armies if a.get("engaged")}
            for a in list(self.troops):          # 回血只给本国军队（野人不在 `n` 的账上）
                if a["owner"] != n:
                    continue
                if short or (a["x"], a["y"]) in battle_tiles:
                    continue  # 断粮或所在格正在交战（含防御方守军）→ 不回血
                a["hp"] = min(unit_max_hp(a), a["hp"] + ARMY_HEAL_PER_TURN)
        for n, (short, per, dead) in famine.items():
            self.log(f"⚠ {n} 补给断粮（缺 {short}，每军 -{per}HP）：{dead} 支军队饿毙", phase="内政", nation=n)

        # 4.9) 脱离清扫：撤退军已落地离开原格，格上留守者不该再背「交战中」
        cleared = self._clear_disengaged()
        if cleared:
            self.log(f" {cleared} 支军队解除交战（敌军已撤离/覆灭）", phase="战报")

        # 4.95) 弃城即陷：本回合发生过战斗的格子，守军全撤走/覆灭后格上只剩唯一一方
        # （且与格主交战）→ 直接改旗。兑现 rules 既定的「守军弃城即陷」，无需再补一刀 atk。
        # ★ sorted：_conquer 会改归属、可触发亡国（改 wars/truce/blocs），
        #   裸 set 迭代序受 hash 随机化 → 同 seed 换进程改旗顺序不同 → 战局分叉。
        for bx, by in sorted({(x, y) for x, y, _ in war_lines}):
            owner = self.owned_by(bx, by)
            if owner is None or owner not in self.nations:
                continue
            holders: dict[str, list[dict]] = {}
            for a in self.armies:
                if (a["x"], a["y"]) == (bx, by):
                    holders.setdefault(a["owner"], []).append(a)
            if owner in holders:
                continue  # 格主守军还在 → 不动
            # 守军尽撤 → 围攻方按索取顺序接管（第一个 atk 者优先；多个互相交战者仍算混战）
            foes = {g: fs for g, fs in holders.items() if g != "野人" and self.war_between(g, owner)}
            g = self._claim_winner(foes)
            if g is None:
                continue
            ok, msg = self._conquer(bx, by, g, "守军尽撤")
            if ok:
                self.log(f"⚔ 守军尽撤 @({bx + 1},{by + 1}){self.ter_char(bx, by)}，{msg}",
                         phase="战报", x=bx, y=by)

        # 5) 非法滞留 → 自动遣返（断盟/退盟/停战后必须撤出）
        self._withdraw_illegal()

        # 5.5) 联盟投票逾期未决 → 作废（发起回合的下一回合结束前须决出）
        self._expire_votes()

        # 6) 市场：按本回合世界供需算均衡价，市价向均衡价回归
        self._update_market()

        # 6.5) 在建建筑落地（施工 1 回合）：本回合结算不产出，落地后从下回合开始生效
        for t in self.tiles.values():
            p = t.get("pending") or {}
            for k, c in p.items():
                if c:
                    t["buildings"][k] += c
            t["pending"] = {k: 0 for k in BUILDINGS}

        # 6.8) 世界央行：储蓄结息 + 贷款计息/到期强制扣款（开关关着就是空转）
        self._bank_settle()

        # 7) 各国结算摘要（供 agent 看）
        for n in self.alive():
            et, mt, short = self.energy_report.get(n, (0, 0, False))
            parts = [f"{k}+{v}" for k, v in prod[n].items() if v]
            if gold_in[n]:
                parts.append(f"金+{gold_in[n]}")
            fam = f"，⚠断粮缺{famine[n][0]}" if famine.get(n) else ""
            grid = "停摆" if short else f"电{et}/{mt}"
            armies = len(self.nation_armies(n))
            self.econ_summary[n] = (
                f"产出 {' '.join(parts) if parts else '无'} | 电网 {grid} | 军队 {armies} 支{fam} | "
                f"国库{self.res(n,'黄金')} 木{self.res(n,'木头')} 补给仓{self.res(n,'补给')}"
            )

        # 7.5) 经济报表：每 REPORT_EVERY 回合自动结一期（第 11/21/31… 回合开局可查）
        # 报表是派生数据（坏了不影响世界状态），所以这里兜底：出岔子只跳过本期并记进纪事，
        # 绝不把异常抛进 resolve_turn 拖垮整局（与「不静默」原则一致——日志里看得见）。
        if self.turn and self.turn % REPORT_EVERY == 0:
            try:
                self._close_report_period(runrate_from)
            except Exception as e:
                self.log(f"⚠ 经济报表生成失败，本期跳过：{type(e).__name__}: {e}", phase="内政")
        return {"war_lines": flat_lines, "famine": famine}

    def _supply_need(self, n: str, ps: list[dict]) -> int:
        """全军每回合补给需求：步1/骑2；民兵驻在**自家**军屯格免费——每座军屯覆盖本格 1 支
        （同格第 2 支起、以及离格/军屯格被夺后的民兵，照常吃补给）。"""
        free: dict[tuple[int, int], int] = {}
        need = 0
        for a in ps:
            if unit_kind(a) == "民":
                t = self.tiles.get((a["x"], a["y"]))
                if t is not None and t["owner"] == n:
                    cap = t["buildings"].get("军屯", 0)
                    used = free.get((a["x"], a["y"]), 0)
                    if used < cap:
                        free[(a["x"], a["y"])] = used + 1
                        continue
            need += unit_supply(a)
        return need

    def _clear_disengaged(self) -> int:
        """脱离战斗清扫：格上已无活敌军的 engaged 军队就地解除交战。
        覆盖两处死角——敌军撤退落地离开原格（留守者傻等下回合结算才解锁）、
        以及任何路径留下的空交战标记。返回解除数。"""
        cleared = 0
        for a in self.armies:
            if not a.get("engaged"):
                continue
            foes = []
            for d in self.armies:
                if d is a or (d["x"], d["y"]) != (a["x"], a["y"]):
                    continue
                if d["owner"] == "野人":
                    if a["owner"] != "野人":
                        foes.append(d)  # 我方与野人交战（野人只接战进攻方）
                    continue
                if d["owner"] != a["owner"] and self.war_between(a["owner"], d["owner"]):
                    foes.append(d)
            if a["owner"] == "野人":
                # 野人只与「交战中的进攻方」为敌，和平停驻者不算
                foes = [d for d in self.armies
                        if d is not a and (d["x"], d["y"]) == (a["x"], a["y"])
                        and d["owner"] != "野人" and d.get("engaged")]
            if not foes:
                a["engaged"] = False
                cleared += 1
        return cleared

    def _withdraw_illegal(self):
        """断盟/停战后身处他国中立领土的军队，每回合按兵种速度朝最近的合法地(本国/盟国)撤
        （步兵 1 格=3×3、骑兵 2 格=5×5，一步步走）。"""
        for n in list(self.alive()):
            legal_tiles = self.own_tiles(n)
            for m in self.alive():
                if m != n and self.allied_between(n, m):
                    legal_tiles += self.own_tiles(m)
            if not legal_tiles:
                continue
            for a in list(self.troops):              # 撤军只可能是国家军队（野人不 retreat）
                if a["owner"] != n or a.get("engaged"):
                    continue
                owner = self.owned_by(a["x"], a["y"])
                if owner is None or owner == n or self.allied_between(n, owner) or self.war_between(n, owner):
                    continue  # 合法
                # 非法：按兵种速度（骑兵 2 格/步兵 1 格）朝最近的合法地一步步走
                tx, ty = min(legal_tiles, key=lambda p: max(abs(p[0] - a["x"]), abs(p[1] - a["y"])))
                moved = False
                for _ in range(unit_speed(a)):
                    best, bd = None, 10 ** 9
                    for nx, ny in self.neighbors(a["x"], a["y"]):
                        if not self._enterable_step(n, nx, ny, forced=True):
                            continue
                        dd = max(abs(nx - tx), abs(ny - ty))
                        if dd < bd or (dd == bd and (nx, ny) < (best or (9 ** 9, 0))):
                            best, bd = (nx, ny), dd
                    if best is None:
                        break
                    a["x"], a["y"] = best
                    moved = True
                if moved:
                    self.log(f"🚶 {n} 军队{a['id']} 自敌境「遣返」撤向合法地（{a['x']+1},{a['y']+1}）", phase="事件", nation=n, x=a["x"], y=a["y"])

    def _enterable_step(self, n: str, x: int, y: int, forced: bool = False) -> bool:
        """军队能否落步到 (x,y)。forced=True 供强制遣返用：军队已非法滞留他国（断盟/停战后），
        只允许落在合法地会把它永远困死，故遣返途中允许踩过中立地，一路走回本国/盟国。"""
        if forced:
            return True
        o = self.owned_by(x, y)
        return o is None or o == n or self.allied_between(n, o) or self.war_between(n, o)

    # ------------------------------------------------------------- 回合推进
    def begin_turn(self) -> int:
        """开始下一回合：投递上一回合发出的信件。返回投递数。"""
        self.turn += 1
        due = [m for m in self.mail_pending if m["arrive"] <= self.turn]
        self.mail_pending = [m for m in self.mail_pending if m["arrive"] > self.turn]
        for m in due:
            box = self.mailbox.get(m["to"])
            if box is not None:
                box.append({"from": m["from"], "text": m["text"], "turn": m["arrive"]})
        for m in due:
            if m["to"] in self.nations:
                self.log(f"📮 {m['to']} 收到 {m['from']} 的信", phase="事件", nation=m["to"])
        # 在途馈赠：下回合到账；收方已亡国则退回
        due_g = [g for g in self.gift_pending if g["arrive"] <= self.turn]
        self.gift_pending = [g for g in self.gift_pending if g["arrive"] > self.turn]
        for g in due_g:
            if g["to"] not in self.nations:
                if g["from"] in self.nations:
                    self.add_res(g["from"], g["good"], g["n"])  # 退回
                continue
            self.add_res(g["to"], g["good"], g["n"])
            self.log(f"🎁 {g['from']} 赠你的 {g['good']}×{g['n']} 到账", phase="事件", nation=g["to"])
        # 在途地图情报：下回合到账
        due_m = [m for m in self.map_pending if m["arrive"] <= self.turn]
        self.map_pending = [m for m in self.map_pending if m["arrive"] > self.turn]
        for m in due_m:
            if m["to"] not in self.nations:
                continue
            store = self.maps.setdefault(m["to"], [])
            store.append({"from": m["from"], "turn": m["arrive"], "text": m["text"]})
            del store[:-KEEP_MAPS]  # 只留最近 N 张图，控体积
            self.log(f"🗺 {m['to']} 收到 {m['from']} 的地图", phase="事件", nation=m["to"])
        # 间谍回报：3回合后盗回目标当前经济情报（含粗略军情数量）+ 地图；目标亡国则任务失败
        due_sp = [s for s in self.spy_pending if s["arrive"] <= self.turn]
        self.spy_pending = [s for s in self.spy_pending if s["arrive"] > self.turn]
        for s in due_sp:
            if s["from"] not in self.nations:
                continue
            if s["to"] not in self.nations:
                self.log(f"🕵 {s['from']} 的间谍回报：目标 {s['to']} 已亡国，情报落空",
                         phase="事件", nation=s["from"])
                continue
            store = self.econ_intel.setdefault(s["from"], [])
            store.append({"from": s["to"], "turn": s["arrive"], "text": self._econ_snapshot(s["to"])})
            del store[:-KEEP_SAVES]  # 只留最近 N 份，控体积
            # 间谍偷来的地图也进 intel（world.maps，与 share_map 同池，留最近 N 张）
            mstore = self.maps.setdefault(s["from"], [])
            mstore.append({"from": f"{s['to']}(间谍)", "turn": s["arrive"],
                           "text": self._map_snapshot(s["to"])})
            del mstore[:-KEEP_MAPS]
            self.log(f"🕵 {s['from']} 的间谍回报了 {s['to']} 的情报与地图",
                     phase="事件", nation=s["from"])
        return len(due)

    # ------------------------------------------------------------- 市场
    def market_price(self, good: str) -> float:
        return round(self.prices[good], 1)

    def market_depth(self, good: str) -> int:
        """该商品的市场深度（单位数）：每卖光这么多单位，市价大约被压掉「基准价×PRICE_IMPACT」。
        深度 = MARKET_DEPTH[g] × max(1, 现存国家数) ÷ MARKET_DEPTH_NATIONS_DIV
        ——国家越多市场越深（N 国为基准档）。"""
        return max(1, round(MARKET_DEPTH[good] * max(1, len(self.alive())) / MARKET_DEPTH_NATIONS_DIV))

    def market_tick(self, good: str) -> float:
        """每单位推动（金/单位）：基准价 × PRICE_IMPACT ÷ 深度。"""
        return MARKET[good] * PRICE_IMPACT / self.market_depth(good)

    def _clamp_price(self, good: str, p: float) -> float:
        base = MARKET[good]
        return min(max(p, base * PRICE_MIN_RATIO), base * PRICE_MAX_RATIO)

    def market_walk(self, good: str, n: int, side: str) -> tuple[float, float, float]:
        """沿价格曲线走 n 单位。返回 (成交单价含价差, 成交后中间价 p1, 总额)。
        成交单价 = 沿曲线均价 (p0+p1)/2 再加/减半个价差——不再整笔按 p1 结算。"""
        p0 = self.prices[good]
        tick = self.market_tick(good)
        d = tick * n * (1 if side == "buy" else -1)
        p1 = self._clamp_price(good, p0 + d)
        avg = (p0 + p1) / 2
        unit = avg * (1 + MARKET_SPREAD / 2) if side == "buy" else avg * (1 - MARKET_SPREAD / 2)
        return unit, p1, unit * n

    def market_quote(self, good: str, n: int, side: str) -> tuple[float, int]:
        """试算：不实际成交，返回 (成交单价, 总额)。供面板显示「卖 N 实收多少」。"""
        unit, _p1, total = self.market_walk(good, n, side)
        return unit, int(round(total))

    def buy(self, name: str, good: str, n: int) -> tuple[bool, str]:
        if good not in TRADEABLE:
            return False, f"「{good}」不可交易（可交易：{'、'.join(TRADEABLE)}）"
        if n <= 0:
            return False, "数量需为正整数"
        p0 = self.prices[good]
        unit, p1, total = self.market_walk(good, n, "buy")
        cost = int(round(total))
        if self.res(name, "黄金") < cost:
            return False, (f"黄金不足：买 {good}×{n}（均价 {unit:.2f}）需 {cost}，"
                           f"国库 {self.res(name,'黄金')}")
        self.add_res(name, "黄金", -cost)
        self.add_res(name, good, n)
        self.prices[good] = p1
        self._ledger(name)["import_gold"] += cost   # 经济报表：进口额（外贸占比用）
        return True, (f"购入 {good}×{n}（中间价 {p0:.2f}→{p1:.2f}，含价差均价 {unit:.2f}，"
                      f"实付 {cost}），余{self.res(name,good)}")

    def sell(self, name: str, good: str, n: int) -> tuple[bool, str]:
        if good not in TRADEABLE:
            return False, f"「{good}」不可交易"
        if n <= 0:
            return False, "数量需为正整数"
        if self.res(name, good) < n:
            return False, f"储备不足：{good} 现有 {self.res(name,good)}"
        p0 = self.prices[good]
        unit, p1, total = self.market_walk(good, n, "sell")
        gold = int(round(total))
        self.add_res(name, good, -n)
        self.add_res(name, "黄金", gold)
        self.prices[good] = p1
        self._ledger(name)["export_gold"] += gold   # 经济报表：出口额
        return True, (f"售出 {good}×{n}（中间价 {p0:.2f}→{p1:.2f}，含价差均价 {unit:.2f}，"
                      f"实收 {gold}），余{self.res(name,good)}")

    def _update_market(self) -> None:
        """每回合末：按全世界本回合流量算供需均衡价，市价向均衡价回归（而非死盯基准价）。
        产多耗少 → 均衡价低（全世界都在产，自然便宜）；战时军需耗大 → 均衡价高。"""
        for g in TRADEABLE:
            base = MARKET[g]
            prod, use = self.flow_in.get(g, 0), self.flow_out.get(g, 0)
            if prod and use:
                gap = (use - prod) / (use + prod)
            elif prod or use:
                gap = -MARKET_GAP_ONE_SIDE if prod else MARKET_GAP_ONE_SIDE
            else:
                gap = 0.0
            eq = base * (1 + MARKET_SENS * gap)
            eq = min(max(eq, base * MARKET_EQ_MIN_RATIO), base * MARKET_EQ_MAX_RATIO)
            self.equilibrium[g] = round(eq, 3)
            p = self.prices[g]
            self.prices[g] = round(min(max(eq + (p - eq) * PRICE_REVERT, base * PRICE_MIN_RATIO),
                                       base * PRICE_MAX_RATIO), 3)
        self.flow_in = {g: 0 for g in TRADEABLE}
        self.flow_out = {g: 0 for g in TRADEABLE}

    # ------------------------------------------------------------- 世界央行
    # 口径（2026-09-19 用户）：国库现金**默认就是储蓄**（不用存）；观察者设储蓄利率、可为负
    # （负则每回合扣钱，**扣到 0 为止**）；贷款 = 储蓄利率 + BANK_SPREAD（也可为负）；
    # 单笔 ≤1000 金、≤10 回合、**还清前不能再借**；到期**强制扣款**（这一笔允许扣成负的）。
    def bank_on(self) -> bool:
        return bool(self.bank.get("on"))

    def bank_enable(self) -> bool:
        """[配置开关] 打开世界央行，返回"本次是否真的打开了"。

        ★ 口径（2026-09-19 用户）：「银行**只能在配置文件开、不能关**，**可以中途加**」
        ⇒ 单向：`on` 只允许 false → true。配置说 true 就在本局打开（续局中途开也行）；
        配置说 false **也不会**把已经开着的关掉——一局里边开边关，账目与贷款期限就对不上了。
        """
        if self.bank.get("on"):
            return False
        self.bank["on"] = True
        return True

    def bank_rate(self) -> float:
        """储蓄利率（观察者设，可为负）。"""
        return float(self.bank.get("rate", 0.0))

    def bank_loan_rate(self) -> float:
        """贷款利率 = 储蓄利率 + BANK_SPREAD（可为负 ⇒ 欠款每回合**缩水**）。"""
        return self.bank_rate() + BANK_SPREAD

    def bank_set_rate(self, rate: float) -> tuple[bool, str]:
        """[观察者] 设储蓄利率；**变了就全世界播报**。

        播报口径按用户原话：**上调＝抑制通货膨胀、下调＝减少紧缩**，带**变动幅度 + 现值**。
        越界会夹到 [BANK_RATE_MIN, BANK_RATE_MAX]，并在回执里说明夹过。
        """
        if not self.bank_on():
            return False, "本局没开世界央行（mp_config.json 的 world_bank=true 才生效）"
        r = float(rate)
        clamped = max(BANK_RATE_MIN, min(BANK_RATE_MAX, r))
        old = self.bank_rate()
        if abs(clamped - old) < 1e-9:
            return False, f"利率没变（还是 {old:+.1%}）"
        self.bank["rate"] = clamped
        d = abs(clamped - old)
        if clamped > old:
            msg = f"🏦 世界央行公告：为了抑制通货膨胀，上调了 {d:.1%} 利率（现为 {clamped:+.1%}）"
        else:
            msg = f"🏦 世界央行公告：为了减少紧缩，下调了 {d:.1%} 利率（现为 {clamped:+.1%}）"
        if abs(clamped - r) > 1e-9:
            msg += f"（{r:+.1%} 超出可设区间，已夹到 {clamped:+.1%}）"
        self.broadcast(msg)
        return True, msg

    def bank_loan(self, name: str, amount: int, turns: int) -> tuple[bool, str]:
        """向世界央行借一笔：现金即刻到账；**还清之前不能再借**（不叠加）。"""
        if not self.bank_on():
            return False, "本局没开世界央行（mp_config.json 的 world_bank=true 才生效）"
        if name not in self.nations:
            return False, "借款方必须是现存国家"
        if name in self.bank["loans"]:
            ln = self.bank["loans"][name]
            return False, (f"你在第 {ln['taken_turn']} 回合借的那笔还没还清"
                           f"（到期应还 {ln['due']} 金、还剩 {ln['turns_left']} 回合）——"
                           "还清前不能再借")
        if not isinstance(amount, int) or amount <= 0:
            return False, "借款额需为正整数"
        if amount > BANK_LOAN_MAX:
            return False, f"单笔上限 {BANK_LOAN_MAX} 金（你借 {amount}）"
        if not isinstance(turns, int) or not (1 <= turns <= BANK_LOAN_MAX_TURNS):
            return False, f"期限需为 1~{BANK_LOAN_MAX_TURNS} 回合（你给 {turns}）"
        self.add_res(name, "黄金", amount)
        self.bank["loans"][name] = {"principal": amount, "due": amount, "turns_left": turns,
                                    "taken_turn": self.turn, "rate": self.bank_loan_rate()}
        self.log(f"🏦 {name} 向世界央行借款 {amount} 金（{turns} 回合后到期，"
                 f"利率 {self.bank_loan_rate():+.1%}）", phase="事件", nation=name)
        return True, (f"已到账 {amount} 金：{turns} 回合后到期，当前利率 "
                      f"{self.bank_loan_rate():+.1%}（= 储蓄 {self.bank_rate():+.1%} + "
                      f"{BANK_SPREAD:.0%}）；到期**强制扣款**，还清前不能再借")

    def _bank_settle(self) -> None:
        """每回合末的央行结算：储蓄结息 + 贷款计息/到期强制扣款（开关关着就空转）。"""
        if not self.bank_on():
            return
        r, lr = self.bank_rate(), self.bank_loan_rate()
        for n in self.alive():
            gold = self.res(n, "黄金")
            if r and gold > 0:
                d = int(gold * r)                     # 金是整数，截断取整
                if d < 0 and gold + d < 0:
                    d = -gold                         # ★ 储蓄**扣不到负**（用户口径）
                if d:
                    self.add_res(n, "黄金", d)
                    self.log(f"🏦 {n} 储蓄结息 {d:+d} 金（利率 {r:+.1%}，"
                             f"国库 {self.res(n, '黄金')}）", phase="事件", nation=n)
            ln = self.bank["loans"].get(n)
            if not ln:
                continue
            if lr:
                ln["due"] = max(0, int(round(ln["due"] * (1 + lr))))
            ln["turns_left"] -= 1
            if ln["turns_left"] <= 0:
                pay = ln["due"]
                self.add_res(n, "黄金", -pay)          # ★ 贷款**允许扣成负的**（用户口径）
                self.bank["loans"].pop(n, None)
                self.log(f"🏦 {n} 的央行贷款到期：强制扣款 {pay} 金"
                         f"（本金 {ln['principal']}、利率 {ln['rate']:+.1%}，"
                         f"国库 {self.res(n, '黄金')}）", phase="事件", nation=n)
            else:
                self.log(f"🏦 {n} 的央行贷款计息：应还 {ln['due']} 金、"
                         f"还剩 {ln['turns_left']} 回合（利率 {lr:+.1%}）",
                         phase="事件", nation=n)

    # ------------------------------------------------------------- 信箱
    def send_mail(self, frm: str, to: str, text: str) -> tuple[bool, str]:
        if frm not in self.nations or to not in self.nations:
            return False, "收发双方都必须是现存国家"
        if to == frm:
            return False, "不能给自己写信"
        self.mail_pending.append({"from": frm, "to": to, "text": text, "arrive": self.turn + 1})
        # 信件正文不单独记一条（信件=一次行动，正文已在行动行里）；送达时另有"收到信"事件+收件箱
        return True, f"信已发出，{to} 将于第 {self.turn+1} 回合收到"

    # ------------------------------------------------------------- 神秘人来信（Observer 用）
    def mystery_letter(self, to: str, text: str) -> tuple[bool, str]:
        """Observer 从看海终端给任意国家寄一封『神秘人』来信（下回合到其信箱）。

        发件人恒为「神秘人」，各国 AI 无从判断是谁；只记一条观察者可见的
        日志（nations 收不到），收件人下回合在信箱看到。
        """
        if to not in self.nations:
            return False, f"国家 {to} 不存在"
        text = (text or "").strip()
        if not text:
            return False, "神秘来信内容不能为空"
        self.mail_pending.append({"from": "神秘人", "to": to, "text": text,
                                  "arrive": self.turn + 1})
        self.log(f"🕵️ 神秘人来信 → {to}：{text}", phase="事件")  # 观察者可见，各国近讯收不到
        return True, f"神秘人来信已寄给 {to}，将于第 {self.turn+1} 回合送达其信箱"

    # ------------------------------------------------------------- 外交馈赠
    def gift(self, frm: str, to: str, good: str, n: int) -> tuple[bool, str]:
        """馈赠：把本国储备赠与他国（粮木矿油装补给或黄金）。本回合垫支扣出，下回合到账。"""
        if frm not in self.nations or to not in self.nations:
            return False, "馈赠双方都必须是现存国家"
        if to == frm:
            return False, "不能赠给自己"
        if good not in RES_KEYS:
            return False, f"不可赠送：{good}（可赠：{'/'.join(RES_KEYS)}）"
        if not isinstance(n, int) or n <= 0:
            return False, "数量需为正整数"
        have = self.res(frm, good)
        if have < n:
            return False, f"你{good}不够：现 {have}，赠不出 {n}"
        self.add_res(frm, good, -n)  # 先垫支扣出（锁住）
        self.gift_pending.append({"from": frm, "to": to, "good": good, "n": n,
                                  "arrive": self.turn + 1})
        return True, f"已赠 {to} {good}×{n}（你余 {self.res(frm, good)}），将于第 {self.turn+1} 回合到账"

    # ------------------------------------------------------------- 交换地图
    def _map_snapshot(self, n: str) -> str:
        """把某国的"已知地图"做成文本：全部国土块 + 边界外可见块，全部带坐标（不截断）。"""
        own = self.own_tiles(n)
        fr = sorted(self.frontier_of(n))
        L = [f"{n} 已知地图：国土 {len(own)} 块、边界外可拓地 {len(fr)} 格（全部坐标）"]
        for p in own:
            t = self.tiles[p]
            L.append(f"  {t.get('name', '?')} {t['terrain']}({p[0] + 1},{p[1] + 1}) "
                     f"城L{t['buildings']['城堡']} 位{sum(t['buildings'].values())}")
        if fr:
            L.append("  边界外可见（未占）:")
            for p in fr:
                L.append(f"  {self.tile_terrain(*p)}({p[0] + 1},{p[1] + 1})")
        return "\n".join(L)

    def share_map(self, frm: str, to: str) -> tuple[bool, str]:
        """把你的已知地图发给别国（交换情报/展示势力），下回合到对方【地图情报】。"""
        if frm not in self.nations or to not in self.nations:
            return False, "交换地图双方都必须是现存国家"
        if to == frm:
            return False, "不能把地图发给自己"
        self.map_pending.append({"from": frm, "to": to, "text": self._map_snapshot(frm),
                                 "arrive": self.turn + 1})
        return True, f"已把你的地图发给 {to}，将于第 {self.turn+1} 回合到账"

    # ------------------------------------------------------------- 间谍
    def _econ_snapshot(self, n: str) -> str:
        """目标国当前完整底细：国库/储备 + 收入结算 + 全部地块建设(含在建) + 粗略军情。"""
        r = self.nations[n].res
        res_txt = " ".join(f"{k}{r.get(k, 0)}" for k in RES_KEYS)
        et, mt, short = self.energy_report.get(n, (0, 0, False))
        grid = "停摆" if short else f"产{et}/需{mt}"
        L = [f"【{n} 情报】国库/储备: {res_txt} | 电网: {grid}"]
        summ = self.econ_summary.get(n)
        if summ:
            L.append(f"  上回合收入结算: {summ}")
        L.append(f"  全部地块建设情况（{len(self.own_tiles(n))} 块）:")
        for (x, y) in self.own_tiles(n):
            t = self.tiles[(x, y)]
            b = t["buildings"]
            p = t.get("pending") or {}
            built = " ".join(f"{bn}×{c}" for bn, c in b.items() if c) or "无"
            pend = " ".join(f"{bn}×{c}(在建)" for bn, c in p.items() if c)
            extra = ("；" + pend) if pend else ""
            L.append(f"    {t.get('name', '?')} {t['terrain']}({x + 1},{y + 1}) "
                     f"城L{b['城堡']} 位{sum(b.values())}/20 建筑[{built}{extra}]")
        # 粗略军情：只有各兵种数量——位置/血量/番号不外泄（间谍能探到敌国在扩军，但别想精准侦察）
        kinds: dict[str, int] = {}
        for a in self.nation_armies(n):
            k = unit_kind(a)
            kinds[k] = kinds.get(k, 0) + 1
        L.append("  军情（仅数量，位置未知）: " + ("、".join(f"{k}×{c}" for k, c in kinds.items()) or "无军队"))
        return "\n".join(L)

    def spy(self, frm: str, to: str) -> tuple[bool, str]:
        """派间谍刺探别国（花 SPY_COST 金），SPY_TURNS 回合后盗回其经济底细 +
        粗略军情（仅各兵种数量，位置/血量/番号不外泄）+ 整张已知地图。不能对自己用。"""
        if frm not in self.nations or to not in self.nations:
            return False, "间谍双方都必须是现存国家"
        if to == frm:
            return False, "不能派间谍刺探自己"
        if self.res(frm, "黄金") < SPY_COST:
            return False, f"国库不足：派间谍需 {SPY_COST} 金，你现 {self.res(frm, '黄金')}"
        self.add_res(frm, "黄金", -SPY_COST)
        self.spy_pending.append({"from": frm, "to": to, "arrive": self.turn + SPY_TURNS})
        return True, (f"已派间谍前往 {to}（-{SPY_COST}金），"
                      f"将于第 {self.turn + SPY_TURNS} 回合拿回其情报（经济底细+粗略军情+地图）")

    # ------------------------------------------------------------- 外交
    def _next_offer_id(self) -> int:
        self._offer_id += 1
        return self._offer_id

    def propose_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        """提议『共同防御』。**签约方是外交实体**：a 在盟 → 先过联盟投票再对外发邀约；
        a 独立 → 直接发邀约。对方若是联盟，接受时同样要过它的联盟投票。"""
        if kind == "同盟":
            return False, "双边同盟已由多边联盟取代：用 bloc_found(name=联盟名, tos=[创始成员]) 发起结盟（需起名，全体创始成员同意）"
        if kind != "共同防御":
            return False, f"未知盟约类型：{kind}（可选：共同防御；联盟请用 bloc_found）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.polity.get(b) == "huns":
            return False, f"{b} 是游牧政体，不接受盟约（别在它身上花外交费）"
        A, B = self.entity_of(a), self.entity_of(b)
        bad = self._pact_block(kind, A, B)
        if bad:
            return False, bad
        if self.entity_at_war(A):
            return False, f"战争期间不能缔结{kind}：{self._war_brief_ent(A)}（先议和）"
        if self.entity_at_war(B):
            return False, f"{self.entity_label(B)} 正在交战，战争期间不能与它缔结{kind}（先议和）"
        if any(p["kind"] == kind and {p.get("A"), p.get("B")} == {A, B} for p in self.proposals):
            return False, "该提议已在桌上"
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a,
                               {"pact": kind, "A": A, "B": B, "offer": True})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：向 {self.entity_label(B)} "
                     f"提议{kind}（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后由 {self.entity_label(A)} "
                          f"向 {self.entity_label(B)} 提议{kind}；成员用 vote {v['id']} true/false 表态")
        self.proposals.append({"id": self._next_offer_id(), "kind": kind, "A": A, "B": B,
                               "asker": a, "turn": self.turn})
        return True, f"{self.entity_label(A)} 向 {self.entity_label(B)} 提议{kind}，等对方接受"

    def accept_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        """接受收到的邀约。对方若是**联盟**，接受本身也要过联盟投票（一盟一票制）。"""
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None:
            return False, "没有这个邀约"
        if p["kind"] == "联盟":
            if me not in p.get("invitees", []):
                return False, "没有这个给你的邀约"
            return self._accept_bloc_founding(p, me)
        A, B = p.get("A"), p.get("B")
        if B is None or self.entity_of(me) != B:
            return False, "没有这个给你的邀约"
        if self.entity_of(p.get("asker", "")) != A:
            self.proposals.remove(p)
            return False, "提出方已变更外交实体（入盟/退盟），提议作废"
        kind = p["kind"]
        bad = self._pact_block(kind, A, B)
        if bad:
            self.proposals.remove(p)
            return False, bad
        if self.entity_at_war(A) or self.entity_at_war(B):
            self.proposals.remove(p)
            return False, "战争期间不能缔结盟约（提议作废，先议和）"
        if is_bloc_ent(B):
            if any(v["kind"] == "缔约" and v["payload"].get("offer_id") == p["id"]
                   for v in self.votes):
                return False, f"「{ent_key(B)}」已在表决这个邀约（投票进行中，用 vote 表态）"
            v = self._new_vote("缔约", ent_key(B), me,
                               {"pact": kind, "A": A, "B": B, "offer_id": p["id"]})
            self.log(f"🗳 {me} 把 {self.entity_label(A)} 的{kind}邀约提交「{ent_key(B)}」表决"
                     f"（投票#{v['id']}）", phase="外交", nation=me)
            return True, (f"已把邀约提交「{ent_key(B)}」表决（投票#{v['id']}）：赞成 > 反对"
                          f"即通过与 {self.entity_label(A)} 缔约")
        self.proposals.remove(p)
        return self._conclude_pact(kind, A, B)

    def reject_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None:
            return False, "没有这个邀约"
        if p["kind"] == "联盟":
            if me not in p.get("invitees", []):
                return False, "没有这个给你的邀约"
            self.proposals.remove(p)
            self.log(f"💔 {me} 拒绝了 {p['a']} 的结盟提议——「{p['name']}」创始流产（全体创始成员须一致同意）",
                     phase="外交", nation=me)
            return True, f"你拒绝了 {p['a']} 的结盟提议「{p['name']}」（创始流产）"
        A, B = p.get("A"), p.get("B")
        if B is None or self.entity_of(me) != B:
            return False, "没有这个邀约"
        if is_bloc_ent(B) and self.entity_chief(B) != me:
            self.proposals.remove(p)
            return True, (f"盟主 {self.entity_chief(B)} 代表 {self.entity_label(B)} 拒绝了 "
                          f"{self.entity_label(A)} 的{p['kind']}")
        self.proposals.remove(p)
        return True, f"你拒绝了 {self.entity_label(A)} 的{p['kind']}"

    def break_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        """解除共同防御。在盟国家须联盟投票。

        ★ **战争期间一律不准解除**（2026-09-18 改）：条约在战时**冻结**——平时缔结不了、
        战时也解不掉。原先是"断约顺带退出该条约带来的战线"（`_pact_exit_wars`），
        那等于留了一条「打不过就背弃盟友跑路」的脱战通道；现在只有**议和**能停战。"""
        if kind == "同盟":
            return False, "双边同盟已由多边联盟取代（bloc_found 结盟 / bloc_leave 退盟）"
        if kind != "共同防御":
            return False, f"未知盟约类型：{kind}（可选：共同防御）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        A, B = self.entity_of(a), self.entity_of(b)
        if not self.has_pact("共同防御", A, B):
            return False, f"{self.entity_label(A)} 与 {self.entity_label(B)} 并无共同防御"
        if self.entity_at_war(A) or self.entity_at_war(B):
            who = A if self.entity_at_war(A) else B
            return False, (f"战争期间不能解除共同防御：{self._war_brief_ent(who)}"
                           "（**条约在战时冻结**，缔结与解除都不行——先议和停战）")
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a,
                               {"pact": kind, "A": A, "B": B, "cancel": True})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：解除与 {self.entity_label(B)} "
                     f"的共同防御（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后解除与 "
                          f"{self.entity_label(B)} 的共同防御；成员用 vote {v['id']} true/false 表态")
        self._drop_pact(kind, A, B)
        exited = self._pact_exit_wars(self.entity_members(A), self.entity_members(B))
        extxt = f"，并退出 {self.entity_label(B)} 所在的 {len(exited)} 条战线" if exited else ""
        self.log(f"💔 {self.entity_label(A)} 单方面解除与 {self.entity_label(B)} 的{kind}{extxt}"
                 f"（{'、'.join(self.entity_members(A))} 在他国境内的军队将全部撤出）",
                 phase="外交", nation=a)
        return True, (f"{self.entity_label(A)} 已解除与 {self.entity_label(B)} 的{kind}{extxt}。"
                      "如你在他国境内，会于回合末自动遣返回国")

    # ------------------------------------------------------------- 联盟
    def propose_bloc(self, a: str, name: str, invitees: list[str]) -> tuple[bool, str]:
        """发起结盟：起名 + 邀全体创始成员，所有人接受才成立（全体成员同意）。
        发起方即盟主；战争期间不得缔结同盟（避免战时抱团脱战）。"""
        if a not in self.nations:
            return False, "发起方必须是现存国家"
        if self.at_war(a):
            return False, f"战争期间不能缔结同盟：{self._war_brief(a)}（先议和再谈结盟）"
        if self.bloc_of(a) is not None:
            return False, f"你已在联盟「{self.bloc_of(a)['name']}」中（一国同时只属一个联盟）"
        name = (name or "").strip()
        if not name or " " in name or len(name) > BLOC_NAME_MAX:
            return False, f"联盟名需为 1~{BLOC_NAME_MAX} 字、不含空格（name 参数）"
        if self.bloc_by_name(name) is not None:
            return False, f"联盟名「{name}」已被占用"
        inv = []
        for x in invitees:
            if x == a or x in inv:
                continue
            if x not in self.nations:
                return False, f"创始成员 {x} 不是现存国家"
            if self.bloc_of(x) is not None:
                return False, f"{x} 已在联盟「{self.bloc_of(x)['name']}」中"
            if self.polity.get(x) == "huns":
                return False, f"{x} 是游牧政体，不参与结盟（邀它只会让提议悬空）"
            if self.at_war(x):
                return False, f"创始成员 {x} 正在交战，战争期间不能缔结同盟（先议和）"
            inv.append(x)
        if not inv:
            return False, "至少邀请一个创始成员（tos=[国名,…]）；单国无需结盟"
        for i in range(len([a] + inv)):
            for j in range(i + 1, len([a] + inv)):
                x, y = ([a] + inv)[i], ([a] + inv)[j]
                if self.war_between(x, y):
                    return False, f"创始成员 {x} 与 {y} 正在交战，不能结盟（先议和）"
        if any(p["kind"] == "联盟" and p["a"] == a and p["name"] == name for p in self.proposals):
            return False, "该结盟提议已在桌上"
        self.proposals.append({"id": self._next_offer_id(), "kind": "联盟", "a": a, "b": "",
                               "name": name, "invitees": inv, "turn": self.turn})
        self.log(f"🕊 {a} 发起结盟「{name}」：邀 {'、'.join(inv)} 为创始成员（全体同意才成立）",
                 phase="外交", nation=a)
        return True, (f"已发起结盟「{name}」：等 {'、'.join(inv)} 全部 respond_proposal 接受后成立；"
                      f"任一拒绝即流产")

    def _accept_bloc_founding(self, p: dict, me: str) -> tuple[bool, str]:
        """创始成员接受结盟提议；全体接受即立盟。"""
        p.setdefault("accepted", [])
        if me in p["accepted"]:
            return False, "你已接受过该提议"
        p["accepted"].append(me)
        self.log(f"🕊 {me} 接受加入联盟「{p['name']}」", phase="外交", nation=me)
        pending = [x for x in p["invitees"] if x not in p["accepted"]]
        if pending:
            return True, f"你已接受结盟「{p['name']}」：还差 {'、'.join(pending)} 同意"
        # 全体同意 → 立盟
        self.proposals.remove(p)
        members = [p["a"]] + list(p["invitees"])
        for x in members:
            if x not in self.nations or self.bloc_of(x) is not None:
                return False, f"立盟失败：{x} 已不在可入盟状态（提议作废）"
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                if self.war_between(members[i], members[j]):
                    return False, f"立盟失败：{members[i]} 与 {members[j]} 已交战（提议作废）"
        self.blocs.append({"name": p["name"], "chief": p["a"], "members": members,
                           "turn": self.turn})
        # ★ 入盟即放弃个人条约：创始成员手里原有的保障/共同防御一律作废（它们本来就
        #   是"国家级"的，而入盟后这个国家不再是签约主体）。别想带着条约入伙。
        absorbed = self._absorb_personal_pacts(members)
        ab = f"（{'、'.join(absorbed)} 自动作废）" if absorbed else ""
        self.log(f"🕊 联盟「{p['name']}」成立！成员：{'、'.join(members)}（盟主 {p['a']}）{ab}",
                 phase="外交", nation=p["a"])
        return True, (f"联盟「{p['name']}」成立！成员：{'、'.join(members)}，盟主 {p['a']}"
                      f"（你为创始成员）{ab}")

    def _absorb_personal_pacts(self, members: list[str]) -> list[str]:
        """入盟清账：把成员手里的**个人**条约全部作废（成员不再是签约主体）。
        返回人话描述，供日志与提示。"""
        out = []
        for m in members:
            out.extend(self.drop_pacts_of(ent_nation(m)))
        if out:
            self.log(f"💔 入盟即放弃个人条约：{'、'.join(out)} 作废", phase="外交")
        return out

    def bloc_leave(self, a: str) -> tuple[bool, str]:
        """退盟：普通成员单方面退出，立即生效。**战争期间一律不准退**——盟员在战时被锁死
        （2026-09-18 外交改革：坚壁到底，不给"打不过就跑"的通道），直到整盟停战。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        if self.bloc_chief(bloc) == a:
            return False, ("你是盟主，不能退盟——请用 bloc_transfer(to=成员) 移交盟主之位，"
                           "或 bloc_dissolve 解散联盟")
        fighting = [m for m in bloc["members"] if self.at_war(m)]
        if fighting:
            return False, (f"战争期间不能退盟：{'、'.join(fighting)} 正在交战"
                           "（盟员在战时被锁死，直到整盟停战——先议和）")
        bloc["members"].remove(a)
        self.log(f"💔 {a} 单方面退出联盟「{bloc['name']}」", phase="外交", nation=a)
        if len(bloc["members"]) < 2:   # 只剩盟主一人 → 联盟自动解散
            chief = self.bloc_chief(bloc)
            self.blocs.remove(bloc)
            self.votes = [v for v in self.votes if v["bloc"] != bloc["name"]]
            self.log(f"💔 联盟「{bloc['name']}」仅剩盟主 {chief}，自动解散", phase="外交")
            return True, f"你已退出「{bloc['name']}」——联盟只剩盟主，随之解散"
        return True, (f"你已单方面退出「{bloc['name']}」。"
                      "（滞留他国领土的军队将自回合末起自动遣返）")

    def bloc_transfer(self, a: str, to: str) -> tuple[bool, str]:
        """盟主移交：把盟主之位让给本联盟另一成员（只有现任盟主能调）。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        if chief != a:
            return False, f"只有盟主能移交（现任盟主是 {chief}）"
        if to == a:
            return False, "不能把盟主之位移交给自己"
        if to not in bloc["members"]:
            return False, f"{to} 不是本联盟成员（成员：{'、'.join(bloc['members'])}）"
        bloc["chief"] = to
        self.log(f"👑 {a} 把联盟「{bloc['name']}」盟主之位移交给 {to}", phase="外交", nation=a)
        return True, f"已把「{bloc['name']}」盟主之位移交给 {to}"

    def bloc_rename(self, a: str, new_name: str) -> tuple[bool, str]:
        """盟主给联盟改名（只有盟主能改）。命名规则与立盟一致：1~12 字、不含空格、全局唯一。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        name = (new_name or "").strip()
        if chief != a:
            return False, f"只有盟主能给联盟改名（现任盟主是 {chief}）"
        if not name or " " in name or len(name) > BLOC_NAME_MAX:
            return False, f"联盟名需为 1~{BLOC_NAME_MAX} 字、不含空格（name 参数）"
        if name == bloc["name"]:
            return False, f"你的联盟已经叫「{name}」了"
        if self.bloc_by_name(name) is not None:
            return False, f"联盟名「{name}」已被占用"
        old = bloc["name"]
        bloc["name"] = name
        self._rename_bloc_refs(old, name)
        self.log(f"🏷 盟主 {a} 把联盟「{old}」改名为「{name}」", phase="外交", nation=a)
        return True, f"联盟已改名：「{old}」→「{name}」"

    def _rename_bloc_refs(self, old: str, new: str) -> None:
        """联盟改名 → 把按**实体 id** 索引的引用整体同步（条约表），顺手同步按联盟名索引的
        投票。实体 id 里嵌着联盟名（"盟:<名>"），漏一处就会出现指向不存在实体的条约。"""
        for p in self.pacts:
            for k in ("a", "b"):
                if p[k] == ent_bloc(old):
                    p[k] = ent_bloc(new)
        for v in self.votes:        # 进行中的投票按联盟名索引，一并改掉，免得面板/日志对不上
            if v["bloc"] == old:
                v["bloc"] = new

    def bloc_dissolve(self, a: str) -> tuple[bool, str]:
        """盟主解散联盟（只有盟主能调）。**战争期间一律不准解散**（盟员在战时被锁死），
        防止盟主用解散脱战坑盟友。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        if chief != a:
            return False, f"只有盟主能解散联盟（现任盟主是 {chief}）"
        fighting = [m for m in bloc["members"] if self.at_war(m)]
        if fighting:
            return False, (f"战争期间不能解散联盟：{'、'.join(fighting)} 正在交战"
                           "（坚壁到底——先议和停战再谈解散）")
        members = list(bloc["members"])
        self.blocs.remove(bloc)
        self.votes = [v for v in self.votes if v["bloc"] != bloc["name"]]
        self.log(f"💔 盟主 {a} 解散联盟「{bloc['name']}」（原成员：{'、'.join(members)}）",
                 phase="外交", nation=a)
        return True, (f"已解散联盟「{bloc['name']}」：原成员 {'、'.join(members)} 恢复各自独立"
                      "（他们此后才是各自的外交实体）")

    def bloc_join(self, a: str, bloc_name: str) -> tuple[bool, str]:
        """申请入盟：联盟现成员投票（赞成 > 反对，盟主可否决），通过即入盟。
        战争期间不能入盟（先议和）。"""
        if a not in self.nations:
            return False, "申请方必须是现存国家"
        if self.at_war(a):
            return False, f"战争期间不能缔结同盟：{self._war_brief(a)}（先议和再谈入盟）"
        bloc = self.bloc_by_name(bloc_name)
        if bloc is None:
            return False, f"联盟「{bloc_name}」不存在（可用面板查联盟列表）"
        if self.bloc_of(a) is not None:
            return False, f"你已在联盟「{self.bloc_of(a)['name']}」中（一国同时只属一个联盟）"
        if any(self.war_between(a, m) for m in bloc["members"]):
            return False, "你与该联盟成员正在交战，不能入盟（先议和）"
        v = self._new_vote("入盟", bloc["name"], a, {"candidate": a})
        self.log(f"🗳 {a} 申请加入联盟「{bloc_name}」（投票#{v['id']}，赞成>反对通过，盟主可否决）",
                 phase="外交", nation=a)
        return True, (f"已向「{bloc_name}」申请入盟（投票#{v['id']}）：成员用 "
                      f"vote {v['id']} yes/no/abstain 表态；赞成多于反对即通过（盟主投 no 可否决）")

    # ------------------------------------------------------------- 联盟投票
    def _new_vote(self, kind: str, bloc: str, proposer: str, payload: dict) -> dict:
        self._vote_id += 1
        v = {"id": self._vote_id, "kind": kind, "bloc": bloc, "proposer": proposer,
             "payload": payload, "votes": {}, "turn": self.turn}
        # 发起人默认投赞成票（入盟投票中候选人不投票）
        if not (kind == "入盟" and payload.get("candidate") == proposer):
            v["votes"][proposer] = True
        self.votes.append(v)
        return v

    def cast_vote(self, me: str, vote_id: int, choice) -> tuple[bool, str]:
        """联盟成员表态：choice = True 赞成 / False 反对 / None 弃权（可改票）。
        盟主投反对 = 一票否决，议案立即作废。"""
        v = next((x for x in self.votes if x["id"] == vote_id), None)
        if v is None:
            return False, "没有这个投票（diplomacy 面板查看进行中的投票）"
        bloc = self.bloc_by_name(v["bloc"])
        if bloc is None or self.bloc_of(me) is not bloc:
            return False, "该投票不属于你所在的联盟"
        if v["kind"] == "入盟" and v["payload"].get("candidate") == me:
            return False, "入盟投票由现成员表决，申请人不投票"
        if choice is False and self.bloc_chief(bloc) == me:
            self.votes.remove(v)
            self.log(f"🚫 盟主 {me} 否决了「{v['bloc']}」投票#{v['id']}（{v['kind']}）",
                     phase="外交", nation=me)
            return False, f"你以盟主身份否决了投票#{v['id']}（{v['kind']}）——议案立即作废"
        v["votes"][me] = choice
        label = "赞成" if choice is True else ("反对" if choice is False else "弃权")
        self.log(f"🗳 {me} 在「{v['bloc']}」投票#{v['id']}（{v['kind']}）：{label}",
                 phase="外交", nation=me)
        return self._tally(v)

    @staticmethod
    def _vote_counts(v: dict, members: list[str]) -> tuple[int, int, int, int]:
        """(赞成, 反对, 弃权, 未投)。未投 = 到期即弃权。"""
        yes = sum(1 for m in members if v["votes"].get(m) is True)
        no = sum(1 for m in members if v["votes"].get(m) is False)
        abst = sum(1 for m in members if m in v["votes"] and v["votes"][m] is None)
        return yes, no, abst, len(members) - yes - no - abst

    def _pass_vote(self, v: dict, members: list[str], yes: int, no: int, abst: int) -> tuple[bool, str]:
        """投票通过：移出投票列表、记账、执行。"""
        self.votes.remove(v)
        self.log(f"🗳 「{v['bloc']}」投票#{v['id']}（{v['kind']}）通过：赞成 {yes}/反对 {no}/弃权 {abst}",
                 phase="外交", nation=v["proposer"])
        ok, msg = self._execute_vote(v)
        return ok, f"投票通过（赞成 {yes}/反对 {no}）——{msg}"

    def _tally(self, v: dict) -> tuple[bool, str]:
        """计票：赞成 > 反对 → 通过并立即执行（弃权不计入分母）；结果已无悬念时提前定论。"""
        bloc = self.bloc_by_name(v["bloc"])
        if bloc is None:
            self.votes.remove(v)
            return False, "联盟已不存在，投票作废"
        members = [m for m in bloc["members"] if m in self.nations]
        yes, no, abst, pending = self._vote_counts(v, members)
        if yes > no + pending:          # 未投的全投反对也追不上 → 通过
            return self._pass_vote(v, members, yes, no, abst)
        if yes + pending <= no:         # 未投的全赞成也超不过 → 未通过
            self.votes.remove(v)
            self.log(f"🗳 「{v['bloc']}」投票#{v['id']}（{v['kind']}）未通过：赞成 {yes}/反对 {no}",
                     phase="外交", nation=v["proposer"])
            return False, f"投票未通过（赞成 {yes}/反对 {no}，反对已不可能被超过）"
        return True, (f"已记票（赞成 {yes}/反对 {no}/弃权 {abst}/未投 {pending}，需赞成 > 反对）："
                      f"等其余成员表态")

    def _expire_votes(self) -> None:
        """结算时处理逾期投票：未投 = 弃权，按 赞成 > 反对 定论（不再直接作废）。"""
        for v in list(self.votes):
            if self.turn <= v["turn"]:
                continue
            bloc = self.bloc_by_name(v["bloc"])
            if bloc is None:
                self.votes.remove(v)
                continue
            members = [m for m in bloc["members"] if m in self.nations]
            yes, no, abst, pending = self._vote_counts(v, members)
            if yes > no:
                self._pass_vote(v, members, yes, no, abst + pending)
            else:
                self.votes.remove(v)
                self.log(f"🗳 「{v['bloc']}」投票#{v['id']}（{v['kind']}）逾期未通过："
                         f"赞成 {yes}/反对 {no}/弃权 {abst + pending}", phase="外交", nation=v["proposer"])

    def _execute_vote(self, v: dict) -> tuple[bool, str]:
        """投票通过后的实际执行。"""
        pl = v["payload"]
        if v["kind"] == "宣战":
            bloc = self.bloc_by_name(v["bloc"])
            if bloc is None:
                return False, "联盟已散，宣战落空"
            target = pl.get("target")          # ★ 实体 id（不是国名）
            if not self.entity_members(ent_bloc(v["bloc"])) or not self.entity_members(target):
                return False, f"目标 {self.entity_label(target)} 已不在，宣战落空"
            return self._declare_war_internal(ent_bloc(v["bloc"]), target, v["proposer"])
        if v["kind"] == "缔约":
            return self._execute_pact_vote(v)
        if v["kind"] == "入盟":
            bloc = self.bloc_by_name(v["bloc"])
            cand = pl.get("candidate")
            if bloc is None or cand not in self.nations:
                return False, "入盟条件已变，申请落空"
            if self.bloc_of(cand) is not None or any(self.war_between(cand, m) for m in bloc["members"]):
                return False, f"{cand} 已入他盟/与成员交战，入盟落空"
            absorbed = self._absorb_personal_pacts([cand])
            bloc["members"].append(cand)
            ab = f"（入盟即放弃个人条约：{'、'.join(absorbed)} 作废）" if absorbed else ""
            self.log(f"🕊 {cand} 加入联盟「{bloc['name']}」{ab}", phase="外交", nation=cand)
            return True, f"{cand} 正式加入「{bloc['name']}」{ab}"
        if v["kind"] == "议和":
            if pl.get("type") == "offer":
                w = next((x for x in self.wars if x["id"] == pl.get("war_id")), None)
                if w is None:
                    return False, "战争已结束，议和投票落空"
                a_side = "atk" if v["proposer"] in self._war_sides(w)[0] else "def"
                if self._peace_rep(w, a_side) != v["proposer"]:
                    return False, "你已不是本方谈判代表，议和落空"
                return self._record_peace_offer(w, v["proposer"], pl["to"], pl["kind"],
                                                pl["gold"], pl.get("note", ""), pl.get("truce", 0))
            if pl.get("type") == "accept":
                p = next((x for x in self.peace_offers if x["id"] == pl.get("offer_id")), None)
                if p is None:
                    return False, "求和提议已不在，接受落空"
                return self._do_accept_peace(p)
        return False, f"未知投票类型 {v['kind']}"

    def _execute_pact_vote(self, v: dict) -> tuple[bool, str]:
        """联盟「缔约」投票通过后的执行：签保障 / 签共同防御 / 撤回保障 / 解除共同防御 /
        对外发出共同防御邀约。**条约的表决与执行都只走这条路径**——成员个人签不了。"""
        pl = v["payload"]
        kind, A, B = pl["pact"], pl["A"], pl["B"]
        if not self.entity_members(A):
            return False, f"{self.entity_label(A)} 已不存在，{kind}表决落空"
        if not self.entity_members(B):
            return False, f"{self.entity_label(B)} 已不存在，{kind}表决落空"
        if pl.get("cancel"):                       # 撤回保障 / 解除共同防御
            if self.entity_at_war(A) or self.entity_at_war(B):
                return False, "战争期间条约冻结：表决通过也失效了（先议和停战）"
            if self._drop_pact(kind, A, B):
                self.log(f"💔 {self.entity_label(A)} 经联盟表决解除与 {self.entity_label(B)} 的{kind}",
                         phase="外交")
                return True, f"{self.entity_label(A)} 已解除与 {self.entity_label(B)} 的{kind}"
            return False, f"条约已不在，解除{kind}落空"
        if pl.get("offer"):                        # 我方实体对外提议共同防御
            p = {"id": self._next_offer_id(), "kind": kind, "A": A, "B": B,
                 "asker": v["proposer"], "turn": self.turn}
            self.proposals.append(p)
            self.log(f"🗳 {self.entity_label(A)} 经联盟表决，向 {self.entity_label(B)} "
                     f"提议{kind}（邀约#{p['id']}）", phase="外交")
            return True, (f"已以 {self.entity_label(A)} 的名义向 {self.entity_label(B)} 发出"
                          f"{kind}邀约（#{p['id']}），等对方回应")
        oid = pl.get("offer_id")                   # 对方邀约的表决通过 → 落条约
        if oid is not None:
            self.proposals = [x for x in self.proposals if x["id"] != oid]
        bad = self._pact_block(kind, A, B)
        if bad:
            return False, bad
        return self._conclude_pact(kind, A, B)

    # ------------------------------------------------------------- 核心领土
    def _snapshot_cores(self, participants: list[str]) -> None:
        """战争结束：参战各国（含跟随方）实际持有的地块重算为其核心领土（议和即对现状追认）。"""
        for p in set(participants):   # 序无关：每格只有一个 owner，写入键互斥（区别于战争闭包/弃城那两处需 sorted 的 set 迭代）
            if p not in self.nations:
                continue
            for (x, y) in self.own_tiles(p):
                self.tiles[(x, y)]["core"] = p

    def _same_front(self, a: str, b: str) -> bool:
        """a 与 b 是否处于同一场战争的同一侧（同战线，含跟随方）。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if (a in atk and b in atk) or (a in dfs and b in dfs):
                return True
        return False

    def _return_core(self, x: int, y: int, by: str) -> str:
        """同战线盟友自动归还核心领土：by 刚占领 (x,y)，若此地是同联盟且同战线的
        盟友的核心，则立即归还盟友（驻军原地不动，盟国领土合法停留）。返回附加说明。"""
        t = self.tiles.get((x, y))
        core = t.get("core") if t else None
        if not core or core == by or core not in self.nations or core == t.get("owner"):
            return ""
        if self.bloc_of(by) is None or self.bloc_of(by) is not self.bloc_of(core):
            return ""
        if not self._same_front(by, core):
            return ""
        t["owner"] = core
        return f"——此乃盟友 {core} 的核心领土，已自动归还（同战线·联盟「{self.bloc_of(core)['name']}」）"

    def declare_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        """a 宣布保障 b 独立。**签约方是外交实体**：a 在盟 → 由它的联盟出面，须联盟投票通过；
        a 独立 → 直接生效（它自己就是实体）。"""
        if a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        A, B = self.entity_of(a), self.entity_of(b)
        bad = self._pact_block("保障", A, B)
        if bad:
            return False, bad
        if self.entity_at_war(A):
            return False, f"战争期间不能提供保障独立：{self._war_brief_ent(A)}（先议和）"
        if self.entity_at_war(B):
            return False, f"{self.entity_label(B)} 正在交战，战争期间不能保障它（先议和）"
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a, {"pact": "保障", "A": A, "B": B})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：{self.entity_label(A)} 保障 "
                     f"{self.entity_label(B)} 独立（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后由 {self.entity_label(A)} "
                          f"保障 {self.entity_label(B)} 独立；成员用 vote {v['id']} true/false 表态")
        return self._conclude_pact("保障", A, B)

    def cancel_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        """撤回保障（方向敏感：只有保障方能撤）。在盟国家须联盟投票。"""
        if a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        A, B = self.entity_of(a), self.entity_of(b)
        if A == B:
            return False, "你与它同属一个实体，没有保障可撤"
        if not self.has_pact("保障", A, B):
            return False, f"{self.entity_label(A)} 并未保障 {self.entity_label(B)}"
        if self.entity_at_war(A) or self.entity_at_war(B):
            who = A if self.entity_at_war(A) else B
            return False, (f"战争期间不能撤回独立保障：{self._war_brief_ent(who)}"
                           "（**条约在战时冻结**，缔结与撤回都不行——先议和停战）")
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("缔约", bl["name"], a,
                               {"pact": "保障", "A": A, "B": B, "cancel": True})
            self.log(f"🗳 {a} 发起联盟投票（「{bl['name']}」）：撤回对 {self.entity_label(B)} 的保障"
                     f"（投票#{v['id']}）", phase="外交", nation=a)
            return True, (f"已发起联盟投票（投票#{v['id']}）：通过后撤回对 "
                          f"{self.entity_label(B)} 的独立保障")
        self._drop_pact("保障", A, B)
        self.log(f"{self.entity_label(A)} 撤回对 {self.entity_label(B)} 的独立保障",
                 phase="外交", nation=a)
        return True, f"{self.entity_label(A)} 已撤回对 {self.entity_label(B)} 的保障"

    def declare_war(self, a: str, b: str) -> tuple[bool, str]:
        """宣战。**交战方是外交实体**：在盟国家不能擅自开战，调用即转为联盟宣战投票
        （多数决通过后全盟参战）；独立国家直接开战。

        守侧传导 = 保障 / 共同防御 / 联盟 的**传递闭包**（无限跳，以实体为单位）。"""
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.war_between(a, b):
            return False, "你们已经在交战"
        t = self.truce.get(_pair(a, b))
        if t is not None:
            if self.turn < t:
                return False, f"休战中：你与 {b} 约定休战至第 {t} 回合（还剩 {t - self.turn} 回合），不得再宣战"
            self.truce.pop(_pair(a, b), None)  # 到期清除
        A, B = self.entity_of(a), self.entity_of(b)
        if A == B:
            return False, (f"{b} 与你同属 {self.entity_label(A)}，不能宣战"
                           "（盟内互不攻击是联盟自带效果）")
        bl = self.bloc_of(a)
        if bl is not None:
            v = self._new_vote("宣战", bl["name"], a, {"target": B})
            self.log(f"🗳 {a} 发起联盟宣战投票（「{bl['name']}」）：对 {self.entity_label(B)} "
                     f"宣战（投票#{v['id']}，多数决）", phase="外交", nation=a)
            return True, (f"已发起联盟宣战投票（投票#{v['id']}）：多数同意后全盟对 "
                          f"{self.entity_label(B)} 宣战；成员用 vote {v['id']} true/false 表态")
        return self._declare_war_internal(A, B, a)

    def _declare_war_internal(self, atk_ent: str, def_ent: str,
                              proposer: str) -> tuple[bool, str]:
        """实际开战。atk_ent / def_ent 都是**外交实体**（独立国家或联盟）；proposer=发起国名。

        守侧传导：从 def_ent 出发的 保障/共同防御 传递闭包（无限跳，以实体为单位）——
        联盟成员整体落在守侧（打成员=打联盟）。
        ★ 战线条目本身仍记为**国家**（atk=进攻主导国=盟主、def=目标实体代表、
        followers=守侧跟随国），作战/结算/遣返/迷雾那一堆既有代码不必换口径。
        """
        members = self.entity_members(atk_ent)
        def_members = self.entity_members(def_ent)
        if not members:
            return False, f"{self.entity_label(atk_ent)} 已不存在，宣战落空"
        if not def_members:
            return False, f"目标 {self.entity_label(def_ent)} 已灭亡，宣战落空"
        b = self.entity_chief(def_ent) or def_members[0]
        # 休战检查（任一进攻侧成员与任一守侧成员休战中则不能开战；到期的顺手清掉）
        for m in members:
            for d in def_members:
                t = self.truce.get(_pair(m, d))
                if t is None:
                    continue
                if self.turn < t:
                    return False, f"休战中：{m} 与 {d} 约定休战至第 {t} 回合，不得开战"
                self.truce.pop(_pair(m, d), None)
        # 并入现有战线（不开平行战争）：目标实体正在攻打我方实体的盟友/共同防御对象 → 守侧并入
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if any(d in atk for d in def_members) and any(
                    self._same_camp(m, d) for m in members for d in dfs):
                self._break_pacts_between(atk_ent, def_ent)
                added = [m for m in members if m not in dfs
                         and not any(self.war_between(m, x) for x in atk)]
                w["followers"].extend(added)
                for m in added:
                    self.log(f"⚔ {m} 对 {self.entity_label(def_ent)} 宣战：盟友正被它攻打，"
                             "并入该战线当防守方跟随方（不开第二场战争；跟随方不能单独议和）",
                             phase="外交", nation=m)
                return True, (f"对 {self.entity_label(def_ent)} 宣战：并入既有战线当防守方"
                              f"（新增 {'、'.join(added) or '无'}；跟随方不能单独议和，"
                              "主导者议和则整条战线停战）")
        # 目标实体正被我方实体的盟友/共同防御对象攻打 → 随攻并入
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if any(d in dfs for d in def_members) and any(
                    self._same_camp(m, x) for m in members for x in atk):
                self._break_pacts_between(atk_ent, def_ent)
                added = [m for m in members if m not in atk
                         and not any(self.war_between(m, x) for x in dfs)]
                w.setdefault("atk_followers", []).extend(added)
                for m in added:
                    self.log(f"⚔ {m} 对 {self.entity_label(def_ent)} 宣战：盟友正在攻打它，"
                             "并入该战线随攻（不开第二场战争；跟随方不能单独议和）",
                             phase="外交", nation=m)
                return True, (f"对 {self.entity_label(def_ent)} 宣战：并入既有战线随攻"
                              f"（新增 {'、'.join(added) or '无'}）")
        # 两实体之间的保障/共同防御自动解除（不打自己人）
        self._break_pacts_between(atk_ent, def_ent)
        # 守侧闭包（无限跳）：保障（谁保障它）+ 共同防御（谁与它互卫）
        def_side = {def_ent}
        stack = [def_ent]
        while stack:
            x = stack.pop()
            cands = set(self.guarantors_of(x)) | set(self.defense_partners_of(x))
            # ★ 必须 sorted：循环体读**增长中的 def_members**（下面的 war_between 剪枝），
            #   集合迭代序受 PYTHONHASHSEED 影响 → 同 seed 换进程可能收编不同的跟随方，
            #   整条历史分叉——"同种子同结果"就毁在这一行裸迭代上。
            for c in sorted(cands):
                if c in def_side or c == atk_ent:
                    continue
                cm = self.entity_members(c)
                if not cm:
                    continue
                if any(self.war_between(m, x2) for m in cm for x2 in members):
                    continue        # 已与攻方交战，不并入守侧
                if any(self.war_between(m, d) for m in cm for d in def_members):
                    continue        # 已与守侧某员交战，不并入
                def_side.add(c)
                def_members = def_members + cm
                stack.append(c)
        followers = [c for c in def_members if c != b]
        # 防守义务优先：守侧成员与进攻侧实体的保障/共同防御自动解除；逐国通知
        for c in followers:
            self._break_pacts_between(self.entity_of(c), atk_ent)
            self.log(f"⚔ {c} 因联盟/共同防御/保障义务被拖入守侧，自动参战打 {'、'.join(members)}"
                     "（跟随方不能单独议和，主导者议和则整条战线停战）", phase="外交", nation=c)
        leader = self.entity_chief(atk_ent) or members[0]
        self.wars.append({"id": self._next_war_id(), "atk": leader, "def": b,
                          "followers": followers,
                          "atk_followers": [m for m in members if m != leader],
                          "turn": self.turn})
        mtxt = "、".join(members)
        self.log(f"⚔ {proposer} 发起：{mtxt}（{self.entity_label(atk_ent)}）对 "
                 f"{self.entity_label(def_ent)} 宣战！", phase="外交", nation=leader)
        if leader == proposer and len(members) == 1:
            self.log(f"⚔ {proposer} 对 {b} 宣战！{b} 必须应战", phase="外交", nation=proposer)
        jtxt = f"；守侧传导参战：{'、'.join(followers)}" if followers else ""
        return True, f"对 {self.entity_label(def_ent)} 宣战（进攻侧：{mtxt}）{jtxt}"

    def _peace_rep(self, w: dict, side: str) -> str:
        """某侧的谈判代表：主导者本人；主导者属联盟时 = 联盟主体（盟主）——
        议和必须由联盟主体身份出面，普通成员/跟随方不能谈。"""
        leader = w["atk"] if side == "atk" else w["def"]
        if leader in self.nations:
            bloc = self.bloc_of(leader)
            if bloc is not None:
                chief = self.bloc_chief(bloc)
                if chief in self.nations and chief != leader:
                    return chief
        return leader

    def offer_peace(self, a: str, b: str, kind: str, gold: int = 0, note: str = "",
                    truce: int = 0) -> tuple[bool, str]:
        if kind not in ("pay", "demand", "white"):
            return False, "kind 须为 pay(我方赔款) / demand(要求对方赔款) / white(白和)"
        if not isinstance(truce, int) or truce < 0:
            return False, "休战回合数须为非负整数（0=不休战，自行约定）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        w = self._war_of(a, b)
        if w is None:
            return False, "你们并不在交战"
        # 谈判代表：每侧=主导者；主导者有联盟时=其盟主（联盟主体身份）
        a_side = "atk" if a in self._war_sides(w)[0] else "def"
        b_side = "def" if a_side == "atk" else "atk"
        rep_a, rep_b = self._peace_rep(w, a_side), self._peace_rep(w, b_side)
        if a != rep_a:
            hint = f"（你方主体是盟主 {rep_a}，由其出面并经联盟投票）" if rep_a != w["atk" if a_side == "atk" else "def"] else ""
            return False, f"议和必须由本方谈判代表 {rep_a} 出面{hint}"
        if b != rep_b:
            return False, f"求和对象须为对方谈判代表 {rep_b}（主导者或其盟主）"
        if gold < 0 or (kind in ("pay", "demand") and gold == 0):
            if kind == "white":
                gold = 0
            else:
                return False, "赔款量需为正整数（white 则不带赔款）"
        bloc = self.bloc_of(a)
        # 判断"是否联盟主体出面"必须用权威 bloc_chief，不能用 members[0]——
        # bloc_transfer 后盟主已换人，members[0] 仍是创始旧盟主，会让新盟主绕过投票私签和平。
        if bloc is not None and self.bloc_chief(bloc) == a:
            # 联盟主体议和须先过联盟投票（多数决）
            v = self._new_vote("议和", bloc["name"], a,
                               {"type": "offer", "war_id": w["id"], "to": rep_b,
                                "kind": kind, "gold": gold, "note": note, "truce": truce})
            self.log(f"🗳 {a} 发起联盟议和投票（「{bloc['name']}」）：向 {rep_b} 求和（投票#{v['id']}，多数决）",
                     phase="外交", nation=a)
            return True, (f"已发起联盟议和投票（投票#{v['id']}）：多数同意后正式向 {rep_b} 提出；"
                          f"成员用 vote {v['id']} true/false 表态")
        return self._record_peace_offer(w, a, b, kind, gold, note, truce)

    def _record_peace_offer(self, w: dict, a: str, b: str, kind: str, gold: int,
                            note: str, truce: int) -> tuple[bool, str]:
        """记录求和提议（投票通过后或非联盟主体直接提出）。"""
        self.peace_offers.append({"id": self._next_offer_id(), "a": a, "b": b,
                                  "kind": kind, "gold": gold, "note": note, "turn": self.turn,
                                  "war_id": w["id"], "truce": truce})
        k = {"pay": f"{a} 愿赔 {gold} 金求和", "demand": f"{a} 要求 {b} 赔 {gold} 金",
             "white": "白和（不赔不索）"}[kind]
        if truce > 0:
            k += f"，约定休战 {truce} 回合"
        self.log(f"🕊 求和提议：{k}" + (f"——{note}" if note else ""), phase="外交", nation=a)
        return True, f"已向 {b} 提出：{k}，等 {b} 在下一回合回应（accept/reject 议和 id）"

    def accept_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.peace_offers if x["id"] == offer_id and x["b"] == me), None)
        if p is None:
            return False, "没有这个给你的求和提议"
        bloc = self.bloc_of(me)
        # 同 offer_peace：认盟主用 bloc_chief，防 bloc_transfer 后新盟主绕过投票直接接受
        if bloc is not None and self.bloc_chief(bloc) == me:
            # 联盟主体接受议和须先过联盟投票（多数决）
            v = self._new_vote("议和", bloc["name"], me, {"type": "accept", "offer_id": offer_id})
            self.log(f"🗳 {me} 发起联盟议和投票（「{bloc['name']}」）：接受 {p['a']} 的求和（投票#{v['id']}）",
                     phase="外交", nation=me)
            return True, (f"已发起联盟议和投票（投票#{v['id']}）：多数同意后正式接受；"
                          f"成员用 vote {v['id']} true/false 表态")
        return self._do_accept_peace(p)

    def _do_accept_peace(self, p: dict) -> tuple[bool, str]:
        a, b, kind, gold = p["a"], p["b"], p["kind"], p["gold"]
        me = p["b"]
        w = self._war_of(a, b)
        if w is None:
            self.peace_offers.remove(p)
            return False, "你们已不在交战（提议作废）"
        if kind == "pay":
            if self.res(a, "黄金") < gold:
                return False, f"{a} 国库不足 {gold}，赔不起这个价"
            self.add_res(a, "黄金", -gold)
            self.add_res(b, "黄金", gold)
        elif kind == "demand":
            if self.res(b, "黄金") < gold:
                return False, f"你的国库不足 {gold}，付不起这笔赔款（可回提 pay 求和）"
            self.add_res(b, "黄金", -gold)
            self.add_res(a, "黄金", gold)
        self.peace_offers.remove(p)
        self.peace_offers = [x for x in self.peace_offers if x.get("war_id") != w["id"]]
        members = [w["atk"], w["def"]] + list(w["followers"]) + list(w.get("atk_followers", []))
        self.wars.remove(w)
        # 战争结束：参战各国实际持有的地块重算为核心领土（议和即对现状追认）
        self._snapshot_cores(members)
        # 自行约定休战期：主导者议和时定的 truce 覆盖整条战线（含跟随方）
        truce_n = int(p.get("truce") or 0)
        until = 0
        if truce_n > 0:
            until = self.turn + truce_n
            for x in [w["atk"]] + list(w.get("atk_followers", [])):
                for y in [w["def"]] + list(w["followers"]):
                    self.truce[_pair(x, y)] = until
        for m in self.armies:
            if m["owner"] in members and m.get("engaged"):
                m["engaged"] = False  # 整条战线（含跟随方）一并解除交战
        extra = {"pay": f"{a} 付 {b} {gold} 金", "demand": f"{b} 赔 {a} {gold} 金", "white": "白和"}[kind]
        fls = list(w["followers"]) + list(w.get("atk_followers", []))
        who = ("（含跟随方 " + "、".join(fls) + "）") if fls else ""
        truce_txt = f"，休战 {truce_n} 回合（至第 {until} 回合）" if truce_n > 0 else ""
        self.log(f"🕊 {a} 与 {b} 议和停战{who}{truce_txt}（{extra}）", phase="外交", nation=me)
        return True, (f"停战议成：{extra}{truce_txt}。整条战线已停{who}，各方军队解除交战；"
                      f"各方实际持有地块重算为核心领土；滞留在对方领土的军队将于回合末遣返")

    def reject_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.peace_offers if x["id"] == offer_id and x["b"] == me), None)
        if p is None:
            return False, "没有这个求和提议"
        self.peace_offers.remove(p)
        return True, f"你拒绝 {p['a']} 的求和（战争继续）"

    # ------------------------------------------------------------- 关系查询
    def allied_between(self, a: str, b: str) -> bool:
        """a、b 是否同属一个外交实体（同一联盟的成员，或本来就是同一个国家）。"""
        return self.entity_of(a) == self.entity_of(b)

    def _war_sides(self, w: dict) -> tuple[list[str], list[str]]:
        """进攻方名单 / 防御方名单（含跟随方；atk_followers=随攻的进攻侧跟随方）。"""
        return ([w["atk"]] + list(w.get("atk_followers", [])),
                [w["def"]] + list(w["followers"]))

    def _war_of(self, a: str, b: str) -> dict | None:
        """a 与 b 处于对立双方的战争冲突；不在交战返回 None。"""
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if (a in atk and b in dfs) or (a in dfs and b in atk):
                return w
        return None

    def _next_war_id(self) -> int:
        self._war_id += 1
        return self._war_id

    def war_between(self, a: str, b: str) -> bool:
        return self._war_of(a, b) is not None

    def dp_between(self, a: str, b: str) -> bool:
        """两国**所在实体**之间是否有共同防御。"""
        return self.has_pact("共同防御", self.entity_of(a), self.entity_of(b))

    def guarantee_of(self, b: str) -> list[str]:
        """保障 b 所在实体的实体 id 列表（面板用 entity_label 转人话）。"""
        return self.guarantors_of(self.entity_of(b))

    # ------------------------------------------------------------- 关系汇总（供面板）
    def rel_desc(self, me: str) -> str:
        """本国视角的全员关系一览（实体口径：条约挂在实体上，不挂在国家上）。"""
        my = self.entity_of(me)
        out = []
        for n in self.alive():
            if n == me:
                continue
            them = self.entity_of(n)
            tags = []
            if them == my:
                tags.append(f"联盟成员（同属{self.entity_label(my)}）")
            if self.war_between(me, n):
                tags.append("交战")
            if self.has_pact("共同防御", my, them):
                tags.append("共同防御")
            if self.has_pact("保障", my, them):
                tags.append("本实体保障它")
            if self.has_pact("保障", them, my):
                tags.append("它保障本实体")
            out.append(f"{n}=" + ("、".join(tags) if tags else "中立"))
        return "  ".join(out) if out else "（只有你一个国了）"

    # ------------------------------------------------------------- 存档
    def save(self, path: str | Path) -> None:
        data = {
            "version": SAVE_VERSION,
            "size": self.size, "seed": self.seed, "turn": self.turn,
            "rng_state": list(self.rng.getstate()),
            "nations": {n: nat.res for n, nat in self.nations.items()},
            "order": self.order,
            # ★ 按**插入序**（物化序）写，**不 sorted**：读回来之后 `self.tiles` 的遍历顺序
            #   才与"从没存过档"的进程一致。sorted 会让续跑进程按坐标序遍历，而
            #   `prod_value` 这类钱账是**逐格浮点累加**的 ⇒ 累加顺序一变就差 1 ULP
            #   （`test_resume_mid_game_equals_straight_run` 抓的就是这种病）。
            "tiles": {f"{x},{y}": t for (x, y), t in self.tiles.items()},
            "armies": self.armies, "next_army_seq": self.next_army_seq,
            "diplo_built": self.diplo_built,
            "nation_code": self.nation_code,
            "guard_once": [list(k) for k in sorted(self.guard_once)],
            "wars": self.wars,
            "war_id": self._war_id,
            "truce": [[a, b, until] for (a, b), until in self.truce.items()],
            "pacts": self.pacts,
            "bank": self.bank,
            "blocs": self.blocs,
            "votes": self.votes,
            "vote_id": self._vote_id,

            "mail_pending": self.mail_pending,
            "mailbox": self.mailbox,
            "summaries": self.summaries,
            "summary_blocks": self.summary_blocks,
            "long_memory": self.long_memory,
            "turn_memory": self.turn_memory,
            "gift_pending": self.gift_pending,
            "map_pending": self.map_pending,
            "maps": self.maps,
            "spy_pending": self.spy_pending,
            "econ_intel": self.econ_intel,
            "plans": self.plans,
            "polity": self.polity,
            "extra_prompt": self.extra_prompt,
            "peace_offers": self.peace_offers,
            "proposals": self.proposals,
            "offer_id": self._offer_id,
            "prices": self.prices,
            "equilibrium": self.equilibrium,
            "flow_in": self.flow_in,
            "flow_out": self.flow_out,
            "grid_short": self.grid_short,
            "energy_report": self.energy_report,
            "econ_summary": self.econ_summary,
            "econ_reports": self.econ_reports,
            "ledger": self.ledger,
            "spend": self.spend,
            "history": self.history,
            "history_seen": self.history_seen,
        }
        # 契约断言：save 的键集合必须与 SAVE_KEYS 严丝合缝——忘了登记的新字段
        # 在第一次存档就炸（开发期），而不是变成静默丢档（运行时才发现=惨案）
        keys, want = set(data), set(SAVE_KEYS)
        if keys != want:
            raise RuntimeError(
                f"存档契约漂移：save 多出 {sorted(keys - want)}、少写 {sorted(want - keys)}"
                f"——新增状态字段请同步 SAVE_KEYS 清单与 load 搬运")
        # 原子写（tmp+rename）：turn_memory 使存档变大近一倍，避免写一半中断损坏档
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "World":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("version") != SAVE_VERSION:
            raise SaveFormatError(
                f"存档版本不符（档内 {data.get('version', '无版本号＝旧档')} ≠ 当前 {SAVE_VERSION}）："
                f"本项目不做旧档兼容，请用 --new 重开，或删掉 {path}")
        missing = [k for k in SAVE_KEYS if k not in data]
        if missing:
            raise SaveFormatError(f"存档缺字段：{missing}（文件被截断或改坏），请重开一局")
        w = cls(size=data["size"], seed=data["seed"], gen=False)   # 空壳：世界由存档整体还原（不预建默认三国，否则残出幽灵地块）
        w.turn = data["turn"]
        ver, internal, gauss = data["rng_state"]
        w.rng.setstate((ver, tuple(internal), gauss))
        # 以下全部按"版本已核对、键已核对齐"直读：只重建序列化丢掉的容器类型
        # （tuple/frozenset/set），不再有任何旧格式迁移分支。
        w.nations = {n: Nation(n, res) for n, res in data["nations"].items()}
        w.order = data["order"]
        w.mailbox = {n: data["mailbox"].get(n, []) for n in w.nations}
        # summaries 条目恒为 {turn,text}（旧字符串格式随版本锁死一并退休）
        w.summaries = {n: [{"turn": int(it["turn"]), "text": str(it["text"])}
                           for it in lst] for n, lst in data["summaries"].items()
                       if n in w.nations}
        w.summary_blocks = {n: list(v) for n, v in data["summary_blocks"].items()
                            if n in w.nations}
        w.long_memory = {n: str(v) for n, v in data["long_memory"].items() if n in w.nations}
        w.turn_memory = {n: list(v) for n, v in data["turn_memory"].items() if n in w.nations}
        w.gift_pending = data["gift_pending"]
        w.map_pending = data["map_pending"]
        w.maps = {n: list(v) for n, v in data["maps"].items() if n in w.nations}
        w.spy_pending = data["spy_pending"]
        w.econ_intel = {n: list(v) for n, v in data["econ_intel"].items() if n in w.nations}
        w.plans = {n: dict(v) for n, v in data["plans"].items() if n in w.nations}
        w.polity = {n: v for n, v in data["polity"].items() if n in w.nations}
        w.extra_prompt = {n: dict(v) for n, v in data["extra_prompt"].items() if n in w.nations}
        w.armies = data["armies"]
        # 野地索取顺序计数器：从档内现有最大入场序号续起（未参战军队无此键，按 0 计）
        w._engage_seq = max((int(a.get("engage_seq", 0) or 0) for a in w.armies), default=0)
        # 军队编号：各国独立番号(AI 所见) + 国家码×1e8 全局唯一 gid(内部)
        w.nation_code = {k: int(v) for k, v in data["nation_code"].items()}
        w._next_code = max(w.nation_code.values(), default=0) + 1
        w.next_army_seq = {k: int(v) for k, v in data["next_army_seq"].items()}
        w.diplo_built = {k: int(v) for k, v in data["diplo_built"].items() if k in w.nations}
        # 战争恒为冲突对象 {id,atk,def,followers,atk_followers,turn}（旧 [a,b] 边对已随版本退休）
        w.wars = []
        w._war_id = int(data["war_id"])
        for item in data["wars"]:
            w.wars.append({"id": int(item["id"]), "atk": item["atk"], "def": item["def"],
                           "followers": list(item.get("followers", [])),
                           "atk_followers": list(item.get("atk_followers", [])),
                           "turn": int(item["turn"])})
            w._war_id = max(w._war_id, int(item["id"]) + 1)
        w.truce = {_pair(ab[0], ab[1]): int(ab[2]) for ab in data["truce"]}
        # 联盟：members 已过滤亡国，chief 权威
        w.blocs = []
        for b in data["blocs"]:
            members = [m for m in b["members"] if m in w.nations]
            if members:
                w.blocs.append({"name": str(b["name"]), "members": members,
                                "chief": b["chief"], "turn": int(b["turn"])})
        w.votes = [dict(v) for v in data["votes"]]
        w._vote_id = int(data["vote_id"])
        w.pacts = [dict(p) for p in data["pacts"]]
        # 世界央行：on/rate/loans 全在档里（开关也持久化——续局不该把央行开了又关）
        _bk = data["bank"]
        w.bank = {"on": bool(_bk.get("on")), "rate": float(_bk.get("rate", 0.0)),
                  "loans": {k: dict(v) for k, v in (_bk.get("loans") or {}).items()}}
        w.mail_pending = data["mail_pending"]
        w.peace_offers = data["peace_offers"]
        w.proposals = data["proposals"]
        w._offer_id = data["offer_id"]
        w.prices = {g: float(data["prices"][g]) for g in TRADEABLE}
        w.equilibrium = {g: float(data["equilibrium"][g]) for g in TRADEABLE}
        w.flow_in = {g: int(data["flow_in"][g]) for g in TRADEABLE}
        w.flow_out = {g: int(data["flow_out"][g]) for g in TRADEABLE}
        w.history = data["history"]
        w.history_seen = data["history_seen"]
        # 电网/结算摘要也持久化：否则续档后第一回合 all 面板电力 0、上回合结算丢失
        w.grid_short = {n: bool(v) for n, v in data["grid_short"].items() if n in w.nations}
        w.energy_report = {n: tuple(v) for n, v in data["energy_report"].items() if n in w.nations}
        w.econ_summary = {n: s for n, s in data["econ_summary"].items() if n in w.nations}
        w.econ_reports = {n: list(v) for n, v in data["econ_reports"].items() if n in w.nations}
        w.ledger = {n: {**{k: float(v[k]) for k in LEDGER_FIELDS},
                        "_since": max(1, int(v["_since"]))}
                    for n, v in data["ledger"].items() if n in w.nations}
        w.spend = {n: {k: float(v[k]) for k in SPEND_FIELDS}
                   for n, v in data["spend"].items() if n in w.nations}
        # 地块：存档即完整（recruited/built/buildings/pending/core 与全部建筑键都在），
        # 只做 "x,y" 字符串键 → (x,y) 元组键的还原，不再补字段
        for k, t in data["tiles"].items():
            x, y = map(int, k.split(","))
            w.tiles[(x, y)] = t
        w.guard_once = {tuple(k) for k in data["guard_once"]}
        w._ensure_guardians()
        return w


class Nation:
    def __init__(self, name: str, res: dict[str, int] | None = None):
        self.name = name
        # 构造即全键：传部分字典也补齐 START_RES（缺键国家首次结算 add_res 会 KeyError）
        self.res = {**START_RES, **(res or {})}

    def __repr__(self):
        return f"<Nation {self.name}>"
