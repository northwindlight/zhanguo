# -*- coding: utf-8 -*-
"""战国 RL 训练环境（单智能体）。

**单国独局**：地图上只有 `agent` 一国（可选加对手，默认没有），扩张只能打野人。
不依赖 LLM 层（无 tool schema / 无文本面板 / 无国策 plan），只调游戏层 `game.py` + `mp.py`。
本分支已移除国家间外交：各国永久中立。

设计
----
- **动作 = 合法动作清单（candidate set）**：每步由 env 枚举当前全部合法动作，
  策略在清单上做 softmax。清单天然可行，不必拼装因子化掩码，也不会输出非法动作。
- **观测 = 地块网格通道 + 全局向量**（通道名见 `obs_channels()`）。
- **奖励 = 总消费的增量**：`world.spend_total()` 全期只增不减，
  Σ 每步增量 ≡ 终局总消费 —— 密集奖励与目标函数**逐分相等**，不是塑形。

用法：
    env = ZhanguoEnv(map_size=16, seed=0, rivals=("楚",), max_turns=40)
    obs = env.reset()
    obs, r, done, info = env.step(obs.cand["actions"][i])
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

import numpy as np

from game import (BUILDINGS, ENGINEER_DISCOUNT, MARKET, MAX_SLOTS, TERRAIN_STATS,
                  TERRAINS, TRADEABLE, UNIT_TYPES, WATCHTOWER_RADIUS,
                  unit_kind, unit_max_hp, unit_speed)
from mp import World

# 动作种类（固定顺序，模型的下标语义依赖它）
KINDS = ("build", "recruit", "move", "attack", "retreat", "buy", "sell", "end_turn")
KIND_INDEX = {k: i for i, k in enumerate(KINDS)}

# 数量档位（征兵/买卖）。离散化：不做连续回归，降低动作空间难度。
AMOUNTS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 16)

# 军队特征维度：兵种 one-hot(3) + 血量 + 坐标 x/y + 交战 + 本回合已动
ARMY_FEAT = len(UNIT_TYPES) + 1 + 2 + 1 + 1

MAX_ARMIES = 32          # 候选里最多引用多少支军队（超出部分本回合不可选）
# 候选动作按类别限额：帝国越大候选越多（地块×建筑、军队×目标格），不设上界会让
# PPO 每步的候选打分开销随国力线性膨胀。限额 + **轮流起点**（按回合轮转）保证
# 被裁掉的选项在后续回合仍会被看到，长期无死角。
CAPS = {"build": 64, "recruit": 32, "move": 64, "attack": 64,
        "retreat": 16, "buy": 24, "sell": 24}
RES_KEYS = ("黄金", "粮食", "木头", "矿石", "石油", "装备", "补给")
RES_MAX = {"矿石": 5, "黄金": 2, "耕地": 5, "石油": 4, "木头": 5}


@dataclass
class Action:
    """一个合法动作。tile=(x,y) 为 0-based；army 为该国军队番号 id。"""
    kind: str
    sub: str = ""
    tile: tuple[int, int] | None = None
    army: int = 0
    amount: int = 1

    def label(self) -> str:
        t = f"({self.tile[0] + 1},{self.tile[1] + 1})" if self.tile else ""
        if self.kind == "build":
            return f"build {self.sub}@{t}"
        if self.kind == "recruit":
            return f"recruit {self.sub}×{self.amount}@{t}"
        if self.kind in ("move", "attack", "retreat"):
            return f"{self.kind} 军{self.army}→{t}"
        if self.kind in ("buy", "sell"):
            return f"{self.kind} {self.sub}×{self.amount}"
        return "end_turn"


@dataclass
class Obs:
    grid: np.ndarray      # [C,H,W] float32
    glob: np.ndarray      # [G] float32
    cand: dict            # 见 env._cand_pack()


class ZhanguoEnv:
    def __init__(self, *, map_size: int = 20, seed: int = 0, agent: str = "秦",
                 rivals: tuple[str, ...] = (), max_turns: int = 40,
                 max_actions_per_turn: int = 24, reward_scale: float = 0.01):
        self.map_size = int(map_size)
        self.seed = int(seed)
        self.agent = agent
        self.rivals = tuple(rivals)          # 默认无对手（单国独局）
        self.max_turns = int(max_turns)
        self.max_actions_per_turn = int(max_actions_per_turn)
        self.reward_scale = float(reward_scale)

        self.bnames = tuple(BUILDINGS)          # 建筑子表（顺序即子下标）
        self.unames = tuple(UNIT_TYPES)         # 兵种子表
        self.goods = tuple(TRADEABLE)           # 物资子表
        # 每个 kind 的子表：不在表里的 kind 只有空子项
        self.sub_tables = {
            "build": self.bnames, "recruit": self.unames,
            "buy": self.goods, "sell": self.goods,
            "move": ("",), "attack": ("",), "retreat": ("",), "end_turn": ("",),
        }
        self.world: World | None = None
        self.rng = random.Random(self.seed)
        self.turn_actions = 0
        self.prev_spend = 0.0
        self._done = False
        self._terrain: list[list[str]] = []
        self.army_ids: list[int] = []           # 当前军队列表（下标 → 番号 id）
        self.army_index: dict[int, int] = {}

    # ------------------------------------------------------------------ 生命周期
    def reset(self, seed: int | None = None) -> Obs:
        if seed is not None:
            self.seed = int(seed)
        n = self.map_size
        names = [self.agent] + [r for r in self.rivals if r != self.agent]
        self.world = World(size=n, seed=self.seed, nations=names)
        self.rng = random.Random(self.seed ^ 0x5F5F5F5F)
        self._terrain = [[self.world.tile_terrain(x, y) for y in range(n)] for x in range(n)]
        self.turn_actions = 0
        self._done = False
        self.world.begin_turn()
        self.prev_spend = self.world.spend_total(self.agent)
        return self._obs()

    def step(self, action: Action):
        """执行一个动作。返回 (obs, reward, done, info)。done 时 obs 为 None。"""
        assert self.world is not None and not self._done, "env 未 reset 或已结束"
        ok, msg = self._apply(action)
        self.turn_actions += 1
        ended = action.kind == "end_turn" or self.turn_actions >= self.max_actions_per_turn
        events = {}
        if ended and not self._done:
            events = self._run_round_end()
        total = self.world.spend_total(self.agent) if self.agent in self.world.nations else self.prev_spend
        reward = (total - self.prev_spend) * self.reward_scale
        self.prev_spend = total
        info = {"ok": ok, "msg": msg, "events": events, "turn": self.world.turn,
                "spend_total": total, "ended": ended}
        return (None if self._done else self._obs()), reward, self._done, info

    def _run_round_end(self) -> dict:
        """回合结算 → 开新回合（或终局）。单国独局：没有别的国家要行动。"""
        w = self.world
        events = w.resolve_turn()
        self.turn_actions = 0
        if (self.agent not in w.nations) or w.turn >= self.max_turns:
            self._done = True
        else:
            w.begin_turn()
        return events or {}

    # ------------------------------------------------------------------ 动作执行
    def _apply(self, a: Action) -> tuple[bool, str]:
        w, me = self.world, self.agent
        if a.kind == "build":
            return w.build(me, a.tile[0], a.tile[1], a.sub)
        if a.kind == "recruit":
            return w.recruit(me, a.tile[0], a.tile[1], a.amount, a.sub)
        if a.kind == "move":
            return w.move(me, a.army, a.tile[0], a.tile[1])
        if a.kind == "attack":
            return w.attack(me, [a.army], a.tile[0], a.tile[1])
        if a.kind == "retreat":
            return w.retreat(me, a.army, a.tile[0], a.tile[1])
        if a.kind == "buy":
            return w.buy(me, a.sub, a.amount)
        if a.kind == "sell":
            return w.sell(me, a.sub, a.amount)
        if a.kind == "end_turn":
            return True, "结束回合"
        return False, f"未知动作：{a.kind}"

    # ------------------------------------------------------------------ 合法动作枚举
    @staticmethod
    def _eff(t: dict) -> dict:
        b, p = t["buildings"], t.get("pending", {})
        return {k: b.get(k, 0) + p.get(k, 0) for k in b}

    def _in_battle(self, a: dict) -> bool:
        w = self.world
        if a.get("engaged"):
            return True
        return any(d["owner"] != a["owner"] and d["owner"] != "野人" and d.get("engaged")
                   and (d["x"], d["y"]) == (a["x"], a["y"]) for d in w.armies)

    def _retreat_legal(self, x: int, y: int) -> bool:
        o = self.world.owned_by(x, y)
        return o is None or o == self.agent

    def _refresh_armies(self) -> list[dict]:
        """我方军队列表（按番号排序，上限 MAX_ARMIES）——候选动作与观测共用同一套下标。"""
        armies = sorted(self.world.nation_armies(self.agent), key=lambda a: a["id"])[:MAX_ARMIES]
        self.army_ids = [a["id"] for a in armies]
        self.army_index = {aid: i for i, aid in enumerate(self.army_ids)}
        return armies

    def legal_actions(self) -> list[Action]:
        w, me, n = self.world, self.agent, self.map_size
        if me not in w.nations:
            return [Action("end_turn")]
        self._refresh_armies()
        cats: dict[str, list[Action]] = {k: [] for k in CAPS}
        res = w.nations[me].res
        gold, wood = res.get("黄金", 0), res.get("木头", 0)
        huns = w.polity.get(me) == "huns"
        own = w.own_tiles(me)

        # ---- build
        for (x, y) in own:
            t = w.tiles[(x, y)]
            if t["built_this_turn"]:
                continue
            eff = self._eff(t)
            used = sum(eff.values())
            if used >= MAX_SLOTS:
                continue
            for b in self.bnames:
                info = BUILDINGS[b]
                lv = eff[b]
                cost = info["cost"][lv] if info["kind"] == "castle" else info["cost"]
                bp = TERRAIN_STATS[t["terrain"]]["build_penalty"]
                if bp:
                    cost = cost * (100 + bp) // 100
                if t["buildings"].get("工程院") and b != "工程院":
                    cost = cost * (100 - ENGINEER_DISCOUNT) // 100
                if huns:
                    cost = cost * 13 // 10
                if gold < cost or wood < info["wood"]:
                    continue
                cr = info.get("cap_resource")
                if cr is not None:
                    have = t["resources"].get(cr, 0)
                    if have <= 0 or eff[b] >= have:
                        continue
                if info["kind"] == "castle" and eff[b] >= info["max_level"]:
                    continue
                if info.get("min_slots") and used < info["min_slots"]:
                    continue
                if info.get("limit") and eff[b] >= info["limit"]:
                    continue
                cats["build"].append(Action("build", b, (x, y)))

        # ---- recruit
        militia_quota = w.nation_building_count(me, "军屯")
        militia_alive = sum(1 for a in w.armies if a["owner"] == me and unit_kind(a) == "民")
        for (x, y) in own:
            t = w.tiles[(x, y)]
            for kind in self.unames:
                if kind == "民":
                    if t["buildings"]["军屯"] <= 0:
                        continue
                    cap = min(t["buildings"]["军屯"] - t.get("militia_recruited_this_turn", 0),
                              militia_quota - militia_alive)
                else:
                    if w.grid_short.get(me) or t["buildings"]["兵营"] <= 0:
                        continue
                    cap = t["buildings"]["兵营"] - t["recruited_this_turn"]
                if cap <= 0:
                    continue
                cost = UNIT_TYPES[kind]["recruit"]
                if kind == "骑" and huns:
                    cost = {"粮食": 8, "装备": 8}
                maxn = min(cap, min(res.get(f, 0) // amt for f, amt in cost.items()))
                for amt in AMOUNTS:
                    if amt <= maxn:
                        cats["recruit"].append(Action("recruit", kind, (x, y), 0, amt))

        # ---- move / attack / retreat（只认前 MAX_ARMIES 支军队，保持候选集有界）
        for a in w.nation_armies(me):
            if a["id"] not in self.army_index:
                continue
            speed = unit_speed(a)
            moved = a.get("moved_turn") == w.turn
            if not a.get("engaged") and not moved:
                for dx in range(-speed, speed + 1):
                    for dy in range(-speed, speed + 1):
                        if dx == 0 and dy == 0:
                            continue
                        x, y = a["x"] + dx, a["y"] + dy
                        if not (0 <= x < n and 0 <= y < n):
                            continue
                        o = w.owned_by(x, y)
                        if o is not None and o != me:
                            continue          # 他国领土：外交已移除，永久中立，不得进入
                        cats["move"].append(Action("move", "", (x, y), a["id"]))
                        if o is None:         # 无主野地：可 atk（不抢别人的战斗）
                            busy = any(d["owner"] not in ("野人", me) and d.get("engaged")
                                       and (d["x"], d["y"]) == (x, y) for d in w.armies)
                            if not busy:
                                cats["attack"].append(Action("attack", "", (x, y), a["id"]))
            if not moved and self._in_battle(a):
                nb = [(a["x"] + dx, a["y"] + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                      if (dx or dy)]
                nb = [(x, y) for x, y in nb
                      if 0 <= x < n and 0 <= y < n and self._retreat_legal(x, y)]
                for (x, y) in nb:
                    cats["retreat"].append(Action("retreat", "", (x, y), a["id"]))

        # ---- 市场
        for g in self.goods:
            for amt in AMOUNTS:
                if amt <= res.get(g, 0):
                    cats["sell"].append(Action("sell", g, None, 0, amt))
                _unit, total = w.market_quote(g, amt, "buy")
                if total <= gold:
                    cats["buy"].append(Action("buy", g, None, 0, amt))

        # ---- 按类别限额（轮流起点：被裁掉的选项下个回合会轮到，长期无死角）
        out: list[Action] = []
        for k, lst in cats.items():
            cap = CAPS.get(k, len(lst))
            if len(lst) > cap:
                off = (w.turn * 7 + KIND_INDEX[k]) % len(lst)
                lst = (lst[off:] + lst[:off])[:cap]
            out.extend(lst)
        out.append(Action("end_turn"))
        return out

    # ------------------------------------------------------------------ 观测
    def obs_channels(self) -> list[str]:
        ch = [f"terrain:{t}" for t in TERRAINS]
        ch += [f"res:{r}" for r in ("矿石", "黄金", "耕地", "石油", "木头")]
        ch += ["owner:me"] + [f"owner:{r}" for r in self.rivals] + ["owner:neutral"]
        ch += [f"bld:{b}" for b in self.bnames]
        ch += ["mine", "frontier", "my_army_hp", "foe_army_hp", "barb_army_hp",
               "pending", "built_this_turn", "visible"]
        return ch

    def glob_size(self) -> int:
        return (len(RES_KEYS) + 2 * len(TRADEABLE) + 3 + 2 + 2
                + len(self.bnames) + 3 + len(self.unames) + 3 * len(self.rivals) + 1)

    def _vision_mask(self) -> np.ndarray:
        """引擎视野（`World.visible_to` 的等价物）：自家格 + 八邻 + 瞭望塔半径 4 圆。

        视野之外的地形/资源/归属/建筑/敌军一律不可见——**和 LLM 玩家看到的一样多**
        （`mp_ai` 也是用 `visible_to` 过滤军队的）。自己的军队与自己的地块始终可见。
        """
        w, me, n = self.world, self.agent, self.map_size
        vis = np.zeros((n, n), np.float32)
        for (x, y) in w.own_tiles(me):
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < n and 0 <= yy < n:
                        vis[xx, yy] = 1.0
        r2 = WATCHTOWER_RADIUS ** 2
        for (tx, ty), t in w.tiles.items():
            if t["owner"] == me and t["buildings"].get("瞭望塔"):
                for dx in range(-WATCHTOWER_RADIUS, WATCHTOWER_RADIUS + 1):
                    for dy in range(-WATCHTOWER_RADIUS, WATCHTOWER_RADIUS + 1):
                        if dx * dx + dy * dy <= r2:
                            xx, yy = tx + dx, ty + dy
                            if 0 <= xx < n and 0 <= yy < n:
                                vis[xx, yy] = 1.0
        return vis

    def _obs(self) -> Obs:
        w, me, n = self.world, self.agent, self.map_size
        chans: list[np.ndarray] = []

        # 地形 one-hot（静态，reset 时缓存）
        for ter in TERRAINS:
            chans.append(np.array([[1.0 if self._terrain[x][y] == ter else 0.0
                                    for y in range(n)] for x in range(n)], dtype=np.float32))

        # 资源（只有已探明的地块——未占领的地块资源在游戏里本就未知）
        for r in ("矿石", "黄金", "耕地", "石油", "木头"):
            arr = np.zeros((n, n), np.float32)
            mx = RES_MAX[r]
            for (x, y), t in w.tiles.items():
                arr[x, y] = t["resources"].get(r, 0) / mx
            chans.append(arr)

        # 归属 one-hot：我 / 各对手 / 无主
        n_own = 1 + len(self.rivals)
        own = np.zeros((n_own + 1, n, n), np.float32)
        own[n_own] = 1.0
        for (x, y), t in w.tiles.items():
            o = t["owner"]
            i = 0 if o == me else (1 + self.rivals.index(o) if o in self.rivals else -1)
            if i >= 0:
                own[i, x, y] = 1.0
                own[n_own, x, y] = 0.0
        for i in range(n_own + 1):
            chans.append(own[i])

        # 建筑数量
        for b in self.bnames:
            arr = np.zeros((n, n), np.float32)
            for (x, y), t in w.tiles.items():
                c = t["buildings"].get(b, 0)
                if c:
                    arr[x, y] = math.log1p(c) / 3.0
            chans.append(arr)

        # ---- 视野门控：上面这些「世界知识」通道（地形/资源/归属/建筑），视野外一律置 0
        vis = self._vision_mask()
        for i in range(len(chans)):
            chans[i] *= vis

        # 附加通道
        mine = np.zeros((n, n), np.float32)
        frontier = np.zeros((n, n), np.float32)
        for (x, y) in w.own_tiles(me):
            mine[x, y] = 1.0
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    xx, yy = x + dx, y + dy
                    if 0 <= xx < n and 0 <= yy < n:
                        frontier[xx, yy] = 1.0
        myhp = np.zeros((n, n), np.float32)
        foehp = np.zeros((n, n), np.float32)
        barhp = np.zeros((n, n), np.float32)
        for a in w.armies:
            if a["hp"] <= 0:
                continue
            x, y = a["x"], a["y"]
            if not (0 <= x < n and 0 <= y < n):
                continue
            hp = a["hp"] / 100.0
            if a["owner"] == me:
                myhp[x, y] += hp
            elif a["owner"] == "野人":
                barhp[x, y] += hp
            else:
                foehp[x, y] += hp
        foehp *= vis      # 敌军只在视野内可见（自家军队始终可见）
        barhp *= vis      # 野人同理
        pend = np.zeros((n, n), np.float32)
        bflag = np.zeros((n, n), np.float32)
        for (x, y), t in w.tiles.items():
            if t["owner"] == me:
                pend[x, y] = sum(t.get("pending", {}).values()) / 5.0
                bflag[x, y] = 1.0 if t["built_this_turn"] else 0.0
        chans += [mine, frontier, myhp, foehp, barhp, pend, bflag, vis]

        # 军队特征表（候选动作按 army_index 引用）
        armies = self._refresh_armies()
        afeat = np.zeros((len(armies), ARMY_FEAT), np.float32)
        for i, a in enumerate(armies):
            k = unit_kind(a)
            for j, uk in enumerate(self.unames):
                afeat[i, j] = 1.0 if k == uk else 0.0
            afeat[i, len(self.unames)] = a["hp"] / max(1, unit_max_hp(a))
            afeat[i, len(self.unames) + 1] = (a["x"] + 0.5) / n
            afeat[i, len(self.unames) + 2] = (a["y"] + 0.5) / n
            afeat[i, len(self.unames) + 3] = 1.0 if a.get("engaged") else 0.0
            afeat[i, len(self.unames) + 4] = 1.0 if a.get("moved_turn") == w.turn else 0.0

        grid = np.stack(chans, axis=0)

        # ---- 全局向量
        g: list[float] = []
        for k in RES_KEYS:
            g.append(res_get(w, me, k) / 1000.0)
        for gd in TRADEABLE:
            g.append(w.prices.get(gd, MARKET[gd]) / MARKET[gd])
        for gd in TRADEABLE:
            g.append(w.equilibrium.get(gd, MARKET[gd]) / MARKET[gd])
        sp = (w.spend.get(me) or {})
        for k in ("build", "recruit", "supply"):
            g.append(float(sp.get(k, 0.0)) / 2000.0)
        g.append(w.turn / max(1, self.max_turns))
        g.append(self.turn_actions / max(1, self.max_actions_per_turn))
        g.append(1.0 if w.grid_short.get(me) else 0.0)
        eh, en = (w.energy_report.get(me) or (0, 0, False))[:2]
        g.append(eh / 20.0)
        g.append(en / 20.0)
        tot = {b: 0 for b in self.bnames}
        for t in w.tiles.values():
            if t["owner"] == me:
                for b in self.bnames:
                    tot[b] += t["buildings"].get(b, 0)
        for b in self.bnames:
            g.append(math.log1p(tot[b]) / 3.0)
        my_armies = w.nation_armies(me)
        g.append(len(my_armies) / 10.0)
        g.append(sum(a["hp"] for a in my_armies) / 1000.0)
        for uk in self.unames:
            g.append(sum(1 for a in my_armies if unit_kind(a) == uk) / 10.0)
        for r in self.rivals:
            if r in w.nations:
                g += [len(w.own_tiles(r)) / (n * n), len(w.nation_armies(r)) / 10.0,
                      sum(a["hp"] for a in w.nation_armies(r)) / 1000.0]
            else:
                g += [0.0, 0.0, 0.0]
        g.append(len(w.own_tiles(me)) / (n * n))

        assert len(g) == self.glob_size(), f"全局向量维度不符：{len(g)} != {self.glob_size()}"
        return Obs(grid=grid, glob=np.asarray(g, dtype=np.float32),
                   cand=self._cand_pack(afeat))

    def _cand_pack(self, army_feats: np.ndarray) -> dict:
        acts = self.legal_actions()
        k = len(acts)
        n = self.map_size
        null_tile = n * n
        null_army = army_feats.shape[0]
        t_idx = np.empty(k, np.int64)
        s_idx = np.empty(k, np.int64)
        tile_idx = np.full(k, null_tile, np.int64)
        army_idx = np.full(k, null_army, np.int64)
        amt_idx = np.zeros(k, np.int64)
        for i, a in enumerate(acts):
            t_idx[i] = KIND_INDEX[a.kind]
            table = self.sub_tables[a.kind]
            s_idx[i] = table.index(a.sub) if a.sub in table else 0
            if a.tile is not None:
                tile_idx[i] = a.tile[1] * n + a.tile[0]
            if a.kind in ("move", "attack", "retreat"):
                army_idx[i] = self.army_index.get(a.army, null_army)
            if a.amount in AMOUNTS:
                amt_idx[i] = AMOUNTS.index(a.amount)
        return {"actions": acts, "type_idx": t_idx, "sub_idx": s_idx, "tile_idx": tile_idx,
                "army_idx": army_idx, "amount_idx": amt_idx,
                "mask": np.ones(k, bool), "army_feats": army_feats,
                "n_armies": army_feats.shape[0]}

    # ------------------------------------------------------------------ 统计
    def summary(self) -> dict:
        w = self.world
        if w is None:
            return {}
        me = self.agent
        out = {"turn": w.turn, "alive": me in w.nations,
               "tiles": len(w.own_tiles(me)) if me in w.nations else 0,
               "armies": len(w.nation_armies(me)) if me in w.nations else 0,
               "spend_total": w.spend_total(me) if me in w.nations else 0.0}
        sp = w.spend.get(me) or {}
        out.update({f"spend_{k}": float(sp.get(k, 0.0)) for k in ("build", "recruit", "supply")})
        return out


def res_get(world: World, name: str, key: str) -> int:
    n = world.nations.get(name)
    return int(n.res.get(key, 0)) if n is not None else 0
