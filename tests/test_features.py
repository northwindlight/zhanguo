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
        """宽度是模型输入契约（§10.6）：F_B 45→62（+kind 留位 3 + 城堡逐级 5 + 效果 9）、
        F_T 4（新）、F_U 11、F_G 2。改这里 = 改模型输入 = 旧 ckpt 全废。"""
        self.assertEqual(F.F_B, 62)
        self.assertEqual(F.F_U, 11)
        self.assertEqual(F.F_G, 2)
        self.assertEqual(F.F_T, 4)
        self.assertEqual(len(F.EFFECT_KEYS), 9)
        self.assertEqual(len(F.REAL_KINDS), 11)
        self.assertEqual(len(F.BUILD_KINDS), 14, "kind one-hot 含 3 个留位槽")

    def test_table_shapes(self):
        self.assertEqual(F.building_table().shape, (len(V.OBS_BUILDING), F.F_B))
        self.assertEqual(F.unit_table().shape, (len(V.UNIT), F.F_U))
        self.assertEqual(F.good_table().shape, (len(V.TRADEABLE), F.F_G))
        self.assertEqual(F.terrain_table().shape, (len(V.TERRAIN), F.F_T))

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
    """尺度写死在 `features.py` 的常量里；这里锁住**有代表性的实测值**。

    数值改了（平衡调整）这些用例会红 —— 那是**故意**的：要么同步改用例，
    要么承认自己动了模型输入。随机化不该让它们红（随机化只改 game 的表，
    用例里读的是同一次现算的结果，两边一起变）。

    ★除数一律**引用常量**（`F.RES_DIV` 等），不写 `1000`/`2000` 这种字面量 ——
    2026-09-12 晚就是靠这条纪律发现"候选侧 /2000、窗口侧 /1000"那次不一致的。
    """

    def test_building_fields(self):
        v = F.building_vector("农场")
        self.assertEqual(v[F.BUILD_KINDS.index("extract")], 1.0)
        cost_i = len(F.BUILD_KINDS)
        self.assertAlmostEqual(float(v[cost_i]), 50 / F.RES_DIV, places=6)     # 造价
        self.assertAlmostEqual(float(v[cost_i + 1]), 5 / F.RES_DIV, places=6)  # 木耗
        self.assertEqual(float(v[cost_i + 2 + F.CAP_RES_SLOTS.index("耕地")]), 1.0)  # 要耕地
        # outputs 段里「粮食」那一维 = 1/4
        out_start = cost_i + 2 + len(F.CAP_RES_SLOTS)
        self.assertAlmostEqual(float(v[out_start + V.STOCK.index("粮食")]),
                             1 / F.RES_DIV, places=6)

    def test_castle_uses_level1_cost(self):
        """城堡的 cost 是逐级表，取 L1（与 `legal_actions` 的取向一致，不越界）。"""
        v = F.building_vector("城堡")
        self.assertAlmostEqual(float(v[len(F.BUILD_KINDS)]), 100 / F.RES_DIV, places=6)

    def test_unit_fields(self):
        v = F.unit_vector("步")
        self.assertAlmostEqual(float(v[0]), 100 / 200, places=6)   # hp
        self.assertAlmostEqual(float(v[1]), 1 / 2, places=6)       # speed
        self.assertAlmostEqual(float(v[3]), 50 / 100, places=6)    # atk
        # 征兵成本是**资源量** ⇒ 与窗口侧资源同尺度（同 RES_DIV）
        self.assertAlmostEqual(float(v[4 + V.STOCK.index("粮食")]), 10 / F.RES_DIV, places=6)
        self.assertAlmostEqual(float(v[4 + V.STOCK.index("装备")]), 5 / F.RES_DIV, places=6)

    def test_reserved_kinds_stay_zero(self):
        """kind 留位槽恒零 —— 引擎里没有任何建筑会是那几种 kind；新 kind 填进来即生效。"""
        import game as _g
        k0 = len(F.REAL_KINDS)
        for b in _g.BUILDINGS:
            self.assertFalse(F.building_vector(b)[k0:k0 + 3].any(), f"{b} 碰了留位 kind 槽")
        for k in F.BUILD_KINDS[k0:]:
            self.assertNotIn(k, {v.get("kind") for v in _g.BUILDINGS.values()})

    def test_effect_fields(self):
        """★效果是**数据**（`BUILDINGS[*].effects`），所以这里读的就是引擎那份。"""
        v = F.building_vector("工程院")
        e0 = F.F_B - len(F.EFFECT_KEYS)
        self.assertAlmostEqual(float(v[e0 + F.EFFECT_KEYS.index("build_discount")]),
                               25 / 50, places=6)
        v = F.building_vector("瞭望塔")
        self.assertAlmostEqual(float(v[e0 + F.EFFECT_KEYS.index("vision_radius")]),
                               4 / 8, places=6)
        # 没有效果的建筑：整块为 0
        self.assertFalse(F.building_vector("农场")[e0:].any())

    def test_castle_level_costs(self):
        """城堡的逐级造价（5 级）要表达出来 —— 它 5 个等级造价差了 16 倍。"""
        v = F.building_vector("城堡")
        c0 = F.F_B - len(F.EFFECT_KEYS) - F.MAX_LEVELS
        for j, c in enumerate([100, 200, 400, 800, 1600]):
            self.assertAlmostEqual(float(v[c0 + j]), c / F.RES_DIV, places=6)
        # 非逐级建筑：那 5 维为 0
        self.assertFalse(F.building_vector("农场")[c0:c0 + F.MAX_LEVELS].any())

    def test_terrain_fields(self):
        v = F.terrain_vector("山地")
        self.assertAlmostEqual(float(v[0]), 50 / 100, places=6)   # 减伤
        self.assertAlmostEqual(float(v[1]), 50 / 100, places=6)   # 造价惩罚
        self.assertAlmostEqual(float(F.terrain_vector("沙漠")[0]), -10 / 100, places=6)
        self.assertFalse(F.terrain_vector("_reserved_g1").any())

    def test_build_cost_factor_matches_engine_formula(self):
        """建造成本倍率 = 地形惩罚 × 工程院减免（与 mp.py 的 build() 同式）。"""
        self.assertAlmostEqual(F.build_cost_factor("平原", False), 1.0, places=6)
        self.assertAlmostEqual(F.build_cost_factor("山地", False), 1.5, places=6)
        self.assertAlmostEqual(F.build_cost_factor("山地", True), 1.125, places=6)

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
            self.assertAlmostEqual(float(after[cost_i]), 180 / F.RES_DIV, places=6)
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


class TestScaleConsistency(unittest.TestCase):
    """★**同一个物理量在窗口侧与候选侧必须同一尺度**（2026-09-12 晚立的规矩）。

    为什么值得一条测试盯着（实测教训）：老师整局 95% 的回合黄金 < 350（中位 54），
    "这笔付得起吗"是每一步唯一承重的比较。而原先两侧是两套除数 ——
    窗口 `黄金/1000` vs 候选 `cost/2000`、窗口 `木头/1000` vs 候选 `wood/100` ——
    模型于是不能靠一次差/阈值判断，得先为每个特征学出缩放系数。
    代价实测：在**老师自己的状态上**也有 24% 的选择会被引擎拒（86% 是"卖我没有的货"）。

    这条测试**不看具体数值**，只看"两侧是不是同一个常量" —— 那样以后调尺度
    只改一个地方，不会再分家。
    """

    def test_window_and_candidate_share_resource_divisor(self):
        from rl.env import RES_KEYS, ZhanguoEnv
        env = ZhanguoEnv(map_size=16, max_turns=5)
        env.reset(1234)
        me = env.agent
        w = env.world
        # 把黄金设成一个好认的值，读窗口里那一维，反推它用的除数
        w.nations[me].res["黄金"] = 500
        env._obs()
        g = env._obs().glob
        i = env.glob_channels().index("res:黄金")
        self.assertAlmostEqual(float(g[i]), 500 / F.RES_DIV, places=6,
                               msg="窗口的资源除数变了 —— 候选侧（RES_DIV）必须跟着改")
        self.assertIn("黄金", RES_KEYS)
        # 候选侧：建筑造价用的必须**是同一个常量**
        v = F.building_vector("农场")
        cost_i = len(F.BUILD_KINDS)
        import game as _g
        self.assertAlmostEqual(float(v[cost_i]), _g.BUILDINGS["农场"]["cost"] / F.RES_DIV,
                               places=6)
        self.assertEqual(F._COST_DIV, F.RES_DIV)
        self.assertEqual(F._WOOD_DIV, F.RES_DIV)
        self.assertEqual(F._GOODS_DIV, F.RES_DIV)
        self.assertEqual(F._RECRUIT_DIV, F.RES_DIV)

    def test_window_and_candidate_share_energy_divisor(self):
        from rl.env import ZhanguoEnv
        self.assertEqual(F._ENERGY_DIV, F.ENERGY_DIV)
        self.assertEqual(F._ENERGY_OUT_DIV, F.ENERGY_DIV)
        env = ZhanguoEnv(map_size=16, max_turns=5)
        env.reset(1234)
        env.world.energy_report[env.agent] = (7, 9, False)
        g = env._obs().glob
        ch = env.glob_channels()
        self.assertAlmostEqual(float(g[ch.index("energy_have")]), 7 / F.ENERGY_DIV, places=6)

    def test_token_and_window_share_resource_divisor(self):
        """C 组的库存那一维也必须用同一个除数（否则又是两套尺度）。"""
        from rl.env import ZhanguoEnv
        from rl.tokenize import tokenize
        import rl.vocab as V
        env = ZhanguoEnv(map_size=16, max_turns=5)
        env.reset(1234)
        env.world.nations[env.agent].res["矿石"] = 800
        win = tokenize(env, env._obs())
        i = V.SUB_TABLE_OF["buy"].index("矿石")
        self.assertAlmostEqual(float(win.feats["c"][i][0]), 800 / F.RES_DIV, places=6)
