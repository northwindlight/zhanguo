# -*- coding: utf-8 -*-
"""多国引擎（MP）。

多个国家共存于同一张地图，各自经营（机制与单机玩家一致，无作弊入口）：
  - 每国独立 国库(黄金)/战略储备/电网(不存储)/军队/国土/信箱；
  - 无人地带的视野内地块由「野人」把守，打赢即拓疆；国家间打赢即夺地，
    空城被敌军队踏入即陷（无防即失）；
  - 移动/攻击受国家关系约束：中立（非同盟非交战）不能入境、不能攻击；
  - 外交：结盟(互通不可攻)/断盟(军队全部撤出)/保障独立/共同防御/宣战(对方必须接受，
    被保障方与共同防御方自动参战)/求和(可赔款/索款/白和)；
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
    ARMY_ATTACK_DAMAGE,
    ARMY_HEAL_PER_TURN,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    CASTLE_DEFENSE_PER_LEVEL,
    COMBAT_DIE_MOD,
    MARKET,
    MAX_SLOTS,
    PRICE_MAX_RATIO,
    PRICE_MIN,
    PRICE_REVERT,
    PRICE_TICK_RATIO,
    RESOURCES,
    TERRAIN_CHARS,
    TERRAIN_STATS,
    TOWN_HALL_GOLD,
    TOWN_HALL_PER_SLOT,
    TRADEABLE,
    UNIT_TYPES,
    army_name,
    roll_resources,
    roll_tile_name,
    unit_kind,
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

SPY_COST = 20     # 经济间谍 花 20 金
SPY_TURNS = 2     # 2 回合后回报目标全部经济情报


def _pair(a: str, b: str) -> frozenset:
    return frozenset((a, b))


class World:
    def __init__(self, size: int = 80, seed: int | None = None, *,
                 nations: list[str] | None = None,
                 starts: dict[str, tuple[int, int]] | None = None,
                 res: dict[str, dict[str, int]] | None = None):
        self.size = size
        self.seed = seed if seed is not None else random.randrange(1 << 31)
        self.rng = random.Random(self.seed)
        self.turn = 0
        self.tiles: dict[tuple[int, int], dict] = {}
        self.nations: dict[str, "Nation"] = {}
        self.order: list[str] = []
        self.wars: list[frozenset] = []
        self.alliances: list[frozenset] = []
        self.defense_pacts: list[frozenset] = []
        self.guarantees: dict[str, set[str]] = {}
        self.mail_pending: list[dict] = []
        self.mailbox: dict[str, list[dict]] = {}
        self.summaries: dict[str, list[str]] = {}  # 各国近 10 回合小结纪事（私有，本国 AI 记忆）
        self.gift_pending: list[dict] = []         # 馈赠在途（下回合到账）
        self.map_pending: list[dict] = []          # 交换地图在途（下回合到账）
        self.maps: dict[str, list[dict]] = {}      # 各国收到的地图情报（{from,turn,text}，留最近3张）
        self.spy_pending: list[dict] = []          # 经济间谍在途（2回合后回报）
        self.econ_intel: dict[str, list[dict]] = {}  # 各国收到的经济情报（{from,turn,text}，留最近2份）
        self.peace_offers: list[dict] = []
        self.proposals: list[dict] = []
        self._offer_id = 1
        self.prices: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}
        self.armies: list[dict] = []
        self.next_army_id = 1
        self.guard_once: set[tuple[int, int]] = set()  # 每格至多出生一支野人：死了就没了，不重生
        self.grid_short: dict[str, bool] = {}
        self.energy_report: dict[str, tuple[int, int, bool]] = {}
        self.econ_summary: dict[str, str] = {}   # 上一回合结算摘要（各国 agent 看）
        self.history: list[dict] = []
        self.history_seen = 0
        names = list(nations or ["秦", "楚", "齐"])
        for nm in names:
            self.nations[nm] = Nation(nm, (res or {}).get(nm))
            self.order.append(nm)
            self.mailbox[nm] = []
            self.grid_short[nm] = False
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

    def tile_by_name(self, name: str) -> tuple[int, int] | None:
        for (x, y), t in self.tiles.items():
            if t.get("name") == name:
                return (x, y)
        return None

    def visible_to(self, name: str, x: int, y: int) -> bool:
        """name 是否看得见 (x,y)：它本身或相邻格（含对角）有自家的地。"""
        cand = [(x, y)] + self.neighbors(x, y)
        return any(self.owned_by(cx, cy) == name for cx, cy in cand)

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
            "resources": roll_resources(self.rng, terrain),
            "buildings": {b: 0 for b in BUILDINGS},
            "pending": {b: 0 for b in BUILDINGS},  # 在建（下回合才生效）
            "name": roll_tile_name(self.rng, used),
            "recruited_this_turn": 0,
            "built_this_turn": 0,
        }

    def _place_crosses(self, starts: dict[str, tuple[int, int]]):
        for nm, (cx, cy) in starts.items():
            for dx, dy in CROSS:
                x, y = cx + dx, cy + dy
                if 0 <= x < self.size and 0 <= y < self.size and (x, y) not in self.tiles:
                    self.tiles[(x, y)] = self._new_tile(x, y, nm)

    def _place_ring(self, names: list[str]):
        """各国环状开局（3 国=三角）。"""
        n = len(names)
        c = self.size / 2
        r = min(self.size * 0.34, self.size * 0.31)
        pts = {}
        for i, nm in enumerate(names):
            ang = -math.pi / 2 + i * 2 * math.pi / n
            pts[nm] = (int(round(c + r * math.cos(ang))), int(round(c + r * math.sin(ang))))
        self._place_crosses(pts)

    def _ensure_guardians(self):
        for nm in self.alive():
            for x, y in self.frontier_of(nm):
                if (x, y) in self.tiles or (x, y) in self.guard_once:
                    continue
                if any(a["owner"] == "野人" and (a["x"], a["y"]) == (x, y) for a in self.armies):
                    continue
                self._spawn_guardian(x, y)

    def _spawn_guardian(self, x: int, y: int):
        aid = self.next_army_id
        self.next_army_id += 1
        self.armies.append({"id": aid, "name": f"野人{aid}", "hp": ARMY_MAX_HP,
                            "x": x, "y": y, "owner": "野人", "moved_turn": -1, "engaged": False})
        self.guard_once.add((x, y))  # 出生过就算数：这格野人死了不再有

    def _drop_guardians(self, x: int, y: int):
        self.armies = [a for a in self.armies
                       if not (a["owner"] == "野人" and (a["x"], a["y"]) == (x, y))]

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
        if cr is not None and eff[building] >= t["resources"][cr]:
            return False, f"{building} 已达上限：本地 {cr}={t['resources'][cr]}"
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
        wood = info["wood"]
        if self.res(name, "黄金") < cost:
            return False, f"黄金不足：{label} 需 {cost}，国库 {self.res(name,'黄金')}"
        if self.res(name, "木头") < wood:
            return False, f"木材不足：{label} 需 {wood}，储备 {self.res(name,'木头')}"
        self.add_res(name, "黄金", -cost)
        self.add_res(name, "木头", -wood)
        t["pending"][building] += 1  # 在建，回合末才落地
        t["built_this_turn"] = 1
        tile_name = t["name"]
        return True, f"动工 {label}（@{tile_name}，本回合在建、下回合生效），-{cost}金 -{wood}木"

    def recruit(self, name: str, x: int, y: int, n: int = 1, kind: str = "步") -> tuple[bool, str]:
        t = self.tiles.get((x, y))
        if kind not in UNIT_TYPES:
            return False, f"未知兵种：{kind}（可选：{'、'.join(UNIT_TYPES)}）"
        if name not in self.nations:
            return False, f"国家 {name} 不存在"
        if t is None or t["owner"] != name:
            return False, "只能在自己有兵营的地块征兵"
        if self.grid_short.get(name):
            return False, "全国电网不足，高级建筑（含兵营）停摆，无法征兵"
        if t["buildings"]["兵营"] <= 0:
            return False, "该地块没有兵营"
        cap = t["buildings"]["兵营"] - t["recruited_this_turn"]
        if cap <= 0:
            return False, "本回合征召产能已用完（每兵营 1 支/回合）"
        n = min(n, cap)
        cost = UNIT_TYPES[kind]["recruit"]
        n = min(n, min(self.res(name, f) // amt for f, amt in cost.items()))
        if n <= 0:
            return False, "战略储备不足（每支耗 " + "、".join(f"{f}x{a}" for f, a in cost.items()) + "）"
        for f, amt in cost.items():
            self.add_res(name, f, -amt * n)
        seq = sum(1 for a in self.armies if a["owner"] == name and unit_kind(a) == kind) + 1
        for i in range(n):
            aid = self.next_army_id
            self.next_army_id += 1
            self.armies.append({"id": aid, "name": army_name(name, seq + i, kind),
                                "type": kind, "hp": ARMY_MAX_HP, "x": x, "y": y,
                                "owner": name, "moved_turn": -1, "engaged": False})
        t["recruited_this_turn"] += n
        return True, f"征召 {n} 支{UNIT_TYPES[kind]['label']} @{t['name']}"

    # ------------------------------------------------------------- 军队
    def _army(self, name: str, aid: int) -> dict | None:
        return next((a for a in self.armies if a["owner"] == name and a["id"] == aid), None)

    def nation_armies(self, name: str) -> list[dict]:
        return [a for a in self.armies if a["owner"] == name]

    def _defs_at(self, name: str, x: int, y: int) -> list[dict]:
        owner = self.owned_by(x, y)
        out = []
        for a in self.armies:
            if (a["x"], a["y"]) != (x, y) or a["owner"] == name:
                continue
            if a["owner"] == "野人":
                if owner is None:
                    out.append(a)
                continue
            if self.war_between(name, a["owner"]):
                out.append(a)
        return out

    def can_enter(self, name: str, x: int, y: int) -> tuple[bool, str]:
        owner = self.owned_by(x, y)
        if owner is None:
            return True, ""
        if owner == name:
            return True, ""
        if self.allied_between(name, owner):
            return True, "（盟国领土，通行无碍）"
        if self.war_between(name, owner):
            return True, "（敌国领土，交战可入）"
        return False, f"中立不可入境：({x+1},{y+1}) 是「{owner}」的领土（结盟或宣战后才能进出）"

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
        # mv 只挪位置，不占地——占地走 atk
        a["x"], a["y"] = x, y
        a["moved_turn"] = self.turn
        return True, f"军队{a['id']} 移防 ({x+1},{y+1})"

    def attack(self, name: str, aids: list[int], x: int, y: int) -> tuple[bool, str]:
        """atk = 一次『进军占地』：派军队进目标格——
        有守军(野人/敌国军)就交战（打赢自动占地）；敌人=0 就直接进驻占领（空城/无主空地）。
        mv 只挪位置不占地；占地一律走 atk，没有特例。"""
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        owner = self.owned_by(x, y)
        if owner is not None and (owner == name or self.allied_between(name, owner)):
            return False, "目标是自己或盟国的领土，不能进攻"
        if owner is not None and owner != name and not self.war_between(name, owner):
            return False, "中立不可攻击他国领土"
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
            who = "野人" if defs[0]["owner"] == "野人" else f"{defs[0]['owner']}军"
            return True, f"{ids} 冲入 ({x+1},{y+1}) 与{who}交战，之后每回合结算一轮；可 retreat 撤出"
        # 敌人=0：atk 进驻即占
        self._conquer(x, y, name, "进驻占领", log_it=False)
        nm2 = self.tiles[(x, y)]["name"]
        self.log(f"{name} {ids} 进驻 ({x+1},{y+1})，占领「{nm2}」", phase="领土", nation=name, x=x, y=y)
        return True, f"{ids} 进驻 ({x+1},{y+1})，敌人为 0，占领「{nm2}」"

    def retreat(self, name: str, aid: int, x: int, y: int) -> tuple[bool, str]:
        """撤出：与 mv/atk 同一个『每回合一次移动』机制。
        只能在交战中用；目标限 己方/同盟/无人荒地（中立与敌国格都不行）；
        用掉本回合的移动并脱离交战；下回合起可正常行动。"""
        a = self._army(name, aid)
        if a is None:
            return False, f"军队 {aid} 不存在"
        if not a.get("engaged"):
            return False, f"{a['name']} 未在交战中"
        try:
            self._check(x, y)
        except IndexError as e:
            return False, str(e)
        speed = unit_speed(a)
        if (a["x"], a["y"]) == (x, y):
            return False, "撤出需选一个不同的格"
        if max(abs(a["x"] - x), abs(a["y"] - y)) > speed:
            return False, f"撤出范围超出 {UNIT_TYPES[unit_kind(a)]['label']} 移动距离（{speed} 格）"
        if a.get("moved_turn") == self.turn:
            return False, f"{a['name']} 本回合已移动/进攻过，移动额度用尽，撤不出（下回合再撤）"
        o = self.owned_by(x, y)
        if o is not None and o != name and not self.allied_between(name, o):
            return False, "不能撤到敌国或中立国的格子；只能撤向 己方/同盟/无人荒地"
        defs = self._defs_at(name, a["x"], a["y"])
        hurt = ""
        if defs:
            _d, mod = self._die()
            dmg = self._round_damage(self._combat_power(len(defs), 0), mod)
            a["hp"] -= dmg
            hurt = f"，撤出时挨守军一击 -{dmg}HP"
            if a["hp"] <= 0:
                self.armies.remove(a)
                return False, f"{a['name']} 撤出时被守军击杀{hurt}"
        a["engaged"] = False
        a["x"], a["y"] = x, y
        a["moved_turn"] = self.turn
        return True, f"{a['name']} 撤到 ({x+1},{y+1}){hurt}，脱离交战；下回合可正常行动"

    # ------------------------------------------------------------- 战斗
    def _die(self):
        d = self.rng.randint(1, 6)
        return d, COMBAT_DIE_MOD[d]

    @staticmethod
    def _combat_power(n: int, def_pct: int) -> int:
        return max(1, n * ARMY_ATTACK_DAMAGE * (100 - def_pct) // 100)

    @staticmethod
    def _round_damage(power: int, mod: int) -> int:
        return max(1, power * (100 + mod) // 100)

    @staticmethod
    def _spread(dmg: int, units: list[dict]):
        per, rem = divmod(dmg, len(units))
        for i, u in enumerate(units):
            u["hp"] -= per + (1 if i < rem else 0)

    def _defense_pct(self, x: int, y: int, def_owner: str | None) -> int:
        t = self.tiles.get((x, y))
        terrain = t["terrain"] if t else self.tile_terrain(x, y)
        castle = t["buildings"]["城堡"] if (t and t["owner"] == def_owner) else 0
        return TERRAIN_STATS[terrain]["defense"] + castle * CASTLE_DEFENSE_PER_LEVEL

    def _resolve_battles(self) -> list[str]:
        lines: list[str] = []
        engaged = [a for a in self.armies if a.get("engaged") and a["owner"] != "野人"]
        for (x, y) in sorted({(a["x"], a["y"]) for a in engaged}):
            # 逐格从当前军队表重算（中途可能有国被灭/军队阵亡，避免引用幽灵）
            atks = [a for a in self.armies if a.get("engaged") and a["owner"] != "野人"
                    and a["x"] == x and a["y"] == y and a["hp"] > 0]
            if not atks:
                continue
            owner = self.owned_by(x, y)
            atk_ns = sorted({a["owner"] for a in atks})
            defs = []
            for a in self.armies:
                if (a["x"], a["y"]) != (x, y) or a["hp"] <= 0:
                    continue
                if a["owner"] == "野人":
                    if owner is None:
                        defs.append(a)
                elif any(self.war_between(an, a["owner"]) for an in atk_ns):
                    defs.append(a)
            if not defs:
                for a in atks:
                    a["engaged"] = False
                continue
            def_owner = next((a["owner"] for a in defs if a["owner"] != "野人"), None)
            tag = f"({x+1},{y+1}){self.ter_char(x, y)}"
            d, mod = self._die()
            # 同时出手：双方按开战兵力全力互击，再一起结算阵亡（允许同归于尽）
            atk_dmg = self._round_damage(self._combat_power(len(atks), self._defense_pct(x, y, def_owner)), mod)
            ret = self._round_damage(self._combat_power(len(defs), 0), mod)
            self._spread(atk_dmg, defs)
            self._spread(ret, atks)
            died = [a for a in defs if a["hp"] <= 0]
            for a in died:
                if a in self.armies:
                    self.armies.remove(a)
            died_a = [a for a in atks if a["hp"] <= 0]
            for a in died_a:
                if a in self.armies:
                    self.armies.remove(a)
            alive_def = [a for a in defs if a["hp"] > 0]
            alive_a = [a for a in atks if a["hp"] > 0]
            if not alive_def and not alive_a:
                # 同归于尽：谁也不占。野人死了就是无主空地（该格已出生过守军、不再重生），谁都能来占
                lines.append(
                    f"⚔ 同归于尽 @{tag}（骰{d}）：守军 {len(died)} 支与我军 {len(died_a)} 支同回合全灭"
                    + ("——此地成无主空地，可直接占领" if owner is None else "——城仍在敌手")
                )
            elif not alive_def:
                winner = atk_ns[0]
                for a in atks:
                    a["engaged"] = False
                ok, msg = self._conquer(x, y, winner, "攻陷")
                lines.append(f"⚔ 全歼守军 @{tag}，{msg}")
            elif not alive_a:
                desc_d = "、".join(f"{a['name']}[{a['hp']}hp]" for a in alive_def)
                lines.append(f"⚔ 攻方全灭 @{tag}（骰{d}）守军余 {desc_d}")
            else:
                desc_a = "、".join(f"{a['name']}[{a['hp']}hp]" for a in alive_a)
                desc_d = "、".join(f"{a['name']}[{a['hp']}hp]" for a in alive_def)
                lines.append(f"⚔ 交火 @{tag}（骰{d} 修正{mod:+d}%）：攻方余 {desc_a}；守军余 {desc_d}")
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
            self.tiles[(x, y)]["owner"] = by
            msg = f"{by} {how}「{self.tiles[(x,y)]['name']}」({x+1},{y+1})"
        else:
            return False, "已是自己领土"
        if log_it:
            self.log(msg, phase="领土", nation=by, x=x, y=y)
        self._ensure_guardians()
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
        for lst in (self.wars, self.alliances, self.defense_pacts):
            self._remove_pair(lst, name)
        self.guarantees.pop(name, None)
        for s in self.guarantees.values():
            s.discard(name)
        self.mailbox.pop(name, None)
        self.summaries.pop(name, None)
        self.maps.pop(name, None)
        self.gift_pending = [g for g in self.gift_pending if g["from"] != name and g["to"] != name]
        self.map_pending = [m for m in self.map_pending if m["from"] != name and m["to"] != name]
        self.spy_pending = [s for s in self.spy_pending if s["from"] != name and s["to"] != name]
        self.econ_intel.pop(name, None)
        self.mail_pending = [m for m in self.mail_pending if m["to"] != name and m["from"] != name]
        self.peace_offers = [p for p in self.peace_offers if p["a"] != name and p["b"] != name]
        self.proposals = [p for p in self.proposals if p["a"] != name and p["b"] != name]
        return True

    @staticmethod
    def _remove_pair(lst: list, name: str):
        lst[:] = [p for p in lst if name not in p]

    # ------------------------------------------------------------- 回合结算
    def resolve_turn(self) -> dict:
        # 0) 刷新每地块每回合配额
        for t in self.tiles.values():
            t["recruited_this_turn"] = 0
            t["built_this_turn"] = 0

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
                    for g, amt in info["outputs"].items():
                        self.add_res(owner, g, amt * cnt)
                        prod[owner][g] += amt * cnt
                elif kind == "gold":
                    gain = info["outputs"].get("黄金", 0) * cnt * MARKET["黄金"]
                    self.add_res(owner, "黄金", gain)
                    gold_in[owner] += gain
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
                    for g, amt in info["outputs"].items():
                        self.add_res(n, g, amt * batches)
                        prod[n][g] += amt * batches
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

        # 3) 战争结算
        war_lines = self._resolve_battles()
        for ln in war_lines:
            self.log(ln, phase="战报")

        # 4) 军队补给 + 回复（每国吃自己的补给仓）
        famine = {}
        for n in self.alive():
            ps = self.nation_armies(n)
            need = sum(unit_supply(a) for a in ps)  # 步1/骑2 补给每回合
            paid = min(need, self.res(n, "补给"))
            self.add_res(n, "补给", -paid)
            short = need - paid
            if short:
                dead = []
                for a in ps:
                    a["hp"] -= ARMY_STARVE_DAMAGE
                    if a["hp"] <= 0:
                        dead.append(a)
                for a in dead:
                    if a in self.armies:
                        self.armies.remove(a)
                famine[n] = (short, len(dead))
                ps = self.nation_armies(n)
            engaged_ids = {id(a) for a in self.armies if a.get("engaged")}
            for a in list(self.armies):
                if a["owner"] != n:
                    continue
                if short or id(a) in engaged_ids:
                    continue
                a["hp"] = min(ARMY_MAX_HP, a["hp"] + ARMY_HEAL_PER_TURN)
        for n, (short, dead) in famine.items():
            self.log(f"⚠ {n} 补给断粮（缺 {short}）：{dead} 支军队饿毙", phase="内政", nation=n)

        # 5) 非法滞留 → 自动遣返（断盟/停战后必须撤出）
        self._withdraw_illegal()

        # 6) 市场向基准回归
        for g in TRADEABLE:
            base = MARKET[g]
            p = self.prices[g]
            self.prices[g] = round(min(max(base + (p - base) * PRICE_REVERT, PRICE_MIN),
                                       base * PRICE_MAX_RATIO), 3)

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
        return {"war_lines": war_lines, "famine": famine}

    def _withdraw_illegal(self):
        """断盟/停战后身处他国中立领土的军队，每回合朝最近的合法地(本国/盟国)撤 1 格。"""
        for n in list(self.alive()):
            legal_tiles = self.own_tiles(n)
            for m in self.alive():
                if m != n and self.allied_between(n, m):
                    legal_tiles += self.own_tiles(m)
            if not legal_tiles:
                continue
            for a in list(self.armies):
                if a["owner"] != n or a.get("engaged"):
                    continue
                owner = self.owned_by(a["x"], a["y"])
                if owner is None or owner == n or self.allied_between(n, owner) or self.war_between(n, owner):
                    continue  # 合法
                # 非法：向最近的合法地块走 1 步
                tx, ty = min(legal_tiles, key=lambda p: max(abs(p[0] - a["x"]), abs(p[1] - a["y"])))
                best, bd = None, 10 ** 9
                for nx, ny in self.neighbors(a["x"], a["y"]):
                    if not self._enterable_step(n, nx, ny):
                        continue
                    dd = max(abs(nx - tx), abs(ny - ty))
                    if dd < bd or (dd == bd and (nx, ny) < (best or (9 ** 9, 0))):
                        best, bd = (nx, ny), dd
                if best is not None:
                    a["x"], a["y"] = best
                    self.log(f"🚶 {n} 军队{a['id']} 自敌境「遣返」撤向合法地（{best[0]+1},{best[1]+1}）", phase="事件", nation=n, x=best[0], y=best[1])

    def _enterable_step(self, n: str, x: int, y: int) -> bool:
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
            del store[:-3]  # 只留最近 3 张图，控体积
            self.log(f"🗺 {m['to']} 收到 {m['from']} 的地图", phase="事件", nation=m["to"])
        # 经济间谍回报：2回合后盗回目标当前经济情报；目标亡国则任务失败
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
            del store[:-2]  # 只留最近 2 份，控体积
            self.log(f"🕵 {s['from']} 的间谍回报了 {s['to']} 的经济情报",
                     phase="事件", nation=s["from"])
        return len(due)

    # ------------------------------------------------------------- 市场
    def market_price(self, good: str) -> float:
        return round(self.prices[good], 1)

    def _clamp_price(self, good: str, p: float) -> float:
        base = MARKET[good]
        return min(max(p, PRICE_MIN), base * PRICE_MAX_RATIO)

    def buy(self, name: str, good: str, n: int) -> tuple[bool, str]:
        if good not in TRADEABLE:
            return False, f"「{good}」不可交易（可交易：{'、'.join(TRADEABLE)}）"
        if n <= 0:
            return False, "数量需为正整数"
        base = MARKET[good]
        p0 = self.prices[good]
        p1 = self._clamp_price(good, p0 + base * PRICE_TICK_RATIO * n)
        cost = int(round(p1 * n))
        if self.res(name, "黄金") < cost:
            return False, f"黄金不足：买 {good}×{n}（市价推到 {p1:.1f}）需 {cost}，国库 {self.res(name,'黄金')}"
        self.add_res(name, "黄金", -cost)
        self.add_res(name, good, n)
        self.prices[good] = p1
        return True, f"购入 {good}×{n}（市价 {p0:.1f}→{p1:.1f} 实付 {cost}），余{self.res(name,good)}"

    def sell(self, name: str, good: str, n: int) -> tuple[bool, str]:
        if good not in TRADEABLE:
            return False, f"「{good}」不可交易"
        if n <= 0:
            return False, "数量需为正整数"
        if self.res(name, good) < n:
            return False, f"储备不足：{good} 现有 {self.res(name,good)}"
        base = MARKET[good]
        p0 = self.prices[good]
        p1 = self._clamp_price(good, p0 - base * PRICE_TICK_RATIO * n)
        gold = int(round(p1 * n))
        self.add_res(name, good, -n)
        self.add_res(name, "黄金", gold)
        self.prices[good] = p1
        return True, f"售出 {good}×{n}（市价 {p0:.1f}→{p1:.1f} 实收 {gold}），余{self.res(name,good)}"

    # ------------------------------------------------------------- 信箱
    def send_mail(self, frm: str, to: str, text: str) -> tuple[bool, str]:
        if frm not in self.nations or to not in self.nations:
            return False, "收发双方都必须是现存国家"
        if to == frm:
            return False, "不能给自己写信"
        self.mail_pending.append({"from": frm, "to": to, "text": text, "arrive": self.turn + 1})
        # 信件正文不单独记一条（信件=一次行动，正文已在行动行里）；送达时另有"收到信"事件+收件箱
        return True, f"信已发出，{to} 将于第 {self.turn+1} 回合收到"

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

    # ------------------------------------------------------------- 经济间谍
    def _econ_snapshot(self, n: str) -> str:
        """目标国当前完整经济底细：国库/储备 + 收入结算 + 全部地块建设(含在建)。"""
        r = self.nations[n].res
        res_txt = " ".join(f"{k}{r.get(k, 0)}" for k in RES_KEYS)
        et, mt, short = self.energy_report.get(n, (0, 0, False))
        grid = "停摆" if short else f"产{et}/需{mt}"
        L = [f"【{n} 经济情报】国库/储备: {res_txt} | 电网: {grid}"]
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
        return "\n".join(L)

    def spy(self, frm: str, to: str) -> tuple[bool, str]:
        """派经济间谍刺探别国（花 20 金），2 回合后盗回其全部经济情报。不能对自己用。"""
        if frm not in self.nations or to not in self.nations:
            return False, "间谍双方都必须是现存国家"
        if to == frm:
            return False, "不能派间谍刺探自己"
        if self.res(frm, "黄金") < SPY_COST:
            return False, f"国库不足：派经济间谍需 {SPY_COST} 金，你现 {self.res(frm, '黄金')}"
        self.add_res(frm, "黄金", -SPY_COST)
        self.spy_pending.append({"from": frm, "to": to, "arrive": self.turn + SPY_TURNS})
        return True, (f"已派经济间谍前往 {to}（-{SPY_COST}金），"
                      f"将于第 {self.turn + SPY_TURNS} 回合拿回其经济情报")

    # ------------------------------------------------------------- 外交
    def _next_offer_id(self) -> int:
        self._offer_id += 1
        return self._offer_id

    def propose_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        """kind: '同盟' | '共同防御'。b 需要 accept_pact 才生效。"""
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        target = self.alliances if kind == "同盟" else self.defense_pacts
        if _pair(a, b) in target:
            return False, f"你们已是{kind}"
        if self.war_between(a, b):
            return False, "交战中不能提议" + kind
        if any(p["kind"] == kind and set((p["a"], p["b"])) == {a, b} for p in self.proposals):
            return False, "该提议已在桌上"
        self.proposals.append({"id": self._next_offer_id(), "kind": kind, "a": a, "b": b, "turn": self.turn})
        return True, f"{a} 向 {b} 提议{kind}，等 {b} 接受"

    def accept_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None or p["b"] != me:
            return False, "没有这个给你的邀约"
        a, b, kind = p["a"], p["b"], p["kind"]
        if self.war_between(a, b):
            self.proposals.remove(p)
            return False, "你们已交战，不能结盟"
        self.proposals.remove(p)
        target = self.alliances if kind == "同盟" else self.defense_pacts
        target.append(_pair(a, b))
        self.log(f"🕊 {a} 与 {b} 结为{kind}", phase="外交", nation=b)
        return True, f"你接受 {a} 的{kind}：现在你们互通/互卫（{kind}期间不可互相攻击）"

    def reject_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None or p["b"] != me:
            return False, "没有这个邀约"
        self.proposals.remove(p)
        return True, f"你拒绝了 {p['a']} 的{p['kind']}"

    def break_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        target = self.alliances if kind == "同盟" else self.defense_pacts
        if _pair(a, b) not in target:
            return False, f"你们不是{kind}"
        target.remove(_pair(a, b))
        self.log(f"💔 {a} 单方面解除与 {b} 的{kind}（{b}境内 {a} 的军队将全部撤出）", phase="外交", nation=a)
        return True, f"{a} 已与 {b} 解除{kind}。如你在他国境内，会于回合末自动遣返回国"

    def declare_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.war_between(a, b):
            return False, "不能保障正在交战的国家的独立"
        self.guarantees.setdefault(a, set()).add(b)
        self.log(f"🛡 {a} 宣布保障 {b} 独立：任何国家攻击 {b}，{a} 将自动参战", phase="外交", nation=a)
        return True, f"{a} 保障 {b} 独立"

    def cancel_guarantee(self, a: str, b: str) -> tuple[bool, str]:
        s = self.guarantees.get(a)
        if not s or b not in s:
            return False, "你并未保障该国的独立"
        s.discard(b)
        self.log(f"{a} 撤回对 {b} 的独立保障", phase="外交", nation=a)
        return True, "已撤回保障"

    def declare_war(self, a: str, b: str) -> tuple[bool, str]:
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.war_between(a, b):
            return False, "你们已经在交战"
        notes = []
        # 与盟国/共同防御对象开战 → 先解除该约束
        if self.allied_between(a, b):
            self.alliances.remove(_pair(a, b))
            notes.append("（先与对方断盟）")
        if self.defense_pacts and _pair(a, b) in self.defense_pacts:
            self.defense_pacts.remove(_pair(a, b))
            notes.append("（背弃共同防御）")
        self.wars.append(_pair(a, b))
        # 保障独立 / 共同防御 → b 的支持者自动对 a 开战
        joiners = []
        for c in self.alive():
            if c in (a, b) or self.war_between(c, a):
                continue
            backer = c in self.guarantee_of(b) or (self.defense_pacts and _pair(b, c) in self.defense_pacts)
            if not backer:
                continue
            if self.allied_between(c, a) or (self.defense_pacts and _pair(c, a) in self.defense_pacts):
                if self.allied_between(c, a):
                    self.alliances.remove(_pair(c, a))
                if self.defense_pacts and _pair(c, a) in self.defense_pacts:
                    self.defense_pacts.remove(_pair(c, a))
                notes.append(f"（{c} 为履行保障/共同防御，背弃与你的盟约）")
            self.wars.append(_pair(c, a))
            joiners.append(c)
        self.log(f"⚔ {a} 对 {b} 宣战！{b} 必须应战{('；' + '、'.join(joiners) + ' 依约参战') if joiners else ''}",
                 phase="外交", nation=a)
        jtxt = f"；参战：{'、'.join(joiners)}" if joiners else ""
        return True, f"{a} 对 {b} 宣战（{b} 必须接受）{''.join(notes)}{jtxt}"

    def offer_peace(self, a: str, b: str, kind: str, gold: int = 0, note: str = "") -> tuple[bool, str]:
        if kind not in ("pay", "demand", "white"):
            return False, "kind 须为 pay(我方赔款) / demand(要求对方赔款) / white(白和)"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if not self.war_between(a, b):
            return False, "你们并不在交战"
        if gold < 0 or (kind in ("pay", "demand") and gold == 0):
            if kind == "white":
                gold = 0
            else:
                return False, "赔款量需为正整数（white 则不带赔款）"
        self.peace_offers.append({"id": self._next_offer_id(), "a": a, "b": b,
                                  "kind": kind, "gold": gold, "note": note, "turn": self.turn})
        k = {"pay": f"{a} 愿赔 {gold} 金求和", "demand": f"{a} 要求 {b} 赔 {gold} 金",
             "white": "白和（不赔不索）"}[kind]
        self.log(f"🕊 求和提议：{k}" + (f"——{note}" if note else ""), phase="外交", nation=a)
        return True, f"已向 {b} 提出：{k}，等 {b} 在下一回合回应（accept/reject 议和 id）"

    def accept_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.peace_offers if x["id"] == offer_id and x["b"] == me), None)
        if p is None:
            return False, "没有这个给你的求和提议"
        a, b, kind, gold = p["a"], p["b"], p["kind"], p["gold"]
        if not self.war_between(a, b):
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
        self.peace_offers = [x for x in self.peace_offers if {x["a"], x["b"]} != {a, b}]
        self.wars.remove(_pair(a, b))
        for m in self.armies:
            if m["owner"] in (a, b) and m.get("engaged"):
                m["engaged"] = False
        extra = {"pay": f"{a} 付 {b} {gold} 金", "demand": f"{b} 赔 {a} {gold} 金", "white": "白和"}[kind]
        self.log(f"🕊 {a} 与 {b} 议和停战（{extra}）", phase="外交", nation=me)
        return True, f"停战议成：{extra}。请留意：滞留在对方领土的军队将于回合末遣返"

    def reject_peace(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.peace_offers if x["id"] == offer_id and x["b"] == me), None)
        if p is None:
            return False, "没有这个求和提议"
        self.peace_offers.remove(p)
        return True, f"你拒绝 {p['a']} 的求和（战争继续）"

    # ------------------------------------------------------------- 关系查询
    def allied_between(self, a: str, b: str) -> bool:
        return _pair(a, b) in self.alliances

    def war_between(self, a: str, b: str) -> bool:
        return _pair(a, b) in self.wars

    def dp_between(self, a: str, b: str) -> bool:
        return _pair(a, b) in self.defense_pacts

    def guarantee_of(self, b: str) -> list[str]:
        return [g for g, s in self.guarantees.items() if b in s]

    # ------------------------------------------------------------- 关系汇总（供面板）
    def rel_desc(self, me: str) -> str:
        out = []
        for n in self.alive():
            if n == me:
                continue
            tags = []
            if self.allied_between(me, n):
                tags.append("同盟")
            if self.defense_pacts and _pair(me, n) in self.defense_pacts:
                tags.append("共同防御")
            if self.war_between(me, n):
                tags.append("交战")
            if n in self.guarantee_of(me):
                tags.append("保障我")      # n 保障我
            if self.guarantees.get(me) and n in self.guarantees[me]:
                tags.append("我保障")      # 我保障 n
            out.append(f"{n}=" + ("、".join(tags) if tags else "中立"))
        return "  ".join(out) if out else "（只有你一个国了）"

    # ------------------------------------------------------------- 存档
    def save(self, path: str | Path) -> None:
        data = {
            "size": self.size, "seed": self.seed, "turn": self.turn,
            "rng_state": list(self.rng.getstate()),
            "nations": {n: nat.res for n, nat in self.nations.items()},
            "order": self.order,
            "tiles": {f"{x},{y}": t for (x, y), t in sorted(self.tiles.items())},
            "armies": self.armies, "next_army_id": self.next_army_id,
            "guard_once": [list(k) for k in sorted(self.guard_once)],
            "wars": [list(p) for p in self.wars],
            "alliances": [list(p) for p in self.alliances],
            "defense_pacts": [list(p) for p in self.defense_pacts],
            "guarantees": {k: sorted(v) for k, v in self.guarantees.items()},
            "mail_pending": self.mail_pending,
            "mailbox": self.mailbox,
            "summaries": self.summaries,
            "gift_pending": self.gift_pending,
            "map_pending": self.map_pending,
            "maps": self.maps,
            "spy_pending": self.spy_pending,
            "econ_intel": self.econ_intel,
            "peace_offers": self.peace_offers,
            "proposals": self.proposals,
            "offer_id": self._offer_id,
            "prices": self.prices,
            "history": self.history,
            "history_seen": self.history_seen,
        }
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "World":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        w = cls(size=data["size"], seed=data["seed"])
        w.turn = data.get("turn", 0)
        ver, internal, gauss = data["rng_state"]
        w.rng.setstate((ver, tuple(internal), gauss))
        w.nations = {n: Nation(n, res) for n, res in data.get("nations", {}).items()}
        w.order = data.get("order") or list(w.nations)
        w.mailbox = {n: data.get("mailbox", {}).get(n, []) for n in w.nations}
        w.summaries = {n: list(v) for n, v in data.get("summaries", {}).items() if n in w.nations}
        w.gift_pending = data.get("gift_pending", [])
        w.map_pending = data.get("map_pending", [])
        w.maps = {n: list(v) for n, v in data.get("maps", {}).items() if n in w.nations}
        w.spy_pending = data.get("spy_pending", [])
        w.econ_intel = {n: list(v) for n, v in data.get("econ_intel", {}).items() if n in w.nations}
        w.armies = data.get("armies", [])
        w.next_army_id = data.get("next_army_id", 1)
        w.wars = [_pair(*p) for p in data.get("wars", [])]
        w.alliances = [_pair(*p) for p in data.get("alliances", [])]
        w.defense_pacts = [_pair(*p) for p in data.get("defense_pacts", [])]
        w.guarantees = {k: set(v) for k, v in data.get("guarantees", {}).items()}
        w.mail_pending = data.get("mail_pending", [])
        w.peace_offers = data.get("peace_offers", [])
        w.proposals = data.get("proposals", [])
        w._offer_id = data.get("offer_id", 1)
        w.prices = {g: float(data.get("prices", {}).get(g, MARKET[g])) for g in TRADEABLE}
        w.history = data.get("history", [])
        w.history_seen = data.get("history_seen", 0)
        for k, t in data["tiles"].items():
            x, y = map(int, k.split(","))
            t.setdefault("recruited_this_turn", 0)
            t.setdefault("built_this_turn", 0)
            t.setdefault("buildings", {})
            t.setdefault("pending", {})
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
