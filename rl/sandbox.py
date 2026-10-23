# -*- coding: utf-8 -*-
"""RL 沙盒：一局 8×8 攻取国祚。

★ 本文件是 2026-10-22 审查（`review-feat-rl-tactics-20261022.md`）后的**重写版**。
  旧版有一层 `rl/engine.py` 门面（1648 行），把新引擎包成旧 `world.*`，
  自己记回合号/地块表/军队表/建筑表，还自己复刻了野人、摊分、亡国、新兵等**引擎规则**
  ⇒ 引擎改了规则，RL 侧不跟着改，**而且不报错**。那份门面**已删除**。

  现在的形状：

      rl/release.toml          发行版清单（不硬编码在代码里）
        ├── ../mods/experimental   主线原生 mod + `research:combat-probabilities`
        ├── rl/mods/train         `rl:sandbox` / `rl:truth` / `rl:odds`
        └── rl/mods/scenario      `rl:scenario`（摆局的**唯一出处**）
      rl/env.py               能力客户端：只**转发** + **现算**投影，**不缓存**
      rl/sandbox.py           ← 本文件：RL 侧状态（回合序、待办、记忆账本）

★ 三条红线（review §8）
  ────────────────────
  ① 不复刻引擎规则：合法性、野人、伤害、亡国、新兵 —— 一律问 native；
  ② 不记影子状态：回合号读 `native:government.period`，地块问 `native:territory`；
  ③ 不吞引擎拒绝：引擎拒了就拿到**它自己的**回执（`status` / `message`）。

★ 身份
  ────
  · 国家：RL 侧说**中文短名**（甲/乙），引擎说**内容 ID**（`rl:n0`）——
    换词在 `rl/nations.py`（**纯函数**，不是状态）。
  · 军队：跨实体只认 **`Unit.gid`**（全局唯一）；`Unit.id` 是**本国序号**，
    甲的第 1 支与乙的第 1 支都是 `id=1` ⇒ 用它当身份会**撞车**（review P0-3）。
"""
from __future__ import annotations

import random

from . import evaluate
from . import nations as N
from . import scoring as S
from .env import Env
from .hall_memory import HallMemory
from .intel import Intel
from .vocab import PLAYER_NAMES
from .war_memory import WarMemory

# ★ 国家名池（**上限 6**，天干序）—— 唯一出处是 `vocab.PLAYER_NAMES`。
#   本文件里的"国家名"一律是**短名**；内容 ID 由 `rl/nations.py` 推。
PLAYERS = PLAYER_NAMES[:2]

# ------------------------------------------------------------------ 常量
T_MAX = 200                 # ★ 兜底上限（只防僵局）
BASE_CAP = 5                # ★ 补员基础上限（"上限 = 5 + 国土数 // 10"）
TILES_PER_CAP = 10          # ★ 每多少格国土涨 1 个补员名额
RESUPPLY_EVERY = 5          # ★ 每 5 回合补员一次
# ★ 旧公式"民兵上限 = 步兵 × 2"**已删**：那是 RL 侧自造的**第二份上限口径**
#   （引擎的真上限是"每座军屯 2 支"）⇒ 复刻引擎规则，review §8 禁。
#   这里只留名字给外部引用；实际口径见 `resupply()`（**问引擎**）。
MILITIA_PER_FOOT = 2
# ★ 国家数 = 地图面积 / 60（用户 2026-09-25 定的：起炉范围 12-20 要能出 3-5 国）
TILES_PER_NATION = 60
# ★ 下限 3 是**目的**（解僵局），不是几何 ⇒ 它**优先于**面积估计。
N_NATIONS_MIN = 3
N_NATIONS_MAX = 5

# ★ 抽"回合偏移"的盐（与开局位置、国土各走各的随机流）。
TURN_OFFSET_SALT = 0x7A17

# ★ 抽结盟的盐（与地图、开局位置、回合偏移、国土**各走各的**）。
ALLIANCE_SALT = 0x2A11

# ★ 建筑内容 ID。
TOWN_HALL = "native:town-hall"
CASTLE = "native:castle"
BARRACKS = "native:barracks"

# ★ 引擎的"野人"归属标记。
BARBARIAN = "野人"

# ★ 摆局的几何口径**归剧本管**（`rl/mods/scenario/mod.py`）——
#   这里只做**转发**，免得两处各算一套（那必然漂）。
from .mods.scenario.mod import min_margin, territory_range  # noqa: E402,F401


def n_nations_for(size: int) -> int:
    """地图大小 → 国家数。

    ★ 口径（用户 2026-09-24：「3 人起步 + `n_nations = f(size)`」）：
      面积 / 64 起步 ⇒ 8×8 给 1 ⇒ 夹到 **[2, 5]**。
    ★ 与 `min_margin(size, n)` 的配合：`n` 定下来之后，开局最小间距就是
      `min_margin` 给的 `size/√n` —— 两者是同一个几何的两个方向，别各调各的。
    """
    by_area = round(size * size / TILES_PER_NATION)
    return max(N_NATIONS_MIN, min(N_NATIONS_MAX, int(by_area)))


class KillLedger:
    """★ **累计战果账本**（击杀支数 + 打掉的血量）—— 用户 2026-09-25：

        「会不会太复杂了，**只计算我军杀掉的敌军来加分**就行了」
        「`W_HP × (我的血 − **看得见的**敌方血)` **也要改，和击杀一样**」

    ★★ **按 `gid` 记身份**（review P0-3）：旧版用 `a["id"]`（**本国序号**）当 key，
      甲的第 1 支与乙的第 1 支**撞成同一个 key** ⇒ 账目互相覆盖，
      **而且不报错**。现在用引擎的 `Unit.gid`（全局唯一）✔
    """

    def __init__(self):
        self._kills: dict = {}      # (凶手, 受害者) → 累计击杀支数
        self._dmg: dict = {}        # (凶手, 受害者) → 累计打掉的血

    def observe(self, env, before: dict) -> None:
        """结算后调一次。`before` = 结算前的 `{gid: (主人, x, y, 血)}`。"""
        now = {u.gid: u for u in env.armies()}
        # 每格上（结算前）有哪些国家在场 —— 结算一跑位置就变了，只能用快照判
        at: dict = {}
        for owner, x, y, _hp in before.values():
            at.setdefault((x, y), set()).add(owner)
        for gid, (victim, x, y, hp0) in before.items():
            unit = now.get(gid)
            if unit is None:
                loss, killed = int(hp0), True        # 战死：整条血都算打掉的
            else:
                loss, killed = int(hp0) - int(unit.hp), False
            if loss <= 0:
                continue                             # 没掉血（可能还回了血）⇒ 无事
            for killer in at.get((x, y), ()):
                if killer == victim or killer == BARBARIAN:
                    continue
                key = (killer, victim)
                self._dmg[key] = self._dmg.get(key, 0) + loss
                if killed:
                    self._kills[key] = self._kills.get(key, 0) + 1

    @staticmethod
    def _sum(table: dict, name, victims) -> int:
        if not name:
            return 0
        return sum(n for (k, v), n in table.items()
                   if k == name and (victims is None or v in victims))

    def kills_by(self, name, victims=None) -> int:
        """`name` **累计**杀了多少支军（`victims` 给了就只数那些受害者国）。"""
        return self._sum(self._kills, name, victims)

    def dmg_by(self, name, victims=None) -> int:
        """`name` **累计**打掉多少点血（`victims` 给了就只数那些受害者国）。"""
        return self._sum(self._dmg, name, victims)

    def snapshot(self):
        """`(击杀表, 血量表)` 的**副本**，都是 `{(凶手, 受害者): 累计}`。"""
        return dict(self._kills), dict(self._dmg)

    def clone(self) -> "KillLedger":
        k = KillLedger()
        k._kills, k._dmg = dict(self._kills), dict(self._dmg)
        return k


class Sandbox:
    """一局 8×8 攻取国祚。**规则归引擎、动作归 v11plus**。"""

    def __init__(self, seed: int = 0, size: int = 8, t_max: int = T_MAX,
                 war: bool = True, first: str | None = None,
                 halls_known: bool = False, n_nations: int | None = None,
                 wars: list[tuple[str, str]] | None = None,
                 territory: bool = True, alliances: str = "none",
                 starts: dict | None = None, env: Env | None = None):
        self.seed = seed
        self.size = size
        self.t_max = t_max
        self.war = war                    # 开局是否宣战（★不宣战 v11plus 不会进攻）
        # ★★ **开局国土随机**（用户 2026-09-25）：`False` ⇒ 只留十字那 5 格（老行为，版间对照用）。
        self.territory = bool(territory)
        # 本局**实际达成**的每国国土格数（测试/报表要看）
        self.territory_target = 0
        # ★★ **随机结盟**：`"none"`（缺省）或 `"random2v2"`。只对 **4 国局**生效。
        self.alliances = str(alliances)
        # 本局抽到的**两个同盟**（没结盟 ⇒ `()`）—— 测试/报表要看
        self.alliance_pairs: tuple = ()
        # ★★ **对账计数器**（报告 §7.3）：在**产生它们的地方**记，不在别处另算一遍。
        self.n_dup_candidates = 0        # ★ `legal()` 去重时**丢掉**的重复动作数
        self.n_rejected = 0              # ★ 引擎**拒收**的动作数
        # ★ 显式战争对：给了就**只**宣这些（造中立国/局部战争场景）。
        self.wars = wars
        # ★ **显式指定**各国核心格（剧本参数；不给就按 seed 抽）。
        self.starts = dict(starts) if starts else None
        self.first = first                # ★ 谁先手（`None` ⇒ `self.players[0]`）
        # ★★ **建局时的队伍身份**（2026-10-23 修，review P1-4）：`{国家: 实体}`，
        #   在 `reset()` 里（结盟之后）填好 ⇒ 亡国之后也查得到"我原来属于谁"。
        self.team_of: dict = {}
        self.n_nations = int(n_nations if n_nations else n_nations_for(size))
        if not (2 <= self.n_nations <= len(PLAYER_NAMES)):
            raise ValueError(
                f"n_nations 必须在 2..{len(PLAYER_NAMES)}（名字池上限），"
                f"给了 {self.n_nations}")
        self.players = tuple(PLAYER_NAMES[:self.n_nations])
        # ★★ **他国市政厅是否已知**（"已派间谍" vs "自己找厅"）
        self.halls_known = halls_known
        # ★★ **已知市政厅的账本**（厅拆不掉、不能动 ⇒ 只增不减）
        self.halls = HallMemory(all_known=halls_known)
        self.war_mem = WarMemory()
        self.intel = Intel()
        # ★ 累计击杀账本
        self.kills = KillLedger()
        # ★★ **引擎世界**：只有 `Env`（能力客户端），**没有门面**。
        self.env = env
        self.turn = 0
        # ★ 引擎期号的**建局基线**（"摆局"推进的期号不算玩家走的一步）
        self.turn_base = 0
        # ★★ **本局的回合偏移**（观测里给的是 `(turn + turn_offset) / TURN_SCALE` ⇒ 逐局随机）
        self.turn_offset = 0
        # ★★ **"原地不动"用掉的军**（本回合内）。
        #   ★ 引擎**没有**"不动"这个动作（它的 `Unit.can_act` 只认 `born_turn`/`moved_turn`），
        #     而沙盒的动作空间里有"原地不动"（用户 2026-09-24 的要求）⇒
        #     这份"本回合谁已经用过行动"是**沙盒自己的状态**，不是引擎状态的影子：
        #     它记的是**引擎里根本没有的那个动作**。
        self.held: set = set()
        self.log: list[str] = []

    # ============================================================ 建局
    def reset(self, *, random_starts: bool = True) -> "Sandbox":
        """★ `random_starts=True`（缺省）⇒ **随机开局**，但两国核心至少隔 `min_margin`。

        用户 2026-09-24：「训练改成**随机开局**，但是必须至少有足够距离，这个距离是
        **对于国家总量和地图大小**的函数」。固定 (1,1)/(6,6) 会让模型过拟合那个布局。
        """
        from .teacher import grouping
        # ★ 每次 `reset()` 都是**一个全新的世界**（native 的地图**不能重铺**
        #   ——`territory.configure` 遇到已物化的格会直接拒绝）。
        #   ⇒ 运行时复用（`Env.load` 内部缓存），**世界**每次新建。
        self.env = Env.load()
        # ★ 摆局**整个**交给剧本（`rl:scenario`）—— 十字、强制平原核心、
        #   扩地口径、兵营/城堡/市政厅**全在剧本里**，这里不再自己造一遍。
        plan = self.env.setup(
            size=self.size, seed=self.seed, nations=self.players,
            territory_target=0 if self.territory else 5,
            armies=0, kind="native:infantry", random_starts=random_starts,
            starts=self.starts)
        self.territory_target = int(plan["territory_actual"])
        # ★ 摆局**计划**（纯数据）：核心格、扩地口径、开局兵 —— 报表与测试要用。
        self.plan = plan
        # ★★ **每局重抽偏移**（用户 2026-09-25：「每次开局传入一个随机偏移就行」）。
        #   用**独立的随机流**（盐 `TURN_OFFSET_SALT`）⇒ 抽偏移**不会扰动**地图与开局位置。
        self.turn_offset = random.Random(self.seed ^ TURN_OFFSET_SALT).randrange(64)
        self.log = []
        self.halls = HallMemory(all_known=self.halls_known)   # ★ 每局重开账本
        self.war_mem = WarMemory()                            # ★ 每局重开敌军番号账本
        self.intel = Intel()                                  # ★ 每局重开情报账本
        self.kills = KillLedger()                             # ★ 每局重开击杀账本
        self.held = set()
        # ★★ **顺序变了：先结盟，再宣战**（新引擎的联盟是真实体，且明文禁止
        #   「正在战争中，不能创盟」⇒ 顺序反了就会建盟失败）。
        self._make_alliances()
        # ★★★ **训练队伍身份**（2026-10-23 修，review P1-4）：
        #   "**这局开打时，谁跟谁一队**" —— 记下来，之后**不变**（**亡国不改**）。
        #   ★ 为什么必须记：引擎的 `Diplomacy.eliminate`（国家亡国）会把死者
        #     从联盟名单里**删掉**，人不够两个就把联盟**整个删掉**
        #     ⇒ 事后 `diplomacy.entity(死者)` 直接报错 ⇒ **队伍身份丢失**。
        #   ⇒ 于是"我战死但我的联盟赢了"被当成"我输了"
        #     （实测：乙、丁同队 ⇒ 乙先亡、丁最终获胜 ⇒ 两人拿到 −1 / +1）——
        #     这把"**有利于团队、但本国先亡**"的策略标成失败。
        #   ★ 这是**训练口径**（一局的分队），不是引擎状态 ⇒ 记在沙盒这边
        #     （RL 状态本来就归沙盒，见 `rl/env.py` 的"状态归属"那段）。
        #   ★ 建局**之后**才结的盟，`set_alliance` 会把成员的身份刷成联盟实体
        #     （引擎"正在战争不能建盟"那条规矩逼着调用方先 `war=False` 建局再结盟
        #       ⇒ 这条路是**正路**，不是边角）。
        self.team_of = {n: self.env.entity(n) for n in self.players}
        if self.wars is not None:
            self.env.declare_wars([(a, b) for a, b in self.wars
                                   if a in self.players and b in self.players])
        elif self.war:
            self.env.declare_wars([(a, b) for i, a in enumerate(self.players)
                                   for b in self.players[i + 1:]
                                   if not self.env.allied(a, b)])
        # ★ 开局摆兵（native 的 `seed` ＝ 初始化/导入 ⇒ **本回合可以动**）。
        #   ★ 与**回合内补员**（`recruit` ＝ 征召 ⇒ 本回合**不能动**）是两回事，
        #     引擎自己分得很清，RL 侧不再有第三种"造兵方式"。
        for name in self.players:
            core = self.core_of(name)
            if core is not None:
                self.env.spawn(name, "native:infantry", core[0], core[1], BASE_CAP)
        # ★ 编组状态是**模块内存**：每局开始必须清，否则上一局的编组漏进来。
        grouping.clear()
        # ★ **先手可换**（用户 2026-09-24：「每 8 局换先后手」）
        order = list(self.players)
        if self.first in order:
            order.remove(self.first)
            order.insert(0, self.first)
        self.pending = [n for n in order if self.alive(n)]
        self.last_ok = True
        # ★★ 回合号 = **引擎的期号减去建局期的基线**。
        #   · 基线取在**建局全部做完之后**：建局里的 native 外交流程
        #     （提交 → 结算 → 代投 → 再结算）会**推进 2 个期号**，
        #     那些是"摆局"的一部分，**不是玩家走的一步** ⇒ 不该占 `t_max`。
        #   · 源头上仍然是**引擎的 `period()`**（不自己数 ⇒ 不会漂），
        #     这里只是给它一个**基线偏移**（`turn_base` 是一次性取的常量，不是会漂的影子状态）。
        self.turn_base = self.env.period()
        self.turn = self.env.period() - self.turn_base
        return self

    # ------------------------------------------------------------ 行动顺序
    def _turn_order(self) -> list[str]:
        """本回合的**行动顺序**。★ **≥3 人每回合轮换先手；两人局不轮换。**

        ★★★ 两人局这里**必须**用 `self.first` 排头（用户 2026-09-27 口径）：
          局内**固定顺序**，先手优势由**跨局**轮换 `first` 来摊平。
          旧写法 `return alive` 把 `self.first` 丢了 ⇒ **一局里只有第 1 个回合认先手**
          （实测 60 回合里乙先动 1 次、甲先动 59 次 ⇒ 补偿失效）。
        """
        alive = [n for n in self.players if self.alive(n)]
        if len(alive) < 3:
            order = list(alive)
            if self.first in order:
                order.remove(self.first)
                order.insert(0, self.first)
            return order
        start = (self.turn - 1) % len(alive)   # ★ 与 `mp_run.py` 同一个公式
        return [alive[(start + i) % len(alive)] for i in range(len(alive))]

    # ------------------------------------------------------------ 结盟
    def _make_alliances(self) -> None:
        """抽本局的同盟（**只限 2v2**：4 国随机配成两个两人实体；3 国/5 国不结盟）。

        用户 2026-09-26：「考虑加入随机结盟(**只限 2v2 对局**)」。

        ★ 为什么"只限 2v2"是**对**的：胜者口径是"场上只剩一个实体"
          （`winning_entity`）—— 2v2 下只剩两个实体、灭掉对面那一家就赢
          ⇒ 大图重新变得**可赢**。
        ★ 抽签走**独立流**（盐 `ALLIANCE_SALT`）：与地图/开局位置/回合偏移/国土
          **各走各的** ⇒ 加这个特性不会挪动同一 seed 的其它东西。
        """
        self.alliance_pairs = ()
        if self.alliances != "random2v2" or len(self.players) != 4:
            return
        rng = random.Random(self.seed ^ ALLIANCE_SALT)
        order = list(self.players)
        rng.shuffle(order)
        pairs = ((order[0], order[1]), (order[2], order[3]))
        self.alliance_pairs = pairs
        for members in pairs:
            self.set_alliance(members)

    def set_alliance(self, *blocs) -> None:
        """结盟（**转调 `rl:sandbox`**，走 native 的 `bloc_found` + `accept_proposal`）。

        ★ 接受两种写法（旧的调用点两种都有）：
          · `set_alliance(("北方同盟", ["甲", "乙"]))` —— `(名字, 成员)`；
          · `set_alliance(["甲", "乙"])` —— 纯成员名单（名字自动生成）。
        ★ 结算后**回读校验**（review P1-4）："accept 就成功"是假的。
        """
        for bloc in blocs:
            if (isinstance(bloc, (tuple, list)) and len(bloc) == 2
                    and isinstance(bloc[0], str) and isinstance(bloc[1], (list, tuple))):
                name, members = bloc[0], list(bloc[1])
            else:
                name, members = None, list(bloc)
            living = [m for m in members if m in self.players]
            if len(living) < 2:
                continue
            self.env.set_alliance(living, name or f"盟{'-'.join(living)}")
            # ★★★ **队伍身份跟着结盟走**（2026-10-23 修，review P1-4 的回归）：
            #   队伍身份**不是**只在 `reset()` 里记一次 —— 联盟**可以是建局之后才结的**
            #   （`set_alliance` 是公开用法；引擎那条"正在战争不能建盟"还逼着调用方
            #   **先 `war=False` 建局、再结盟** ⇒ 这条路**不是边角**，是正路）。
            #   ⇒ 原来只在 `reset()` 记 ⇒ 这种局里甲乙拿到的还是"各自为政"的旧标签
            #     ⇒ 胜方判据看到**两个队伍** ⇒ `winner()` 返回 `None`
            #     （实测：`tests/test_rl_terminal_outcome.py` 那条"成员亡国但联盟赢了"
            #       ⇒ 联盟明明赢了，`winner()` 却是 `None`）。
            #   ⇒ 现在：**每次结盟都把成员的身份刷成新的联盟实体**。
            #   ★ 亡国**不会**改这张表（那才是这条修复的要点）：
            #     引擎 `Diplomacy.eliminate` 把死者从名单里删掉、人不够两个就解散联盟
            #     ⇒ 引擎认不出死者原来属于谁 ⇒ 我们这张表记得。
            for m in living:
                self.team_of[m] = self.env.entity(m)

    # ============================================================ 查询
    def core_of(self, name: str) -> tuple[int, int] | None:
        """该国的**市政厅格**（= 核心 = 国祚）。`None` ⇒ 已亡。"""
        return self.env.core_of(name)

    def armies_of(self, name: str) -> list:
        """该国**活着的**军队（引擎的 `Unit` 对象）。"""
        return [u for u in self.env.armies() if self.env.owns(u, name) and u.alive]

    def tiles_of(self, name: str, mask=None) -> int:
        """国土格数。★ 数**对手**时必须传 `mask`（视野外看不见谁占了哪）。"""
        return sum(1 for row in self.env.tiles()
                   if row.owner == name and (mask is None or row.position in mask))

    def cap_of(self, name: str, mask=None) -> int:
        """补员上限 = `BASE_CAP + 国土数 // TILES_PER_CAP`（★ 对手要过 `mask`）。"""
        return BASE_CAP + self.tiles_of(name, mask) // TILES_PER_CAP

    def count_of(self, name: str, kind: str) -> int:
        """该国某兵种的支数（`民` = 民兵）。"""
        return sum(1 for u in self.armies_of(name) if u.kind == kind)

    def alive(self, name: str) -> bool:
        """该国还在不在（在册 **且** 还有市政厅）。"""
        return name in self.env.nations() and self.env.has_townhall(name)

    def halls_of(self, name: str) -> int:
        """该国**已落成**的市政厅数（= 国祚，也是补员的"产能"）。"""
        return sum(row.buildings.get(TOWN_HALL, 0) for row in self.env.tiles()
                   if row.owner == name)

    # ============================================================ 胜负
    def done(self) -> bool:
        if self.winning_entity() is not None:
            return True
        return self.turn >= self.t_max

    def winning_entity(self) -> str | None:
        """★★ 胜者 = 场上**唯一还活着的队伍**（`None` = 还没定局）。

        用户 2026-09-24：「应该是联盟胜利或者单国胜利」⇒ 判据不是"某一个国家还在不在"，
        而是"还剩几个**队伍**"：只剩 1 个 ⇒ 它赢；一个不剩 ⇒ 同归于尽/平局 ⇒ `None`。

        ★★★ 2026-10-23 修（review P1-4）：队伍按**建局时记下的身份**算
          （`self.team_of`），**不是**按引擎"现在还认不认这个实体"。
          原因：引擎的 `Diplomacy.eliminate`（亡国）会把死者从联盟里删掉，
          **人不够两个就把联盟整个解散** ⇒ 活下来那位变成"单国" ⇒
          ① 事后查不到死者原来属于谁（队伍身份丢失）；
          ② 连**活着**那位的标签都变了（`bloc/…` → `nation/…`）⇒
          "我战死但我的队赢了"被算成"我输了"（实测：乙、丁同队 ⇒
          乙先亡、丁最终获胜 ⇒ 两人拿到 −1 / +1）。
          ⇒ 现在：**队伍在建局/结盟时定下，之后不变**（**亡国不改**）；
          最后剩下的那个队伍赢，**它的全部成员（活着的、亡国的）都算赢**。
        """
        alive = [n for n in self.players if self.alive(n)]
        if not alive:
            return None
        teams = {self.team_of.get(n) or self.env.entity(n) for n in alive}
        return teams.pop() if len(teams) == 1 else None

    def winner_members(self) -> tuple[str, ...]:
        """胜方实体里的国家名单（单国胜利 ⇒ 只有一个）。没定局 ⇒ `()`。

        ★★ 2026-10-23 修（review P1-4）：按**建局时记下的队伍身份**算，
          **不是**按"引擎现在还认不认这个实体"——
          亡国者的名字会被引擎从联盟里删掉 ⇒ 事后查不到 ⇒
          "**我战死但我的联盟赢了**"会被算成"我输了"
          （实测：乙、丁同队 ⇒ 乙先亡 ⇒ 乙拿到 −1、丁拿到 +1）。
          ⇒ 现在用 `self.team_of` ⇒ 胜方队伍里的**所有人**（含已亡国的）都算赢。
        """
        e = self.winning_entity()
        if not e:
            return ()
        members = tuple(n for n in self.players if self.team_of.get(n) == e)
        return members or tuple(self.env.entity_members(e))

    def winner(self) -> str | None:
        """胜方的**实体标签**；`None` = 平局 / 超时 / 未定局。"""
        return self.winning_entity()

    def reward(self, for_player: str) -> float:
        """★ 赢 ⇒ `1 − turns / T_MAX`（时间越短越高）；输 ⇒ −1；平 ⇒ 0。

        ★★ **平局时已亡国的一方按 −1**（review §9 第 1 条，2026-10-22 裁定）：
          亡国**就是输**（native 的 `native:succession` 已经这么判了），
          而旧版两个分支口径不一致（一个 −0.5 一个 0）⇒
          "早死比输划算" ⇒ 模型会学出"主动送死"这种荒谬策略。
          ⇒ **两条分支统一成 −1**：亡国方 = 输。
        """
        from . import scoring as S
        if self.winner() is None:
            # ★ 平局：还活着 ⇒ 0（互有胜负则各 −1）；**已亡国 ⇒ −1**（亡国就是输）。
            if for_player in self.env.nations() and self.alive(for_player):
                return S.REWARD_DRAW
            return S.REWARD_LOSS
        if for_player not in self.winner_members():
            return S.REWARD_LOSS
        # ★ 赢的价：**口径在 `scoring` 里**（单一出处）；要不要带时间奖励
        #   由 `WIN_TIME_BONUS` 决定（当前是关的 ⇒ 赢就是 1.0）。
        if S.WIN_TIME_BONUS:
            return S.REWARD_WIN * (1.0 - self.turn / float(self.t_max))
        return S.REWARD_WIN

    def outcome_for(self, p: str, *, global_terminal: bool = True):
        """该玩家本局的**最终结果**（+1 / −1 / 平 / `None`＝还没定）。

        ★ 放在**沙盒**这边（不依赖 torch）—— 它只算**终局口径**，
          跟训练没关系；`rl/train.py` 那边是**转发**（同一份实现）。
        ★ `global_terminal=False`（被步数预算截断）时**不伪造结果**：
          只有"我的外交实体已经出局"这一种情况是**已定**的（输），其余一律 `None`
          —— `t_max` 判平**只在真终局时**给。
        """
        if global_terminal:
            return self.reward(p)                 # ★ 全局口径（`winner_members` ⇒ 联盟友好）
        # ★ 未完局 ⇒ 只有"实体出局"才是已定的结果。
        #   ★★ **带上建局时的队伍身份**（review P1-4）：亡国后引擎认不出实体了
        #   ⇒ "我战死但我的盟还活着"会被算成"我出局了"（输）⇒ 口径不统一。
        t = evaluate.terminal(self.env, p, teams=self.team_of)
        if t is None:
            return None
        if t > 0:
            return S.REWARD_WIN
        if t < 0:
            return S.REWARD_LOSS
        return S.REWARD_DRAW

    # ============================================================ 观测记忆
    def known_halls(self, name: str, mask=None) -> dict:
        """该国**已知**的市政厅 `{格: 最后看见时的主人}` —— **记忆 ∪ 本帧视野**。"""
        env = self.env
        if env is None:
            return {}
        if mask is None:
            mask = env.vision_mask(name)
        self.halls.observe(env, name, mask)
        return self.halls.known(env, name)

    def known_enemies(self, me: str, mask=None, armies=None) -> list[dict]:
        """记忆里**此刻不可见**的敌军（按番号，附 `age`）。"""
        env = self.env
        if env is None:
            return []
        if mask is None:
            mask = env.vision_mask(me)
        self.war_mem.observe(env, me, mask, self.turn)
        if armies is None:
            from .encode import window_armies
            armies = window_armies(self, me, mask)
        vis = {a["gid"] for a in armies if a["owner"] != me}
        return self.war_mem.known(me, self.turn, vis)

    def tell_halls(self, name: str, items: dict) -> int:
        """**外部注入厅的情报** `{格: 主人}`（记忆是**可写**的，配情报系统用）。"""
        return self.halls.tell(name, items)

    def tell_armies(self, name: str, items: dict, turn: int | None = None) -> int:
        """**外部告知某国的军情** `{来源国: {兵种: n}}`（只有数量，位置/番号不外泄）。"""
        t = self.turn if turn is None else int(turn)
        if t > self.turn:
            raise ValueError(
                f"情报的回合 {t} 晚于当前回合 {self.turn} —— 情报不可能来自未来")
        return self.intel.tell_armies(name, items, t)

    def visible_enemies(self, me: str, mask=None) -> dict:
        """本帧**看得见**的敌军快照 `{gid: {...}}`。"""
        env = self.env
        if env is None:
            return {}
        if mask is None:
            mask = env.vision_mask(me)
        from .war_memory import visible_foes
        return visible_foes(env, me, mask)

    # ============================================================ 试演
    def clone(self) -> "Sandbox":
        """试演副本。★ 引擎状态用**引擎自己的存档机制**复制，不是 `deepcopy(world)`。"""
        sb = Sandbox.__new__(Sandbox)
        # ★ 全量浅拷（手列属性**必然漏**：加一个属性就漏一个，而且不报错）
        sb.__dict__.update(self.__dict__)
        sb.env = self.env.clone()          # ★ 引擎状态：mod 存档复制
        sb.log = list(self.log)
        sb.pending = list(self.pending)
        sb.held = set(self.held)
        sb.halls = self.halls.clone()       # 记忆要跟着副本走
        sb.kills = self.kills.clone()       # 击杀账本
        sb.war_mem = self.war_mem.clone()   # 敌军番号账本
        sb.intel = self.intel.clone()       # 情报账本
        sb.team_of = dict(getattr(self, "team_of", {}) or {})   # ★ 队伍身份跟着副本走
        return sb

    # ============================================================ 行动
    def current_player(self) -> str | None:
        """该谁动（`None` = 本局已结束）。"""
        return self.pending[0] if self.pending else None

    def legal(self) -> list[tuple]:
        """当前玩家的**合法动作**：`(aid, kind, x, y)`。

        ★ 合法性**全部来自引擎**：
          · `move` 的目标取 `native:army.reachable(..., attack=False)`（引擎的可达集）；
          · `attack` 的目标取 `reachable(..., attack=True)` 里**不是自家/盟国**的格。
        ⇒ 结构上不会撞墙，也**不重算**引擎的可达性。

        ★★ **野地也在进攻候选里**（2026-10-23 修，review P1-1）：
          旧实现是"**攻击集 − 移动集**"⇒ 野地（无主格）**同时在两个集里**
          ⇒ 被减掉 ⇒ **候选里一个 `attack` 都没有**（实测 8×8 开局：move 40 个、
          attack **0 个**；新口径 attack 5 个）⇒ **模型永远学不会"主动打野占地"**。
          为什么这是真bug：引擎里 **`move` 不占地、`attack` 才占地**
          （`native:army.move` 只挪位置；`attack` 走 `_claim` 改归属）
          ⇒ 不给 `attack` 候选 ⇒ **领土永远长不大**。
          ⇒ 现在：攻击候选 = `reachable(attack=True)` **减去自家/盟国**
            （那两格引擎会拒："那是你自己（或盟国）的地块"）。
            ★ 敌国领土**仍然给**（引擎会按"有没有生效的战争"当场回话，
              那是**教学式报错**，不是结构性非法）。
        """
        name = self.current_player()
        if name is None or not self.alive(name):
            return []
        out: list[tuple] = []
        period = self.env.period()
        # ★★ **视野整次 `legal()` 只算一遍**（原来每支军队各算一次，纯重复计算）。
        mask = None
        for unit in self.armies_of(name):
            if unit.engaged or unit.moved_turn == period or unit.gid in self.held:
                continue                       # ★ 已用过的军 / 交战中的军：整支屏蔽
            here = (unit.x, unit.y)
            # ★★ **"原地不动"**（用户 2026-09-24）：给军队的特殊动作，用后 mask 这支军。
            out.append((unit.id, "hold", here[0], here[1]))
            walk = self.env.reachable(name, unit, for_attack=False)
            for cell in sorted(walk):
                if cell != here:
                    out.append((unit.id, "move", cell[0], cell[1]))
            for cell in sorted(self.env.reachable(name, unit, for_attack=True)):
                if cell == here:
                    continue                   # ★ 打自己脚下：引擎会拒
                holder = self.env.owner(*cell)
                if holder == name or (holder is not None and self.env.allied(name, holder)):
                    continue                   # ★ 自家/盟国：引擎会拒（"不能进攻自家领土"）
                out.append((unit.id, "attack", cell[0], cell[1]))
            # ★ **无视野的邻格：移动与进攻两条路都给**（用户 2026-09-24）——
            #   看不见的时候模型无从知道那一格是什么 ⇒ 两条都给，让引擎当场判，
            #   并把它那句教学式错误消息当成**侦察的情报来源**。
            if mask is None:
                mask = self.env.vision_mask(name)
            for (x, y) in self._probe_cells(unit, walk, mask):
                out.append((unit.id, "move", x, y))
                out.append((unit.id, "attack", x, y))
        # ★★★ **按完整动作元组稳定去重**（报告 §6.3）：
        #   同一个引擎动作多拿一份概率质量 ⇒ 候选分布被偏置，**且不报错**。
        seen: set = set()
        uniq: list[tuple] = []
        for act in out:
            if act in seen:
                self.n_dup_candidates += 1      # ★ 记下来（对账用）
                continue
            seen.add(act)
            uniq.append(act)
        return uniq

    def _probe_cells(self, unit, walk, mask) -> list[tuple]:
        """★ **允许撞墙的试探格**：该军周围（1 格移动力）里**视野外**的格（边界除外）。

        用户 2026-09-24：「**特殊撞墙 mv 允许存在**，在**无视野**的情况下，全部候选集允许
        （边界除外），**按现有的引擎设计返回错误消息**，为模型那里存在敌国领土」。

        ⇒ 看不见的地方**必须让模型摸得到**：摸了，引擎照报错、额度照烧，
          模型就从那条错误消息里学到"那儿走不进去 / 那儿是敌国领土"。
        """
        n = self.size
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                x, y = unit.x + dx, unit.y + dy
                if not (0 <= x < n and 0 <= y < n):
                    continue                   # ★ 边界除外
                if (x, y) in walk or (x, y) in mask:
                    continue                   # 已给过 / 有视野的引擎已判过
                out.append((x, y))
        return sorted(out)

    def step(self, action: tuple) -> tuple[bool, str]:
        """执行一个动作。`(aid, 'hold'|'move'|'attack', x, y)`。"""
        name = self.current_player()
        if name is None:
            return False, "本局已结束"
        aid, kind, x, y = action
        if kind == "hold":
            # ★ "原地不动"是**沙盒自己的动作**（引擎没有）⇒ 记在沙盒自己的
            #   `held` 集合里，**不改引擎的 `moved_turn`**（那不是我们的字段）。
            unit = self.env.unit(name, aid)
            if unit is None:
                # ★★★ 2026-10-23 修（review P1-1 的收口）：**没有这支军 ⇒ 拒收**。
                #   旧写法：找不到也返回 `ok=True`（"hold" 空转，什么都没发生）
                #   ⇒ 上一位玩家的**旧候选**（`aid` 是**本国序号**，跨国会撞车）
                #   落到下一位玩家头上时，**被当成合法动作执行了** ⇒
                #   "替下一位玩家落子"**还不报错**（动作额度照烧）。
                #   ⇒ 现在：**拿不到军就明说拒收**（`n_rejected` 照记）。
                self.n_rejected += 1
                self.last_ok = False
                self._auto_advance()
                return False, f"本国没有编号 {aid} 的军（hold 无从下手）"
            self.held.add(unit.gid)
            self.last_ok = True
            self._auto_advance()
            return True, "hold"
        if kind == "move":
            ok, msg = self.env.move(name, aid, x, y)
        elif kind == "attack":
            ok, msg = self.env.attack(name, [aid], x, y)
        else:
            ok, msg = False, f"未知动作 {action!r}"
        self.last_ok = bool(ok)
        if not ok:
            self.n_rejected += 1               # ★ 引擎拒收（对账用）
        self._auto_advance()
        return ok, msg

    def can_act(self, name: str) -> bool:
        """该国**还有能动的军**吗（都用过了 / 交战中 ⇒ 没有）。

        ★★★ 判据**必须同时看"国家还在不在"**：亡国但残兵还在的国也算"能动"，
          ⇒ `_auto_advance` 停在它身上 ⇒ `legal()` 空 ⇒ 这一局既不终局、也没胜方，
          **而且全程不报错**（实测：3 国 8×8 第 6 回合的死锁）。
        """
        if not self.alive(name):
            return False
        period = self.env.period()
        return any(not u.engaged and u.moved_turn != period and u.gid not in self.held
                   for u in self.armies_of(name))

    def _auto_advance(self) -> None:
        """★ **全部军队都被 mask 后自动推进**。

        ★★ **必须是循环**：原来只推进**一步**，于是"结算后新一轮的第一个国家就不能动"
          就**卡在中途退出** —— `legal()` 空、`is_terminal()` 假、**没有胜方**，
          **而且不报错** ⇒ 那一局既拿不到终局的 ±1、也不进闸门，**静默地白打**。
        """
        while True:
            if self.done():
                return                      # ★ 收尾：别在终局后继续推进
            name = self.current_player()
            if name is not None and self.can_act(name):
                return
            if self.pending:
                self.pending.pop(0)
                continue
            # 本轮所有人都收手了 ⇒ 结算、开新回合
            self.end_turn()
            self.pending = self._turn_order()
            if not self.pending:
                return                      # 没人活着（终局由 `done()` 判）

    def is_terminal(self) -> bool:
        return self.done()

    # ============================================================ ★ 嫁接点
    def ai_turn(self, name: str, *, teacher: str = "mine", verbose: bool = False) -> list:
        """跑一方的军事回合，返回动作表 `[(tool, args, ok, msg), …]`。"""
        if not self.alive(name):
            return []
        enemy = self._nearest_rival(name)
        if teacher == "v11plus":
            from .teacher import military as v11
            from .teacher.ledger import Ledger
            ledger = Ledger(self.env, name, UNLIMITED)
            v11.run(ledger, self.env, name)
            return ledger.acts
        from . import military as mine
        return mine.run(self.env, name, enemy=enemy, verbose=verbose)

    def _nearest_rival(self, name: str) -> str | None:
        """离我核心**最近**的对手（按切比雪夫距离；没有 ⇒ `None`）。"""
        from .evaluate import rival_nations
        mine = self.core_of(name) or (0, 0)
        foes = rival_nations(self.env, name)
        if not foes:
            return None
        return min(foes, key=lambda f: (max(abs((self.core_of(f) or (0, 0))[0] - mine[0]),
                                            abs((self.core_of(f) or (0, 0))[1] - mine[1])),
                                        f))     # ★ 末尾带 `f` ⇒ 同距时**确定性**

    # ============================================================ 回合推进
    def resupply(self) -> list[str]:
        """每 `RESUPPLY_EVERY` 回合**触发一次补员**，按**配额**出兵。

        ★ 规则（用户 2026-09-24）：「补员**不是一次补满**，而是 **5 回合一支兵**，
        **每有一个市政厅多补一支**，每个市政厅每次补员出一支，**不能补员超上限**」
        ⇒ 补员量 = 1（基础） + 市政厅数，总数**不超过 `cap_of`**。

        ★★ **走 native 的 `recruit`（征召）**：所以**新兵本回合不能动**
          （`Units.spawn(..., turn=period, newborn=True)` ⇒ `born_turn = 当前期`
          ⇒ `Unit.can_act(period)` 为假）。这是 native 自己的规则，
          旧门面用 `military.seed` 绕过它（"造完就能动"）—— 那条影子实现**已删**。
        ★ 引擎的征召额度是**每期每兵营 1 支**（`recruit_cap` 按兵营数给）
          ⇒ 兵营不够就**补不满**，这是 native 的硬约束，**不绕过**。
        """
        notes = []
        if self.turn == 0 or self.turn % RESUPPLY_EVERY != 0:
            return notes
        for name in self.players:
            if not self.alive(name):
                continue
            hall = self.halls_of(name)
            quota = 1 + hall                     # ★ 基础 1 + 每座厅 1
            cap = self.cap_of(name)
            foot = self.count_of(name, "native:infantry")
            gap = cap - foot
            if gap > 0:
                n = min(quota, gap)              # ★ 不能超上限
                got, why = self._recruit(name, "native:infantry", n)
                if got:
                    notes.append(f"{name} **缺员** ⇒ 补步兵 +{n}"
                                 f"（厅×{hall} 配额{quota}，步兵 {foot}/{cap}）")
                else:
                    notes.append(f"{name} **缺员** 但补不进：{why}")
            else:
                # ★★ **不缺员 ⇒ 出民兵**（用户 2026-09-24）。
                mil = self.count_of(name, "native:militia")
                # ★★ **上限归引擎**：民兵的编制上限是"**每座军屯 2 支**"（native 的
                #   `militia_cap`），不是 RL 自己算的"步兵 × 2"（review §8：
                #   **不复刻引擎规则**）⇒ RL 只问额度，让引擎裁。
                #   旧公式 `MILITIA_PER_FOOT × 步兵` 是个**比引擎宽**的上限，
                #   永远轮不到它生效 ⇒ 删掉（留着就是第二份口径）。
                room = 1 if mil < 2 * max(1, self.halls_of(name)) else 0
                if room > 0:
                    n = min(quota, room)
                    got, why = self._recruit(name, "native:militia", n)
                    if got:
                        notes.append(f"{name} **不缺员** ⇒ 民兵 +{n}"
                                     f"（厅×{hall} 配额{quota}，民兵 {mil}）")
                    else:
                        notes.append(f"{name} **不缺员**，但民兵征召被引擎挡下：{why}")
                else:
                    notes.append(f"{name} **不缺员**，但民兵已满编 "
                                 f"（现有 {mil} 支，本回合不出）")
        return notes

    def _recruit(self, name: str, kind: str, n: int) -> tuple[bool, str]:
        """在**核心站点**征召 `n` 支（走 native 的 `recruit`）。

        ★ **先问额度再征**（`recruit_quote`）：征召额度是**引擎的硬约束**
          （每兵营每期 1 支；民兵还有**全国编制**上限）⇒ RL 侧不自己算上限、
          更不让引擎拒了当没发生。
        ★ 引擎拒了 ⇒ **把原因记进日志**（不静默）：那是"这期补不满"的**事实**，
          不是被吞掉的错误。
        """
        core = self.core_of(name)
        if core is None or n <= 0:
            return False, ""
        site = self._site_of(name, core)
        if site is None:
            return False, ""
        try:
            # ★ 用 count=1 问额度：`recruit_quote(count>1)` 在超出额度时**直接抛**，
            #   而我们要的是"还能征几支"这个数 ⇒ 问 1 支拿 quota，再自己取 min。
            quote = self.env.recruit_quote(name, site, kind, 1)
        except Exception as exc:                    # noqa: BLE001 引擎拒了 ⇒ 记下来
            return False, str(exc)
        quota = int(quote.get("quota", 0) or 0)
        if quota <= 0:
            return False, f"引擎额度已满（{kind}）"
        take = min(n, quota)
        try:
            self.env.recruit(name, site, kind, take)
        except Exception as exc:                    # noqa: BLE001
            return False, str(exc)
        return True, "" if take == n else f"引擎额度只给 {take}/{n} 支"

    def _site_of(self, name: str, cell) -> str | None:
        """某一格对应的**站点 id**（`rl:n0/land-3-6` 这样的内容 ID）。"""
        row = self.env.tile(cell[0], cell[1])
        return row.tile_id or None

    def end_turn(self) -> None:
        """双方都行动完 ⇒ 引擎结算 → **开下一回合** → 补员。

        ★ **回合号是引擎的**（`native:government.period()`）——
          结算时引擎自己在 `cycle.end` 推进期号（`government.advance`），
          所以这里**不需要**（也**不应该**）自己 `turn += 1`。
        """
        # ★★ 结算**前**拍快照（击杀归因靠它）—— 结算一跑，战死的军就没了。
        before = {u.gid: (N.name(u.owner), u.x, u.y, u.hp) for u in self.env.armies()}
        self.env.resolve()
        self.kills.observe(self.env, before)
        # ★★★ **亡国之后的衔接**（2026-10-23 修，review P1-2）—— 必须在
        #   **算回合号之前**做：重新宣战要走 native 的"提交 → 结算 → 代投 → 再结算"
        #   ⇒ **会推进 2 个期号** ⇒ 回合号要把它算进去。
        note = self._resume_wars()
        # ★ 与引擎同步（别自己数，免得漂）；减去建局基线 ⇒ 走的是"玩家回合数"
        self.turn = self.env.period() - self.turn_base
        if note:
            self.log.append(f"[T{self.turn}] {note}")
        self.held = set()                  # ★ 新回合 ⇒ "不动"的 mask 清空
        for note in self.resupply():
            self.log.append(f"[T{self.turn}] {note}")

    # ------------------------------------------------------------ 亡国衔接
    def _war_pairs(self) -> list[tuple[str, str]]:
        """本局**应该在打**的两两组合（**只算还活着的国家**）。

        ★ 口径与开局**完全一致**（`reset()` 里那一套）：
          · 显式 `wars` ⇒ 就那些对；
          · 否则 `war=True` ⇒ **所有非同盟的对**。
        ⇒ 亡国之后重新宣战时，**结构不变**（2v2 的局里死了一边，剩下两边继续打）。
        """
        alive = [n for n in self.players if self.alive(n)]
        if self.wars is not None:
            return [(a, b) for a, b in self.wars if a in alive and b in alive]
        if self.war:
            return [(a, b) for i, a in enumerate(alive)
                    for b in alive[i + 1:] if not self.env.allied(a, b)]
        return []

    def _resume_wars(self) -> str | None:
        """★ **亡国之后让幸存者继续打**（2026-10-23 修，review P1-2）。

        ★★ 病灶：引擎的 `Diplomacy.eliminate`（国家亡国时）会
          · **清空全部战争**（`self._wars = []`）；
          · 给**所有幸存者**两两**停战**，直到 `期号 + FallTruce`（native 默认 10 期）。
          ⇒ 引擎的设计是"亡国 ⇒ 战争结束 ⇒ 幸存者冷静一段，之后**可以**再宣战"。
        ★★ 但 **RL 的动作空间里没有"宣战"**（只有 hold/move/attack）⇒
          停战一过，战争**永远不会自动恢复** ⇒ 多国局在第一次灭国后
          **谁也打不了谁** ⇒ 后续"争胜/统一"的目标彻底落空，**而且不报错**
          （实测：甲灭乙后，甲丙都活着、游戏没结束，但战争已经没了）。
        ⇒ 这里补上那一环：**该宣的宣**（`rl:sandbox.declare_wars` → native 的
          宣战流程：提交 → 结算 → 代投 → 再结算）。
        ★ **不绕过引擎规则**：停战期内**不**宣战（引擎会拒："Active truce blocks
          war"）—— 那一层判断在 `rl/mods/train/mod.py::declare_wars` 里
          （它问引擎 `active_truce` 再决定要不要宣）。
        ★ **不产生副作用**：一对都不需要宣时（都在打 / 都在停战）⇒
          `declare_wars` **一次结算都不做** ⇒ 不白推进期号。
        """
        if len([n for n in self.players if self.alive(n)]) < 2:
            return None
        todo = [(a, b) for a, b in self._war_pairs()
                if not self.env.war_between(a, b)]
        if not todo:
            return None
        self.env.declare_wars(todo)
        return (f"★ 亡国后**重新宣战**：{todo}"
                f"（引擎的亡国处理会清空战争并给幸存者停战；"
                f"RL 的动作空间没有'宣战' ⇒ 这里替它走一遍 native 的流程）")

    # ============================================================ 整局
    def rollout(self, verbose: bool = False) -> dict:
        """**双方都由军事层驱动**打完整局（既验沙盒，也产 BC 标签）。"""
        turns = []
        while not self.done():
            for name in self.players:
                acts = self.ai_turn(name)
                if acts:
                    turns.append((self.turn, name, acts))
                if verbose and acts:
                    ok = sum(1 for _, _, good, _ in acts if good)
                    self.log.append(f"[T{self.turn}] {name} {len(acts)} 个动作（成功 {ok}）")
            self.end_turn()
            if self.turn > self.t_max + 5:        # 保险丝：不该走到这
                break
        return {"turns": self.turn, "winner": self.winner(),
                "actions": turns, "log": self.log}

    # ============================================================ BC 数据
    def bc_rows(self, acts: list) -> list[dict]:
        """把动作表转成 BC 样本行（`args` 是 1-based，落库时统一转 0-based）。"""
        rows = []
        for tool, args, ok, msg in acts:
            if not ok:
                continue                       # 只学成功动作（被拒的是废动作）
            row = {"tool": tool, "ok": ok, "msg": msg}
            if "x" in args and "y" in args:
                row["x"] = int(args["x"]) - 1
                row["y"] = int(args["y"]) - 1
            if "army_id" in args:
                row["army_id"] = int(args["army_id"])
            if "army_ids" in args:
                row["army_ids"] = [int(i) for i in args["army_ids"]]
            rows.append(row)
        return rows


# ★ 老师/对照线的动作额度上限。
UNLIMITED = 10 ** 6
