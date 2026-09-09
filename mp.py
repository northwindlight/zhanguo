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
import re
from pathlib import Path

from game import (
    ARMY_HEAL_PER_TURN,
    ARMY_MAX_HP,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    CASTLE_DEFENSE_PER_LEVEL,
    COMBAT_DIE_MOD,
    DIPLO_CENTER_MIN_COST,
    ENGINEER_DISCOUNT,
    MARKET,
    MAX_SLOTS,
    PRICE_MAX_RATIO,
    PRICE_MIN,
    PRICE_REVERT,
    PRICE_TICK_RATIO,
    RETREAT_RANGE,
    TERRAIN_CHARS,
    TERRAIN_STATS,
    TOWN_HALL_GOLD,
    TOWN_HALL_PER_SLOT,
    TRADEABLE,
    UNIT_TYPES,
    WATCHTOWER_RADIUS,
    army_name,
    roll_resources,
    roll_tile_name,
    unit_atk,
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

SPY_COST = 100    # 经济间谍 花 100 金
SPY_TURNS = 3     # 3 回合后回报目标全部经济情报 + 地图（进 intel）；军情只给粗略数量（各兵种几支），位置/血量不外泄
DIPLO_COST = 10   # 外交基础费用：提议/回应/断盟/保障/宣战/求和/换图/馈赠手续费（成功才扣）
LETTER_COST = 20  # 信件单独费用
RETREAT_DEF_COVER = 50   # 防御方撤退：回合末战斗结算中只受 50% 伤害（进攻方撤退全额；0=不减伤、100=免伤）
PLAN_MAX_TURNS = 10  # 国策每 10 回合必须修订一次（否则 end_turn 被拦）


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
        self.wars: list[dict] = []  # 战争冲突：{id, atk(进攻主导), def(防御主导), followers(跟随方), turn}
        self._war_id = 0   # 计数器先自增再取值 → 首个 id 为 1
        self.truce: dict[frozenset, int] = {}  # 休战期：边→ 生效至第 N 回合（含），期内不得再宣战
        # 旧双边「同盟」已由多边联盟取代（alliances 仅作旧档迁移暂存，恒空）：
        # 联盟 = {name 联盟名, chief 盟主(发起方，可移交), members 成员(加入序), turn 创立回合}
        self.alliances: list[frozenset] = []
        self.blocs: list[dict] = []
        # 联盟投票：{id, kind: 宣战|议和|入盟, bloc, proposer, payload, votes{国:bool}, turn}
        self.votes: list[dict] = []
        self._vote_id = 0  # 同上：首个投票 id 为 1
        self.defense_pacts: list[frozenset] = []
        self.guarantees: dict[str, set[str]] = {}
        self.mail_pending: list[dict] = []
        self.mailbox: dict[str, list[dict]] = {}
        self.summaries: dict[str, list[dict]] = {}  # 各国回合小结纪事 [{turn,text}]（私有，本国 AI 记忆；全留，供旧回合汇总）
        self.summary_blocks: dict[str, list[dict]] = {}  # 各国阶段块总结 [{from,to,text,turn}]（滑出 replay 的回合经 LLM 压成一段，覆盖其小结）
        self.turn_memory: dict[str, list[dict]] = {}  # 各国完整回合记录（含思考 reasoning_content），按 ctx_window 预算动态保留最近若干回合
        self.gift_pending: list[dict] = []         # 馈赠在途（下回合到账）
        self.map_pending: list[dict] = []          # 交换地图在途（下回合到账）
        self.maps: dict[str, list[dict]] = {}      # 各国收到的地图情报（{from,turn,text}，留最近3张）
        self.spy_pending: list[dict] = []          # 经济间谍在途（3回合后回报）
        self.econ_intel: dict[str, list[dict]] = {}  # 各国收到的经济情报（{from,turn,text}，留最近2份）
        self.plans: dict[str, dict] = {}           # 各国国策规划 {text, turn}——常驻上下文，每10回合须修订
        self.polity: dict[str, str] = {}           # 政体标记（"huns"=匈奴）→ 造价/征召/外交限制
        self.extra_prompt: dict[str, dict] = {}    # 临时注入的额外上下文 {text, until}——塞入正常 system_prompt，until 后自动消失
        self.peace_offers: list[dict] = []
        self.proposals: list[dict] = []
        self._offer_id = 0  # 同上：首个邀约 id 为 1
        self.prices: dict[str, float] = {g: float(MARKET[g]) for g in TRADEABLE}
        self.armies: list[dict] = []
        self.next_army_seq: dict[str, int] = {}  # 各国独立军队序列：从1递增、阵亡不回收
        self.standby: dict[str, int] = {}        # 待登场国 {国名: 登场回合}（带 polity 的配置国），随存档持久化
        self.diplo_built: dict[str, int] = {}    # 各国「自建」外交中心座数（夺地抢来的不计，不影响自建限额）
        self.militia_recruited: dict[str, int] = {}  # 本回合各国已征民兵数（回合初清零；上限=全国军屯数）
        self.nation_code: dict[str, int] = {}    # 国家码：军队全局唯一id = 码×1e8+序列（野人=0，秦=1→100000001）
        self._next_code = 1
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
        # 瞭望塔：己方/盟方任一瞭望塔半径 WATCHTOWER_RADIUS 圆（欧氏）内也可见（事件视野）
        for (tx, ty), t in self.tiles.items():
            if not t["buildings"].get("瞭望塔"):
                continue
            o = t["owner"]
            if o != name and not (bloc is not None and o in bloc["members"]):
                continue
            if (tx - x) ** 2 + (ty - y) ** 2 <= WATCHTOWER_RADIUS ** 2:
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
            "core": owner,  # 核心领土：首任 owner；每次战争结束按参与者实占重算
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
        r = self.size * 0.31   # 环半径（占地图边长比例）
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
        self.mailbox[name] = []
        self.grid_short[name] = False
        self._assign_code(name)
        self._place_crosses({name: pos})
        if is_huns:
            self.apply_polity(name, "huns", home=pos, start=start)
        if extra:
            self.extra_prompt[name] = {"text": str(extra), "until": self.turn + 20,
                                       "summary": str(summary or "")}
        self._ensure_guardians()
        desc = ("匈奴" if is_huns else "国家") + f" {name} 登场（距各国至少 {margin} 格）"
        if is_huns:
            s = start or {}
            cav = int(s.get("骑", 6)); gold = int(s.get("黄金", 1000)); sup = int(s.get("补给", 200))
            desc += (f"：开局 {cav} 骑兵·金{gold}·补给{sup}·建筑+30%惩罚·骑兵征召8粮8装"
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
            cost = cost * (100 - ENGINEER_DISCOUNT) // 100   # 工程院：本地建造金价 -20%（只认已落成的）
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
            # 民兵走军屯征召：军屯不耗电，不受全国电网停摆影响
            # 双限额：每座军屯 1 支/回合，且全国每回合总上限 = 军屯总数
            if t["buildings"]["军屯"] <= 0:
                return False, "该地块没有军屯（民兵只能在军屯征召：50金/支，每军屯每回合1支）"
            tile_cap = t["buildings"]["军屯"] - t.get("militia_recruited_this_turn", 0)
            if tile_cap <= 0:
                return False, "本回合该地块民兵征召产能已用完（每军屯 1 支/回合）"
            quota = self.nation_building_count(name, "军屯")
            cap = min(tile_cap, quota - self.militia_recruited.get(name, 0))
            if cap <= 0:
                return False, f"本回合民兵征召已达上限（全国军屯 {quota} 座 = {quota} 支/回合）"
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
        for f, amt in cost.items():
            self.add_res(name, f, -amt * n)
        for i in range(n):
            gid, seq = self._new_army(name)
            self.armies.append({"id": seq, "gid": gid, "name": army_name(name, seq, kind),
                                "type": kind, "hp": ARMY_MAX_HP, "x": x, "y": y,
                                "owner": name, "moved_turn": -1, "engaged": False})
        if kind == "民":
            self.militia_recruited[name] = self.militia_recruited.get(name, 0) + n
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
        # 交战地中的军队（含防守方）不能直接 mv 撤离——撤出走 retreat（回合末随战斗结算后脱离）
        if any(d["owner"] != a["owner"] and d["owner"] != "野人" and d.get("engaged")
               and (d["x"], d["y"]) == (a["x"], a["y"]) and self.war_between(a["owner"], d["owner"])
               for d in self.armies):
            return False, f"{a['name']} 所在格正在交战，不能直接 mv 撤离；撤出请用 retreat（回合末随战斗结算后脱离）"
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
        # 禁 mv 停到有敌军(交战方)的地格——要打用 atk（否则两军脸贴脸却不打）
        if any(d["owner"] != name and d["owner"] != "野人"
               and (d["x"], d["y"]) == (x, y) and self.war_between(name, d["owner"])
               for d in self.armies):
            return False, f"({x+1},{y+1}) 有敌军驻守，不能 mv 过去；进攻请用 atk（会交战）"
        # mv 只挪位置，不占地——占地走 atk
        a["x"], a["y"] = x, y
        a["moved_turn"] = self.turn
        return True, f"军队{a['id']} 移防 ({x+1},{y+1})"

    def attack(self, name: str, aids: list[int], x: int, y: int) -> tuple[bool, str]:
        """atk = 一次『进军占地』：派军队进目标格——
        有守军(野人/敌国军)就交战（打赢自动占地）；格上**无任何军队**才直接进驻占领（空城/无主空地），
        有其他军队但非你敌人（中立/第三方）则不能进驻。多势力同时开战各打各的敌人。
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
        # 格上还有其他军队但不是你的敌人（中立/第三方）→ 不能进驻占地
        if any(a["owner"] != name and a["hp"] > 0 and (a["x"], a["y"]) == (x, y) for a in self.armies):
            return False, (f"({x+1},{y+1}) 有他国军队但并非你的敌人（中立/第三方），"
                           f"不能直接进驻；只能攻击敌人或占领无任何守军的空地")
        # 格上无任何军队 → atk 进驻即占
        _ok, cmsg = self._conquer(x, y, name, "进驻占领", log_it=False)
        nm2 = self.tiles[(x, y)]["name"]
        self.log(f"{name} {ids} 进驻 ({x+1},{y+1})，{cmsg}", phase="领土", nation=name, x=x, y=y)
        return True, f"{ids} 进驻 ({x+1},{y+1})，敌人为 0，{cmsg}"

    def _retreat_legal(self, name: str, x: int, y: int) -> bool:
        """撤退合法点：无人荒地 / 己方领土 / 同盟领土（中立与敌国格都不行）。"""
        o = self.owned_by(x, y)
        return o is None or o == name or self.allied_between(name, o)

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
            for d in self.armies)
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
        holder = self.owned_by(a["x"], a["y"])
        cover = RETREAT_DEF_COVER if (holder == name or
                 (holder is not None and self.allied_between(name, holder))) else 100
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
        """atk_total = 该方各军兵种攻击之和（步/骑 50、民兵 30，见 unit_atk）。"""
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
        """每格每回合的多势力交战结算：**进攻方不纯联合**——每方独立只打自己的敌人（互相宣战才互打），
        每方掷自己的骰；野人只守无主格、只打"进攻方"（不打扰和平停驻者）；地形/城堡减伤只给格主/野人；
        同格多方同时出手再统一结算阵亡；占地 = 唯一幸存且野人已清的势力。"""
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
            # 敌人关系：非野人势力 = 格上其他交战方 + (自己是进攻方时)无主格野人；野人 = 只打进攻方
            def _enemies(F: str) -> list[str]:
                if F == "野人":
                    return [G for G in attacker if G != "野人"]
                en = [G for G in forces if G != F and G != "野人" and self.war_between(F, G)]
                if F in attacker and "野人" in forces:
                    en.append("野人")
                return en
            holder = owner if (owner in forces) else ("野人" if "野人" in forces else None)
            soak = {F: (self._defense_pct(x, y, F) if F == holder else 0) for F in forces}
            # 每方掷自己的骰，同时出手（先算全部伤害再统一施加，允许同归于尽）
            dmg: dict[str, int] = {F: 0 for F in forces}
            mods: dict[str, int] = {}
            for F in forces:
                en = _enemies(F)
                if not en:
                    continue
                _d, mod = self._die()
                mods[F] = mod
                power = self._round_damage(self._combat_power(sum(unit_atk(a) for a in forces[F]), 0), mod)
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
            # 占地 / 战报
            if len(survivors) == 1 and "野人" not in alive:
                winner = survivors[0]
                fs = alive[winner]
                info = f"{winner} 余{len(fs)}支[{fs[0]['hp']}hp]"
                if owner == winner:
                    dead = sum(len(v) for F, v in forces.items() if F != winner)
                    lines.append((x, y, f"⚔ 守军坚守 @{tag}：攻方{dead}支全灭，{info}"
                                 f"（骰 {self._modtxt(mods)}）"))
                else:
                    ok, msg = self._conquer(x, y, winner, "攻陷" if owner else "进驻")
                    lines.append((x, y, f"⚔ 全歼守军 @{tag}，{msg}（{info}）"))
            elif len(survivors) == 1 and "野人" in alive:
                fs = alive[survivors[0]]
                lines.append((x, y, f"⚔ {survivors[0]} 仍与野人交战 @{tag}"
                             f"（余{len(fs)}支[{fs[0]['hp']}hp]，守军未清，占不得）"))
            elif not survivors:
                if "野人" in alive:
                    lines.append((x, y, f"⚔ 攻方全灭 @{tag}，野人仍在（无主地守军未清）"))
                else:
                    lines.append((x, y, f"⚔ 同归于尽 @{tag}——" + ("此地成无主空地，可直接占领" if owner is None else "城仍在敌手")))
            else:
                desc = "；".join(f"{F} 余{len(alive[F])}支[{alive[F][0]['hp']}hp]" for F in survivors)
                lines.append((x, y, f"⚔ 多方混战 @{tag}（骰 {self._modtxt(mods)}）：{desc}（战局未定）"))
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
            extra = self._return_core(x, y, by)  # 同战线盟友核心领土 → 自动归还
            if extra:
                t = self.tiles[(x, y)]
                msg += extra
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
                self.truce[p] = max(self.truce.get(p, 0), self.turn + 10)
        for lst in (self.alliances, self.defense_pacts):
            self._remove_pair(lst, name)
        self.guarantees.pop(name, None)
        for s in self.guarantees.values():
            s.discard(name)
        self.mailbox.pop(name, None)
        self.summaries.pop(name, None)
        self.summary_blocks.pop(name, None)
        self.turn_memory.pop(name, None)
        self.maps.pop(name, None)
        self.gift_pending = [g for g in self.gift_pending if g["from"] != name and g["to"] != name]
        self.map_pending = [m for m in self.map_pending if m["from"] != name and m["to"] != name]
        self.spy_pending = [s for s in self.spy_pending if s["from"] != name and s["to"] != name]
        self.econ_intel.pop(name, None)
        self.plans.pop(name, None)
        self.polity.pop(name, None)
        self.extra_prompt.pop(name, None)
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
            t["militia_recruited_this_turn"] = 0
        self.militia_recruited = {}  # 民兵全国配额（上限=军屯数），回合初清零

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
                a["hp"] = min(ARMY_MAX_HP, a["hp"] + ARMY_HEAL_PER_TURN)
        for n, (short, per, dead) in famine.items():
            self.log(f"⚠ {n} 补给断粮（缺 {short}，每军 -{per}HP）：{dead} 支军队饿毙", phase="内政", nation=n)

        # 5) 非法滞留 → 自动遣返（断盟/退盟/停战后必须撤出）
        self._withdraw_illegal()

        # 5.5) 联盟投票逾期未决 → 作废（发起回合的下一回合结束前须决出）
        self._expire_votes()

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
            for a in list(self.armies):
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
            del store[:-3]  # 只留最近 3 张图，控体积
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
            del store[:-2]  # 只留最近 2 份，控体积
            # 间谍偷来的地图也进 intel（world.maps，与 share_map 同池，留最近 3 张）
            mstore = self.maps.setdefault(s["from"], [])
            mstore.append({"from": f"{s['to']}(间谍)", "turn": s["arrive"],
                           "text": self._map_snapshot(s["to"])})
            del mstore[:-3]
            self.log(f"🕵 {s['from']} 的间谍回报了 {s['to']} 的经济情报与地图",
                     phase="事件", nation=s["from"])
        return len(due)

    # ------------------------------------------------------------- 市场
    def market_price(self, good: str) -> float:
        return round(self.prices[good], 1)

    def market_depth(self) -> int:
        """市场深度随玩家数量缩放：现存国家越多，单笔买卖对市价的冲击越小。"""
        return max(1, len(self.alive()))

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
        tick = base * PRICE_TICK_RATIO / self.market_depth()
        p1 = self._clamp_price(good, p0 + tick * n)
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
        tick = base * PRICE_TICK_RATIO / self.market_depth()
        p1 = self._clamp_price(good, p0 - tick * n)
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
        # 粗略军情：只有各兵种数量——位置/血量/番号不外泄（间谍能探到敌国在扩军，但别想精准侦察）
        kinds: dict[str, int] = {}
        for a in self.nation_armies(n):
            k = unit_kind(a)
            kinds[k] = kinds.get(k, 0) + 1
        L.append("  军情（仅数量，位置未知）: " + ("、".join(f"{k}×{c}" for k, c in kinds.items()) or "无军队"))
        return "\n".join(L)

    def spy(self, frm: str, to: str) -> tuple[bool, str]:
        """派间谍刺探别国（花 SPY_COST 金），SPY_TURNS 回合后盗回其全部经济情报 +
        粗略军情（仅各兵种数量，位置/血量/番号不外泄）+ 整张已知地图。不能对自己用。"""
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
        """kind: '共同防御'。双边「同盟」已由多边联盟取代（bloc_found 发起）。"""
        if kind == "同盟":
            return False, "双边同盟已由多边联盟取代：用 bloc_found(name=联盟名, tos=[创始成员]) 发起结盟（需起名，全体创始成员同意）"
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if kind != "共同防御":
            return False, f"未知盟约类型：{kind}（可选：共同防御；联盟请用 bloc_found）"
        if self.polity.get(b) == "huns":
            return False, f"{b} 是游牧政体，不接受盟约（别在它身上花外交费）"
        target = self.alliances if kind == "同盟" else self.defense_pacts
        other = self.defense_pacts if kind == "同盟" else self.alliances
        if _pair(a, b) in target:
            return False, f"你们已是{kind}"
        if kind == "共同防御" and (_pair(a, b) in self.alliances):
            return False, "你们已是同盟（更高一档），无须共同防御"
        # 同盟/共同防御与保障两两互斥 → 缔结高档时自动升级（解除低档）
        if self.war_between(a, b):
            return False, "交战中不能提议" + kind
        if self.at_war(a):
            return False, f"战争期间不能缔结{kind}：{self._war_brief(a)}（先议和）"
        if self.at_war(b):
            return False, f"{b} 正在交战，战争期间不能与它缔结{kind}（先议和）"
        if any(p["kind"] == kind and set((p["a"], p["b"])) == {a, b} for p in self.proposals):
            return False, "该提议已在桌上"
        self.proposals.append({"id": self._next_offer_id(), "kind": kind, "a": a, "b": b, "turn": self.turn})
        return True, f"{a} 向 {b} 提议{kind}，等 {b} 接受"

    def accept_pact(self, me: str, offer_id: int) -> tuple[bool, str]:
        p = next((x for x in self.proposals if x["id"] == offer_id), None)
        if p is None:
            return False, "没有这个邀约"
        if p["kind"] == "联盟":
            if me not in p.get("invitees", []):
                return False, "没有这个给你的邀约"
            return self._accept_bloc_founding(p, me)
        if p["b"] != me:
            return False, "没有这个给你的邀约"
        a, b, kind = p["a"], p["b"], p["kind"]
        if self.war_between(a, b):
            self.proposals.remove(p)
            return False, "你们已交战，不能结盟"
        if self.at_war(a) or self.at_war(b):
            self.proposals.remove(p)
            return False, "战争期间不能缔结盟约（提议作废，先议和）"
        if kind == "共同防御" and _pair(a, b) in self.alliances:
            self.proposals.remove(p)
            return False, "你们已是同盟（更高一档），共同防御提议作废"
        self.proposals.remove(p)
        # 自动升级：缔结高档（同盟>共同防御>保障）自动解除同对之间的低档
        low = []
        if kind == "同盟" and _pair(a, b) in self.defense_pacts:
            self.defense_pacts.remove(_pair(a, b))
            low.append("共同防御")
        for x, y in ((a, b), (b, a)):
            if x in self.guarantee_of(y):
                self.guarantees[x].discard(y)
                low.append(f"{x}对{y}的保障")
        target = self.alliances if kind == "同盟" else self.defense_pacts
        target.append(_pair(a, b))
        up = f"（自动升级：解除{'、'.join(low)}）" if low else ""
        self.log(f"🕊 {a} 与 {b} 结为{kind}{up}", phase="外交", nation=b)
        return True, f"你接受 {a} 的{kind}：现在你们互通/互卫（{kind}期间不可互相攻击）{up}"

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
        if p["b"] != me:
            return False, "没有这个邀约"
        self.proposals.remove(p)
        return True, f"你拒绝了 {p['a']} 的{p['kind']}"

    def break_pact(self, kind: str, a: str, b: str) -> tuple[bool, str]:
        if kind == "同盟":
            if self.bloc_of(a) is not None and self.bloc_of(a) is self.bloc_of(b):
                return False, "联盟退出是单方面的：直接用 bloc_leave 退盟即可，无须对方同意"
            return False, "双边同盟已由多边联盟取代（bloc_found 结盟 / bloc_leave 退盟）"
        target = self.defense_pacts
        if _pair(a, b) not in target:
            return False, f"你们不是{kind}"
        target.remove(_pair(a, b))
        # 断盟退战：跟随方不想打的退出通道——昔日盟友参与的战线，其跟随方身份随之解除
        exited = []
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if b not in atk + dfs:
                continue
            if a in w["followers"]:
                w["followers"].remove(a)
                exited.append(w)
            if a in w.get("atk_followers", []):
                w.get("atk_followers", []).remove(a)
                exited.append(w)
        still_in = any(a in (lambda w: [w["atk"]] + list(w.get("atk_followers", []))
                              + [w["def"]] + list(w["followers"]))(w) for w in self.wars)
        if exited and not still_in:
            for m in self.armies:
                if m["owner"] == a and m.get("engaged"):
                    m["engaged"] = False  # 已无任何战线 → 解除交战，回合末自动遣返
        extxt = f"，并退出 {b} 所在的 {len(exited)} 条战线" if exited else ""
        self.log(f"💔 {a} 单方面解除与 {b} 的{kind}{extxt}（{b}境内 {a} 的军队将全部撤出）",
                 phase="外交", nation=a)
        return True, f"{a} 已与 {b} 解除{kind}{extxt}。如你在他国境内，会于回合末自动遣返回国"

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
        if not name or " " in name or len(name) > 12:
            return False, "联盟名需为 1~12 字、不含空格（name 参数）"
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
        # 盟内保障/共同防御被联盟覆盖，自动解除（避免双重记账）
        absorbed = []
        for lst, label in ((self.defense_pacts, "共同防御"),):
            for x in members:
                for y in members:
                    if x < y and _pair(x, y) in lst:
                        lst.remove(_pair(x, y))
                        absorbed.append(f"{x}-{y} {label}")
        ab = f"（盟内 {'、'.join(absorbed)} 自动并入联盟）" if absorbed else ""
        self.log(f"🕊 联盟「{p['name']}」成立！成员：{'、'.join(members)}（盟主 {p['a']}）{ab}",
                 phase="外交", nation=p["a"])
        return True, f"联盟「{p['name']}」成立！成员：{'、'.join(members)}，盟主 {p['a']}（你为创始成员）"

    def bloc_leave(self, a: str) -> tuple[bool, str]:
        """普通成员单方面退盟，立即生效。盟主不能退盟（先移交或解散）。退盟不退出已参战的战线。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        if self.bloc_chief(bloc) == a:
            return False, ("你是盟主，不能退盟——请用 bloc_transfer(to=成员) 移交盟主之位，"
                           "或 bloc_dissolve 解散联盟")
        bloc["members"].remove(a)
        self.log(f"💔 {a} 单方面退出联盟「{bloc['name']}」", phase="外交", nation=a)
        if len(bloc["members"]) < 2:   # 只剩盟主一人 → 联盟自动解散
            chief = self.bloc_chief(bloc)
            self.blocs.remove(bloc)
            self.votes = [v for v in self.votes if v["bloc"] != bloc["name"]]
            self.log(f"💔 联盟「{bloc['name']}」仅剩盟主 {chief}，自动解散", phase="外交")
            return True, f"你已退出「{bloc['name']}」——联盟只剩盟主，随之解散"
        return True, (f"你已单方面退出「{bloc['name']}」。"
                      f"滞留在前盟友领土的军队将自回合末起自动遣返；已参战的战线不因此退出")

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
        if chief != a:
            return False, f"只有盟主能给联盟改名（现任盟主是 {chief}）"
        name = (new_name or "").strip()
        if not name or " " in name or len(name) > 12:
            return False, "联盟名需为 1~12 字、不含空格（name 参数）"
        if name == bloc["name"]:
            return False, f"你的联盟已经叫「{name}」了"
        if self.bloc_by_name(name) is not None:
            return False, f"联盟名「{name}」已被占用"
        old = bloc["name"]
        bloc["name"] = name
        for v in self.votes:        # 进行中的投票按联盟名索引，一并改掉，免得面板/日志对不上
            if v["bloc"] == old:
                v["bloc"] = name
        self.log(f"🏷 盟主 {a} 把联盟「{old}」改名为「{name}」", phase="外交", nation=a)
        return True, f"联盟已改名：「{old}」→「{name}」"

    def bloc_dissolve(self, a: str) -> tuple[bool, str]:
        """盟主解散联盟（只有盟主能调）。战争期间不得解散——防止盟主用解散脱战坑盟友。"""
        bloc = self.bloc_of(a)
        if bloc is None:
            return False, "你不在任何联盟中"
        chief = self.bloc_chief(bloc)
        if chief != a:
            return False, f"只有盟主能解散联盟（现任盟主是 {chief}）"
        fighting = [m for m in bloc["members"] if self.at_war(m)]
        if fighting:
            return False, (f"战争期间不能解散联盟：{'、'.join(fighting)} 正在交战"
                           "（先议和停战，再解散）")
        members = list(bloc["members"])
        self.blocs.remove(bloc)
        self.votes = [v for v in self.votes if v["bloc"] != bloc["name"]]
        self.log(f"💔 盟主 {a} 解散联盟「{bloc['name']}」（原成员：{'、'.join(members)}）",
                 phase="外交", nation=a)
        return True, (f"已解散联盟「{bloc['name']}」：原成员 {'、'.join(members)} 恢复各自独立"
                      "（滞留他国领土的军队将自回合末起自动遣返）")

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
            members = [m for m in bloc["members"] if m in self.nations] if bloc else []
            target = pl.get("target")
            if not members or target not in self.nations:
                return False, f"目标 {target} 已不在，宣战落空"
            chief = self.bloc_chief(bloc) or members[0]
            return self._declare_war_internal(chief, members, target, v["proposer"])
        if v["kind"] == "入盟":
            bloc = self.bloc_by_name(v["bloc"])
            cand = pl.get("candidate")
            if bloc is None or cand not in self.nations:
                return False, "入盟条件已变，申请落空"
            if self.bloc_of(cand) is not None or any(self.war_between(cand, m) for m in bloc["members"]):
                return False, f"{cand} 已入他盟/与成员交战，入盟落空"
            absorbed = []
            for m in bloc["members"]:
                if _pair(cand, m) in self.defense_pacts:
                    self.defense_pacts.remove(_pair(cand, m))
                    absorbed.append(f"{cand}-{m} 共同防御")
            bloc["members"].append(cand)
            ab = f"（与成员的 {'、'.join(absorbed)} 自动并入联盟）" if absorbed else ""
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

    # ------------------------------------------------------------- 核心领土
    def _snapshot_cores(self, participants: list[str]) -> None:
        """战争结束：参战各国（含跟随方）实际持有的地块重算为其核心领土（议和即对现状追认）。"""
        for p in set(participants):
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
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.war_between(a, b):
            return False, "不能保障正在交战的国家的独立"
        if self.at_war(a):
            return False, f"战争期间不能提供保障独立：{self._war_brief(a)}（先议和）"
        if self.at_war(b):
            return False, f"战争期间不能保障交战国 {b} 的独立（先议和）"
        if b in self.guarantee_of(a):
            return False, "你已保障该国独立，无须重复"
        if self.allied_between(a, b) or self.dp_between(a, b):
            have = f"联盟「{self.bloc_of(a)['name']}」" if self.allied_between(a, b) else "共同防御"
            return False, f"你们已有{have}——联盟/共同防御/保障两两互斥，先解除现有的一档再保障"
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
        """宣战。联盟成员不能擅自开战：必须发起联盟宣战投票（多数决），通过后全盟参战。
        非成员直接宣战；守侧传导=保障/共同防御/联盟关系的**传递闭包**（无限跳）。"""
        if a == b or a not in self.nations or b not in self.nations:
            return False, "双方必须是两个现存国家"
        if self.war_between(a, b):
            return False, "你们已经在交战"
        t = self.truce.get(_pair(a, b))
        if t is not None:
            if self.turn < t:
                return False, f"休战中：你与 {b} 约定休战至第 {t} 回合（还剩 {t - self.turn} 回合），不得再宣战"
            self.truce.pop(_pair(a, b), None)  # 到期清除
        bloc = self.bloc_of(a)
        if bloc is not None:
            if self.bloc_of(b) is bloc:
                return False, f"{b} 是你的联盟「{bloc['name']}」盟友，不能宣战（先 bloc_leave 退盟）"
            v = self._new_vote("宣战", bloc["name"], a, {"target": b})
            self.log(f"🗳 {a} 发起联盟宣战投票（「{bloc['name']}」）：对 {b} 宣战（投票#{v['id']}，多数决）",
                     phase="外交", nation=a)
            return True, (f"已发起联盟宣战投票（投票#{v['id']}）：多数同意后全盟对 {b} 宣战；"
                          f"成员用 vote {v['id']} true/false 表态")
        return self._declare_war_internal(a, [a], b, a)

    def _declare_war_internal(self, leader: str, members: list[str], b: str,
                              proposer: str) -> tuple[bool, str]:
        """实际开战。members=进攻侧全体（联盟战争=全盟，非联盟=[a]），leader=进攻主导（盟主/a）。
        守侧传导：从 b 出发的 保障/共同防御/联盟 传递闭包（无限跳）。"""
        members = [m for m in members if m in self.nations]
        if b not in self.nations:
            return False, f"目标 {b} 已亡国，宣战落空"
        # 休战检查（任一进攻侧成员与 b 休战中则不能开战）
        for m in members:
            t = self.truce.get(_pair(m, b))
            if t is not None:
                if self.turn < t:
                    return False, f"休战中：{m} 与 {b} 约定休战至第 {t} 回合，不得开战"
                self.truce.pop(_pair(m, b), None)
        # 并入现有战线（不开平行战争）：b 正在攻打我方成员/共同防御对象 → 守侧并入
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if b in atk and any(self.allied_between(m, d) or self.dp_between(m, d)
                                for m in members for d in dfs):
                self._break_pacts_to(members, [b])
                added = [m for m in members if m not in dfs
                         and not any(self.war_between(m, x) for x in atk)]
                w["followers"].extend(added)
                for m in added:
                    self.log(f"⚔ {m} 对 {b} 宣战：盟友正被 {b} 攻打，并入该战线当防守方跟随方"
                             "（不开第二场战争；跟随方不能单独议和）", phase="外交", nation=m)
                return True, (f"对 {b} 宣战：并入既有战线当防守方（新增 {'、'.join(added) or '无'}；"
                              "跟随方不能单独议和，主导者议和则整条战线停战）")
        # b 正被我方成员/共同防御对象攻打 → 随攻并入
        for w in self.wars:
            atk, dfs = self._war_sides(w)
            if b in dfs and any(self.allied_between(m, x) or self.dp_between(m, x)
                                for m in members for x in atk):
                self._break_pacts_to(members, [b])
                added = [m for m in members if m not in atk
                         and not any(self.war_between(m, x) for x in dfs)]
                w.setdefault("atk_followers", []).extend(added)
                for m in added:
                    self.log(f"⚔ {m} 对 {b} 宣战：盟友正在攻打 {b}，并入该战线随攻"
                             "（不开第二场战争；跟随方不能单独议和）", phase="外交", nation=m)
                return True, f"对 {b} 宣战：并入既有战线随攻（新增 {'、'.join(added) or '无'}）"
        # 进攻侧成员与 b 的保障/共同防御自动解除（不打自己人）
        self._break_pacts_to(members, [b])
        # 守侧闭包（无限传导）：联盟全体 + 保障/共同防御，逐层扩散
        def_side = {b}
        stack = [b]
        while stack:
            x = stack.pop()
            cands = set(self.guarantee_of(x))
            cands |= {y for y in self.alive() if self.dp_between(x, y)}
            bx = self.bloc_of(x)
            if bx is not None:
                cands |= set(bx["members"])
            for c in cands:
                if c in def_side or c in members or c not in self.nations:
                    continue
                if self.bloc_of(c) is not None and self.bloc_of(c) is self.bloc_of(leader):
                    continue  # 不与自家盟友为敌：进攻方联盟成员不被拖入守侧
                if any(self.war_between(c, m) for m in members):
                    continue  # 已与攻方交战，不并入守侧
                if any(self.war_between(c, d) for d in def_side):
                    continue  # 已与守侧某员交战，不并入
                def_side.add(c)
                stack.append(c)
        followers = [c for c in sorted(def_side) if c != b]
        # 防守义务优先：守侧成员与进攻侧的保障/共同防御自动解除；逐国通知（被拖入战争者可见）
        for c in followers:
            self._break_pacts_to([c], members)
            self.log(f"⚔ {c} 因联盟/共同防御/保障义务被拖入守侧，自动参战打 {'、'.join(members)}"
                     "（跟随方不能单独议和，主导者议和则整条战线停战）", phase="外交", nation=c)
        self.wars.append({"id": self._next_war_id(), "atk": leader, "def": b,
                          "followers": followers,
                          "atk_followers": [m for m in members if m != leader],
                          "turn": self.turn})
        mtxt = "、".join(members)
        self.log(f"⚔ {proposer} 发起、联盟多数决通过：{mtxt}（盟主 {leader}）对 {b} 宣战！",
                 phase="外交", nation=leader)
        if leader == proposer and len(members) == 1:
            self.log(f"⚔ {proposer} 对 {b} 宣战！{b} 必须应战", phase="外交", nation=proposer)
        jtxt = f"；守侧传导参战：{'、'.join(followers)}" if followers else ""
        return True, f"对 {b} 宣战（进攻侧：{mtxt}）{jtxt}"

    def _break_pacts_to(self, side: list[str], others: list[str]) -> list[str]:
        """解除 side 中各国与 others 中各国之间的 共同防御/保障（开战前清约束）。返回描述。"""
        out = []
        for x in side:
            if x not in self.nations:
                continue
            for y in others:
                if y not in self.nations or x == y:
                    continue
                if _pair(x, y) in self.defense_pacts:
                    self.defense_pacts.remove(_pair(x, y))
                    out.append(f"{x} 解除与 {y} 的共同防御")
                    self.log(f"💔 {x} 与 {y} 的共同防御因开战自动解除", phase="外交", nation=x)
                if y in self.guarantee_of(x):  # y 保障 x → 撤回
                    self.guarantees[y].discard(x)
                    out.append(f"{y} 撤回对 {x} 的保障")
                    self.log(f"💔 {y} 撤回对 {x} 的独立保障（双方开战）", phase="外交", nation=y)
                if x in self.guarantee_of(y):  # x 保障 y → 撤回
                    self.guarantees[x].discard(y)
                    out.append(f"{x} 撤回对 {y} 的保障")
                    self.log(f"💔 {x} 撤回对 {y} 的独立保障（双方开战）", phase="外交", nation=x)
        return out

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
        if bloc is not None and bloc["members"][0] == a:
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
        if bloc is not None and bloc["members"][0] == me:
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
        """盟友 = 同一联盟的成员（旧双边同盟恒空，仅存档迁移暂存）。"""
        return _pair(a, b) in self.alliances or (
            self.bloc_of(a) is not None and self.bloc_of(a) is self.bloc_of(b))

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
                tags.append(f"联盟·{self.bloc_of(me)['name']}")
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
            "armies": self.armies, "next_army_seq": self.next_army_seq,
            "standby": self.standby,
            "diplo_built": self.diplo_built,
            "nation_code": self.nation_code,
            "guard_once": [list(k) for k in sorted(self.guard_once)],
            "wars": self.wars,
            "war_id": self._war_id,
            "truce": [[a, b, until] for (a, b), until in self.truce.items()],
            "alliances": [list(p) for p in self.alliances],
            "blocs": self.blocs,
            "votes": self.votes,
            "vote_id": self._vote_id,
            "defense_pacts": [list(p) for p in self.defense_pacts],
            "guarantees": {k: sorted(v) for k, v in self.guarantees.items()},
            "mail_pending": self.mail_pending,
            "mailbox": self.mailbox,
            "summaries": self.summaries,
            "summary_blocks": self.summary_blocks,
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
            "grid_short": self.grid_short,
            "energy_report": self.energy_report,
            "econ_summary": self.econ_summary,
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
        w = cls(size=data["size"], seed=data["seed"])
        w.turn = data.get("turn", 0)
        ver, internal, gauss = data["rng_state"]
        w.rng.setstate((ver, tuple(internal), gauss))
        w.nations = {n: Nation(n, res) for n, res in data.get("nations", {}).items()}
        w.order = data.get("order") or list(w.nations)
        w.mailbox = {n: data.get("mailbox", {}).get(n, []) for n in w.nations}
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
        w.gift_pending = data.get("gift_pending", [])
        w.map_pending = data.get("map_pending", [])
        w.maps = {n: list(v) for n, v in data.get("maps", {}).items() if n in w.nations}
        w.spy_pending = data.get("spy_pending", [])
        w.econ_intel = {n: list(v) for n, v in data.get("econ_intel", {}).items() if n in w.nations}
        w.plans = {n: dict(v) for n, v in data.get("plans", {}).items() if n in w.nations}
        w.polity = {n: v for n, v in data.get("polity", {}).items() if n in w.nations}
        w.extra_prompt = {n: dict(v) for n, v in data.get("extra_prompt", {}).items() if n in w.nations}
        w.armies = data.get("armies", [])
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
        # 待登场国随档持久化；旧档没有此字段则留空（mp_run 会按配置现补）
        w.standby = {k: int(v) for k, v in data.get("standby", {}).items()}
        w.diplo_built = {k: int(v) for k, v in data.get("diplo_built", {}).items() if k in w.nations}
        # wars 迁移：新格式=冲突对象{id,atk,def,followers}；旧档=[a,b] 边对 → 视为无跟随方的双边战争
        w.wars = []
        w._war_id = int(data.get("war_id", 1))
        w.truce = {_pair(ab[0], ab[1]): int(ab[2]) for ab in data.get("truce", [])}
        for item in data.get("wars", []):
            if isinstance(item, dict) and "atk" in item:
                w.wars.append({"id": int(item.get("id", w._war_id)), "atk": item["atk"],
                               "def": item["def"], "followers": list(item.get("followers", [])),
                               "atk_followers": list(item.get("atk_followers", [])),
                               "turn": int(item.get("turn", w.turn))})
                w._war_id = max(w._war_id, int(item.get("id", 0)) + 1)
            else:
                a, b = item[0], item[1]
                w.wars.append({"id": w._war_id, "atk": a, "def": b, "followers": [],
                               "atk_followers": [], "turn": w.turn})
                w._war_id += 1
        w.alliances = [_pair(*p) for p in data.get("alliances", [])]
        # 联盟：新档读 blocs；旧档（无此字段）把双边同盟逐对迁成二人联盟（名=两国名相连+同盟）
        if "blocs" in data:
            w.blocs = []
            for b in data.get("blocs", []):
                members = [m for m in b.get("members", []) if m in w.nations]
                if not members:
                    continue
                chief = b.get("chief")   # 旧档无 chief 字段 → 退回最早加入者
                w.blocs.append({"name": str(b.get("name", "?")), "members": members,
                                "chief": chief if chief in members else members[0],
                                "turn": int(b.get("turn", 0))})
        else:
            w.blocs = [{"name": "".join(sorted(p)) + "同盟", "members": list(p),
                        "chief": sorted(p)[0], "turn": 0}
                       for p in data.get("alliances", [])]
            w.alliances = []
        w.votes = [v for v in data.get("votes", []) if isinstance(v, dict) and "id" in v]
        w._vote_id = int(data.get("vote_id", 1))
        for v in w.votes:
            v.setdefault("votes", {})
            v.setdefault("payload", {})
        w.defense_pacts = [_pair(*p) for p in data.get("defense_pacts", [])]
        w.guarantees = {k: set(v) for k, v in data.get("guarantees", {}).items()}
        w.mail_pending = data.get("mail_pending", [])
        w.peace_offers = data.get("peace_offers", [])
        w.proposals = data.get("proposals", [])
        w._offer_id = data.get("offer_id", 1)
        w.prices = {g: float(data.get("prices", {}).get(g, MARKET[g])) for g in TRADEABLE}
        w.history = data.get("history", [])
        w.history_seen = data.get("history_seen", 0)
        # 电网/结算摘要也持久化：否则续档后第一回合 all 面板电力 0、上回合结算丢失
        w.grid_short = {n: bool(v) for n, v in data.get("grid_short", {}).items() if n in w.nations}
        w.energy_report = {n: tuple(v) for n, v in data.get("energy_report", {}).items() if n in w.nations}
        w.econ_summary = {n: s for n, s in data.get("econ_summary", {}).items() if n in w.nations}
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
