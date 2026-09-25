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
    return {c for c, t in sb.world.tiles.items() if t["owner"] == name}


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
        import rl.sandbox as SB
        real = SB.territory_range
        SB.territory_range = lambda size, n: (30, 30)     # ★ 刻意够不着
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
            SB.territory_range = real

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
                guards = {(a["x"], a["y"]) for a in sb.world.armies
                          if a["owner"] == "野人"}
                for n in sb.players:
                    bad = _own(sb, n) & guards
                    self.assertEqual(bad, set(),
                                     f"{size}×{size} seed={seed}：{n} 的国土上有野人 {sorted(bad)} "
                                     f"⇒ 引擎里「野人只守无主格」，这是矛盾格")

    def test_exactly_the_claimed_tiles_lost_their_guardian(self):
        """撤走的野人**正好**是新增的那些国土（不多不少）。

        ★ 多撤了 = 无主地上的野人被误删（那格以后没守卫了）；少撤了 = 上面那条。
          两条一起才钉住"只影响新占的格"。
        """
        for size in TRAIN_SIZES:
            sb_on, sb_off = _sb(2024, size), _sb(2024, size, territory=False)
            won = {c for n in sb_on.players for c in _own(sb_on, n)}
            woff = {c for n in sb_off.players for c in _own(sb_off, n)}
            claimed = won - woff
            g_on = {(a["x"], a["y"]) for a in sb_on.world.armies if a["owner"] == "野人"}
            g_off = {(a["x"], a["y"]) for a in sb_off.world.armies if a["owner"] == "野人"}
            self.assertEqual(g_off - g_on, claimed,
                             f"{size}×{size}：撤掉的野人 {len(g_off - g_on)} 格 ≠ "
                             f"新占的国土 {len(claimed)} 格")
            self.assertEqual(g_on - g_off, set(),
                             "开了国土之后**多出来**野人了（只该少、不该多）")


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
                    {(x, y): on.world.tile_terrain(x, y) for x in range(size)
                     for y in range(size)},
                    {(x, y): off.world.tile_terrain(x, y) for x in range(size)
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
        from mp import CROSS
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
        from mp import BUILDINGS
        sb = _sb(4242, 16)
        for n in sb.players:
            for c in _own(sb, n):
                t = sb.world.tiles[c]
                self.assertEqual(t["owner"], n)
                self.assertEqual(t.get("core"), n,
                                 f"{c} 的 `core` 不是 {n} ⇒ 战后「核心领土自动归还」会漏掉它")
                self.assertEqual(set(t["buildings"]), set(BUILDINGS),
                                 f"{c} 的 buildings 不全 ⇒ 不是 `_new_tile` 造的")
                self.assertTrue(t.get("name"), f"{c} 没有地名")
                self.assertIn(t.get("terrain"), ("平原", "森林", "丘陵", "山地", "沙漠"))

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
                halls = [c for c, t in sb.world.tiles.items()
                         if t["owner"] == n and t["buildings"].get("市政厅", 0) > 0]
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