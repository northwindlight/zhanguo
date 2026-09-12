# -*- coding: utf-8 -*-
"""战国 RL 训练环境（单智能体）。

**单国独局**：地图上只有 `agent` 一国（可选加对手，默认没有），扩张只能打野人。
不依赖 LLM 层（无 tool schema / 无文本面板 / 无国策 plan），只调游戏层 `game.py` + `mp.py`。

**引擎与 main 逐字相同**（2026-09-12 起不再砍外交）：本环境不含外交动作、观测里也没有
外交建筑（见 `bnames`），但引擎那侧的战争/联盟代码都在——单国独局下它们不会触发。
要加对手时（`rivals=(...)`）它们就会生效，那时再谈外交是否进观测。

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

from game import (BUILDINGS, MARKET, MAX_SLOTS, TERRAIN_STATS,
                  TERRAINS, TRADEABLE, UNIT_TYPES, building_effect,
                  unit_kind, unit_max_hp, unit_speed)
from mp import World
from rl import vocab as V
from rl import features as F
from rl import jitter
from rl.vocab import (AF_ATK, AF_DX, AF_DY, AF_ENGAGED, AF_HP, AF_MOVED, AF_SPEED)

# 动作种类（固定顺序，模型的下标语义依赖它）
KINDS = ("build", "recruit", "move", "attack", "retreat", "buy", "sell", "end_turn")
KIND_INDEX = {k: i for i, k in enumerate(KINDS)}

# 数量档位（征兵/买卖）。离散化：不做连续回归，降低动作空间难度。
AMOUNTS = (1, 2, 3, 4, 5, 6, 8, 10, 12, 16)

# 军队特征维度：兵种 one-hot(len(V.UNIT)=5，含 2 个留位) + 血量 + 速度 + 攻击力
#                + 坐标 x/y + 交战 + 本回合已动
# ⚠ 取 `V.UNIT`（含留位）而不是 `game.UNIT_TYPES`（3）—— 兵种一留位，宽度就与引擎表脱钩。
#   这是**模型输入宽度**：改了它，`model.py`/`transformer.py` 的 `nn.Linear` 也跟着变
#   （显式报错，安全），但它同时是 §10.6 里的口径项。
ARMY_FEAT = V.AF_WIDTH       # = 12（归属3 + 兵种one-hot len(UNIT) + hp/speed/atk/dx/dy/交战/已动）
assert ARMY_FEAT == V.AF_WIDTH, f"ARMY_FEAT={ARMY_FEAT} vs AF_WIDTH={V.AF_WIDTH}"

# 候选**一律不限额**：合法动作全部进候选。
#
# 早先按类别限额（build/move 各 64…）+ 按回合轮转起点，理由是"帝国越大候选越多，
# 不设上界会让 PPO 每步打分开销随国力线性膨胀"。但那个"长期无死角"只在跨回合
# 看时成立，代价是**每一步都有一批动作根本不在清单里**：
#   · 后期 15 支军队只有 2~3 支能下令；BC 时 12% 的老师标签对不上候选
#     （move 29.4%、build 33.2%），而那正是这支 AI 唯一会扩张的部分。
#   · 市场更惨：候选按「商品×数量档」成网格生成、同商品连着排，截断后
#     排在后头的粮食/补给在某一步**根本不存在**，策略连"买粮食"都表达不出来。
# 表达力比那点算力值钱，所以全放开。候选数量级见 rl/README.md。
CATS = ("build", "recruit", "move", "attack", "retreat", "buy", "sell")

# 回合内动作数的观测**不再用固定分母归一化**（2026-09-11 改）。
# 原来按 `min(1.0, turn_actions / ACT_REF)`、ACT_REF=64 —— 学生实测走 67~71 步/回合，
# **从第 64 步起恒为 1.0**，而「走了多少步」正是判断「该不该停手」最需要的那一维
# （DAgger 里 end_turn 标签占 ~90%，这一维饱和等于把判据抹掉了）。
# 现在用 `log1p(turn_actions)/3`：单调、无上界、与领地数/建筑数同口径。
# 保留这个名字只为兼容旧引用与说明；它已不参与观测计算。
ACT_REF = 64

# 位置特征的**绝对尺度**（单位：格）。军队位置按「相对家的偏移 / POS_SCALE」编码。
# ★不能用地图边长归一化 —— 那既泄漏地图尺寸，又把「我在地图哪个位置」喂了进去，
#   而这两件事智能体都不可能知道（地图多大不可知、边界从未探索过）。
# 选 32：移动速度 1~2 格/回合，32 格≈十几回合的路程，覆盖有意义的战术距离。
POS_SCALE = 32.0

# 每回合动作数的**安全上界**（不是游戏规则，是防死循环）。
# 实测老师最多 15 个/回合，所以它从来卡不到；但学会之后想「一次建 100 块地、
# 调动全部军队」的 agent 会被这里挡住，所以给得宽。回合该结束由 agent 自己
# 用 end_turn 决定，不该由这个数替它决定。
ACT_SAFETY = 512
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
    def __init__(self, *, map_size: int = 20, map_sizes: tuple[int, ...] | None = None,
                 seed: int = 0, agent: str = "秦",
                 rivals: tuple[str, ...] = (), max_turns: int = 40,
                 max_actions_per_turn: int = ACT_SAFETY, reward_scale: float = 0.01,
                 rules_jitter: float = 0.0):
        # ★RL 是**通用**的：真实游戏的地图由玩家选（16×16 / 50×50 / 100×100 都可能），
        #   所以**地图尺寸必须每局可变**（用户 2026-09-11 口径）。
        #   传 `map_sizes` 就每局按种子重采样一个；不传 = 固定 `map_size`（旧行为）。
        #   注意智能体**观察不到**地图尺寸（迷雾挡着、从未探索过边界），
        #   所以任何按 `n*n` 归一化的特征都是泄漏 —— 见 `_obs` 里领地数那条注释。
        self.map_sizes = (tuple(int(s) for s in map_sizes) if map_sizes
                          else (int(map_size),))
        self.map_size = self.map_sizes[0]
        self.seed = int(seed)
        self.agent = agent
        self.rivals = tuple(rivals)          # 默认无对手（单国独局）
        self.max_turns = int(max_turns)
        self.max_actions_per_turn = int(max_actions_per_turn)
        self.reward_scale = float(reward_scale)
        # ★训练期域随机化的幅度（0 = 关，见 `rl/jitter.py` 与 §10.4）。
        #   默认关：评估/看海/对拍一律真值 —— 只有训练采样期才该抖。
        self.rules_jitter = float(rules_jitter)

        # ★ 三张子表**全部取自冻结词表**（`rl/vocab.py` 的 `SUB_TABLE_OF`），不取 `game.*`
        #   —— 引擎加一项、或 main 变动，都不该动观测/动作空间的**形状**。
        #   2026-09-12 留位（§10.3）后每张表都是「活跃项 + 留位项」：
        #     `bnames` 19 = 15 真建筑 + 4 留位（**不含** MAIN_ONLY 的「外交中心」）
        #     `unames` 5 = 3 + 2 留位；`goods` 8 = 6 + 2 留位
        #   留位项**只占下标与宽度**：不生成候选（候选走 `buildable`/`recruitable`/
        #   `tradeable`），内容向量全零，token 组里 `mask=0`。将来加实体 = 填一个留位槽
        #   → 下标不动、宽度不变、旧 ckpt 不作废。
        self.bnames = V.SUB_TABLE_OF["build"]    # 19 = 15 真建筑 + 4 留位（不含外交中心）
        self.unames = V.SUB_TABLE_OF["recruit"]  # 5 = 3 + 2 留位
        self.goods = V.SUB_TABLE_OF["buy"]       # 8 = 6 + 2 留位
        self.buildable = V.BUILDABLE             # 15：真正能建（候选枚举用这个）
        self.recruitable = V.RECRUITABLE         # 3
        self.tradeable = V.TRADEABLE_REAL        # 6
        assert len(self.rivals) <= V.NATION_SLOTS, "对手数超过国槽上限"
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
        self._last_ok = True                    # 上一步的成败（见 step）
        self._last_reject = None                # 上一步被拒的原因类（None = 成功）
        self.army_ids: list[int] = []           # 当前军队列表（下标 → 番号 id）
        self.army_index: dict[int, int] = {}

    # ------------------------------------------------------------------ 生命周期
    def reset(self, seed: int | None = None) -> Obs:
        if seed is not None:
            self.seed = int(seed)
        # ★每局重采样**规则表**（域随机化，§10.4）：`rules_jitter=0`（默认）时
        #   走 `restore()` —— 评估/看海/对拍一律真值。开了就 `seed → 一套表`，
        #   同一 seed 必得同一套（可复现；记录见 `jitter.current()`）。
        #   它改的是 `game.*` 的**活表**：候选枚举、引擎结算、观测内容、老师
        #   （`build_econ`/`good_value`）读的都是同一份 —— 不同源就会学出假动力学。
        jitter.apply(self.seed, self.rules_jitter)
        # ★每局重采样地图尺寸（同一 seed 必得同一尺寸 —— 可复现）。
        if len(self.map_sizes) > 1:
            self.map_size = self.map_sizes[
                random.Random(self.seed ^ 0x9E3779B9).randrange(len(self.map_sizes))]
        n = self.map_size
        names = [self.agent] + [r for r in self.rivals if r != self.agent]
        self.world = World(size=n, seed=self.seed, nations=names)
        # ★「家」= 开局那格（与引擎 mp.py 里 `home = own_tiles(name)[0]` 同口径），
        #   **本局内固定**，作为模型坐标系的唯一原点。智能体永远以家为 (0,0) 看世界 ——
        #   于是策略天然平移无关，也看不出自己在地图的哪个位置（它本就不该知道：
        #   地图多大不可知、边界从未探索过，用户 2026-09-11 口径）。
        _own0 = self.world.own_tiles(self.agent)
        self.anchor = _own0[0] if _own0 else (n // 2, n // 2)
        self.rng = random.Random(self.seed ^ 0x5F5F5F5F)
        self._terrain = [[self.world.tile_terrain(x, y) for y in range(n)] for x in range(n)]
        self.turn_actions = 0
        self._done = False
        self._last_ok = True                    # 开局没有"上一步"
        self._last_reject = None
        self.world.begin_turn()
        self.prev_spend = self.world.spend_total(self.agent)
        return self._obs()

    def step(self, action: Action):
        """执行一个动作。返回 (obs, reward, done, info)。done 时 obs 为 None。"""
        assert self.world is not None and not self._done, "env 未 reset 或已结束"
        ok, msg = self._apply(action)
        # ★记下**上一步的反馈**（成败 + 被拒原因）—— 候选集放开之后（不再按钱/货预过滤）
        #   ~49% 的候选是"点不动"的；没有这份反馈，策略分不清「点了无效选项」和
        #   「做了中性动作」（两者对它都是"什么都没发生"），学不出甄别。
        #   而玩家**本来就收得到**这份信息（LLM 的工具返回值就是这个文案）——
        #   编码它不是"帮模型计算"，是补齐信息集。
        self._last_ok = bool(ok)
        self._last_reject = None if ok else F.reject_reason(msg)
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
        """我方军队列表（按番号排序）——候选动作与观测共用同一套下标。

        **不设上限**：早先截到前 MAX_ARMIES 支，结果军队一多就有队伍
        「本回合无法下令」——策略在大地图上根本指挥不动自己的兵。
        collate 按 batch 内最大军队数补零，变长本来就支持。
        """
        armies = sorted(self.world.nation_armies(self.agent), key=lambda a: a["id"])
        self.army_ids = [a["id"] for a in armies]
        self.army_index = {aid: i for i, aid in enumerate(self.army_ids)}
        return armies

    def legal_actions(self) -> list[Action]:
        w, me, n = self.world, self.agent, self.map_size
        if me not in w.nations:
            return [Action("end_turn")]
        self._refresh_armies()
        cats: dict[str, list[Action]] = {k: [] for k in CATS}
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
            for b in self.buildable:      # ★ 只枚举**真建筑**：留位槽不产生候选
                info = BUILDINGS[b]
                lv = eff[b]
                if info["kind"] == "castle" and lv >= info["max_level"]:
                    continue          # 满级（结构约束，不是「买得起」）
                # ★★ 不按「买不起」过滤（2026-09-12 用户口径）：以前这里有一整套造价计算
                #   （地形惩罚/工程院减免/匈奴加成）+ gold < cost 判断，买不起的建筑
                #   连候选都不生成。后果两个都是坏的：
                #     ① 模型看不见目标 —— 没 350 金时「建兵营」这个选项根本不存在，
                #        它连「攒钱的目标」都没有（用户原话）；
                #     ② 候选存在本身泄露一比特「我此刻买得起+合规」，而契约要求
                #        信息集 = 玩家看得见的那一份。
                #   现在候选 = 引擎的合法动作空间（引擎会拒买不起的、如实报错）。
                cr = info.get("cap_resource")
                if cr is not None:
                    have = t["resources"].get(cr, 0)
                    if have <= 0 or eff[b] >= have:
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
            for kind in self.recruitable:   # ★ 只枚举**真兵种**（留位槽不产生候选）
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
                # ★ 不再按「原料够几支」截断（同 build：买不起也进候选，让引擎如实拒）。
                #   cap（每兵营/军屯每回合的编制上限）是规则，保留。
                for amt in AMOUNTS:
                    if amt <= cap:
                        cats["recruit"].append(Action("recruit", kind, (x, y), 0, amt))

        # ---- move / attack / retreat（不限额：所有我方军队都进候选）
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
        for g in self.tradeable:      # ★ 只枚举**真物资**（留位槽不产生候选）
            for amt in AMOUNTS:
                # ★ 买卖同样不按钱/货过滤：卖超过存量、买超过现金都进候选，
                #   由引擎如实拒绝。挂单是合法动作，只是会失败。
                cats["sell"].append(Action("sell", g, None, 0, amt))
                cats["buy"].append(Action("buy", g, None, 0, amt))

        # ---- 不限额：**合法动作全部进候选**
        # 早先按类别限额 + 按回合轮转起点，为的是把候选集大小压住。但那个
        # 「长期无死角」只在 RL 跨回合看时成立，代价是**每一步都有一批动作
        # 根本不在清单里**：后期 15 支军队只能动 2~3 支，BC 更是直接丢掉
        # 12% 的老师标签（move 29%、build 33%）。表达力比那点算力值钱。
        out: list[Action] = []
        for lst in cats.values():
            out.extend(lst)
        out.append(Action("end_turn"))
        return out

    # ------------------------------------------------------------------ 观测
    def obs_channels(self) -> list[str]:
        ch = [f"terrain:{t}" for t in V.TERRAIN]
        ch += [f"res:{r}" for r in ("矿石", "黄金", "耕地", "石油", "木头")]
        ch += [f"owner:{n}" for n in V.OWNER_CHANNELS]   # 11 槽（自己/8 国槽/中立/野人）
        ch += [f"bld:{b}" for b in self.bnames]
        ch += ["build_cost"]        # 该格实际建造金价倍率 − 1（地形惩罚 × 工程院减免）
        ch += ["mine", "frontier", "my_army_hp", "foe_army_hp", "barb_army_hp",
               "pending", "built_this_turn", "visible", "home",
               # ↓ 2026-09-12 并入本版（TOKEN_DESIGN §10.5 / §9.3）：记忆落地前**恒零**，
               #   但**宽度先占住** —— 将来做记忆时不再变形状、不再废 ckpt。
               "remembered",     # 曾经探明过（地图记忆）
               "probe",          # 盲行军撞墙/遭遇的回报（探测记忆）
               ]
        return ch

    def glob_channels(self) -> list[str]:
        """全局向量**逐位通道名**，顺序与 `_obs()` 里 `g` 的构建顺序**逐个对应**。

        为什么要有它：`glob` 是个裸向量，谁想按语义取某一维（比如"把累计消费那三栏
        摘掉"）就只能写死下标 —— 而写死下标在本仓库反复出事（`vocab.py` 的 docstring
        整篇都在讲这个）。给名字之后，取维靠 `index("spend:build")`，**插了新通道也
        不会静默错位**（找不到就抛 ValueError，比取错维强）。

        `_obs()` 里有断言盯着 `len(glob_channels()) == len(g)`：改了构建顺序却忘了
        改这里，会当场炸。
        """
        ch = [f"res:{k}" for k in RES_KEYS]
        ch += [f"price:{g}" for g in self.goods]      # 8（含 2 留位，留位恒 0）
        ch += [f"eq:{g}" for g in self.goods]
        ch += [f"spend:{k}" for k in ("build", "recruit", "supply")]
        ch += ["turn", "turn_actions", "grid_short", "energy_have", "energy_need"]
        ch += [f"bld:{b}" for b in self.bnames]       # 19（含 4 留位，留位恒 0）
        ch += ["armies", "army_hp"]
        ch += [f"army_kind:{u}" for u in self.unames]  # 5（含 2 留位）
        # ★ 这里曾有「每个对手 3 维（领地/军队/兵力）」—— 2026-09-12 用户拍板**删掉**：
        #   它让 glob 宽度随对手数变（58→61→67），加对手就要废 ckpt；而 per-国信息
        #   本来就该由 **N 组 token**（8 槽 × 16 维，现成、恒 mask）承载 ——
        #   按 §9.6，加对手那一炉本来就要跟地图/外交记忆同时落地，那时 N 组才填值。
        #   删掉之后 glob 宽度**与对手数无关**。
        ch += ["own_tiles"]
        # ★上一步的反馈（成败 + 被拒原因 one-hot）。玩家从工具返回值拿到的就是它。
        ch += ["last_ok"] + [f"last_reject:{r}" for r in F.REJECT_REASONS]
        return ch

    def glob_size(self) -> int:
        # ★**与对手数无关**（对手段已删，见 `glob_channels` 的注释）
        return (len(RES_KEYS) + 2 * len(self.goods) + 3 + 2 + 2
                + len(self.bnames) + 3 + len(self.unames) + 1
                + 1 + len(F.REJECT_REASONS))          # last_ok + 被拒原因 one-hot

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
        _tw = building_effect("瞭望塔", "vision_radius")
        r2 = _tw * _tw
        for (tx, ty), t in w.tiles.items():
            if t["owner"] == me and t["buildings"].get("瞭望塔"):
                for dx in range(-_tw, _tw + 1):
                    for dy in range(-_tw, _tw + 1):
                        if dx * dx + dy * dy <= r2:
                            xx, yy = tx + dx, ty + dy
                            if 0 <= xx < n and 0 <= yy < n:
                                vis[xx, yy] = 1.0
        return vis

    def _visible_bbox(self, acts: list) -> tuple[int, int, int, int]:
        """观测网格的外接框（绝对坐标，闭区间）。

        ★这是「可见区裁剪」的关键一步：模型看到的**不是整幅地图**，而是
        「看得见的东西 + 候选动作指向的格」的外接框 —— 成本 O(可见区)，
        **与地图尺寸无关**（用户 2026-09-11 定的方案）。

        · 只裁可见区，所以数组形状反映的是**帝国的铺开程度**，不是地图大小 ——
          不泄漏地图尺寸，也不会像整幅地图那样在边缘 padding 处暴露「我在图角上」。
        · 候选格必须全在框内（否则卷积特征表查不到），所以要并上 `acts` 的落点；
          野地里的军队周围不在视野掩码内，但它自己那格一定要在。
        """
        w, n = self.world, self.map_size
        vis = self._vision_mask()
        xs, ys = [], []
        idx = np.argwhere(vis > 0)
        if len(idx):
            xs += [int(idx[:, 0].min()), int(idx[:, 0].max())]
            ys += [int(idx[:, 1].min()), int(idx[:, 1].max())]
        for a in acts:
            if a.tile is not None:
                xs.append(int(a.tile[0]))
                ys.append(int(a.tile[1]))
        for a in w.armies:                       # 自家军队始终可见
            if a["owner"] == self.agent and a["hp"] > 0:
                xs.append(int(a["x"]))
                ys.append(int(a["y"]))
        ax, ay = self.anchor
        xs.append(ax)
        ys.append(ay)
        x0, x1 = max(0, min(xs)), min(n - 1, max(xs))
        y0, y1 = max(0, min(ys)), min(n - 1, max(ys))
        return x0, y0, x1, y1

    def _obs(self) -> Obs:
        w, me, n = self.world, self.agent, self.map_size
        chans: list[np.ndarray] = []

        # 地形 one-hot（静态，reset 时缓存）。★按 `V.TERRAIN`（7，含 2 留位）出通道：
        #   留位地形没有任何格子会是它 → 恒零。将来加「海洋/河流」时宽度不变。
        for ter in V.TERRAIN:
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
        # ★归属段是**固定槽位**（自己 1 + 8 个国槽 + 中立 + 野人 = 11），
        #   2026-09-12 钉死：以前是 `1 + len(rivals) + 1` 动态出通道 ——
        #   那样"加对手"就会改观测宽度、废掉 ckpt。8 国槽是设计文档 §1 的计划
        #   （`vocab.OWNER_CHANNELS`），实现现在跟上了。
        #   槽位按 `rivals` 的**下标**绑定（本局内固定）；对手死了槽也不回收 ——
        #   槽位是身份，不是"当前还活着几个"。
        n_slots = len(V.OWNER_CHANNELS)
        own = np.zeros((n_slots, n, n), np.float32)
        i_neutral = V.OWNER_CHANNELS.index("neutral")
        own[i_neutral] = 1.0                       # 没主的地（含未探明）算中立
        for (x, y), t in w.tiles.items():
            o = t["owner"]
            i = 0 if o == me else (1 + self.rivals.index(o) if o in self.rivals else -1)
            if i >= 0:
                own[i, x, y] = 1.0
                own[i_neutral, x, y] = 0.0
        for i in range(n_slots):
            chans.append(own[i])

        # 建筑数量
        for b in self.bnames:
            arr = np.zeros((n, n), np.float32)
            for (x, y), t in w.tiles.items():
                c = t["buildings"].get(b, 0)
                if c:
                    arr[x, y] = math.log1p(c) / 3.0
            chans.append(arr)

        # 建造金价倍率（地形惩罚 × 工程院减免）：模型不必自己把两者乘起来 ——
        # ★它俩量级差 3 倍，混算是最容易学错的地方（见 rl/features.py:build_cost_factor）
        bcost = np.zeros((n, n), np.float32)
        for (x, y), t in w.tiles.items():
            bcost[x, y] = F.build_cost_factor(t["terrain"], bool(t["buildings"].get("工程院"))) - 1.0
        chans.append(bcost)

        # ---- 视野门控：上面这些「世界知识」通道（地形/资源/归属/建筑/造价），视野外一律置 0
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
        # ★家的位置单开一个通道。模型看到的是**可见区外接框**而不是整幅地图，
        #   所以得明确告诉它家在哪；有了它，一切相对位置都能自己推出来，
        #   而它在整幅地图上的绝对位置仍然不可知（那本来就不该知道）。
        home_ch = np.zeros((n, n), np.float32)
        home_ch[self.anchor[0], self.anchor[1]] = 1.0
        chans += [mine, frontier, myhp, foehp, barhp, pend, bflag, vis, home_ch]
        # ---- 记忆两通道（§9.3）：**本版恒零**，只占宽度。见 `obs_channels()` 的注释。
        #   为什么现在就占：观测形状一变就要重炼，而"加记忆"迟早要来 —— 宽度先钉住。
        chans += [np.zeros((n, n), np.float32), np.zeros((n, n), np.float32)]

        # ---- ★裁到「可见区外接框」（用户 2026-09-11 定的方案）：
        #   成本 O(可见区)，**与地图尺寸无关**（100×100 不再等于 39× 的算力）。
        #   数组形状反映的是帝国的铺开程度，不是地图大小，所以既不泄漏尺寸，
        #   也不会像整幅地图那样在边缘 padding 处暴露「我在图角上」。
        acts = self.legal_actions()
        x0, y0, x1, y1 = self._visible_bbox(acts)
        self._bbox = (x0, y0, x1, y1)
        chans = [c[x0:x1 + 1, y0:y1 + 1] for c in chans]

        # 军队特征表（候选动作按 army_index 引用）
        armies = self._refresh_armies()
        afeat = np.zeros((len(armies), ARMY_FEAT), np.float32)
        for i, a in enumerate(armies):
            k = unit_kind(a)
            for j, uk in enumerate(self.unames):
                afeat[i, j] = 1.0 if k == uk else 0.0
            afeat[i, AF_HP] = a["hp"] / max(1, unit_max_hp(a))
            # ★速度与攻击力直接写进**军队行**（用户 2026-09-12）：它们在 u 组里按兵种有，
            #   但要模型自己学"这支是哪种兵"的链接；直接给出来就不必学。
            afeat[i, AF_SPEED] = unit_speed(a) / 2.0
            afeat[i, AF_ATK] = float(UNIT_TYPES[k].get("atk", 0)) / 100.0
            # ★军队位置 = **相对家的偏移**，除以**绝对尺度** POS_SCALE（不是地图边长）。
            #   原来写的是 `(x+0.5)/n` —— 既泄漏地图尺寸，又泄漏「我在地图哪个位置」
            #   （智能体不可能知道：地图多大不可知、边界从未探索过）。
            ax, ay = self.anchor
            afeat[i, AF_DX] = (a["x"] - ax) / POS_SCALE
            afeat[i, AF_DY] = (a["y"] - ay) / POS_SCALE
            afeat[i, AF_ENGAGED] = 1.0 if a.get("engaged") else 0.0
            afeat[i, AF_MOVED] = 1.0 if a.get("moved_turn") == w.turn else 0.0

        grid = np.stack(chans, axis=0)

        # ---- 全局向量
        g: list[float] = []
        for k in RES_KEYS:
            g.append(res_get(w, me, k) / 1000.0)
        # ★ 走 `self.goods`（8，含 2 留位）而不是 `game.TRADEABLE`（6）：留位项在引擎里
        #   没有价格，一律 0 —— 它们只是把宽度占住（将来加物资时填进这个槽）。
        for gd in self.goods:
            base = MARKET.get(gd, 0)
            g.append(w.prices.get(gd, base) / base if base else 0.0)
        for gd in self.goods:
            base = MARKET.get(gd, 0)
            g.append(w.equilibrium.get(gd, base) / base if base else 0.0)
        sp = (w.spend.get(me) or {})
        for k in ("build", "recruit", "supply"):
            g.append(float(sp.get(k, 0.0)) / 2000.0)
        g.append(w.turn / max(1, self.max_turns))
        # ★本回合已走步数：**绝对对数尺度，不设上限、不饱和**。
        #   原来写的是 `min(1.0, turn_actions / ACT_REF)`（ACT_REF=64）——
        #   学生实际会走 67~71 步/回合，**从第 64 步起就恒为 1.0**，
        #   模型分不清「走了 70 步」和「走了 500 步」，而这恰恰是判断
        #   「该不该停手」最需要的那一维。改成 log1p/3（与领地数、建筑数同口径）：
        #   0→0、8→0.73、64→1.39、512→2.08，单调且永不饱和。
        g.append(math.log1p(self.turn_actions) / 3.0)
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
        # （这里曾有「每个对手 3 维」—— 2026-09-12 删，见 `glob_channels` 的注释：
        #   per-国信息归 N 组 token，glob 因此与对手数无关。）
        # ★★ 领地数用**绝对量**，不再除以 `n*n`（2026-09-11 用户口径：
        #   「对于模型而言，地图大小其实是不可知的 —— 他们并不知道每次玩的地图多大，
        #     也从未探索过边界」）。
        #   `n*n` 是**真实地图面积**，智能体不可能知道；而且它把「占全图 5%」这种
        #   尺度相关量喂了进去 —— 占 5% 在 16×16 和 100×100 上是完全不同的事。
        #   改成 `log1p(领地数)/3`：与同文件里建筑数的口径一致（`log1p(tot[b])/3.0`），
        #   单调、无上界、**与地图尺寸无关**。
        g.append(math.log1p(len(w.own_tiles(me))) / 3.0)
        # 上一步的反馈：成败 1 维 + 原因 one-hot（成功时全 0）
        g.append(1.0 if self._last_ok else 0.0)
        for r in F.REJECT_REASONS:
            g.append(1.0 if self._last_reject == r else 0.0)

        assert len(g) == self.glob_size(), f"全局向量维度不符：{len(g)} != {self.glob_size()}"
        # ★名字表与构建顺序必须逐位对齐 —— 改了 `g.append` 的顺序却忘了改
        #   `glob_channels()`，这里会当场炸，而不是让某个 `index("spend:build")` 悄悄取错维。
        assert len(self.glob_channels()) == len(g), (
            f"glob_channels() 有 {len(self.glob_channels())} 项，实际 {len(g)} 项 —— "
            f"加/删通道时两处要一起改")
        return Obs(grid=grid, glob=np.asarray(g, dtype=np.float32),
                   cand=self._cand_pack(afeat, acts))

    def _cand_pack(self, army_feats: np.ndarray, acts: list) -> dict:
        k = len(acts)
        # ★下标按**可见区外接框**算，不是整幅地图 —— 网格已经裁过了（见 `_obs`），
        #   再拿 `self.map_size` 当行宽会全部错位。行优先：`下标 = x_rel*Wv + y_rel`
        #   （网格是 `[C, x, y]`，`fmap.flatten(2)` 展平后也是 `x*W + y`）。
        x0, y0, x1, y1 = self._bbox
        hv, wv = x1 - x0 + 1, y1 - y0 + 1
        null_tile = hv * wv
        null_army = army_feats.shape[0]
        t_idx = np.empty(k, np.int64)
        s_idx = np.empty(k, np.int64)
        # ★存**外接框内的相对坐标**，不存扁平下标 —— 拼批时要按补齐后的行宽重算
        #   （不同局的地图/可见区大小不同，`collate` 会把网格补到批内最大）。
        #   -1 = 这个候选没有落点（buy/sell/end_turn）。
        tile_dx = np.full(k, -1, np.int64)
        tile_dy = np.full(k, -1, np.int64)
        # ★**相对家**的那一对（模型的落点特征用它，见 `transformer.cand_pos_block`）。
        #   为什么要两对：候选落点的**位置特征**必须与 token 组（M 组 patch 中心、
        #   A 组军队）**同原点**，而 token 用的是"相对家"（`tokenize.py`）；
        #   而 `tile_dx/tile_dy` 是"相对可见区外接框原点"，拼批时算扁平下标要用它。
        #   以前只有后者 ⇒ 模型得自己学一个每帧变化的偏移量（家 − 框原点）才能把
        #   "候选在哪"和"patch 在哪"对上 —— 白费劲。两套并存，各司其职。
        tile_hx = np.full(k, -1, np.int64)
        tile_hy = np.full(k, -1, np.int64)
        army_idx = np.full(k, null_army, np.int64)
        amt_idx = np.zeros(k, np.int64)
        for i, a in enumerate(acts):
            t_idx[i] = KIND_INDEX[a.kind]
            table = self.sub_tables[a.kind]
            s_idx[i] = table.index(a.sub) if a.sub in table else 0
            if a.tile is not None:
                tile_dx[i] = a.tile[0] - x0               # 框内相对（索引用）
                tile_dy[i] = a.tile[1] - y0
                tile_hx[i] = a.tile[0] - self.anchor[0]   # ★相对家（特征用）
                tile_hy[i] = a.tile[1] - self.anchor[1]
            if a.kind in ("move", "attack", "retreat"):
                army_idx[i] = self.army_index.get(a.army, null_army)
            if a.amount in AMOUNTS:
                amt_idx[i] = AMOUNTS.index(a.amount)
        # ★ 规则表内容（§10.2 载体 B）：每个 kind 一张 `(n_sub, F_kind)` 的**现算**表。
        #   模型用它把"这座建筑现在划不划算"算出来；只有 sub_idx 查表的话，
        #   引擎一改数值（石油能源厂降价）模型就无从适应。
        #   必须**在这里现算**（而不是模型侧缓存）：域随机化每局就地改 game 的表，
        #   缓存会把上一局的数值烤进这一局（`rl/features.py` 的纪律 1）。
        content = {k: F.content_table_for(k) for k in KINDS
                   if F.CONTENT_DIM_OF_KIND[k] > 0}
        return {"actions": acts, "type_idx": t_idx, "sub_idx": s_idx,
                "tile_dx": tile_dx, "tile_dy": tile_dy,
                "tile_hx": tile_hx, "tile_hy": tile_hy,
                "tile_hw": (hv, wv),          # 本帧外接框尺寸（collate 补齐时要看）
                "army_idx": army_idx, "amount_idx": amt_idx,
                "mask": np.ones(k, bool), "army_feats": army_feats,
                "n_armies": army_feats.shape[0],
                "content": content}

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
