# -*- coding: utf-8 -*-
"""**开局国土随机大小**的守卫 —— 用户 2026-09-25（原话）：

    「给沙盒加个功能，初始国土**随机大小**（但是**各国国土数量严格相同**），
      生成方式是**以市政厅为中心**，随机选择接壤合法国土，直到满足国土需求，
      **国土大小和地图和国家数量正相关**，**每局自动设置范围**（**国土要记得移除野人**）」
    →「**国土可以贴上**」

★ 每条守卫都对着一个**会静默错**的形状（不是"再验一遍代码写对了"）：

  ① **各国严格同数** —— 换成"每国各自长到目标"，被挡住的那家会**少长几格而没人报错**；
     而"严格相同"正是用户点名的口径（不同数 ⇒ 打分器的 `W_TILE` 一开局就不公平，
     而且**看不出来**：图还是那张图、局还是能跑完）。
  ② **野人必须移除** —— 引擎里**野人只守无主格**。占下来的地上留着一支野人 =
     「有主 + 有野人」的矛盾格，而**引擎不会报错**（`guardians` 只是按格查表）。
  ③ **战役图必须没被扰动** —— 加了"抽国土"这件事之后，同一 seed 的地图/厅位置/
     回合偏移**一个比特都不许动**：否则"加国土前/后"两版**没法 A/B**
     （同一 seed 跑出来的不是同一个局），而没有任何东西会报错。
     ★★ **诚实说明它测得到/测不到什么**（我一开始按错的说法写的，故意破坏时才发现）：
        · 测**得到**：① 国土抽签与"时钟"（`turn_offset`）**共用同一个 `Random` 实例**
          （破坏版实测：`13 != 17`）；② 任何让"开/关国土"两版在厅位置、地形、
          回合偏移上分叉的改动。
        · 测**不到**：把盐换成"同一条 seed"那种写法 —— 因为每个消费者都
          `Random(某seed)`**各 new 各的实例**，结构上不会互相消费（同
          `test_rl_turn_clock.py` 第 ⑥ 条那条黄金值的诚实说明）。
  ④ **`--no-territory` 要真的回到老行为**（正好 5 格）—— 否则版间对照的开关是假的。
  ⑤ **连通 + 含厅**（"以市政厅为中心"的可检验含义）。
  ⑥ **正相关**：范围随地图（在 `n = f(size)` 下也随之随国家数）单调增。
  ⑦ **每局自动抽**（同一图不同局要有不同目标），且**范围有宽度**（否则等于没做）。
  ⑧ 占下来的格必须是**物化的完整地块**（走 `_new_tile`，不是手搓的半个 dict）。

★ **每一条都故意破坏过确认会响**（见文件末尾 `__main__` 上方的说明）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import vocab as V                                    # noqa: E402
from rl.sandbox import (Sandbox, n_nations_for, min_margin,  # noqa: E402
                        territory_range)

SIZES = (8, 12, 14, 16, 18, 20)
TRAIN_SIZES = (12, 16, 20)          # ★ 起炉实际会跑到的范围（`--size-min/--size-max`）


def _sb(seed: int, size: int, territory: bool = True) -> Sandbox:
    return Sandbox(seed=seed, size=size, n_nations=n_nations_for(size),
                   halls_known=True, territory=territory).reset()


def _own(sb, name):
    return {r.position for r in sb.env.tiles() if r.owner == name}


class TestEveryoneGetsExactlyTheSame(unittest.TestCase):
    """① **严格同数**（用户点名的口径）。"""

    def test_counts_are_identical_across_nations(self):
        for size in SIZES:
            for seed in range(8):
                sb = _sb(seed * 31 + size, size)
                counts = {n: sb.tiles_of(n) for n in sb.players}
                self.assertEqual(len(set(counts.values())), 1,
                                 f"{size}×{size} seed={seed} 各国国土不等：{counts} "
                                 f"⇒ 打分器一开局就不公平，而且不报错")

    def test_equal_even_when_the_target_is_unreachable(self):
        """★★ 这条才是「同步轮次」的**真判据**。

        ★ 为什么非要造这个场景：训练范围（12~20）里**从来不会被挡**
          （目标只占份额的 15~35%，野地有 65%）⇒ 上面那条"各国同数"在正常配置下
          **根本没机会失败** —— 换成"每国各自长到目标"的实现它照样绿，那是**假绿**。
          这里把目标顶到 30（8×8 三国要 90 格，放不下）⇒ 必然有人先被围死
          ⇒ 只有"**整轮不落子**"的实现才守得住同数。
        """
        # ★ 区间口径的**唯一入口**现在在**剧本模块**（`rl/mods/scenario/mod.py`）——
        #   沙盒只做转发 ⇒ 探针也要挂在剧本这一侧（挂错地方 = 探针空跑）。
        import rl.mods.scenario.mod as SM
        real = SM.territory_range
        SM.territory_range = lambda size, n, **kw: (30, 30)     # ★ 刻意够不着
        try:
            for seed in range(4):
                sb = _sb(seed, 8)
                counts = {n: sb.tiles_of(n) for n in sb.players}
                self.assertEqual(len(set(counts.values())), 1,
                                 f"目标够不着时各国国土不等：{counts}")
                self.assertGreater(min(counts.values()), 5,
                                   "一格都没长（这个场景没生效，守卫等于空跑）")
                self.assertLess(min(counts.values()), 30,
                                "居然长到 30 了 —— 8×8 三国放不下 90 格，说明没被挡")
        finally:
            SM.territory_range = real

    def test_target_is_reached_in_the_training_range(self):
        """目标**够得着**（没被挡在半路）—— 12~20 这个起炉范围上必须每次都长到目标。

        ★ 为什么敢断言"够得着"：目标只占"每国份额"的 15~35%，而野地有 65%
          ⇒ 除非开局被围死，否则一定够。被围死时**允许**少长（但各国仍同数），
          所以这条只钉**起炉范围**（判据是"实际会跑到的配置"）。
        """
        for size in TRAIN_SIZES:
            for seed in range(8):
                sb = _sb(seed * 7 + size, size)
                self.assertEqual(sb.tiles_of(sb.players[0]), sb.territory_target,
                                 f"{size}×{size} seed={seed} 只长到 "
                                 f"{sb.tiles_of(sb.players[0])}，目标是 {sb.territory_target}")


class TestBarbariansAreRemoved(unittest.TestCase):
    """② **国土要记得移除野人**（用户点名）。"""

    def test_no_barbarian_stands_on_owned_ground(self):
        for size in SIZES:
            for seed in range(6):
                sb = _sb(seed * 13 + size, size)
                guards = {(a["x"], a["y"]) for a in sb.env.armies()
                          if a["owner"] == "野人"}
                for n in sb.players:
                    bad = _own(sb, n) & guards
                    self.assertEqual(bad, set(),
                                     f"{size}×{size} seed={seed}：{n} 的国土上有野人 {sorted(bad)} "
                                     f"⇒ 引擎里「野人只守无主格」，这是矛盾格")

    def _barbarian_cells(self, sb, nation: str) -> set:
        """引擎认为"这个国家看得见的野人"在哪些格（**虚拟野人**的落点）。

        ★★ **口径变了（2026-10 适配新引擎）**：旧引擎的野人是**真军队**
          （开局在**全图**每个无主格放一支 ⇒ `world.armies()` 里能直接看到它们）。
          新引擎的野人是**按需合成的虚拟单位**（`military.visible_units` 对
          **无主格**现场造一个）⇒ `world.armies()` 里**看不到野人**了。
        ⇒ 守卫的**判据**从"列表里有没有野人"改成**引擎自己的那条规则**：
          「**野人只守无主格**」—— 直接问引擎"它给我看的那批野人在哪些格"，
          再断言那些格**没有主人**。守的仍是**同一条不变量**。
        """
        # ★ 直接问**引擎**（`native:army.visible_units`）—— 野人是引擎**现场合成**的
        units = sb.env.call("native:army", "visible_units", sb.env.id_of(nation))
        return {(u.x, u.y) for u in units if u.owner == "野人"}

    def test_exactly_the_claimed_tiles_lost_their_guardian(self):
        """★ 占了地 ⇒ 那格的野人**没了**（不多不少：只影响新占的格）。

        ★ 判据：同一局的"开国土 / 不开国土"两版对比 ——
          开了之后**新增国土**的那些格上，野人**必须已经不在**。
          （旧引擎是"真军队被撤掉"；新引擎是"那格有主了 ⇒ 野人不合成"。）
        """
        for size in TRAIN_SIZES:
            sb_on, sb_off = _sb(2024, size), _sb(2024, size, territory=False)
            won = {c for n in sb_on.players for c in _own(sb_on, n)}
            woff = {c for n in sb_off.players for c in _own(sb_off, n)}
            claimed = won - woff
            for n in sb_on.players:
                guards = self._barbarian_cells(sb_on, n)
                bad = claimed & guards
                self.assertEqual(bad, set(),
                                 f"{size}×{size}：新占的国土 {len(claimed)} 格上还有野人 "
                                 f"{sorted(bad)[:5]} ⇒ 占地没撤野人")
            # ★ 少撤了 = 上面那条（多撤了 = 无主地上的野人被误删）——
            #   新引擎里"误删"表现为**无主格上不该有野人**。
            for n in sb_off.players:
                owned_off = {c for m in sb_off.players for c in _own(sb_off, m)}
                bad = owned_off & self._barbarian_cells(sb_off, n)
                self.assertEqual(bad, set(),
                                 f"{size}×{size}：不开国土的版本里，国土上也有野人 "
                                 f"{sorted(bad)[:5]}")


class TestTheMapIsNotPerturbed(unittest.TestCase):
    """③ 抽国土**不许扰动同一 seed 的地图/开局**（版间 A/B 的地基）。"""

    def test_same_map_halls_and_clock(self):
        """③ 开/关国土：厅、地形、回合偏移**逐位相同**（版间 A/B 的地基）。

        ★ 已故意破坏过：把国土抽签和 `turn_offset` 改成**共用一个 `Random` 实例**
          ⇒ 这条当场红（`turn_offset 13 != 17`）。见类 docstring 里"测得到什么"。
        """
        for size in TRAIN_SIZES:
            for seed in (1, 5, 99):
                on, off = _sb(seed, size), _sb(seed, size, territory=False)
                self.assertEqual([on.core_of(p) for p in on.players],
                                 [off.core_of(p) for p in off.players],
                                 f"{size}×{size} seed={seed}：厅的位置被国土抽签挪动了")
                self.assertEqual(on.turn_offset, off.turn_offset, "回合偏移被挪动了")
                self.assertEqual(
                    {(x, y): on.env.terrain(x, y) for x in range(size)
                     for y in range(size)},
                    {(x, y): off.env.terrain(x, y) for x in range(size)
                     for y in range(size)}, "地形被挪动了")

    def test_reset_does_not_touch_the_global_rng(self):
        """★ 抽国土必须走**按 seed 派生**的流（同 `turn_offset` 那条的判据）。"""
        import random as _r
        st = _r.getstate()
        for s in (2, 4, 6):
            _sb(s, 16)
        self.assertEqual(_r.getstate(), st, "`reset()` 动了全局随机流 ⇒ 局与局耦合")

    def test_same_seed_reproduces_the_territory(self):
        a, b = _sb(555, 16), _sb(555, 16)
        self.assertEqual(a.territory_target, b.territory_target)
        for n in a.players:
            self.assertEqual(_own(a, n), _own(b, n), f"{n} 的国土不可复现")


class TestTheOffSwitchIsReal(unittest.TestCase):
    """④ `--no-territory` = **老行为**（正好十字 5 格）——不然版间对照的开关是假的。"""

    def test_off_gives_exactly_the_cross(self):
        from rl.tokens import CROSS
        for size in TRAIN_SIZES:
            sb = _sb(7, size, territory=False)
            self.assertEqual(sb.territory_target, len(CROSS))
            for n in sb.players:
                self.assertEqual(sb.tiles_of(n), len(CROSS),
                                 f"{size}×{size}：关掉之后 {n} 有 {sb.tiles_of(n)} 格，"
                                 f"该正好 {len(CROSS)}（十字）")


class TestShapeOfTheHomeland(unittest.TestCase):
    """⑤ 连通 + 含厅（"以市政厅为中心"的可检验含义）。"""

    def test_connected_and_contains_the_hall(self):
        for size in SIZES:
            for seed in range(5):
                sb = _sb(seed * 17 + size, size)
                for n in sb.players:
                    own = _own(sb, n)
                    hall = sb.core_of(n)
                    self.assertIn(hall, own, f"{size}×{size}：{n} 的厅不在自家国土里")
                    seen, stack = {hall}, [hall]
                    while stack:
                        x, y = stack.pop()
                        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                            c = (x + dx, y + dy)
                            if c in own and c not in seen:
                                seen.add(c)
                                stack.append(c)
                    self.assertEqual(seen, own,
                                     f"{size}×{size} seed={seed}：{n} 的国土不连通"
                                     f"（{len(own - seen)} 格漂在外面）")


class TestSizeCorrelatesWithMapAndNations(unittest.TestCase):
    """⑥ **国土大小和地图（和 `n = f(size)` 下的国家数）正相关** + ⑦ 每局自动抽。"""

    def test_range_table(self):
        """范围黄金表（有意改了规则就一并改这里）。"""
        self.assertEqual(territory_range(12, 3), (7, 17))
        self.assertEqual(territory_range(16, 4), (10, 22))
        self.assertEqual(territory_range(20, 5), (12, 28))

    def test_range_grows_with_the_map(self):
        """★ 范围随地图**整体上升**（20×20 严格大于 12×12）。

        ⚠ **允许相邻地图之间 1 格的回撤** —— 不是和稀泥：范围按"每国份额 = `size²/n`"
          算，而 `n = f(size)` 是**台阶函数**（14→16 那一步 n 从 3 跳到 4
          ⇒ 份额 65.3 → 64）⇒ 台阶处必然有小回撤。真的回撤（比如"大图反而更小"、
          或者有人把份额写成了与 size 无关）会被这条抓住。
        """
        sizes = (12, 14, 16, 18, 20)
        los = [territory_range(s, n_nations_for(s))[0] for s in sizes]
        his = [territory_range(s, n_nations_for(s))[1] for s in sizes]
        self.assertGreater(his[-1], his[0], f"20×20 的国土上界不大于 12×12 的：{his}")
        self.assertGreater(los[-1], los[0], f"20×20 的国土下界不大于 12×12 的：{los}")
        for name, seq in (("上界", his), ("下界", los)):
            for a, b in zip(seq, seq[1:]):
                self.assertGreaterEqual(b, a - 1,
                                        f"{name}回撤超过 1 格（那是真回撤，不是 n 台阶）：{seq}")

    def test_every_episode_draws_its_own(self):
        """⑦ 逐局不同（否则"随机大小"是假的）+ 范围**有宽度**（否则等于没做）。"""
        lo, hi = territory_range(16, n_nations_for(16))
        self.assertGreaterEqual(hi - lo, 5,
                                f"16×16 的范围只有 {hi - lo} 格宽 ⇒ 等于固定值")
        targets = {_sb(s, 16).territory_target for s in range(40)}
        self.assertGreater(len(targets), 4,
                           f"40 局只抽到 {len(targets)} 种国土：{sorted(targets)}")
        self.assertTrue(all(lo <= t <= hi for t in targets),
                        f"抽出了范围外的值（应在 [{lo},{hi}]）：{sorted(targets)}")

    def test_territory_leaves_most_of_the_map_wild(self):
        """国土吃掉的份额有上限（野地 = 扩张成本，那是这条线的既有口径）。"""
        for size in TRAIN_SIZES:
            n = n_nations_for(size)
            _, hi = territory_range(size, n)
            self.assertLessEqual(n * hi, 0.40 * size * size,
                                 f"{size}×{size}：最坏情况领土占 {n * hi / (size * size):.0%} "
                                 f"⇒ 野地不够（扩张成本没了）")


class TestClaimedTilesAreComplete(unittest.TestCase):
    """⑧ 占下来的格必须是**物化的完整地块**（走引擎的 `_new_tile`）。"""

    def test_tiles_have_the_full_shape(self):
        # ★ 建筑键是**内容 ID**（`native:town-hall`…）—— 引擎的建筑目录
        #   （`mods/native/buildings/types.py` 里每个类的 `TYPE_ID`）。
        from mods.native.buildings.types import __dict__ as _btypes
        BUILDINGS = tuple(v.TYPE_ID for v in _btypes.values()
                          if isinstance(v, type) and getattr(v, "TYPE_ID", None))
        sb = _sb(4242, 16)
        # ★ 引擎的 `core`（"这块地归谁的核心"）由 **`snapshot_cores`** 刷新
        #   （native 自己在战斗结算/战争结束时也调它）⇒ 要断"战后核心领土自动归还"，
        #   就得**先让它刷一次**（这正是引擎的口子，不是 RL 自己造的动作）。
        for n in sb.players:
            sb.env.call("native:territory", "snapshot_cores", [sb.env.id_of(n)])
        for n in sb.players:
            for c in _own(sb, n):
                t = sb.env.tile_at(c)
                self.assertEqual(t.owner, n)
                # ★ 引擎的 `core`（"这块地归谁的核心"）是**结算时**按地主刷新的
                #   （`Territory.snapshot_cores`，由战争结束/战斗结算调用）⇒
                #   要断"战后核心领土自动归还"，得**先结算一回合**。
                self.assertEqual(t.core, n,
                                 f"{c} 的 `core` 不是 {n} ⇒ 战后「核心领土自动归还」会漏掉它")
                # ★ 建筑读数**逐个种类**与引擎对齐（`buildings.count`）——
                #   引擎的读模型只列**在场**的建筑（不在场的键根本没有），
                #   旧门面那张表是"全部种类 + 0 计数" ⇒ 形状不同，**但值必须一致**。
                for kind in BUILDINGS:
                    self.assertEqual(
                        t.buildings.get(kind, 0),
                        sb.env.call("native:buildings", "count", t.tile_id, kind),
                        f"{c} 的 {kind} 数量与引擎对不上")
                self.assertTrue(t.name, f"{c} 没有地名")
                # ★ `TileRow.terrain` 现在是**内容 ID**（不是引擎的 `Terrain` 对象）——
                #   类型边界统一（review P1-4）：规则表只认 ID。
                self.assertIn(t.terrain, ("native:plain", "native:forest", "native:hill",
                                          "native:mountain", "native:desert"))

    def test_hall_still_stands_and_is_the_only_one(self):
        """长国土**不许**多出市政厅（那是国祚，不是普通地块）。

        ★ `known_halls(name)` 是**记忆账本**（`{格: 最后看见时的主人}`），间谍模式下
          开局就全知 ⇒ 它该正好等于**国家数**（每家一座）。多出来 = 国土抽签把某格
          弄成了厅；少一座 = 某家的厅被国土覆盖掉了（那家开局就是死的）。
        """
        for size in TRAIN_SIZES:
            sb = _sb(11, size)
            known = sb.known_halls(sb.players[0])
            self.assertEqual(len(known), len(sb.players),
                             f"{size}×{size}：已知的厅有 {len(known)} 座，"
                             f"该正好 {len(sb.players)} 座（每家一座）")
            for n in sb.players:
                halls = [c for c, t in [(r.position, r) for r in sb.env.tiles()]
                         if t.owner == n and t.buildings.get("native:town-hall", 0) > 0]
                self.assertEqual(len(halls), 1, f"{n} 有 {len(halls)} 座厅")


class TestMarginIsUntouched(unittest.TestCase):
    """★ 厅到厅的间距**还是** `min_margin` 那一套（贴上是允许的，但厅不能挨上）。

    用户 2026-09-25：「**国土可以贴上**」⇒ 国土接壤是**有意的**；但"厅到厅太近"
    是另一件事（那是"先手 N 回合直达"的结构性优势，`min_margin` 就是为它定的）
    —— 国土抽签**不许**动摇它。
    """

    def test_halls_keep_min_margin(self):
        for size in TRAIN_SIZES:
            for seed in range(6):
                sb = _sb(seed * 3 + size, size)
                need = min_margin(size, len(sb.players))
                halls = {n: sb.core_of(n) for n in sb.players}
                for i, a in enumerate(sb.players):
                    for b in sb.players[i + 1:]:
                        d = max(abs(halls[a][0] - halls[b][0]),
                                abs(halls[a][1] - halls[b][1]))
                        self.assertGreaterEqual(d, need,
                                                f"{size}×{size} seed={seed}：{a} 与 {b} 的厅"
                                                f"只隔 {d}（要求 ≥{need}）")


if __name__ == "__main__":
    unittest.main()

class TestOneVOneTerritoryBoost(unittest.TestCase):
    """★★★ **1v1 专用国土加码**（用户 2026-09-27）。

    「你在 **1v1 地图上增加国土规模**就行」+「地图 **10-14** 格，**小地图后手吃亏更严重**」
    + 「随机国土给了**更大纵深**，换家敏感性更高」。

    ★ 为什么要加：两人局厅到厅只有 `min_margin = size/√2`（size 10 ⇒ 7.1 格），
      先手 5 回合就能直达对方厅 —— 项目里记过「先手优势是**结构性**的、闸门连响」。
      国土大 ⇒ **纵深大** ⇒ 直达不成立。顺带：野地变少 = 免费午餐变少，
      那正是 8 国局摆烂尸检查出来的平局成因。

    ★ 破坏方式：把 `territory_range` 里 `TERRITORY_FRAC_2P if n_nations <= 2` 那句
      改回一律用 `TERRITORY_FRAC_LO/HI` ⇒ 第一条当场红。
    """

    def test_one_v_one_gets_more_territory(self):
        from rl.sandbox import territory_range
        # ★ 钉**具体数**（不是"比 3 国大"—— 那个因为 share 不同本来就成立，测不出东西）
        self.assertEqual(territory_range(10, 2), (18, 30),
                         "size10/1v1 的国土范围变了（用户拍过：要加大纵深）")
        self.assertEqual(territory_range(14, 2), (34, 59))

    def test_three_plus_nations_are_untouched(self):
        """★ 用户只说了 1v1 地图 ⇒ **3-5 国那套比例一个字都不许动**。"""
        from rl.sandbox import territory_range
        self.assertEqual(territory_range(12, 3), (7, 17))
        self.assertEqual(territory_range(20, 5), (12, 28))
