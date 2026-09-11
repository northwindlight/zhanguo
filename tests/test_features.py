# -*- coding: utf-8 -*-
"""`rl/features.py`：规则表 → 内容向量。

这一层最贵的错误是**静默**的：数值错一位、尺度差一个量级，训练照跑、loss 照降，
只是模型学到的是另一套数。所以这里守四条：

1. **同源** —— 向量必须**每次现算**读 `game.*` 的活表。域随机化靠就地改表实现，
   一旦有人加了模块级缓存，随机化就会"改了表但模型看不见"，而且不报错。
2. **留位槽全零** —— 留位项在引擎里不存在，向量必须是零（`mask=0` 的前提）。
3. **宽度冻结** —— `F_B/F_U/F_G` 与整表形状；改了就是改模型输入（§10.6）。
4. **与观测子表对齐** —— 内容表的行数 == `env.sub_tables[kind]` 的长度
   （`sub_idx` 能索引到内容，靠的就是这条）。
"""
from __future__ import annotations

import unittest

import numpy as np

import game
from rl import features as F
from rl import vocab as V
from rl.env import ZhanguoEnv


class TestWidths(unittest.TestCase):
    def test_width_frozen(self):
        self.assertEqual(F.F_B, 45)
        self.assertEqual(F.F_U, 11)
        self.assertEqual(F.F_G, 2)

    def test_table_shapes(self):
        self.assertEqual(F.building_table().shape, (len(V.OBS_BUILDING), F.F_B))
        self.assertEqual(F.unit_table().shape, (len(V.UNIT), F.F_U))
        self.assertEqual(F.good_table().shape, (len(V.TRADEABLE), F.F_G))

    def test_content_dim_of_kind(self):
        self.assertEqual(F.CONTENT_DIM_OF_KIND["build"], F.F_B)
        self.assertEqual(F.CONTENT_DIM_OF_KIND["recruit"], F.F_U)
        self.assertEqual(F.CONTENT_DIM_OF_KIND["buy"], F.F_G)
        self.assertEqual(F.CONTENT_DIM_OF_KIND["move"], 0)


class TestReservedSlots(unittest.TestCase):
    """留位项在引擎里不存在 → 向量全零（它们靠 `mask=0` 不参加注意力）。"""

    def test_reserved_buildings_are_zero(self):
        for b in V.RESERVED_BUILDING:
            self.assertFalse(F.building_vector(b).any(), f"{b} 应当全零")

    def test_reserved_units_and_goods_are_zero(self):
        for u in V.RESERVED_UNIT:
            self.assertFalse(F.unit_vector(u).any(), f"{u} 应当全零")
        for g in V.RESERVED_TRADEABLE:
            self.assertFalse(F.good_vector(g).any(), f"{g} 应当全零")

    def test_main_only_building_still_has_content(self):
        """外交中心是 MAIN_ONLY（不进观测），但引擎里**有**它 —— 向量不该是零。"""
        self.assertTrue(F.building_vector("外交中心").any())


class TestScales(unittest.TestCase):
    """尺度写死在 features.py；这里按**几个有代表性的实测值**锁住它。

    数值改了（平衡调整）这些用例会红 —— 那是**故意**的：要么同步改用例，
    要么承认自己动了模型输入。随机化不该让它们红（随机化只改 game 的表，
    用例里读的是同一次现算的结果，两边一起变）。
    """

    def test_building_fields(self):
        v = F.building_vector("农场")
        self.assertEqual(v[F.BUILD_KINDS.index("extract")], 1.0)
        cost_i = len(F.BUILD_KINDS)
        self.assertAlmostEqual(float(v[cost_i]), 50 / 2000, places=6)          # 造价
        self.assertAlmostEqual(float(v[cost_i + 1]), 5 / 100, places=6)        # 木耗
        self.assertEqual(float(v[cost_i + 2 + F.CAP_RES_SLOTS.index("耕地")]), 1.0)  # 要耕地
        # outputs 段里「粮食」那一维 = 1/4
        out_start = cost_i + 2 + len(F.CAP_RES_SLOTS)
        self.assertAlmostEqual(float(v[out_start + V.STOCK.index("粮食")]), 0.25, places=6)

    def test_castle_uses_level1_cost(self):
        """城堡的 cost 是逐级表，取 L1（与 `legal_actions` 的取向一致，不越界）。"""
        v = F.building_vector("城堡")
        self.assertAlmostEqual(float(v[len(F.BUILD_KINDS)]), 100 / 2000, places=6)

    def test_unit_fields(self):
        v = F.unit_vector("步")
        self.assertAlmostEqual(float(v[0]), 100 / 200, places=6)   # hp
        self.assertAlmostEqual(float(v[1]), 1 / 2, places=6)       # speed
        self.assertAlmostEqual(float(v[3]), 50 / 100, places=6)    # atk
        self.assertAlmostEqual(float(v[4 + V.STOCK.index("粮食")]), 1.0, places=6)
        self.assertAlmostEqual(float(v[4 + V.STOCK.index("装备")]), 0.5, places=6)

    def test_good_fields(self):
        v = F.good_vector("粮食")
        self.assertAlmostEqual(float(v[0]), 2 / 10, places=6)
        self.assertAlmostEqual(float(v[1]), 24 / 24, places=6)


class TestLiveRead(unittest.TestCase):
    """★同源：向量必须跟着**当下的**引擎表走（域随机化的前提）。

    做法：就地改 `game.BUILDINGS` 的造价 → 向量立刻跟着变 → 再改回来。
    （这正是 §10.4 域随机化要做的事；这条测试是它的地基。）
    """

    def test_reflects_inplace_table_change(self):
        name = "石油能源厂"
        before = F.building_vector(name).copy()
        cost_i = len(F.BUILD_KINDS)
        orig = game.BUILDINGS[name]["cost"]
        try:
            game.BUILDINGS[name]["cost"] = 180          # 用户举的那个例子
            after = F.building_vector(name)
            self.assertNotAlmostEqual(float(before[cost_i]), float(after[cost_i]))
            self.assertAlmostEqual(float(after[cost_i]), 180 / 2000, places=6)
        finally:
            game.BUILDINGS[name]["cost"] = orig
        self.assertTrue(np.array_equal(F.building_vector(name), before),
                        "改回真值后向量必须复原（否则有人在某处缓存了）")

    def test_no_module_level_cache(self):
        """同理守一遍兵种与物资，防的是"只给建筑做了现算"这种半吊子。"""
        orig_hp = game.UNIT_TYPES["步"]["hp"]
        orig_price = game.MARKET["粮食"]
        try:
            game.UNIT_TYPES["步"]["hp"] = 999
            game.MARKET["粮食"] = 7
            self.assertNotAlmostEqual(float(F.unit_vector("步")[0]), 100 / 200)
            self.assertNotAlmostEqual(float(F.good_vector("粮食")[0]), 2 / 10)
        finally:
            game.UNIT_TYPES["步"]["hp"] = orig_hp
            game.MARKET["粮食"] = orig_price


class TestAlignsWithEnvSubTables(unittest.TestCase):
    """内容表的行数必须等于 `env.sub_tables[kind]` 的长度 —— `sub_idx` 索引内容靠这条。"""

    def test_rows_match_sub_tables(self):
        env = ZhanguoEnv(map_size=12, max_turns=6)
        for kind in ("build", "recruit", "buy", "sell"):
            tbl = F.content_table_for(kind)
            self.assertEqual(tbl.shape[0], len(env.sub_tables[kind]),
                             f"{kind}: 内容表 {tbl.shape[0]} 行 vs sub_tables "
                             f"{len(env.sub_tables[kind])} 项 —— sub_idx 会错位")
            self.assertEqual(tbl.shape[1], F.CONTENT_DIM_OF_KIND[kind])


if __name__ == "__main__":
    unittest.main()
