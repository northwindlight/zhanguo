# -*- coding: utf-8 -*-
"""多国引擎（MP）。

多个国家共存于同一张地图，各自经营（规则对所有国家一致，无作弊入口）：
  - 每国独立 国库(黄金)/战略储备/电网(不存储)/军队/国土；
  - 无人地带的视野内地块由「野人」把守，打赢即拓疆；
  - **国家间永久中立（本分支为 RL 训练版，无外交）**：不能通信、结盟、宣战，
    他国领土一律不可进入、不可攻击——各国只能各自打野人扩张、做经济，
    唯一的交互是共用同一个世界市场（买卖互相影响市价与均衡价）；
  - 看海：world.history 记下每个行动/每场战，Observer 全可见；
    各国 agent 只见「自己该知道」的（自己的面板/视野内事件）。

坐标 0-based；对外（面板/tool 入参）用 1-based。
"""

from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path

from game import (
    ARMY_HEAL_PER_TURN,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    CASTLE_DEFENSE_PER_LEVEL,
    COMBAT_DIE_MOD,
    ENGINEER_DISCOUNT,
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
    TOWN_HALL_GOLD,
    TOWN_HALL_PER_SLOT,
    TRADEABLE,
    UNIT_TYPES,
    WATCHTOWER_RADIUS,
    army_name,
    roll_tile_name,
    unit_atk,
    unit_kind,
    unit_max_hp,
    unit_speed,
    unit_supply,
)

# 每国开局资源
START_RES = {
    "黄金": 1500, "粮食": 20, "木头": 60,
    "矿石": 10, "石油": 0, "装备": 5, "补给": 10,
}
RES_KEYS = ["黄金", "粮食", "木头", "矿石", "石油", "装备", "补给"]
# 资源在 res 面板里的展示别名（黄金就是国库）
RES_LABEL = {"黄金": "国库", "木头": "木材", "补给": "补给仓"}

CROSS = [(0, 0), (0, -1), (0, 1), (-1, 0), (1, 0)]

RETREAT_DEF_COVER = 50   # 防御方撤退：回合末战斗结算中只受 50% 伤害（进攻方撤退全额；0=不减伤、100=免伤）
PLAN_MAX_TURNS = 10  # 国策每 10 回合必须修订一次（否则 end_turn 被拦）
REPORT_EVERY = 10    # 经济报表每 10 回合自动结一期：第 11/21/31… 回合开局可查（不能手动运行）
# 总消费（累计，按当时市价折金）——RL 版的**目标函数**。只计「被消耗掉的资源」，
# 市场买卖不计（买来的物资在真正被消耗时才入账），避免重复计数。
SPEND_FIELDS = ("build",    # 建造实付金 + 木×市价（含城堡升级）
                "recruit",  # 征兵消耗（粮/装备/金）× 市价
                "supply")   # 军费：军队吃掉的补给 × 市价
# 本期经济账本字段（按国累计，结完报表清零；全部按当时市价折金）
LEDGER_FIELDS = ("prod_value",      # 采集/工厂/军屯 产出 × 市价
                 "mid_value",       # 工厂中间投入 × 市价
                 "fuel_value",      # 能源厂燃料 × 市价
                 "gold_in",         # 金矿 + 市政厅 入国库的金
                 "supply_eaten",    # 军队实际吃掉的补给（单位）——军费口径，不看来源
                 "import_gold",     # 市场买入总额
                 "export_gold",     # 市场卖出总额
                 "invest_gold",     # 建造实付金
                 "invest_wood_value")  # 建造耗木 × 当时市价


# ---------------------------------------------------------------------------
# 建设的经济核算（**游戏层**，一份账两处用：LLM 的经济面板 + 规则 AI 的建造决策）
# ---------------------------------------------------------------------------

def good_value(world: "World", good: str, amt: int, side: str = "mid") -> float:
    """把 amt 单位 good 折成金（黄金按 MARKET['黄金'] 折算）。

    side='mid' 用中间价；'buy'/'sell' 用含价差的实际成交单价（自用替代 / 外销口径）。
    （原在 mp_ai._gval；提到游戏层，免得规则 AI 反向依赖 LLM 层。）
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
    """单个建筑的**数字版**经济核算（数值口径与 mp_ai 的经济面板同源）。

    返回：
        {
          "building": 建筑名,
          "capex":    造价折金（金 + 木×现价；城堡取 L1）,
          "per_turn": 每回合净收益（金）—— 负 = 净支出,
          "payback":  capex / per_turn（per_turn ≤ 0 → None，永远回不了本）,
          "detail":   分项明细（给面板拼文案用）,
        }

    ⚠️ **注意口径**：加工厂（补给厂/装备厂）的产出按"**自用替代**"（买价）计 ——
    即"这些产出替你去市场上买"。这个口径对**流量品**（补给：军队每回合都吃）成立，
    对**存量品**（装备：只在征兵时一次性消耗）会**高估** —— 你并不是每回合都去买装备。
    要不要按真实消耗率折算，见 spend_rules / 建造决策那边。
    """
    info = BUILDINGS[building]
    wp = world.prices.get("木头", float(MARKET["木头"]))
    cost = info["cost"] if isinstance(info["cost"], int) else info["cost"][0]
    # ★ 传了地块就按**该格的实际造价**算（用户 2026-09-11：ROI 必须含地形成本）。
    #   公式与引擎 `World.build` 逐字对齐：地形施工惩罚只上浮**金价**（木材不变），
    #   工程院再减 25%（只认已落成的、且自己不享受自己的减免）。
    #   不传就按基础造价 —— 那在山地上会低估 50%，回本算出来是假的。
    if tile is not None:
        t = world.tiles.get(tile)
        if t is not None:
            bp = TERRAIN_STATS[t["terrain"]]["build_penalty"]
            if bp:
                cost = cost * (100 + bp) // 100
            if t["buildings"].get("工程院") and building != "工程院":
                cost = cost * (100 - ENGINEER_DISCOUNT) // 100
    capex = cost + info.get("wood", 0) * wp
    kind = info["kind"]
    detail: dict = {}

    if kind == "gold":
        per = sum(good_value(world, g, a) for g, a in info["outputs"].items())
        detail["net"] = per
    elif kind == "extract" or kind == "militia_camp":
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
    else:                                   # castle / barracks / townhall / tower
        per = 0.0

    return {"building": building, "capex": capex, "per_turn": per,
            "payback": (capex / per) if per > 0 else None,
            "cost": cost, "wood": info.get("wood", 0), "wood_price": wp,
            "detail": detail}


def best_build(world: "World", candidates,
               max_payback: float | None = None) -> tuple[str | None, float | None]:
    """在候选建筑里挑**回本最快**的一个（回本 ≤ 0 或算不出的排除）。

    `max_payback`：**只接受回本能落在这么多回合内的**（用户 2026-09-11 口径：
    一局才 50 回合，超过剩余回合数的复利不算数 —— 回本 40 回合的农场在
    第 30 回合建就是纯亏）。None = 不限。

    全部按**当前市价**算（`build_econ` 走 world.prices），所以每回合重评才准。

    候选可以是建筑名，也可以是 `(建筑名, 地块)` —— **后者按该格的实际造价算**
    （含地形施工惩罚），山地和平原的同一座建筑回本可以差一倍，必须按格评。

    返回 (候选, 回本回合)——候选就是你传进来的那个元素；没有可建的就 (None, None)。
    """
    best, bp = None, None
    for item in candidates:
        bn, tile = item if isinstance(item, tuple) else (item, None)
        e = build_econ(world, bn, tile)
        if e["payback"] is None:
            continue
        if max_payback is not None and e["payback"] > max_payback:
            continue
        if bp is None or e["payback"] < bp:
            best, bp = item, e["payback"]
    return best, bp


class World:
    def __init__(self, size: int = 80, seed: int | None = None, *,
                 nations: list[str] | None = None,
                 starts: dict[str, tuple[int, int]] | None = None,
                 res: dict[str, dict[str, int]] | None = None,
                 gen: bool = True):
        self.size = size
        self.seed = seed if seed is not None else random.randrange(1 << 31)
        self.rng = random.Random(self.seed)
        self.turn = 0
        self.tiles: dict[tuple[int, int], dict] = {}
        self.nations: dict[str, "Nation"] = {}
        self.order: list[str] = []
        self.summaries: dict[str, list[dict]] = {}  # 各国回合小结纪事 [{turn,text}]（私有，本国 AI 记忆；全留，供旧回合汇总）
        self.summary_blocks: dict[str, list[dict]] = {}  # 各国阶段块总结 [{from,to,text,turn}]（滑出 replay 的回合经 LLM 压成一段，覆盖其小结）
        self.turn_memory: dict[str, list[dict]] = {}  # 各国完整回合记录（含思考 reasoning_content），按 ctx_window 预算动态保留最近若干回合
        self.plans: dict[str, dict] = {}           # 各国国策规划 {text, turn}——常驻上下文，每10回合须修订
        self.polity: dict[str, str] = {}           # 政体标记（"huns"=匈奴）→ 造价/征召差异
        self.extra_prompt: dict[str, dict] = {}    # 临时注入的额外上下文 {text, until}——塞入正常 system_prompt，until 后自动消失
        self.prices: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}   # 中间价 mid
        self.equilibrium: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}  # 供需均衡价（每回合末重算）
        self.flow_in: dict[str, int] = {g: 0 for g in TRADEABLE}   # 本回合世界入库流量（产出），算完均衡价清零
        self.flow_out: dict[str, int] = {g: 0 for g in TRADEABLE}  # 本回合世界出库流量（消耗）
        self.armies: list[dict] = []
        self.next_army_seq: dict[str, int] = {}  # 各国独立军队序列：从1递增、阵亡不回收
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
        self.spend: dict[str, dict] = {}                # 总消费（全期累计，不清零）：RL 目标函数
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
        from game import roll_terrain
        return roll_terrain(random.Random(f"{self.seed}:{x}:{y}"))

    def tile_resources(self, x: int, y: int) -> dict[str, int]:
        """该地块的矿藏/耕地布局（建采集建筑要看的就是它）——与地形同一套做法：
        **纯函数 of (seed, x, y)**，跟谁先占、占之前打过几仗毫无关系。

        以前这里在 `_new_tile` 里写的是 `roll_resources(self.rng, terrain)`，用的是
        **世界共享 RNG**：同一格摇出什么资源，取决于轮到它的时候 RNG 已经走了多远
        （战斗掷骰 `self.rng.randint(1,6)`、地块命名都在这条流上）。于是同一 seed
        两局对不上——地图不是种子的静态属性，「种子可复现」形同虚设。地形早在
        `tile_terrain` 里定死了，资源补上同一套：**开局就排布好，不由开图决定**。

        2026-09-11 补完剩下那一半：**地块命名也搬出了共享流**（见 `_new_tile`）。
        当时只修了资源、漏了命名，而命名是**每建一格都消耗**、且撞名重试导致
        **消耗个数不定**的 —— 战斗掷骰仍会被建地顺序带偏。现在 `self.rng`
        只剩战斗在用（外加开局布点），「同 seed 同战场」才真的成立。
        """
        from game import roll_resources
        return roll_resources(random.Random(f"{self.seed}:{x}:{y}:res"),
                              self.tile_terrain(x, y))

    def ter_char(self, x: int, y: int) -> str:
        t = self.tiles.get((x, y))
        ter = t["terrain"] if t else self.tile_terrain(x, y)
        return TERRAIN_CHARS[ter]

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
        """name 是否看得见 (x,y)：它本身或相邻格（含对角）有自家的地。"""
        cand = [(x, y)] + self.neighbors(x, y)
        for cx, cy in cand:
            if self.owned_by(cx, cy) == name:
                return True
        # 瞭望塔：己方任一瞭望塔半径 WATCHTOWER_RADIUS 圆（欧氏）内也可见（事件视野）
        for (tx, ty), t in self.tiles.items():
            if not t["buildings"].get("瞭望塔"):
                continue
            if t["owner"] != name:
                continue
            if (tx - x) ** 2 + (ty - y) ** 2 <= WATCHTOWER_RADIUS ** 2:
                return True
        return False

    # ------------------------------------------------------------- 看海日志
    def log(self, text: str, phase: str = "事件", nation: str | None = None,
            x: int | None = None, y: int | None = None) -> str:
        self.history.append({
            "turn": self.turn, "phase": phase, "nation": nation,
            "x": x, "y": y, "text": text,
        })
        return text

    def action(self, nation: str, tool: str, args: str, result: str, x=None, y=None):
        self.history.append({
            "turn": self.turn, "phase": "行动", "nation": nation,
            "x": x, "y": y,
            "text": f"{nation} ◇ {tool} {args} → {result}",
        })

    def events_for(self, name: str, limit: int = 14) -> list[str]:
        """该国能看到的近期事件（自己相关，或发生在视野内）。信件走信箱，此处不重复。"""
        out = []
        for h in reversed(self.history):
            if h["phase"] == "信件":
                continue
            if h["nation"] == name:
                out.append(f"[第{h['turn']}回合] {h['text']}")
            elif h.get("x") is not None and self.visible_to(name, h["x"], h["y"]):
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
        r = self.size * 0.31   # 环半径（占地图边长比例）
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
        margin = max(8, self.size // 6)
        pos = None
        if self.tiles:
            for _ in range(400):
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
        self.grid_short[name] = False
        self._assign_code(name)
        self._place_crosses({name: pos})
        if is_huns:
            self.apply_polity(name, "huns", home=pos, start=start)
        if extra:
            self.extra_prompt[name] = {"text": str(extra), "until": self.turn + 20,
                                       "summary": str(summary or "")}
        desc = ("匈奴" if is_huns else "国家") + f" {name} 登场（距各国至少 {margin} 格）"
        if is_huns:
            s = start or {}
            cav = int(s.get("骑", 6)); gold = int(s.get("黄金", 1000)); sup = int(s.get("补给", 200))
            desc += (f"：开局 {cav} 骑兵·金{gold}·补给{sup}·建筑+30%惩罚·骑兵征召8粮8装")
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
        gold = int(start.get("黄金", 1000))
        supply = int(start.get("补给", 200))
        cav = int(start.get("骑", 6))
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
        lv = eff[building]
        cost = info["cost"][lv] if info["kind"] == "castle" else info["cost"]
        label = f"城堡L{lv+1}" if info["kind"] == "castle" else building
        bp = TERRAIN_STATS[t["terrain"]]["build_penalty"]   # 地形施工惩罚（只上浮金价，木材不变）
        if bp:
            cost = cost * (100 + bp) // 100
        disc = ""
        if t["buildings"].get("工程院") and building != "工程院":
            cost = cost * (100 - ENGINEER_DISCOUNT) // 100   # 工程院：本地建造金价 -25%（只认已落成的）
            disc = f"，工程院-{ENGINEER_DISCOUNT}%"
        if self.polity.get(name) == "huns":
            cost = cost * 13 // 10   # 匈奴 +30% 建筑惩罚，乘算（不擅建设，靠抢）
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
            tile_cap = t["buildings"]["军屯"] - t.get("militia_recruited_this_turn", 0)
            if tile_cap <= 0:
                return False, "本回合该地块民兵征召产能已用完（每军屯 1 支/回合）"
            quota = self.nation_building_count(name, "军屯")
            alive = sum(1 for a in self.armies if a["owner"] == name and unit_kind(a) == "民")
            cap = min(tile_cap, quota - alive)
            if cap <= 0:
                return False, (f"民兵总数已达军屯编制上限（{alive}/{quota} 座）："
                               f"军屯即民兵编制——想扩编先建军屯，阵亡后方可补员")
        else:
            if self.grid_short.get(name):
                return False, "全国电网不足，高级建筑（含兵营）停摆，无法征兵"
            if t["buildings"]["兵营"] <= 0:
                return False, "该地块没有兵营"
            cap = t["buildings"]["兵营"] - t["recruited_this_turn"]
            if cap <= 0:
                return False, "本回合征召产能已用完（每兵营 1 支/回合）"
        n = min(n, cap)
        cost = UNIT_TYPES[kind]["recruit"]
        if kind == "骑" and self.polity.get(name) == "huns":
            cost = {"粮食": 8, "装备": 8}   # 匈奴骑兵征召只需 8 粮 8 装
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
        return self.nation_code.get(owner, 0) * 100_000_000 + seq, seq

    def _next_engage_seq(self) -> int:
        """递增的「入场序号」：军队每次 atk 参战领一个新号，用于野地索取顺序。"""
        self._engage_seq += 1
        return self._engage_seq

    def _claim_winner(self, alive: dict[str, list[dict]], *, attackers: set[str] | None = None) -> str | None:
        """该格的归属候选：进攻方中「无活敌」者，按入场序号取最早 atk 的——**索取者优先**；
        索取者已阵亡则顺位给最早入场的同盟者。无候选返回 None。
        attackers=本场战斗的进攻方集合（战斗结算用；此时 engaged 标记可能已被清扫）。
        国家间永久中立（无外交），故「无活敌」= 只是筛进攻方，多方之间不再互相交战。"""
        claim = []
        for F, fs in alive.items():
            if F == "野人":
                continue
            if attackers is not None and F not in attackers:
                continue
            claim.append(F)
        if not claim:
            return None
        return min(claim, key=lambda F: min((a.get("engage_seq", 10 ** 9) for a in alive[F]),
                                            default=10 ** 9))

    def _army(self, name: str, aid: int) -> dict | None:
        return next((a for a in self.armies if a["owner"] == name and a["id"] == aid), None)

    def nation_armies(self, name: str) -> list[dict]:
        return [a for a in self.armies if a["owner"] == name]

    def _defs_at(self, name: str, x: int, y: int) -> list[dict]:
        """该格上「与你为敌」的守军。国家间永久中立：唯一的敌人是野人（且只守无主格）。"""
        owner = self.owned_by(x, y)
        if owner is not None:
            return []   # 有主之地：他国军队与你非敌（无外交、无战争）
        return [a for a in self.armies
                if a["owner"] == "野人" and (a["x"], a["y"]) == (x, y)]

    def can_enter(self, name: str, x: int, y: int) -> tuple[bool, str]:
        owner = self.owned_by(x, y)
        if owner is None or owner == name:
            return True, ""
        return False, f"不可入境：({x+1},{y+1}) 是「{owner}」的领土（国家间永久中立，他国领土不得进入）"

    def move(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        if a.get("engaged"):
            return False, f"{a['name']} 交战中，先 retreat 撤出"
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        speed = unit_speed(a)
        if max(abs(a["x"] - x), abs(a["y"] - y)) > speed:
            return False, f"{UNIT_TYPES[unit_kind(a)]['label']} 每回合只能移动 {speed} 格"
        if a.get("moved_turn") == self.turn:
            return False, "本回合已移动过"
        ok, why = self.can_enter(name, x, y)
        if not ok:
            return False, why
        # mv 合法地块 = 野地（含野人驻守格）/ 自家地。他国领土一律禁 mv。
        # 野地可直接走进/穿过——行军不打野人，野人只在被 atk 时才接战。
        # mv 只挪位置，不占地——占地一律走 atk
        a["x"], a["y"] = x, y
        a["moved_turn"] = self.turn
        return True, f"军队{a['id']} 移防 ({x+1},{y+1})"

    def attack(self, name: str, aids: list[int], x: int, y: int) -> tuple[bool, str]:
        """atk = 一次『进军占地』：派军队进目标格——
        有守军(野人/敌国军)就交战（打赢后按索取顺序占地）；格上**无任何军队**才直接进驻占领（空城/无主空地），
        他国领土一律不可进攻（国家间永久中立）。**野地例外**——和平驻守的第三方不参战、不占地，
        清场后直接进驻（驻守者回合末被遣返）。野地上有别人正在打野时不能插足。
        mv 只挪位置不占地；占地一律走 atk，没有特例。"""
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        owner = self.owned_by(x, y)
        if owner == name:
            return False, "目标是你自己的领土，不能进攻"
        if owner is not None:
            return False, f"({x+1},{y+1}) 是「{owner}」的领土：国家间永久中立，他国领土不可攻击"
        # 野地上别人正在打野 → 不能插足抢地：「不抢别人的战斗」（想旁观仍可 mv 过去，不参战）。
        if owner is None:
            busy = [a for a in self.armies
                    if (a["x"], a["y"]) == (x, y) and a.get("engaged") and a["owner"] not in ("野人", name)]
            if busy:
                other = busy[0]["owner"]
                return False, (f"({x+1},{y+1}) 有 {other}军正在打野，不能插足抢地——"
                               f"等这场战斗打完再 atk；旁观可以 mv 过去（不参战）")
        defs = self._defs_at(name, x, y)
        targets = [a for a in self.armies if a["owner"] == name and a["id"] in aids]
        if not targets:
            return False, f"未找到我方军队 {aids}"
        for a in targets:
            if a.get("engaged") and (a["x"], a["y"]) != (x, y):
                return False, (f"{a['name']} 正在交战中，不能离开战场改攻他处；"
                               f"想脱战先 retreat 军队id 目标格（会挨守军一击）")
            if max(abs(a["x"] - x), abs(a["y"] - y)) > unit_speed(a):
                return False, (f"{a['name']} 距 ({x+1},{y+1}) 超出 "
                               f"{UNIT_TYPES[unit_kind(a)]['label']} 移动范围（{unit_speed(a)} 格），冲不进去")
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
            return True, f"{ids} 冲入 ({x+1},{y+1}) 与{who}交战，之后每回合结算一轮；可 retreat 撤出"
        # 格上还有其他军队但不是你的敌人（他国和平驻守，国家间永久中立）：
        # 野地（无主）→ 和平驻守不产生任何权利、也不构成障碍：atk 只打野人，
        # 清场后直接进驻占地，驻守的第三方被挤走（回合末自动遣返）。
        squatters = sorted({a["owner"] for a in self.armies
                            if a["owner"] != name and a["hp"] > 0 and (a["x"], a["y"]) == (x, y)})
        # 格上无守军/敌人 → atk 进驻即占
        _ok, cmsg = self._conquer(x, y, name, "进驻占领", log_it=False)
        nm2 = self.tiles[(x, y)]["name"]
        self.log(f"{name} {ids} 进驻 ({x+1},{y+1})，{cmsg}", phase="领土", nation=name, x=x, y=y)
        note = (f"（{'、'.join(squatters)}军未参战，回合末自动遣返）" if squatters else "")
        return True, f"{ids} 进驻 ({x+1},{y+1})，敌人为 0，{cmsg}{note}"

    def _retreat_legal(self, name: str, x: int, y: int) -> bool:
        """撤退合法点：无人荒地 / 己方领土。"""
        o = self.owned_by(x, y)
        return o is None or o == name

    def retreat(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        """撤出：与 mv/atk 同一个『每回合一次移动』额度。
        交战中的军队（含防守方守军）都能用；撤退固定只能退相邻 1 格（3×3，所有人，不按兵种速度）；
        目标限 己方/无人荒地；四周没有合法撤退点则不能撤退。
        撤退不立刻结算：军队留在战场参与本回合末的战斗结算（伤害全场分摊；防御方撤退减伤
        RETREAT_DEF_COVER%），结算后自动脱离到目标格——避免『每撤一支各吃一次全额』的灾难。"""
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        in_battle = bool(a.get("engaged"))
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
        # 没有合法撤退点则不能撤退（目标限 己方/无人荒地）
        if not any(self._retreat_legal(name, nx, ny)
                   for nx, ny in self.neighbors(a["x"], a["y"])):
            return False, "四周没有合法撤退点（己方/无人荒地），无法撤退"
        if a.get("moved_turn") == self.turn:
            return False, f"{a['name']} 本回合已移动/进攻过，移动额度已用尽，撤不出（下回合再撤）"
        if not self._retreat_legal(name, x, y):
            return False, "不能撤到他国领土；只能撤向 己方/无人荒地"
        # 撤退不立刻结算：标记 retreat_to 留在原地，本回合结束时随战斗结算走正常战斗机制
        # （敌方伤害全场分摊，撤退者在场照常吃自己那份；防御方撤退减伤 RETREAT_DEF_COVER%），
        # 结算后自动脱离到目标格（见 resolve_turn 撤退落地）。
        # 防御方减伤：我方在该格「未参战」（不是进攻方）、或本身就是格主 → 守方，撤退减伤
        # RETREAT_DEF_COVER%（谁挨打谁是守方，野地和平驻军同理）；主动进攻方撤退是全额。
        holder = self.owned_by(a["x"], a["y"])
        attacking = any(m["owner"] == name and m.get("engaged") and (m["x"], m["y"]) == (a["x"], a["y"])
                        for m in self.armies)
        cover = RETREAT_DEF_COVER if (not attacking or holder == name) else 100
        a["retreat_to"] = [x, y]
        a["retreat_cover"] = cover
        a["moved_turn"] = self.turn
        note = f"（防御方撤退，结算减伤 {100 - cover}%）" if cover < 100 else ""
        return True, (f"{a['name']} 准备撤到 ({x+1},{y+1}){note}：本回合结束时随战斗结算"
                      f"（全场分摊）后自动脱离；结算期间仍在战场")

    # ------------------------------------------------------------- 战斗
    def _die(self):
        d = self.rng.randint(1, 6)
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
        cd = castle * CASTLE_DEFENSE_PER_LEVEL
        return 100 - ((100 - td) * (100 - cd)) // 100

    @staticmethod
    def _modtxt(mods: dict[str, int]) -> str:
        """每方骰修正的简短文本：如「甲+5% 乙-15%」。"""
        return " ".join(f"{F}{m:+d}%" for F, m in sorted(mods.items())) or "—"

    def _resolve_battles(self) -> list[str]:
        """每格每回合的交战结算：**唯一的敌人是野人**（国家间永久中立，各国之间永不互战）。
        每方掷自己的骰；野人只守无主格、只打"进攻方"（不打扰和平停驻者）；地形/城堡减伤只给守方；
        同格多方同时出手再统一结算阵亡；占地 = 野人已清后按索取顺序的第一个进攻方。"""
        lines: list[tuple[int, int, str]] = []  # (x, y, 战报行)——带坐标才进得了事件视野
        engaged = [a for a in self.armies if a.get("engaged") and a["owner"] != "野人"]
        for (x, y) in sorted({(a["x"], a["y"]) for a in engaged}):
            tag = f"({x+1},{y+1}){self.ter_char(x, y)}"
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
            # 敌人关系：非野人势力唯一敌人=无主格野人（且只在自己是进攻方时）；野人=只打进攻方
            def _enemies(F: str) -> list[str]:
                if F == "野人":
                    return [G for G in attacker if G != "野人"]
                if F in attacker and "野人" in forces:
                    return ["野人"]
                return []
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
                # ---- 有主之地：国家间永久中立，他国军队根本进不来、也打不了（can_enter/attack 双拦）。
                # 此分支只在异常状态（如手改存档）下兜底：只报现场，绝不改旗。
                lines.append((x, y, f"⚔ ({x+1},{y+1}) 出现异常交战：{desc_alive or '无人'}"
                                     f"（他国领土，永久中立下不应发生）"))
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
        self.summaries.pop(name, None)
        self.summary_blocks.pop(name, None)
        self.turn_memory.pop(name, None)
        self.ledger.pop(name, None)          # 账本是本期暂态；已出的 econ_reports 留作历史
        self.plans.pop(name, None)
        self.polity.pop(name, None)
        self.extra_prompt.pop(name, None)
        return True

    # ------------------------------------------------------------- 经济报表
    def _ledger(self, n: str) -> dict:
        """取（或新建）该国本期经济账本。新建时记下起始回合——续档/中途登场会得到
        一个不完整的第一期，结账时按**实际覆盖回合数**平均，不能一律 ÷10。"""
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

    def _close_report_period(self) -> None:
        """把本期账本结成一期快照（覆盖最近 REPORT_EVERY 回合），并清零账本。
        只在回合结算末尾调用——AI 没有任何手动触发入口。"""
        for n in self.alive():
            led = self._ledger(n)
            start = int(led.get("_since", self.turn - REPORT_EVERY + 1))
            days = max(1, min(REPORT_EVERY, self.turn - start + 1))   # 不完整首期按实际天数
            gdp = (led["prod_value"] - led["mid_value"] - led["fuel_value"]
                   + led["gold_in"]) / days                      # 每回合平均（市价，不含军费）
            # 军费 = 本期军队实际消耗的补给 ÷10 × 现价（不看来源：自产/外购一视同仁）
            military = led["supply_eaten"] / days * self.prices.get("补给", float(MARKET["补给"]))
            invest = led["invest_gold"] + led["invest_wood_value"]
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
                "supply_eaten": int(led["supply_eaten"]),
                "trade_ratio": round(trade, 4),
            }
            self.econ_reports.setdefault(n, []).append(snap)
            # 公告一条（看海终端可见；该国在【近讯】里也能看到 → 提醒它去 report 查）
            mr = snap["military_ratio"]
            self.log(f"📊 第 {snap['report_turn']} 回合经济报表已生成："
                     f"GDP {snap['gdp']:.1f}/回合、军费占 GDP "
                     f"{f'{mr * 100:.0f}%' if mr is not None else '—'}、"
                     f"总资产 {snap['assets']:.0f}（report 看明细 / report all=true 看趋势）",
                     phase="内政", nation=n)
        self.ledger = {}

    # ------------------------------------------------------------- 回合结算
    def resolve_turn(self) -> dict:
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
                if kind in ("extract", "militia_camp"):
                    # 军屯=屯田：同样按 outputs 产粮（不耗电、不占电网维持）
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
                # 市政厅：每座 = 基础 TOWN_HALL_GOLD + 该地块已占建筑位(不含自身)×PER_SLOT 金；电网不足即停摆
                for (hx, hy), ht in self.tiles.items():
                    if ht["owner"] != n:
                        continue
                    h = ht["buildings"].get("市政厅", 0)
                    if h:
                        others = sum(ht["buildings"].values()) - h
                        hall_gain = (TOWN_HALL_GOLD + others * TOWN_HALL_PER_SLOT) * h
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
            for a in list(self.armies):
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
            self.log(f" {cleared} 支军队解除交战（野人已清/敌军已撤离）", phase="战报")

        # 5) 领土易主 → 滞留他国领土的军队自动遣返（让位；无主野地不算非法）
        self._withdraw_illegal()

        # 6) 市场：按本回合世界供需算均衡价，市价向均衡价回归
        self._update_market()

        # 6.5) 在建建筑落地（施工 1 回合）：本回合结算不产出，落地后从下回合开始生效
        for t in self.tiles.values():
            p = t.get("pending") or {}
            for k, c in p.items():
                if c:
                    t["buildings"][k] += c
            t["pending"] = {k: 0 for k in BUILDINGS}

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
                self._close_report_period()
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
        唯一的敌人是野人；覆盖「野人已清」与任何路径留下的空交战标记。返回解除数。"""
        cleared = 0
        for a in self.armies:
            if not a.get("engaged"):
                continue
            if a["owner"] == "野人":
                # 野人只与「交战中的进攻方」为敌，和平停驻者不算
                foes = [d for d in self.armies
                        if d is not a and (d["x"], d["y"]) == (a["x"], a["y"])
                        and d["owner"] != "野人" and d.get("engaged")]
            else:
                foes = [d for d in self.armies
                        if d is not a and (d["x"], d["y"]) == (a["x"], a["y"])
                        and d["owner"] == "野人"]
            if not foes:
                a["engaged"] = False
                cleared += 1
        return cleared

    def _withdraw_illegal(self):
        """身处**他国领土**的军队，每回合按兵种速度朝最近的自家地撤（步 1 格/骑 2 格，一步步走）。
        合法落点只有「自家地 / 无主野地」——领土易主（别人 atk 占了你脚下的野地）后，
        原先合法的驻军就成了非法滞留，回合末自动让位。"""
        for n in list(self.alive()):
            legal_tiles = self.own_tiles(n)
            if not legal_tiles:
                continue
            for a in list(self.armies):
                if a["owner"] != n or a.get("engaged"):
                    continue
                owner = self.owned_by(a["x"], a["y"])
                if owner is None or owner == n:
                    continue  # 合法
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
                    self.log(f"🚶 {n} 军队{a['id']} 自他国领土「遣返」撤向合法地（{a['x']+1},{a['y']+1}）",
                             phase="事件", nation=n, x=a["x"], y=a["y"])

    def _enterable_step(self, n: str, x: int, y: int, forced: bool = False) -> bool:
        """军队能否落步到 (x,y)。forced=True 供强制遣返用：只允许落在合法地会把它永远困死，
        故遣返途中允许踩过中立地，一路走回自家地。"""
        if forced:
            return True
        o = self.owned_by(x, y)
        return o is None or o == n

    # ------------------------------------------------------------- 回合推进
    def begin_turn(self) -> None:
        """开始下一回合（无外交 → 没有投信/馈赠/换图/间谍在途，开局就是推进回合号）。"""
        self.turn += 1

    # ------------------------------------------------------------- 市场
    def market_price(self, good: str) -> float:
        return round(self.prices[good], 1)

    def market_depth(self, good: str) -> int:
        """该商品的市场深度（单位数）：每卖光这么多单位，市价大约被压掉「基准价×PRICE_IMPACT」。
        深度 = MARKET_DEPTH[g] × max(1, 现存国家数) ÷ 4——国家越多市场越深（4 国为基准档）。"""
        return max(1, round(MARKET_DEPTH[good] * max(1, len(self.alive())) / 4))

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


    # ------------------------------------------------------------- 存档
    def save(self, path: str | Path) -> None:
        data = {
            "size": self.size, "seed": self.seed, "turn": self.turn,
            "rng_state": list(self.rng.getstate()),
            "nations": {n: nat.res for n, nat in self.nations.items()},
            "order": self.order,
            "tiles": {f"{x},{y}": t for (x, y), t in sorted(self.tiles.items())},
            "armies": self.armies, "next_army_seq": self.next_army_seq,
            "nation_code": self.nation_code,
            "guard_once": [list(k) for k in sorted(self.guard_once)],
            "summaries": self.summaries,
            "summary_blocks": self.summary_blocks,
            "turn_memory": self.turn_memory,
            "plans": self.plans,
            "polity": self.polity,
            "extra_prompt": self.extra_prompt,
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
        # 原子写（tmp+rename）：turn_memory 使存档变大近一倍，避免写一半中断损坏档
        tmp = Path(str(path) + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: str | Path) -> "World":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        w = cls(size=data["size"], seed=data["seed"], gen=False)   # 空壳：世界由存档整体还原
        w.turn = data.get("turn", 0)
        ver, internal, gauss = data["rng_state"]
        w.rng.setstate((ver, tuple(internal), gauss))
        w.nations = {n: Nation(n, res) for n, res in data.get("nations", {}).items()}
        w.order = data.get("order") or list(w.nations)
        w.summaries = {}
        for n, lst in data.get("summaries", {}).items():
            if n not in w.nations:
                continue
            rows = []
            for item in lst:
                if isinstance(item, dict):  # 新格式 {turn,text}
                    rows.append({"turn": int(item.get("turn", 0)), "text": str(item.get("text", ""))})
                else:  # 旧格式字符串 "第X回合：..." → 迁移
                    m = re.match(r"^第(\d+)回合[:：]\s*(.*)$", str(item))
                    rows.append({"turn": int(m.group(1)) if m else 0,
                                 "text": (m.group(2) if m else str(item)).strip()})
            w.summaries[n] = rows
        w.summary_blocks = {n: list(v) for n, v in data.get("summary_blocks", {}).items()
                            if n in w.nations}
        w.turn_memory = {n: list(v) for n, v in data.get("turn_memory", {}).items() if n in w.nations}
        w.plans = {n: dict(v) for n, v in data.get("plans", {}).items() if n in w.nations}
        w.polity = {n: v for n, v in data.get("polity", {}).items() if n in w.nations}
        w.extra_prompt = {n: dict(v) for n, v in data.get("extra_prompt", {}).items() if n in w.nations}
        w.armies = data.get("armies", [])
        # 野地索取顺序计数器：从档内现有最大入场序号续起（旧档无此字段 → 0，缺失序号当最大）
        w._engage_seq = max((int(a.get("engage_seq", 0) or 0) for a in w.armies), default=0)
        # 军队编号：各国独立番号(AI 所见) + 国家码×1e8 全局唯一 gid(内部)。旧档按旧全局 id 顺序迁移重编。
        if "nation_code" in data:
            w.nation_code = {k: int(v) for k, v in data["nation_code"].items()}
            w._next_code = max(w.nation_code.values(), default=0) + 1
        else:
            w.nation_code = {}
            w._next_code = 1
            w._assign_code("野人")
            for nm in w.nations:
                w._assign_code(nm)
        if "next_army_seq" in data:
            w.next_army_seq = {k: int(v) for k, v in data["next_army_seq"].items()}
            for a in w.armies:
                a.setdefault("gid", w.nation_code.get(a["owner"], 0) * 100_000_000 + a["id"])
        else:
            w.next_army_seq = {}
            for a in sorted(w.armies, key=lambda x: x["id"]):
                s = w.next_army_seq.get(a["owner"], 0) + 1
                w.next_army_seq[a["owner"]] = s
                a["id"] = s
                a["gid"] = w.nation_code.get(a["owner"], 0) * 100_000_000 + s
                if a["owner"] != "野人":
                    a["name"] = army_name(a["owner"], s, a.get("type", "步"))
        w.prices = {g: float(data.get("prices", {}).get(g, MARKET[g])) for g in TRADEABLE}
        w.equilibrium = {g: float(data.get("equilibrium", {}).get(g, MARKET[g])) for g in TRADEABLE}
        w.flow_in = {g: int(data.get("flow_in", {}).get(g, 0)) for g in TRADEABLE}
        w.flow_out = {g: int(data.get("flow_out", {}).get(g, 0)) for g in TRADEABLE}
        w.history = data.get("history", [])
        w.history_seen = data.get("history_seen", 0)
        # 电网/结算摘要也持久化：否则续档后第一回合 all 面板电力 0、上回合结算丢失
        w.grid_short = {n: bool(v) for n, v in data.get("grid_short", {}).items() if n in w.nations}
        w.energy_report = {n: tuple(v) for n, v in data.get("energy_report", {}).items() if n in w.nations}
        w.econ_summary = {n: s for n, s in data.get("econ_summary", {}).items() if n in w.nations}
        # 经济报表：已出的期数 + 本期未结账本（旧档没有 → 空，从下个报表回合开始积累）
        w.econ_reports = {n: list(v) for n, v in data.get("econ_reports", {}).items()
                          if n in w.nations}
        w.ledger = {n: {**{k: float(v.get(k, 0) or 0) for k in LEDGER_FIELDS},
                        "_since": max(1, int(v.get("_since", w.turn)))}
                    for n, v in data.get("ledger", {}).items() if n in w.nations}
        # 总消费：旧档没有此字段 → 空（从本档开始累计）
        w.spend = {n: {k: float(v.get(k, 0) or 0) for k in SPEND_FIELDS}
                   for n, v in data.get("spend", {}).items() if n in w.nations}
        for k, t in data["tiles"].items():
            x, y = map(int, k.split(","))
            t.setdefault("recruited_this_turn", 0)
            t.setdefault("built_this_turn", 0)
            t.setdefault("buildings", {})
            t.setdefault("pending", {})
            t.setdefault("core", t.get("owner"))  # 旧档迁移：现有持有追认为核心领土
            for name in BUILDINGS:  # 旧档迁移：补全新增建筑(如市政厅)的键
                t["buildings"].setdefault(name, 0)
                t["pending"].setdefault(name, 0)
            w.tiles[(x, y)] = t
        w.guard_once = {tuple(k) for k in data.get("guard_once", [])}
        w._ensure_guardians()
        return w


class Nation:
    def __init__(self, name: str, res: dict[str, int] | None = None):
        self.name = name
        self.res = dict(START_RES if res is None else res)

    def __repr__(self):
        return f"<Nation {self.name}>"
