# -*- coding: utf-8 -*-
"""8×8 沙盒：**攻取国祚**（两国）。★ **军事动作全部嫁接 `v11plus` 的军事层**。

    用户 2026-09-24 的设计
    ──────────────────────
    · 地图 8×8，两国（甲 / 乙），**核心放对角**（`starts`）
    · 开局各 **5 个步兵**，摆在自己核心格（市政厅所在格）
    · **补员**：上限 `BASE_CAP + 国土数 // TILES_PER_CAP`；**每 5 回合触发一次**，
      补**足到上限**（不是每次 +1，不无限加兵）
    · **终局**：一方拿到对手的市政厅 ⇒ 立即结束；兜底 `T_MAX`
    · **奖励**：赢 ⇒ `1 − turns / T_MAX`（时间越短越高）；输 ⇒ −1；平 ⇒ 0

    ★★ 军事层在哪（用户 2026-09-24 的口径：「我让你抄的是 v11plus 的**目标评估**、
    **编组逻辑**，谁让你一个字都不写了，直接拿一个半成品用?」）
    ──────────────────────────────────────────────────────────────
    ⇒ 军事动作走 **`rl/military.py`**：**抄** v11plus 的部件
      （`combat.assess` 目标评估 / `grouping._solve` 编组求解 / `pathfind` 寻路），
      **自己写**决策层（目标池 / 守家 / 侦察 / 出手时机）——
      那几样正是 v11plus 明确留空的（底稿 §十「守土/驻防/撤退」）。
    **不调** `ruleai.v11plus.military.run`（那是"整套拿一个半成品"，它只会平推：
    编组→能打就打→打不了朝目标走一格，既不会守也不会侦察）；
    但它留着当**对照线**：`ai_turn(name, teacher="v11plus")`。

    引擎口径（实测 2026-09-24，详见 `rl/PLAN.md` §3.2）
    ────────────────────────────────────────────────
    · `World(size=8, nations=[…], starts={…})` 生效；核心十字各 **5 格**（(1,1)/(6,6) 对角最远）
    · ★ **野人必须保留**：引擎的"可攻"判定 `_atk_target_ok` 要求**格上有驻军**，
      无主空格**不算**可攻 ⇒ 清掉野人 = **军队出不了自家十字**（实测过）。
      野人在本沙盒里就是**扩张成本**：8×8 上 54 个，每格一支。
    · 动作接口 **0-based**：`world.move/attack(name, …, x, y)`；而 `ledger.acts` 里
      的 `args` 是**给 LLM 看的 1-based**（`cell[0] + 1`）—— 两边别混。
    · ★ **开局必须宣战**：v11plus 候选池的源③ 是"**交战**敌国领土"，
      不宣战 ⇒ 对手领土一格都不进候选池 ⇒ 它根本不会朝对手走。
"""
from __future__ import annotations

from game import unit_max_hp

from . import vocab as V
from .hall_memory import HallMemory
from .intel import Intel
from .war_memory import WarMemory

# ---------------------------------------------------------------- 规格常量
# ★★ **国家名池**（**上限 6**，天干序）—— 唯一出处是 `vocab.PLAYER_NAMES`，
#   这里只是转发（两处各写一份必然漂移，而漂移的后果是"某个国名对不上"）。
from .vocab import PLAYER_NAMES  # noqa: E402

# ⚠ **遗留的"两国"常量**：只有 `rl/mcts.py`（**早已作废、用户明说别动**）还在引它。
#   沙盒本身**不再用它** —— 一切走 `self.players`。
PLAYERS = PLAYER_NAMES[:2]
# 固定开局（`random_starts=False` 时的对照用）。多国时由 `_fallback_starts` 兜底。
STARTS = {"甲": (1, 1), "乙": (6, 6)}   # ★ 对角、最远；十字各 5 格完整（(0,0) 会缺两臂）
T_MAX = 200           # 兜底上限（只防僵局）
BASE_CAP = 5          # 补员上限基础值（= 开局兵数，自洽）
TILES_PER_CAP = 10    # 每控制这么多格国土，补员上限 +1
RESUPPLY_EVERY = 5    # 每几回合触发一次补员
# ★★ **民兵编制上限 = 步兵 × 这个数**（用户 2026-09-25：「以前民兵是无上限增长的，
#   改成上限是步兵2倍」）。★ 这条是**训练环境**的规则，**不是引擎的** ——
#   引擎里民兵另有一套（只能在军屯征召、总数 ≤ 军屯数），沙盒为了不让模型被经济层
#   卡住而绕过了它（见 `resupply` 的注释），所以"编制上限"这件事只能在这里定。
MILITIA_PER_FOOT = 2
UNLIMITED = 10 ** 9   # 动作额度（引擎早已删掉"看海 12 个"那条上限）
END = "end"           # 「本方收手」的哨兵动作（换人 / 结算回合）

# --------------------------------------------------------------- ★ 几个国家
# 用户 2026-09-24：「多玩家（**3 人起步**）+ ≥3 轮流手 + `n_nations = f(size)`」。
# ★ 为什么要多玩家：8×8 两国已到天花板 —— `min_margin(8,2)=5`、步兵 1 格/回合
#   ⇒ **先手 5 回合直达、回防不可能**，先手优势是**结构性**的（实测闸门连响）。
#   多一个对手，"抽空家里去偷家"就得付代价 ⇒ 这才有真正的攻守取舍。
#
# ★ 下面三个数是**可调先验**（不是硬编码的物理常数），按"每国摊到多少格"来估：
N_NATIONS_MIN = 3     # ★ 用户：「3 人起步」—— 小图也至少 3 个（否则等于没做这件事）
N_NATIONS_MAX = 5     # ★ 用户 2026-09-25：「**3-5 个国家**」⇒ 上限 5
# ★★ **每国摊多少格**（可调先验，不是物理常数）。
#   2026-09-25 从 150 调到 **60**：用户把起炉地图从 12-30 改成 **12-20**
#   （治"换家流"、让一局装得进步数预算），**而 150 在 12-20 上只会算出 3 国**
#   （12²/150=0.96、20²/150=2.67 ⇒ 都夹到下限 3）⇒ 用户要的"**玩家 3-5 个**"就没了。
#   60 ⇒ 12⇒3、16⇒4、20⇒5，两个口径同时满足。
#   ★ 副作用要认：地**更挤**了（每国 48~80 格，原来按 150 估是"每国 12×12=144 格"）。
#     挤了就更接近"贴脸"，而贴脸正是"换家流"的温床 —— 所以这里跟 `min_margin`
#     是**同一个几何的两个方向**，将来觉得太挤就一起调。
TILES_PER_NATION = 60


def n_nations_for(size: int) -> int:
    """★ **`n_nations = f(size)`** —— 地图越大、放得下的国家越多。

        8×8  (64 格) ⇒ 3   ← 用户的"3 人起步"兜住（面积算出来只有 1）
        12×12(144)   ⇒ 3   ← 起炉范围的**下端**
        16×16(256)   ⇒ 4
        20×20(400)   ⇒ 5   ← 起炉范围的**上端**
        40×40(1600)  ⇒ 5   （封顶）

    ★ 上限 6 有两条独立的理由：① 名字池 6 个；② **每个国家一份网络**
      （`train.py`）⇒ 份数就是显存/内存与收敛速度。
    ★ 下限 3 是**目的**（解僵局），不是几何 —— 所以它**优先于**面积估计。
    ⚠ **这一版是我拍的**（用户逐条问准过的口径里没这一条）：真跑起来觉得
      16×16 该放 4 个、或者太挤，改 `TILES_PER_NATION` 即可（一处生效）。
      ★ 2026-09-25 已按"起炉范围 12-20 必须能出 3-5 国"调到 **60**（见常量的注释）。
    ★ 与 `min_margin(size, n)` 的配合：`n` 定下来之后，开局最小间距就是
      `min_margin` 给的 `size/√n` —— 两者是同一个几何的两个方向，别各调各的。
    """
    by_area = round(size * size / TILES_PER_NATION)
    return max(N_NATIONS_MIN, min(N_NATIONS_MAX, int(by_area)))


class KillLedger:
    """★ **累计战果账本**（击杀支数 + 打掉的血量）—— 用户 2026-09-25：

        「会不会太复杂了，**只计算我军杀掉的敌军来加分**就行了」
        「`W_HP × (我的血 − **看得见的**敌方血)` **也要改，和击杀一样**」

    它替换掉打分器原来那**两**项「看得见的敌国军队数 / 看得见的敌方血量」——
    两项是**同一个病**（与厅那条一模一样）：

      ① **迷雾悖论**：只数看得见的 ⇒ **侦察到敌军反而当场扣分**（势函数取差分），
         而"丢视野"反而涨分 —— 恰好把"该去侦察"教成负收益；
      ② **闪断**：同一件事实一帧读得到、一帧读成 0 ⇒ 差分变噪声。

    ⇒ 两项都改成**单调事件计数**：打了就加，**只增不减、与视野无关**
      （同 `HallMemory` 的道理）。

    **归因怎么做的**（引擎不记账，而 `mp.py` 与本线必须逐字一致 ⇒ 不许改引擎）：
    结算**之前**拍一张 `{军id: (主人, 格, 血)}`，`resolve_turn()` 之后：
      · **没了的** ⇒ 战死（击杀 +1，血量按**结算前**的血算）
      · **还在但血少了** ⇒ 挨了打（血量按差额算）
    挨打/战死发生在哪一格，**那一格上（结算前）还有别的国家的军队** ⇒ 那些国家就是凶手。
      · 互殴同归于尽**照样算**（用结算**前**的快照 ⇒ 死者也能当凶手）
      · 多国同格 ⇒ **每国都记一笔**（这是塑造用的先验，不是逐笔对账的账本）
      · 饿死/撤退/无人同格 ⇒ **没人记账**（沙盒里补给管够，这条路基本不会走）

    ★★ **按 `(凶手, 受害者)` 成对记账** —— 不是为了好看：打分器要的是"打**敌国**"，
      而"打野人 / 打盟友"**不该**算进那一项（原来数 `foe_armies` 时野人本来就不在内）。
      成对记之后，**口径由打分器按当时的敌我关系去筛**，账本自己不猜关系
      （关系是会变的：今天的中立国明天就宣战了）。

    ⚠ 它**不记**"哪支军打的、打了几轮"：用户要的是"只算杀掉/打掉的量"，
      别把它做成战斗日志。
    """

    def __init__(self):
        self._kills: dict = {}      # (凶手, 受害者) → 累计击杀支数
        self._dmg: dict = {}        # (凶手, 受害者) → 累计打掉的血

    def observe(self, world, before: dict) -> None:
        """结算后调一次。`before` = 结算前的 `{军id: (主人, x, y, 血)}`。"""
        now = {a["id"]: a for a in world.armies}
        # 每格上（结算前）有哪些国家在场 —— 结算一跑位置就变了，只能用快照判
        at: dict = {}
        for o, x, y, _hp in before.values():
            at.setdefault((x, y), set()).add(o)
        for aid, (victim, x, y, hp0) in before.items():
            a = now.get(aid)
            if a is None:
                loss, killed = int(hp0), True        # 战死：整条血都算打掉的
            else:
                loss, killed = int(hp0) - int(a.get("hp", 0)), False
            if loss <= 0:
                continue                             # 没掉血（可能还回了血）⇒ 无事
            for killer in at.get((x, y), ()):
                if killer == victim or killer == "野人":
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
        """`(击杀表, 血量表)` 的**副本**，都是 `{(凶手, 受害者): 累计}`。

        ★ 打分器/编码层要的就是这份**纯数据**（它们不该认识沙盒类）。
        """
        return dict(self._kills), dict(self._dmg)

    def clone(self) -> "KillLedger":
        k = KillLedger()
        k._kills, k._dmg = dict(self._kills), dict(self._dmg)
        return k


def min_margin(size: int, n_nations: int) -> int:
    """随机开局时两国核心的**最小距离**。

    用户 2026-09-24：「必须至少有足够距离，这个距离是**对于国家总量和地图大小**的函数」。

    取"平均铺开间距"的几何估计：`N` 国在 `size × size` 上铺开 ⇒ 理想间距 ≈ `size / √N`，
    再夹一个下限 2（小于 2 就是贴脸，没有对抗空间可言）。

        8×8、2 国  ⇒ 8/1.414 ≈ 5.66 ⇒ **5**   ← 正好等于原来那个固定开局 (1,1)-(6,6) 的距离
        40×40、2 国 ⇒ 28；40×40、5 国 ⇒ 17

    ★ 引擎自己也有同类口径可参照：中途加国用
      `max(balance.ARRIVE_MARGIN_MIN, size // balance.ARRIVE_MARGIN_DIV)` ——
      但它**只跟 size 走、不看国家数**，所以这里不直接用它。
    """
    import math
    return max(2, int(size / max(1.0, math.sqrt(max(1, n_nations)))))


class Sandbox:
    """一局 8×8 攻取国祚。**规则归沙盒、动作归 v11plus**。"""

    def __init__(self, seed: int = 0, size: int = 8, t_max: int = T_MAX,
                 war: bool = True, first: str | None = None,
                 halls_known: bool = False, n_nations: int | None = None,
                 wars: list[tuple[str, str]] | None = None):
        self.seed = seed
        self.size = size
        self.t_max = t_max
        self.war = war                    # 开局是否宣战（★不宣战 v11plus 不会进攻）
        # ★ 显式战争对：给了就**只**宣这些（用来造**中立国**/局部战争场景）。
        #   没给 ⇒ `war=True` 时**全对宣战**（多玩家的缺省：默认全体敌对）。
        self.wars = wars
        self.first = first                # ★ 谁先手（`None` ⇒ `self.players[0]`）
        # ★★ **几个国家**（用户 2026-09-24：「3 人起步 + `n_nations = f(size)`」）。
        #   `None` ⇒ 按地图大小算（`n_nations_for`）；显式给数则用它（测试/对照用）。
        self.n_nations = int(n_nations if n_nations else n_nations_for(size))
        if not (2 <= self.n_nations <= len(PLAYER_NAMES)):
            raise ValueError(
                f"n_nations 必须在 2..{len(PLAYER_NAMES)}（名字池上限），"
                f"给了 {self.n_nations}")
        self.players = tuple(PLAYER_NAMES[:self.n_nations])
        # ★★ **他国市政厅是否已知** —— 用户 2026-09-24：
        #   「对手的厅应该是**明知**的，有**两种模式**，一个是 llm **已经派了间谍**、
        #    明知对手厅了，一个是没有、**rl 模型自己找厅**」。
        #   · `False`（缺省，"自己找厅"）：沿用引擎 `_public_buildings` 的口径 ——
        #     厅**进了视野就公开**（"看不见就打不着"）。
        #   · `True`（"已派间谍"）：他国的厅**位置直接已知**，不必先侦察。
        #
        #   ⚠ 它**只影响"厅在哪"这一件事，不影响"厅上有什么"** ——
        #     知道一座厅的位置**不等于**看得见驻守它的军队（那是两件情报）。
        #     ⇒ 实现上是一个**独立的开关**，**不能**靠"把这些格塞进 `vision_mask`"
        #       （那会连守军、地形一起暴露，是偷看）。见 `rl/encode.py` 的 `halls_known`。
        self.halls_known = halls_known
        # ★★ **已知市政厅的账本**（用户 2026-09-24：「**发现厅了就应该永久标记，
        #   因为厅是拆不掉也不能移动的**」）—— 两种模式是**同一个机制的两种初值**：
        #     · 间谍模式（`halls_known=True`）⇒ 账本 `all_known`（开局全知）
        #     · 自己找厅（`False`）⇒ 账本从空开始，靠 `known_halls()` 累积
        #   ⇒ 调用方（`encode`/`evaluate`）**不用分情况**，一律问 `known_halls`。
        self.halls = HallMemory(all_known=halls_known)
        # ★★ 敌军**番号账本**（用户 2026-09-25：「模型要识别的出，这次击退了 a 兵团，
        #   下次露头的是 a 军团的**残余**，还是一支没见过的、满编的 b 军团」）
        #   ★ 与 `halls` 的差别：厅拆不掉、不能动 ⇒ 只增不减；
        #     军队**会动会死** ⇒ 必须带**时间戳**、必须**会过期**。两件事不能共用一套。
        self.war_mem = WarMemory()
        # ★★ **可写观测层**：外部告知的情报（对应引擎玩家的**间谍内容**，见 `rl/intel.py`）
        self.intel = Intel()
        # ★ 累计击杀账本（用户 2026-09-25：「只计算我军杀掉的敌军来加分」）
        self.kills = KillLedger()
        self.world = None
        self.turn = 0
        # ★★ **本局的回合偏移**（`my_turn` 那一列要用，见 `vocab.GLOB` 的三条口径）：
        #   观测里给的是 `(turn + turn_offset) / TURN_SCALE` ⇒ **逐局随机**，
        #   于是「第 50 回合就该这么打」这种死记无处落脚，而局内的增量完整保留。
        #   ★ `reset()` 里按 seed 抽 ⇒ 同一 seed 仍然**可复现**（确定性没丢）。
        self.turn_offset = 0
        self.log: list[str] = []

    # ============================================================ 建局
    def reset(self, *, random_starts: bool = True) -> "Sandbox":
        """★ `random_starts=True`（缺省）⇒ **随机开局**，但两国核心至少隔 `min_margin`。

        用户 2026-09-24：「训练改成**随机开局**，但是必须至少有足够距离，这个距离是
        **对于国家总量和地图大小**的函数」。固定 (1,1)/(6,6) 会让模型过拟合那个布局；
        随机 + 足够距离才逼它学"相对位置"而不是"记住坐标"。
        `random_starts=False` ⇒ 退回固定的 `STARTS`（对照/复现用）。
        """
        from mp import World
        from ruleai.v11plus import grouping
        starts = self._random_starts() if random_starts else self._fixed_starts()
        w = World(size=self.size, seed=self.seed, nations=list(self.players),
                  starts=starts)
        w.max_turns = self.t_max
        self.world = w
        self.turn = 0
        # ★★ **每局重抽偏移**（用户 2026-09-25：「**每次开局传入一个随机偏移就行**」）。
        #   ★ 用 `self.seed` 派生 + **一个异或盐**：与 `_random_starts()` 的
        #     `random.Random(self.seed)` **各走各的流** ⇒ 抽偏移**不会扰动开局位置**
        #     （否则就是「加了个时间戳、顺带把地图换了」——那种耦合极难查）。
        #   ★ 逐局不同：训练在 `train` 里给每局**新的随机 seed** ⇒ 偏移自然逐局不同。
        import random as _random
        self.turn_offset = _random.Random(
            self.seed ^ 0x7A17).randrange(V.TURN_OFFSET_SPAN)
        self.log = []
        self.halls = HallMemory(all_known=self.halls_known)   # ★ 每局重开账本
        self.war_mem = WarMemory()                            # ★ 每局重开**敌军番号账本**
        self.intel = Intel()                                  # ★ 每局重开**情报账本**
        #   ★★ **必须每局重置**：上一局的"敌军在某处"对新的一局是**纯噪声**，
        #     而它不会自己消失（`age` 只在同一局的时间轴上算）⇒ 忘了重置 =
        #     模型带着上一局的幽灵开局，而且**不报错**。
        self.kills = KillLedger()                             # ★ 每局重开击杀账本
        if self.wars is not None:
            # ★ 显式给了战争对 ⇒ **只**宣这些（其余国家互为中立国：mv/atk 都不行）
            for a, b in self.wars:
                if a in self.players and b in self.players:
                    w.declare_war(a, b)
        elif self.war:
            # ★★ **全对宣战**（多玩家）—— 用户 2026-09-24：「是否中立和联盟和模型
            #   无关，开局直接指定」⇒ 没被 `set_alliance` 写进任何联盟的国家**默认敌对**。
            #   ⚠ 原来只宣战 `(甲, 乙)` 一对 ⇒ 三国局里第三个国家**谁都不打**，
            #     而 `_owner_class` 会把它当 `OWN_NEUTRAL_NATION`（mv/atk **都不行**）
            #     ⇒ 它一步也走不出去、也不挨打，成一块"冻住的石头"。
            #   引擎的 `declare_war` 本来就建的是**一对多**的战争记录（`_war_sides`
            #   带 followers）⇒ 逐对调用即可，语义与"全体交战"一致。
            #   ★ 与联盟并存是**安全**的：引擎两处判定都是"**联盟优先**"
            #     （`_atk_target_ok` 要 `not allied_between` **且** `war_between`；
            #      `_mv_wall` 也放行盟国地）⇒ 先全对宣战、再 `set_alliance` 结盟，
            #      盟友之间照样"可 mv 不可 atk"。所以**造中立国要用 `wars=[...]`**，
            #      不能靠"不结盟"（缺省已经是全体交战了）。
            for i, a in enumerate(self.players):
                for b in self.players[i + 1:]:
                    w.declare_war(a, b)
        for name in self.players:
            # ★ 沙盒**不管经济** ⇒ 补给必须管够：引擎每回合收军粮（步1/骑2），
            #   断粮则**每军扣 HP**、扣到 0 饿毙。资源给 0 的话军队是**饿死**的不是战死的
            #   （实测踩过：无人交战却每回合稳定掉 35 hp，全灭后靠补员复活 ⇒ 死循环）。
            w.nations[name].res["补给"] = 10 ** 6
            self.spawn(name, BASE_CAP)
        # ★ 主城默认 **L2 城堡**（用户 2026-09-24：「顺便 rl 线给主城加一个默认 l2 的城堡」）：
        #   引擎里 `t["buildings"]["城堡"]` 的**计数就是等级**（`_defense_pct` 按它算减伤）
        #   ⇒ 主城更难打、防守更站得住，攻守才有真正的取舍。
        for name in self.players:
            core = self.core_of(name)
            if core is not None:
                w.tiles[core]["buildings"]["城堡"] = 2
        # ★ 编组状态是**模块内存**：每局开始必须清，否则上一局的编组漏进来
        #   （`ruleai/v11plus/__init__.py` 明文要求）
        grouping.clear()
        # ★ **先手可换**（用户 2026-09-24：「每 8 局换先后手」）—— 固定先手会把
        #   "先手优势"永远记在同一个网络的头上；轮换后每个网络都当过得利/吃亏的那一方。
        #   ★ 多玩家下 `first` 是 `self.players` 里的**任意一个**（≥3 轮流手）。
        order = list(self.players)
        if self.first in order:
            order.remove(self.first)
            order.insert(0, self.first)
        self.pending = [n for n in order if self.alive(n)]   # 本回合还轮到谁行动
        self.last_ok = True                                    # 上一步是否被引擎接受（进观测）
        return self

    def _random_starts(self) -> dict:
        """随机开局：N 国核心随机、但**两两切比雪夫距离 ≥ `min_margin(size, 国数)`**。

        十字开局要 3×3 的空间 ⇒ 核心落在 `[1, size-2]`（贴边会让十字缺臂）。
        200 次抽不到就退回 `_fallback_starts()`（确定性铺开）。
        """
        import random
        rng = random.Random(self.seed)
        lo, hi = 1, max(1, self.size - 2)
        need = min_margin(self.size, len(self.players))
        for _ in range(200):
            pts = [(rng.randint(lo, hi), rng.randint(lo, hi))
                   for _ in self.players]
            ok = all(max(abs(pts[i][0] - pts[j][0]), abs(pts[i][1] - pts[j][1])) >= need
                     for i in range(len(pts)) for j in range(i + 1, len(pts)))
            if ok:
                return dict(zip(self.players, pts))
        return self._fallback_starts()

    def _fallback_starts(self) -> dict:
        """★ **确定性兜底开局**（随机 200 次抽不到时）。

        ⚠ 原来退回的是写死的 `STARTS`（**两个**坐标）—— 多国时会
          `zip(self.players, STARTS)` 出长度不符，或者更糟：**两个国家同一个核心**
          （`_place_crosses` 里 `(x,y) not in self.tiles` 会静默跳过第二家 ⇒ 那一家
          **开局就没有厅 ⇒ 一出场就是死的**，而**不报错**）。
        ⇒ 现在改成**贪心最远点**：从一角起，每次挑"离已选点最远"的格。
          确定性、总给出 N 个互不相同的核心，且只要格子够就自动最大化间距。
        """
        lo, hi = 1, max(1, self.size - 2)
        grid = [(x, y) for x in range(lo, hi + 1) for y in range(lo, hi + 1)]
        if len(grid) < len(self.players):          # 图太小：退回边界内的前 N 格（仍互不相同）
            grid = [(x, y) for x in range(self.size) for y in range(self.size)]
        chosen = [grid[0]]
        while len(chosen) < len(self.players):
            best = max((p for p in grid if p not in chosen),
                       key=lambda p: min(max(abs(p[0] - q[0]), abs(p[1] - q[1]))
                                         for q in chosen))
            chosen.append(best)
        return dict(zip(self.players, chosen))

    def _fixed_starts(self) -> dict:
        """`random_starts=False` 的固定开局（对照/复现用）。

        两国 ⇒ 老的 `STARTS`（对角最远，**逐位不变**，老测试与老结论都还成立）；
        多国 ⇒ 用确定性兜底（`STARTS` 只有两个坐标，`zip` 到 3 国就出错了）。
        """
        if len(self.players) == len(STARTS):
            return dict(STARTS)
        return self._fallback_starts()

    def set_alliance(self, *blocs: tuple[str, list[str]]) -> None:
        """★★ **开局直接指定**联盟 —— 外交关系是**场景条件**，不是模型的动作。

        用户 2026-09-24：「**是否中立和联盟和模型无关，开局直接指定，而不是让模型
        发起和接受**」。⇒ 沙盒的动作空间只有 `hold/move/attack`（`vocab.KIND`），
        **没有** propose/accept 那一套；联盟/中立由这里（或调用方）在开局摆好。

        用法：`sb.set_alliance(("北方同盟", ["甲", "丙"]))` ⇒ 甲丙互为盟友（`allied_between`
        为真、可 mv 不可 atk）；**没被写进任何联盟的国家就是中立国** —— 它的地
        `mv` 走不进、`atk` 也打不了（引擎："先结盟或先宣战"），观测里是
        `vocab.OWN_NEUTRAL_NATION`，与"敌国"分得清清楚楚。

        ★ 引擎侧的实现就是往 `world.blocs` 放一条 `{name, chief, members}`
          （`allied_between` = `entity_of` 相等）—— 这里不做 propose/accept 的流程，
          因为那套流程的产出**等价于**直接给这条记录，而流程本身是给 LLM 玩家用的。
        """
        for name, members in blocs:
            living = [m for m in members if m in self.world.nations]
            if len(living) < 2:
                continue
            self.world.blocs.append({"name": name, "chief": living[0],
                                     "members": living})

    def count_of(self, name: str, kind: str) -> int:
        """该国某兵种的支数（`民` = 民兵）。"""
        return sum(1 for a in self.armies_of(name) if a.get("type", "步") == kind)

    def spawn(self, name: str, n: int, kind: str = "步") -> None:
        """在**核心格**摆 `n` 支步兵。

        ★ 别用 `own_tiles[0]` —— 那是字典序第一格（实测 (3,2) 之类），**不是核心**。
        """
        core = self.core_of(name)
        if core is None:
            return
        x, y = core
        for _ in range(n):
            gid, seq = self.world._new_army(name)
            self.world.armies.append({
                "id": seq, "gid": gid, "name": f"{name}{seq}", "type": kind,
                "hp": unit_max_hp({"type": kind}), "x": x, "y": y,
                "owner": name, "moved_turn": -1, "engaged": False,
            })

    # ============================================================ 查询
    def core_of(self, name: str) -> tuple[int, int] | None:
        """该国的**市政厅格**（= 核心 = 国祚）。`None` ⇒ 已亡。"""
        for cell, t in sorted(self.world.tiles.items()):
            if t["owner"] == name and t["buildings"].get("市政厅", 0) > 0:
                return cell
        return None

    def armies_of(self, name: str) -> list[dict]:
        return [a for a in self.world.armies if a["owner"] == name and a.get("hp", 0) > 0]

    def tiles_of(self, name: str, mask=None) -> int:
        """国土格数。★ 数**对手**时必须传 `mask`（视野外看不见谁占了哪）。

        ★ 为什么给 mask 而不是让调用方自己数：补员上限那条公式
          （`BASE_CAP + 国土 // TILES_PER_CAP`）**只能有一处** ——
          调用方自己数一遍就会和 `cap_of` 漂移，而那种错不报错、只是观测慢慢说谎。
        """
        return sum(1 for cell, t in self.world.tiles.items()
                   if t["owner"] == name and (mask is None or cell in mask))

    def cap_of(self, name: str, mask=None) -> int:
        """补员上限 = `BASE_CAP + 国土数 // TILES_PER_CAP`（★ 对手要过 `mask`）。"""
        return BASE_CAP + self.tiles_of(name, mask) // TILES_PER_CAP

    def alive(self, name: str) -> bool:
        return name in self.world.nations and self.world.has_townhall(name)

    def done(self) -> bool:
        if self.winning_entity() is not None:
            return True
        return self.turn >= self.t_max

    def winning_entity(self) -> str | None:
        """★★ 胜者 = 场上**唯一剩下的外交实体**（`None` = 还没定局）。

        用户 2026-09-24：「**应该是联盟胜利或者单国胜利**」。⇒ 判据不是"某一个国家
        还在不在"，而是"还剩几个**实体**"：
          · 只剩 1 个实体 ⇒ 它赢（**单国**= 它没盟友；**联盟**= 它的盟赢）
          · 一个不剩       ⇒ 同归于尽/平局 ⇒ `None`
        这也把"多国时灭掉一家不算结束"**结构性地**表达出来了 —— 不再依赖"两国"这个假设。
        """
        alive = [n for n in self.world.order
                 if n in self.world.nations and self.alive(n)]
        if not alive:
            return None
        ents = {self.world.entity_of(n) for n in alive}
        return ents.pop() if len(ents) == 1 else None

    def winner_members(self) -> tuple[str, ...]:
        """胜方实体里的国家名单（单国胜利 ⇒ 只有一个）。没定局 ⇒ `()`。"""
        e = self.winning_entity()
        return tuple(self.world.entity_members(e)) if e else ()

    def winner(self) -> str | None:
        """胜方的**实体标签**（`国:甲` / `盟:X`）；`None` = 平局 / 超时 / 未定局。

        ★ 返回的是**实体**不是国名 —— 调用方要判断"某一国赢没赢"，用
          `name in winner_members()`，别拿国名跟它比字符串。
        """
        return self.winning_entity()

    def reward(self, for_player: str) -> float:
        """★ 赢 ⇒ `1 − turns / T_MAX`（时间越短越高）；输 ⇒ −1；平 ⇒ 0。

        ★ **赢 = 我的实体赢**（联盟胜利或单国胜利）⇒ 查 `winner_members()`，
          **不是**拿国名跟实体标签比字符串（改实体口径时这里最容易漏）。
        """
        if self.winner() is None:
            return 0.0
        if for_player not in self.winner_members():
            return -1.0
        return 1.0 - self.turn / self.t_max

    # ============================================================ 环境接口（给 MCTS / RL）
    def known_halls(self, name: str, mask=None) -> dict:
        """该国**已知**的市政厅 `{格: 最后看见时的主人}` —— **记忆 ∪ 本帧视野**。

        ★ 顺手把**本帧看得见的**并进记忆（lazy latch）：调用点（`encode`/`evaluate`）
          手里正好攥着 `mask`，不必再算一遍视野。★ 只有被问到的那个国会被记账，
          而"问谁"恰好就是"谁在观测/被评分" ⇒ 账本永远是齐的。
        ★ `mask=None` ⇒ 自己现算一遍视野（方便、但比传进来贵一次 `vision_mask`）。
        """
        w = self.world
        if w is None:
            return {}
        if mask is None:
            from ruleai.v11plus import pathfind
            mask = pathfind.vision_mask(w, name)
        self.halls.observe(w, name, mask)
        return self.halls.known(w, name)

    def known_enemies(self, me: str, mask=None, armies=None) -> list[dict]:
        """记忆里**此刻不可见**的敌军（按番号，附 `age`）—— **顺手把本帧看得见的记下来**。

        ★ 与 `known_halls` 同一个套路（lazy latch）：调用方手里正好攥着 `mask`/`armies`，
          不必再算一遍。★ 只有被问到的那个国会被记账，而"问谁"就是"谁在观测"⇒ 账本永远是齐的。
        ★ `armies` = `window_armies` 的结果：用它来算"哪些已经进 token 了"，
          **与军队 token 的口径逐字一致**（连"野人不进"这条也一致）⇒ 不会重复发、也不会漏发。
        """
        w = self.world
        if w is None:
            return []
        if mask is None:
            from ruleai.v11plus import pathfind
            mask = pathfind.vision_mask(w, me)
        self.war_mem.observe(w, me, mask, self.turn)
        if armies is None:
            from .encode import window_armies
            armies = window_armies(self, me, mask)
        vis = {a["gid"] for a in armies if a["owner"] != me}
        return self.war_mem.known(me, self.turn, vis)

    # ============================================================ ★★ 情报注入（唯一口子）
    def tell_halls(self, name: str, items: dict) -> int:
        """**外部注入厅的情报** `{格: 主人}` —— 见 `hall_memory.HallMemory.tell`。

        ★★ 用户 2026-09-25：「**任何记忆都允许合法外部修改，或者有办法传入新的，
          这是配合情报的设计**」—— 记忆是**可写的**，因为这套东西最终要跟
          **LLM 玩家**配合，而玩家的情报来源不止自己那点视野（间谍/盟友/战报/推理）。
        ★★ **口子只开在这里**（沙盒层）。`encode` **不许**调 `tell`：
          观测只**读**记忆，写记忆只有两条合法路径 ——
          ① "**看见就覆盖**"（`observe`，来自视野）；② **这里**（来自情报）。
          把引擎真值**自动**倒进来 = 偷看（本线最忌），所以注入**必须**是
          显式的、由调用方（将来的 LLM 面板/间谍系统）按合法性决定内容。
        ★ `all_known=True`（间谍模式）就是本口子的一个特例（构造时一次性全注）。
        """
        return self.halls.tell(name, items)

    def tell_armies(self, name: str, items: dict, turn: int | None = None) -> int:
        """**外部告知某国的军情** `{来源国: {"步": n, "骑": n, "民": n}}`（见 `rl/intel.py`）。

        ★★ 口径**照抄引擎的间谍**（`mp.World._econ_snapshot` 的注释）：
          「粗略军情：**只有各兵种数量 —— 位置/血量/番号不外泄**」
          ⇒ 这里**只有数量**。
        ★ 我一度写过"逐军注入位置 + 番号"的 `tell_enemies` —— **那正是引擎禁止
          间谍给的东西**，已删。**位置/番号只能来自自己看见**（`war_memory`）。
        `turn`（缺省当前回合）= 这份情报**是什么时候的**（间谍 3 回合才回报 ⇒ 常常更旧）。
        ★ 合法性由调用方负责；**口子只在这里**（同 `tell_halls`）。
        """
        t = self.turn if turn is None else int(turn)
        # ★★ **未来回合的情报 ⇒ 当场报错**（不许静默丢）：`age = 现在 − 情报的回合`
        #   一旦为负，读取侧会把它整条丢掉 —— 那是"注了但看不见、也不报错"，
        #   正是本线最忌讳的形状（注入口尤其不能这样）。调用方给错 turn 就该炸。
        if t > self.turn:
            raise ValueError(
                f"情报的回合 {t} 晚于当前回合 {self.turn} —— 情报不可能来自未来"
                f"（要么是调用方给错了 turn，要么是想「预告」还没发生的事）")
        return self.intel.tell_armies(name, items, t)

    def visible_enemies(self, me: str, mask=None) -> dict:
        """本帧**看得见**的敌军快照 `{gid: {x,y,kind,hp,no,owner}}`。

        ★ 唯一的用处是给潜槽的**辅助目标**算"下一帧哪些会离开视野"
          （`rl/mem_aux.py`）。★ 口径与番号账本**同一份**（`war_memory.visible_foes`）——
          两处抄两遍就会慢慢漂开，而漂开的后果是**在教模型错的东西**且不报错。
        """
        w = self.world
        if w is None:
            return {}
        if mask is None:
            from ruleai.v11plus import pathfind
            mask = pathfind.vision_mask(w, me)
        from .war_memory import visible_foes
        return visible_foes(w, me, mask)

    def clone(self) -> "Sandbox":
        """试演副本。★ 8×8 上 `deepcopy` 实测 **~1.8 ms** ⇒ MCTS 可以**真实试演**
        （不用搞"记录动作再重放"那套）。"""
        import copy
        sb = Sandbox.__new__(Sandbox)
        sb.seed, sb.size, sb.t_max, sb.war = self.seed, self.size, self.t_max, self.war
        sb.world = copy.deepcopy(self.world)
        sb.turn = self.turn
        sb.turn_offset = self.turn_offset   # ★ 试演里「现在几点」必须和真身一致
        sb.log = list(self.log)
        sb.pending = list(self.pending)
        sb.last_ok = self.last_ok
        sb.first = self.first
        sb.halls_known = self.halls_known
        sb.halls = self.halls.clone()            # ★ 记忆要跟着副本走（试演不能凭空多知道）
        sb.kills = self.kills.clone()            # ★ 击杀账本同理
        sb.war_mem = self.war_mem.clone()        # ★ 敌军番号账本同理（试演不能共享）
        sb.intel = self.intel.clone()            # ★ 情报账本同理
        return sb

    def current_player(self) -> str | None:
        """该谁动（`None` = 本局已结束）。"""
        return self.pending[0] if self.pending else None

    def legal(self) -> list[tuple]:
        """当前玩家的**合法动作**：`(aid, kind, x, y)` + 收手 `(END, None, None, None)`。

        **合法集在这里预先过滤干净**，别指望引擎报错：
        · `move` 的目标从 `world._reachable`（引擎合法集）里拿 ⇒ **结构上不会撞墙**
          （撞墙会 `_blind_cost` 烧掉整队移动额度 —— 那是"废动作"，不该进模型的选择集）
        · `attack` 只收"走不通、但够得着"的格（`_reachable(for_attack=True)` 里、不在 move 集里的）
        · 交战中的军 / 本回合已动过的军，直接不给动作
        """
        name = self.current_player()
        if name is None or not self.alive(name):
            return []
        out: list[tuple] = []
        for a in self.armies_of(name):
            if a.get("engaged") or a.get("moved_turn") == self.world.turn:
                continue                       # ★ 已用过的军 / 交战中的军：整支屏蔽
            here = (a["x"], a["y"])
            # ★★ **"原地不动"**（用户 2026-09-24：「不应该回合结束，改成一个给军队的
            #   特殊动作，**原地不动**，使用后 **mask 这个军队**，当全部军队 mask 后，
            #   **自动结束**，而不是手动结束」）
            #   ⇒ 取代了原来的**全局** `end_turn` 动作。这样每个候选都挂在某支军队上
            #   （动作空间同质），模型必须**为每支军各自决定**，不能一按 END 跳过全部。
            out.append((a["id"], "hold", here[0], here[1]))
            walk = self.world._reachable(name, a, for_attack=False)
            for cell in sorted(walk):
                if cell != here:
                    out.append((a["id"], "move", cell[0], cell[1]))
            for cell in sorted(self.world._reachable(name, a, for_attack=True)):
                if cell not in walk:
                    out.append((a["id"], "attack", cell[0], cell[1]))
            # ★ **无视野的邻格：移动与进攻两条路都给**（用户 2026-09-24）
            #   引擎的规矩是「**敌国领土不能 mv，但允许 atk**」（`_mv_wall` 拒 mv / `attack` 收），
            #   而看不见的时候模型**无从知道那一格是什么** ⇒ 两条都给，让引擎当场判，
            #   并把它那句教学式错误消息（实测原话：「(5,5) 有敌军驻守，不能 mv 过去；
            #   **进攻请用 atk（会交战）**」）当成**侦察的情报来源**。
            for (x, y) in self._probe_cells(name, a, walk):
                out.append((a["id"], "move", x, y))
                out.append((a["id"], "attack", x, y))
        return out

    def _probe_cells(self, name: str, a: dict, walk) -> list[tuple]:
        """★ **允许撞墙的试探格**：该军周围（1 格移动力）里**视野外**的格（边界除外）。

        用户 2026-09-24：「**特殊撞墙 mv 允许存在**，在**无视野**的情况下，全部候选集允许
        （边界除外），**按现有的引擎设计返回错误消息**，为模型那里存在敌国领土」。

        ⇒ 看不见的地方**必须让模型摸得到**：摸了，引擎照报错、**额度照烧**
          （`mp.py` 的 `_blind_cost` 原话：「视野外撞墙 → 报错照给、额度照烧，**侦察要付钱**」），
          模型就**从那条错误消息里**学到"那儿走不进去 / 那儿是敌国领土"。
        ★ 把候选卡死在 `_reachable` 上等于**把侦察这条路由封死** —— 模型永远发现不了
          视野外的敌国领土（那正是它要去拔的厅所在的地方）。
        """
        from ruleai.v11plus import pathfind
        mask = pathfind.vision_mask(self.world, name)
        n = self.size
        out = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                x, y = a["x"] + dx, a["y"] + dy
                if not (0 <= x < n and 0 <= y < n):
                    continue                   # ★ 边界除外
                if (x, y) in walk or (x, y) in mask:
                    continue                   # 已给过 / 有视野的引擎已判过
                out.append((x, y))
        return sorted(out)

    def step(self, action: tuple) -> tuple[bool, str]:
        """执行一个动作。`END` = 本方收手 ⇒ 换人；双方都收手 ⇒ 结算 + 开下一回合。"""
        name = self.current_player()
        if name is None:
            return False, "本局已结束"
        aid, kind, x, y = action
        if kind == "hold":
            a = self.world._army(name, aid)
            if a is not None:
                a["moved_turn"] = self.world.turn     # ★ mask 这支军（本回合不再有动作）
            self.last_ok = True
            self._auto_advance()
            return True, "hold"
        if kind == "move":
            ok, msg = self.world.move(name, aid, x, y)
        elif kind == "attack":
            ok, msg = self.world.attack(name, [aid], x, y)
        else:
            ok, msg = False, f"未知动作 {action!r}"
        self.last_ok = bool(ok)
        self._auto_advance()
        return ok, msg

    def can_act(self, name: str) -> bool:
        """该国**还有能动的军**吗（都 mask 过 / 交战中 ⇒ 没有）。

        ★ `_auto_advance` 的判据 —— "全部军队 mask 后自动结束"落在这条上。
        """
        return any(not a.get("engaged") and a.get("moved_turn") != self.world.turn
                   for a in self.armies_of(name))

    def _auto_advance(self) -> None:
        """★ **全部军队都被 mask 后自动推进**（用户 2026-09-24：「当全部军队 mask 后，
        **自动结束**，而不是手动结束」）。

        判据是 `can_act()`（还有没有能动的军）。没有 ⇒ 本方收手；全员收手 ⇒ 结算 + 开下一回合。

        ★★ **必须是循环**（2026-09-25 修的真 bug）：原来只推进**一步**，
          于是"结算后新一轮的第一个国家就不能动"就**卡在中途退出** ——
          `legal()` 空、`is_terminal()` 假、**没有胜方**，而**不报错**。
          ⇒ 那一局既拿不到终局的 ±1、也不进"先手连赢"闸门，**静默地白打**。
        ★ 为什么"活着却不能动"是正常的：沙盒**没有征兵动作**（只有补员），
          **兵全打光了的国家**要等下一次补员（每 `RESUPPLY_EVERY` 回合）才有事可做。
          ⇒ 卡住的是这种国家，而且它**每一步都可能是当前玩家**。
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
            self.pending = [n for n in self.players if self.alive(n)]
            if not self.pending:
                return                      # 没人活着（终局由 `done()` 判）

    def is_terminal(self) -> bool:
        return self.done()

    # ============================================================ ★ 嫁接点
    def ai_turn(self, name: str, *, teacher: str = "mine", verbose: bool = False) -> list:
        """跑一方的军事回合，返回动作表 `[(tool, args, ok, msg), …]`（`args` 是 **1-based**）。

        `teacher="mine"` ⇒ **`rl/military.py`**（抄 v11plus 的评估/编组/寻路，
        **自己写**目标池与守家/侦察决策）—— 默认。
        `teacher="v11plus"` ⇒ 直接调 `ruleai.v11plus.military.run`，当**对照线**
        （它只会平推：不会守、不侦察）。
        """
        if not self.alive(name):
            return []
        # ★ 对照线老师（`rl/military.py`）只吃**一个** `enemy` ⇒ 多玩家下取
        #   **最近的那个对手**（按双方核心的切比雪夫距离）。
        #   ⚠ 这是**近似**：真正的多玩家目标池它还没写。它只是**对照线**
        #     （RL 训练走 `train.py` 的自对弈，不经过这里）⇒ 先能跑、够用即可，
        #     别拿它的成绩当多玩家的基准。
        enemy = self._nearest_rival(name)
        if teacher == "v11plus":
            from ruleai.v11plus import military as v11
            from ruleai.v11plus.ledger import Ledger
            ledger = Ledger(self.world, name, UNLIMITED)
            v11.run(ledger, self.world, name)
            return ledger.acts
        from . import military as mine
        return mine.run(self.world, name, enemy=enemy, verbose=verbose)

    # ---- 查询：厅数（= 补员产能）----
    def _nearest_rival(self, name: str) -> str | None:
        """离我核心**最近**的对手（按切比雪夫距离；没有 ⇒ `None`）。

        ★ 只给**对照线老师**用（它只吃一个 `enemy`）—— 见 `ai_turn` 的注释。
        """
        from .evaluate import rival_nations
        mine = self.core_of(name) or (0, 0)
        foes = rival_nations(self.world, name)
        if not foes:
            return None
        return min(foes, key=lambda f: (max(abs((self.core_of(f) or (0, 0))[0] - mine[0]),
                                            abs((self.core_of(f) or (0, 0))[1] - mine[1])),
                                        f))     # ★ 末尾带 `f` ⇒ 同距时**确定性**（不靠字典序）

    def halls_of(self, name: str) -> int:
        """该国**已落成**的市政厅数（= 国祚，也是补员的"产能"）。"""
        return sum(t["buildings"].get("市政厅", 0) for t in self.world.tiles.values()
                   if t["owner"] == name)

    # ============================================================ 回合推进
    def resupply(self) -> list[str]:
        """每 `RESUPPLY_EVERY` 回合**触发一次补员**，按**配额**出兵。

        ★ 规则（用户 2026-09-24）：「补员**不是一次补满**，而是 **5 回合一支兵**，
        **每有一个市政厅多补一支**，每个市政厅每次补员出一支，**不能补员超上限**」

        ⇒ **补员量 = 1（基础） + 市政厅数**，总数**不超过 `cap_of`**（= 5 + 国土//10）。

        ★ **不缺员时改出民兵**（见下），而民兵的编制上限 = **步兵 × `MILITIA_PER_FOOT`**
          （用户 2026-09-25：「以前民兵是**无上限增长**的，改成**上限是步兵 2 倍**」）。
          ⇒ 两个分支都是**有限**的：缺员补步兵（到 `cap_of`）、不缺员补民兵（到步兵×2）。
          满编时**本回合什么都不出**（不再"白送"）。

        ★ 效果：开局 5 支已经等于上限 ⇒ **只有战损后才补得进来**，兵成了稀缺资源；
          而"多一座厅多一支"⇒ **疆域里的厅数决定补员速度**（厅只有核心白送那一座，
          再想多要得自己建 —— 门槛 `min_slots≥6`、且贵）。
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
            foot = self.count_of(name, "步")     # ★ 只数**步兵** —— 上限是给步兵设的
            gap = cap - foot
            if gap > 0:
                n = min(quota, gap)              # ★ 不能超上限
                self.spawn(name, n, kind="步")
                notes.append(f"{name} **缺员** ⇒ 补步兵 +{n}"
                             f"（厅×{hall} 配额{quota}，步兵 {foot}/{cap}）")
            else:
                # ★★ **不缺员 ⇒ 出民兵**（用户 2026-09-24：「如果军队不缺员，市政厅每 5 个
                #   回合的增援改成**一支民兵**，缺员就改为补步兵」）。
                #   民兵在引擎里是**廉价驻守兵**（80hp / 攻 20 = 步骑的四成）⇒ 它天生是
                #   守家用：守方增援可 `mv` 进自家地（见 `legal()` 与墙规则），正好补上防守。
                #   ⚠ 引擎原本的民兵有"只能军屯征召、总数 ≤ 军屯数"的限制，这里按用户
                #     的要求**绕过它**（沙盒不走经济层）。
                # ★★ **但编制要有上限**（用户 2026-09-25：「以前民兵是**无上限增长**的，
                #   改成**上限是步兵 2 倍**」）—— 原先这里照抄"无上限"，于是"不缺员"这个
                #   分支每 5 回合白送 `厅×1` 支、**永远收支为正** ⇒ 只要不打仗，民兵就能
                #   一路堆到几十支。那不是"守家增益"，是**一台不需要对手配合的印钞机**：
                #   势函数里 `W_ARMY` 那一项会一路涨，模型学到的将是"别打仗、苟着刷民兵"。
                mil = self.count_of(name, "民")
                mcap = MILITIA_PER_FOOT * foot    # ★ 上限 = 步兵 × 2（现役步兵数，非上限）
                room = mcap - mil
                if room > 0:
                    n = min(quota, room)
                    self.spawn(name, n, kind="民")
                    notes.append(f"{name} **不缺员** ⇒ 民兵 +{n}"
                                 f"（厅×{hall} 配额{quota}，民兵 {mil}/{mcap}=步兵×2）")
                else:
                    notes.append(f"{name} **不缺员**，但民兵已满编 "
                                 f"（{mil}/{mcap}=步兵×2，本回合不出）")
        return notes

    def end_turn(self) -> None:
        """双方都行动完 ⇒ 引擎结算 → **开下一回合** → 补员。

        ★ **`begin_turn()` 不能漏**：`world.turn += 1` 在 `begin_turn`（`mp.py:2597`）里，
          **不在 `resolve_turn`** 里。漏了它 ⇒ `world.turn` 永远是 0 ⇒ 每支军的
          `moved_turn == world.turn` 恒成立 ⇒ `military.run` 认为"本回合都动过了"
          ⇒ **全军一步不走**（实测踩过：30 回合原地不动，无任何动作）。
          引擎的回合模型是 `begin_turn → 各国行动 → resolve_turn`。
        """
        # ★★ 结算**前**拍快照（击杀归因靠它）—— `resolve_turn` 一跑，战死的军
        #   就从 `world.armies` 里没了，事后无法复原"它死在那一格"。
        before = {a["id"]: (a["owner"], a["x"], a["y"], a.get("hp", 0))
                  for a in self.world.armies}
        self.world.resolve_turn()
        self.kills.observe(self.world, before)
        self.world.begin_turn()
        self.turn = self.world.turn          # ★ 与引擎同步，别自己数（免得漂）
        for note in self.resupply():
            self.log.append(f"[T{self.turn}] {note}")

    # ============================================================ 整局
    def rollout(self, verbose: bool = False) -> dict:
        """**双方都由 v11plus 军事层驱动**打完整局。

        这一个函数干两件事：① 验证沙盒能跑通并正确判定胜负；
        ② 生成 BC 数据 —— 每回合每方的 `ledger.acts` 就是标签。
        """
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
        """把 `ledger.acts` 转成 BC 样本行（**这里先只落"动作 + 成功与否"**）。

        ⚠ 观测还没接（那是任务 2/分词器的事）—— 现在先把"标签"定型：
        `args` 是 **1-based**（给 LLM 的写法），落库时统一转 **0-based** 免得下游踩。
        """
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


# ================================================================ 自测
def _playout(seed: int = 0, verbose: bool = True) -> dict:
    sb = Sandbox(seed=seed).reset()
    if verbose:
        cores = {n: sb.core_of(n) for n in sb.players}
        wars = [(a, b) for i, a in enumerate(sb.players)
                for b in sb.players[i + 1:] if sb.world.war_between(a, b)]
        print(f"建局：{sb.n_nations} 国 {cores}"
              f"  tiles={len(sb.world.tiles)}"
              f"  野人={sum(1 for a in sb.world.armies if a['owner'] == '野人')}"
              f"  步兵={[len(sb.armies_of(n)) for n in sb.players]}"
              f"  交战对={wars}")
    out = sb.rollout(verbose=verbose)
    if verbose:
        print(f"结果：{out['turns']} 回合  winner={out['winner']}"
              f"  （实体口径：{out.get('winner_members', sb.winner_members())}）")
        for n in sb.players:
            print(f"  {n}: 国土 {sb.tiles_of(n)}  军队 {len(sb.armies_of(n))}"
                  f"  上限 {sb.cap_of(n)}  国祚 {sb.alive(n)}  核心 {sb.core_of(n)}")
        n_acts = sum(len(a) for _, _, a in out["actions"])
        print(f"  动作总数 {n_acts}")
        for line in out["log"][-6:]:
            print("   ", line)
    return out


if __name__ == "__main__":
    import sys
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    for s in range(n):
        print(f"─── 种子 {s} ───")
        _playout(seed=s)