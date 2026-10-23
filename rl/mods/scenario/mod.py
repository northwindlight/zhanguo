# -*- coding: utf-8 -*-
"""RL 沙盒剧本 `scenario:rl-sandbox` —— **摆局的唯一出处**。

★ 职责边界
  ─────────
  · **本 mod**：决定"局面长什么样"（十字开局、核心强制平原、开局兵、扩地口径）。
    它只**调 `rl:sandbox` 的机械动作**，**不直接调 native**。
  · **`rl:train`**：提供机械动作（建国、铺地、种建筑、造兵、结盟、宣战），
    每个动作都**只调 native 已声明的方法**。
  · **native**：裁决一切规则（野人怎么合成、伤害怎么摊、亡国怎么判、兵能不能动）。

★ 为什么不再有"门面模拟旧开局"
  ─────────────────────────────
  旧 `rl/engine.py` 的 `place_cross` / `_grow_territory` / `seed_barbarian` 把开局
  写死在自己身上，还顺手把 `world.units.guard_once` 当私有字段改
  （那个字段的语义**恰好相反**：加进去是"这格**不**再合成野人"）。
  ⇒ 现在这些全是剧本数据 + 明示能力调用。
"""
from __future__ import annotations

import math
import random

from engine.scenario_mod import ScenarioMod

from ...tokens import CROSS
from ... import nations as N

# ★ 抽国土用的**独立随机流**盐（与开局位置、回合偏移各走各的）。
TERRITORY_SALT = 0x7E77

# ★ 引擎的建筑内容 ID。
TOWN_HALL = "native:town-hall"
CASTLE = "native:castle"
BARRACKS = "native:barracks"
MILITIA_CAMP = "native:militia-camp"


def min_margin(size: int, n_nations: int) -> int:
    """随机开局时两国核心的**最小距离**（切比雪夫距离）。

    用户 2026-09-24：「必须至少有足够距离，这个距离是**对于国家总量和地图大小**的函数」。

    取"平均铺开间距"的几何估计：`N` 国在 `size × size` 上铺开 ⇒ 理想间距 ≈ `size / √N`，
    再夹一个下限 2（小于 2 就是贴脸，没有对抗空间可言）。

        8×8、2 国  ⇒ 8/1.414 ≈ 5.66 ⇒ **5**   ← 正好等于原来那个固定开局 (1,1)-(6,6) 的距离
        40×40、2 国 ⇒ 28；40×40、5 国 ⇒ 17
    """
    return max(2, int(size / max(1.0, math.sqrt(max(1, n_nations)))))


# ★ 开局国土的比例（**可调先验**，不是物理常数）——
#   模组里的权威值在 `mod.toml` 的 `[Scenario]`；这里给的是**同一组数**的模块级副本，
#   供**纯函数**（`territory_range`）使用 —— 改模组时两处要一起改（都有注释指路）。
TERRITORY_FRAC_LO = 0.15
TERRITORY_FRAC_HI = 0.35
TERRITORY_FRAC_2P = (0.35, 0.60)


def territory_range(size: int, n_nations: int, *,
                    lo: float = None, hi: float = None,
                    two_p: tuple = None) -> tuple[int, int]:
    """本局国土格数的**范围** `[lo, hi]`（含端点）—— **每局自动算**，不是命令行常数。

    份额 = `size² / n`（每国摊到的格数）⇒ `lo = frac_lo × 份额`、`hi = frac_hi × 份额`，
    再夹一个下界（十字 5 格）。**没有上界闸** —— 见 `TERRITORY_FRAC_HI` 那段说明。

        12×12、3 国 ⇒ 份额 48 ⇒ **[7, 17]**
        16×16、4 国 ⇒ 份额 64 ⇒ **[10, 22]**
        20×20、5 国 ⇒ 份额 80 ⇒ **[12, 28]**
         8×8、3 国 ⇒ 份额 21 ⇒ **[5,  7]**

    ★ 两个比例是**份额**的比例 ⇒ 全国加起来最多占地图的 `frac_hi = 35%`，
      其余 65% 还是野地（= 扩张成本，那是这条线的既有口径）。
    """
    floor = len(CROSS)
    share = (size * size) / max(1, n_nations)
    # ★ 1v1（`n<=2`）走**加码**的比例
    lo = TERRITORY_FRAC_LO if lo is None else lo
    hi = TERRITORY_FRAC_HI if hi is None else hi
    two_p = TERRITORY_FRAC_2P if two_p is None else tuple(two_p)
    flo, fhi = (two_p if n_nations <= 2 else (lo, hi))
    lo = max(floor, int(round(flo * share)))
    hi = max(lo, int(round(fhi * share)))
    return lo, hi


class RLSandboxMod(ScenarioMod):
    """RL 沙盒剧本。"""

    text = "RL 沙盒：8×8，两国对角，十字开局，各 5 步兵，市政厅 + 城堡 + 兵营。"

    def mixins(self):
        return ()

    def configure(self, declaration, path):
        super().configure(declaration, path)
        scenario = declaration.get('Scenario', {})
        if set(scenario) != {'Castle', 'MilitiaCamp', 'Barracks', 'Army',
                             'TerritoryFracLo', 'TerritoryFracHi', 'TerritoryFrac2P'}:
            raise ValueError('Scenario 段落必须声明 Castle/MilitiaCamp/Barracks/Army/TerritoryFrac*')
        for key in ('Castle', 'MilitiaCamp', 'Barracks', 'Army'):
            if type(scenario[key]) is not int or scenario[key] < 0:
                raise ValueError(f'Scenario.{key} 必须是非负整数')
        if scenario['Barracks'] < 1:
            raise ValueError('Scenario.Barracks 至少为 1（没有兵营就没有征召额度）')

    def objects(self, world):
        call = lambda cap, method, *a, **k: self.runtime.object_call(world, cap, method, *a, **k)
        return {'rl:scenario': ScenarioService(call, self.declaration['Scenario'])}

    def on_start(self, world):
        """剧本开局：摆成 RL 训练用的**标准局面**（8×8、两国对角）。"""
        call = lambda cap, method, *a: self.runtime.object_call(world, cap, method, *a)
        call('rl:scenario', 'setup', 8, 0, ['rl:n0', 'rl:n1'], 0, 5,
             'native:infantry', False)


class ScenarioService:
    """**摆局**：把一张空世界摆成一局可训练的沙盒。"""

    def __init__(self, call, settings):
        self.call = call
        self.settings = settings

    # ------------------------------------------------------------ 摆局
    def setup(self, size: int, seed: int, nations, territory_target: int = 0,
              armies: int = 0, kind: str = "native:infantry",
              random_starts: bool = True, starts: dict | None = None) -> dict:
        """摆一局。返回**摆局计划**（纯数据，供沙盒记账与报表）。

        ★ `territory_target` = 0 表示"按剧本参数**随机抽**"；给了数就用那个数
          （**各国严格同数**，见 `_grow`）。
        ★ `starts` = **显式指定**各国核心格 `{国家: (x, y)}`（剧本参数）。
          给了就**照办**（不抽签）—— 固定局面/测试夹具要的就是这个。
        """
        nations = list(nations)
        if len(nations) < 2:
            raise ValueError('沙盒至少要两个国家')
        self.call('rl:sandbox', 'reset', int(size), int(seed))
        if starts:
            missing = [n for n in nations if n not in starts]
            if missing:
                raise ValueError(f'指定的核心格不完整，缺：{missing}')
            starts = {n: (int(starts[n][0]), int(starts[n][1])) for n in nations}
        else:
            starts = self._starts(int(size), int(seed), nations, bool(random_starts))
        cores = {}
        for ident in nations:
            core = self._place_cross(ident, N.name(ident), starts[ident], int(size))
            cores[ident] = core
        target = int(territory_target) or self._draw_target(int(size), int(seed), len(nations))
        land = self._grow(int(size), int(seed), nations, target)
        if armies:
            for ident in nations:
                x, y = cores[ident]
                self.call('rl:sandbox', 'spawn', ident, kind, x, y, int(armies))
        return {
            'size': int(size),
            'seed': int(seed),
            'nations': tuple(nations),
            'starts': dict(starts),
            'cores': dict(cores),
            'territory_target': int(target),
            'territory_actual': int(land),
        }

    # ------------------------------------------------------------ 开局位置
    def _starts(self, size: int, seed: int, nations, random_starts: bool) -> dict:
        """各国核心格。

        ★ 十字开局要 3×3 的空间 ⇒ 核心落在 `[1, size-2]`（贴边会让十字缺臂）。
        ★ 随机 200 次抽不到就退回**确定性兜底**（贪心最远点）——
          绝不退回"写死的两个坐标"（多国时会 `zip` 出长度不符，或者
          **两个国家同一个核心** ⇒ 那一���开局就没有厅 ⇒ 一出场就是死的，而不报错）。
        """
        if not random_starts:
            return self._fallback_starts(size, nations)
        rng = random.Random(seed)
        lo, hi = 1, max(1, size - 2)
        need = min_margin(size, len(nations))
        for _ in range(200):
            points = [(rng.randint(lo, hi), rng.randint(lo, hi)) for _ in nations]
            if all(max(abs(points[i][0] - points[j][0]), abs(points[i][1] - points[j][1])) >= need
                   for i in range(len(nations)) for j in range(i + 1, len(nations))):
                return dict(zip(nations, points))
        return self._fallback_starts(size, nations)

    @staticmethod
    def _fallback_starts(size: int, nations) -> dict:
        """**确定性兜底开局**：从一角起，每次挑"离已选点最远"的格。"""
        lo, hi = 1, max(1, size - 2)
        grid = [(x, y) for x in range(lo, hi + 1) for y in range(lo, hi + 1)]
        if len(grid) < len(nations):
            grid = [(x, y) for x in range(size) for y in range(size)]
        chosen = [grid[0]]
        while len(chosen) < len(nations):
            best = max((p for p in grid if p not in chosen),
                       key=lambda p: min(max(abs(p[0] - q[0]), abs(p[1] - q[1]))
                                         for q in chosen))
            chosen.append(best)
        return dict(zip(nations, chosen))

    # ------------------------------------------------------------ 十字
    def _place_cross(self, ident: str, name: str, center, size: int) -> tuple:
        """十字开局：中心格 + 上下左右，物化并归 `ident`。

        ★ 中心格**强制平原**（旧引擎 `_place_crosses` 的口径）——
          否则核心可能落在山地，守方天然减伤，开局就不公平。
        ★ 中心格**送一座市政厅**（国祚）—— 新引擎没有这条自动规则，
          而沙盒的胜负判据就是"对方的厅还在不在"。
        ★ 建格与建国**一次做完**（`place_nation`）：native 的 `territory.create`
          要求 owner 已在册 ⇒ **先建国、后铺地**。
        """
        sites = {}
        for dx, dy in CROSS:
            x, y = center[0] + dx, center[1] + dy
            if not (0 <= x < size and 0 <= y < size):
                continue
            if self.call('rl:sandbox', 'owner_of', x, y) is not None:
                continue
            sites[f"{ident}/land-{x}-{y}"] = (x, y)
        if not sites:
            raise ValueError(f'{ident} 的十字开局一格都没放下去：中心 {center} 不可用')
        core_site = f"{ident}/land-{center[0]}-{center[1]}"
        if core_site not in sites:
            core_site = sorted(sites)[0]
        self.call('rl:sandbox', 'place_nation', ident, name, sites, core_site, "native:plain")
        self.call('rl:sandbox', 'seed_building', core_site, TOWN_HALL, 1)
        # ★★ **登记进 native 的继承序列**（2026-10-23 修，review P1-2）：
        #   引擎的亡国判据是"在册国还有没有**带首都角色**的站点"
        #   （`native:succession` 的结算阶段）⇒ **不登记就永远不判亡国**
        #   ⇒ 引擎那边"国家仍在册、军队还在、战争还在"，RL 侧却早判它死了 ⇒ 分叉。
        #   ★ 必须在**市政厅落成之后**登记（`enroll` 的语义是"从此按国祚判生死"，
        #     还没首都就登记 = 下一个结算点立刻判死）。
        self.call('rl:sandbox', 'enroll_succession', ident)
        self.call('rl:sandbox', 'seed_building', core_site, CASTLE, int(self.settings['Castle']))
        self.call('rl:sandbox', 'seed_building', core_site, BARRACKS, int(self.settings['Barracks']))
        # ★ 军屯（民兵的征召口）：没有它，`recruit('native:militia')` 会被引擎拒。
        #   ★ 军屯要**站点的农田**（native 的 `SITE_RESOURCE` 硬约束）⇒
        #     站点没有农田就**不种**（不是种一半，也不是报错）——
        #     引擎的拒绝是**事实**，脚本要如实反映它。
        if int(self.settings['MilitiaCamp']):
            try:
                self.call('rl:sandbox', 'seed_building', core_site, MILITIA_CAMP,
                          int(self.settings['MilitiaCamp']))
            except Exception:                        # noqa: BLE001 站点没有农田 ⇒ 就不种
                pass
        return center

    # ------------------------------------------------------------ 扩地
    def _target_range(self, size: int, n_nations: int) -> tuple[int, int]:
        """本局国土格数的**范围**（**转发**模块级的 `territory_range`）。

        ★ 为什么要转发：改"国土上限"只有**一个**入口（模块级那个纯函数），
          剧本与沙盒都读它 ⇒ 不会出现"两处各算一套"（探针也只需挂那一处）。
        """
        return territory_range(size, n_nations,
                               lo=self.settings['TerritoryFracLo'],
                               hi=self.settings['TerritoryFracHi'],
                               two_p=self.settings['TerritoryFrac2P'])

    def _draw_target(self, size: int, seed: int, n_nations: int) -> int:
        """本局**每国**的国土目标格数（抽签，走独立随机流）。

        ★ 份额 = `size² / n`（每国摊到的格数）⇒ 国土大小按份额取。
        ★ 抽签走 `Random(seed ^ TERRITORY_SALT)` —— 与开局位置、回合偏移**各走各的**，
          所以加这个特性**不会挪动**同一 seed 的地图与开局位置。
        """
        lo, hi = self._target_range(size, n_nations)
        return random.Random(seed ^ TERRITORY_SALT).randint(lo, hi)

    def _grow(self, size: int, seed: int, nations, target: int) -> int:
        """把各国国土从**十字 5 格**长到 `target` 格（**各国严格同数**）。

        ★★ **同步轮次**：每轮**每国各吃一格**，谁都不许多吃；
          某国一旦无格可吃，**整轮不落子**、全体一起停
          ⇒ "各国严格同数"是**构造出来的**，不是"长完再补齐"
          （那种写法在被围住时会静默不等）。
        ★ 合法 = 图内 + **无主**。别国的地抢不到，结构上就不可能。
        ★ 占一格就走 native 的 `create_claim`（建新地块并把核心设成占领者）
          或 `claim`（占已有的无主地块）—— **不再有"先撤野人再物化"那套影子逻辑**：
          native 的野人是**现场合成**的，格一旦物化，合成条件就不成立 ✔。
        """
        rng = random.Random(seed ^ TERRITORY_SALT)
        own = {n: set(self.call('rl:sandbox', 'owned_cells', n)) for n in nations}
        while True:
            todo = [n for n in nations if len(own[n]) < target]
            if not todo:
                break
            rng.shuffle(todo)                 # ★ 每轮洗牌：先挑的会挡住后挑的
            picks: dict = {}
            used: set = set()
            blocked = False
            for n in todo:
                cand = self._frontier(own[n], used, size)
                if not cand:
                    blocked = True
                    break
                picks[n] = cand[rng.randrange(len(cand))]
                used.add(picks[n])
            if blocked:
                break                          # ★ 整轮不落子 ⇒ 各国仍严格同数
            for n, cell in picks.items():
                self._claim(cell, n)
                own[n].add(cell)
        return min(len(s) for s in own.values())

    def _frontier(self, own: set, used: set, size: int) -> list:
        """`own` 的**合法接壤格**（图内 + 无主 + 不在 `own` + 本轮没被别人挑走）。

        ★ 返回**排序后的列表**：抽签是 `rng.randrange(len(cand))` ⇒ 顺序变了
          就等于换了随机流（同一 seed 复现不出来），所以不许依赖 set 的迭代序。
        """
        out = []
        for (x, y) in own:
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                cell = (x + dx, y + dy)
                if not (0 <= cell[0] < size and 0 <= cell[1] < size):
                    continue
                if cell in own or cell in used:
                    continue
                if self.call('rl:sandbox', 'owner_of', cell[0], cell[1]) is not None:
                    continue
                out.append(cell)
        return sorted(set(out))

    def _claim(self, cell, ident: str) -> None:
        """占一格当**开局国土**（走 native 的 `create_claim` / `claim`）。"""
        self.call('rl:sandbox', 'claim', ident, cell[0], cell[1])
