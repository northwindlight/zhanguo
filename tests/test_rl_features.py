# -*- coding: utf-8 -*-
"""`rl/features.py` 守卫：规则表的内容向量必须**跟着引擎表动**。

这是这一层存在的**唯一理由** —— 旧线实锤「石油厂造价 240→180，模型动作一字不变」，
根因就是数值没进输入。所以最要紧的一条不是"形状对"，而是：

    ★ **就地改引擎表 ⇒ 向量必须立刻变。**

如果哪天有人给它加了模块级缓存（"省点 CPU"），那条测试会红 —— 而那种错
**在训练里不报错**，只会让"引擎改了数值模型却不改决策"这个病悄悄复发。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                          # noqa: E402

import balance                              # noqa: E402
import game                                 # noqa: E402
from rl import features as F                # noqa: E402
from rl import vocab as V                   # noqa: E402


class TestShapes(unittest.TestCase):
    def test_widths(self):
        self.assertEqual(F.F_U, 4, "兵种数值 4 列（hp/atk/speed/supply）")
        self.assertEqual(F.F_T, 4, "地形数值 4 列（defense + 三兵种移动代价）")
        self.assertEqual(F.F_CAND, 8)
        self.assertEqual(F.F_GLOB, 11, "骰 6 + 撤退 2 + 续战 3")

    def test_tables_use_vocab_order(self):
        """批量表的行序必须是 `vocab` 的**冻结顺序**（下标即身份），不是 dict 顺序。"""
        ut, tt = F.unit_table(), F.terrain_table()
        self.assertEqual(ut.shape, (len(V.UNIT), F.F_U))
        self.assertEqual(tt.shape, (len(V.TERRAIN), F.F_T))
        for i, k in enumerate(V.UNIT):
            self.assertTrue((ut[i] == F.unit_vector(k)).all(), f"UNIT 第 {i} 行不是 {k}")
        for i, t in enumerate(V.TERRAIN):
            self.assertTrue((tt[i] == F.terrain_vector(t)).all(),
                            f"TERRAIN 第 {i} 行不是 {t}")


class TestValuesMatchEngine(unittest.TestCase):
    """数值必须是**引擎的数字**，不是长得像的数字。"""

    def test_unit(self):
        # 用 `allclose` 不用 `==`：向量是 **float32**，0.8/0.2 在 float32 里不是精确值
        # （`np.float32(0.8) == 0.8` 是 **False**）—— 别拿这个当"数值不对"。
        np.testing.assert_allclose(F.unit_vector("步"), [1.0, 0.5, 0.5, 0.5])
        np.testing.assert_allclose(F.unit_vector("骑"), [1.0, 0.5, 1.0, 1.0])
        np.testing.assert_allclose(F.unit_vector("民"), [0.8, 0.2, 0.5, 0.5])
        # 表里没有的 kind（野人）按步兵兜底，不是崩、也不是全 0
        self.assertTrue((F.unit_vector("野人") == F.unit_vector("步")).all())

    def test_terrain(self):
        # 丘陵 defense 25 ⇒ 0.25；移动代价 步1/骑1/民1 ⇒ 0.5
        np.testing.assert_allclose(F.terrain_vector("丘陵"), [0.25, 0.5, 0.5, 0.5])
        # 沙漠 defense −10 ⇒ **负值要如实传**（"无险可守"是信息，别夹到 0）
        self.assertAlmostEqual(float(F.terrain_vector("沙漠")[0]), -0.10, places=6)
        # 森林/山地：骑兵移动代价 2 ⇒ 1.0（吃满）—— 这是"骑兵怕崎岖"的唯一来源
        self.assertEqual(F.terrain_vector("森林")[2], 1.0)
        self.assertEqual(F.terrain_vector("山地")[2], 1.0)
        self.assertEqual(F.terrain_vector("平原")[2], 0.5)

    def test_die_and_retreat(self):
        dv = F.die_vector()
        self.assertEqual(list(dv), [-1.0, -0.6, -0.2, 0.2, 0.6, 1.0])   # 骰面 1..6
        rv = F.retreat_vector()
        self.assertEqual(list(rv), [0.8, 0.5])                          # 惩罚80% / 减伤50%

    def test_cand_and_glob_compose(self):
        c = F.cand_content("民", "山地")
        self.assertEqual(c.shape, (F.F_CAND,))
        self.assertTrue((c[:F.F_U] == F.unit_vector("民")).all())
        self.assertTrue((c[F.F_U:] == F.terrain_vector("山地")).all())
        self.assertEqual(F.glob_rule_vector().shape, (F.F_GLOB,))


class TestLiveNotCached(unittest.TestCase):
    """★★ 本模块存在理由的那条测试：**表一变，向量必须立刻变**。"""

    def test_unit_change_is_visible(self):
        before = F.unit_vector("民").copy()
        try:
            game.UNIT_TYPES["民"]["atk"] = 30          # 就地改（与域随机化同一手法）
            after = F.unit_vector("民")
        finally:
            game.UNIT_TYPES["民"]["atk"] = 20
        self.assertNotEqual(after[1], before[1],
                            "★ 改了 `UNIT_TYPES['民']['atk']` 向量却没变 ⇒ 有缓存烤进去了")
        self.assertAlmostEqual(after[1], 0.30, places=6)
        self.assertTrue((F.unit_vector("民") == before).all(), "改回去之后没复原")

    def test_terrain_change_is_visible(self):
        before = F.terrain_vector("丘陵").copy()
        try:
            balance.TERRAIN_STATS["丘陵"]["defense"] = 40
            after = F.terrain_vector("丘陵")
        finally:
            balance.TERRAIN_STATS["丘陵"]["defense"] = 25
        self.assertAlmostEqual(after[0], 0.40, places=6)
        self.assertNotEqual(after[0], before[0])

    def test_move_cost_change_is_visible(self):
        """★ "让民兵也怕森林" 这类改动必须看得见（现在 步/民 同值 ⇒ 两列冗余是**故意**的）。"""
        try:
            balance.MOVE_COST["民"]["森林"] = 2
            v = F.terrain_vector("森林")
        finally:
            balance.MOVE_COST["民"]["森林"] = 1
        self.assertEqual(v[3], 1.0, "民 的移动代价改了却没进向量第 4 列")

    def test_die_change_is_visible(self):
        before = F.die_vector().copy()
        try:
            balance.COMBAT_DIE_MOD[6] = 50
            after = F.die_vector()
        finally:
            balance.COMBAT_DIE_MOD[6] = 25
        self.assertAlmostEqual(after[5], 2.0, places=6)
        self.assertNotEqual(after[5], before[5])

    def test_batch_tables_are_live_too(self):
        """批量表也得现算 —— 只让单条现算、批量走缓存，是最容易漏的那种半吊子。"""
        before = F.unit_table()[V.UNIT.index("骑")].copy()
        try:
            game.UNIT_TYPES["骑"]["atk"] = 60
            after = F.unit_table()[V.UNIT.index("骑")]
        finally:
            game.UNIT_TYPES["骑"]["atk"] = 50
        self.assertNotEqual(after[1], before[1], "★ 批量表被缓存了")


if __name__ == "__main__":
    unittest.main()