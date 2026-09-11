# -*- coding: utf-8 -*-
"""`rl/jitter.py`：训练期域随机化（§10.4）。

这一层最贵的错误也是**静默**的：

1. **不可复现** —— 同 seed 两套表，回放/对拍全废，而且没人会发现（loss 照降）。
2. **基线漂移** —— 若在"上一次抖过的表"上再抖，连跑几十局就是随机游走，
   分布漂到没人认得的地方。所以每次必须从**真值**出发。
3. **渗进评估** —— 评估期抖的话，所有"这一版比上一版好"的结论都掺了噪声。
4. **改错了字段** —— `cap_resource`/`limit` 这类是**可行性拓扑**（决定哪儿能建），
   抖了就不是"换一族数值"，而是换了个游戏。

★ 每条测试都自己善后（`jitter.restore()`）：随机化改的是**进程级**的 `game.*`，
漏一次就会污染后面所有用例 —— 那种失败极难定位。
"""
from __future__ import annotations

import unittest

import game
from rl import features as F
from rl import jitter
from rl.env import ZhanguoEnv


class JitterCase(unittest.TestCase):
    def setUp(self):
        jitter.restore()          # 保证从真值出发
        self._true_cost = game.BUILDINGS["石油能源厂"]["cost"]
        self._true_price = game.MARKET["粮食"]
        self._true_hp = game.UNIT_TYPES["步"]["hp"]
        self._true_cap = game.BUILDINGS["农场"]["cap_resource"]

    def tearDown(self):
        jitter.restore()


class TestDeterminism(JitterCase):
    def test_same_seed_same_tables(self):
        a = jitter.apply(1234, 0.2)
        b = jitter.apply(1234, 0.2)
        self.assertEqual(a, b, "同 seed 必须得到同一套表（回放/对拍的前提）")

    def test_different_seed_different_tables(self):
        a = jitter.apply(1, 0.2)
        b = jitter.apply(2, 0.2)
        self.assertNotEqual(a["buildings"], b["buildings"],
                           "不同 seed 不该得到同一套表（否则等于没随机化）")

    def test_always_drawn_from_the_true_table(self):
        """★不许在抖动结果上再抖：连抖很多次，分布不许漂走（否则就是随机游走）。"""
        jitter.apply(999, 0.2)
        drifting = game.BUILDINGS["石油能源厂"]["cost"]
        for s in range(30):
            jitter.apply(s, 0.2)
        # 每套表都从真值派生 → 任何一套都该落在 ±20% 内（不是累计漂移）
        lo, hi = self._true_cost * 0.75, self._true_cost * 1.25
        for s in range(30):
            jitter.apply(s, 0.2)
            c = game.BUILDINGS["石油能源厂"]["cost"]
            self.assertTrue(lo <= c <= hi,
                            f"seed={s} 的造价 {c} 跑出 ±20% 之外（基线漂了？）")
        self.assertNotEqual(drifting, None)


class TestScope(JitterCase):
    def test_zero_means_off_and_restores(self):
        jitter.apply(7, 0.2)
        self.assertNotEqual(game.BUILDINGS["石油能源厂"]["cost"], self._true_cost)
        rec = jitter.apply(7, 0.0)
        self.assertEqual(rec, {})
        self.assertFalse(jitter.is_on())
        self.assertEqual(game.BUILDINGS["石油能源厂"]["cost"], self._true_cost)
        self.assertEqual(game.MARKET["粮食"], self._true_price)

    def test_restore_is_lossless(self):
        """还原必须逐字段等于真值 —— 包括**没抖过的**那些（别把 dict 清空了）。"""
        before = {k: dict(v) for k, v in game.BUILDINGS.items()}
        jitter.apply(11, 0.2)
        jitter.restore()
        self.assertEqual({k: dict(v) for k, v in game.BUILDINGS.items()}, before)

    def test_topology_fields_untouched(self):
        """可行性拓扑类字段**不许**抖：它们决定"哪儿能建"，一抖就换了游戏。"""
        jitter.apply(5, 0.2)
        for name, b in game.BUILDINGS.items():
            for f in ("cap_resource", "min_slots", "limit", "max_level", "kind"):
                if f in b:
                    self.assertEqual(b[f], jitter._TRUE["buildings"][name][f],
                                     f"{name}.{f} 不该被随机化")

    def test_unit_supply_untouched(self):
        """`supply` 不抖：`spend_rules.SUPPLY_PER_ARMY` 有一份硬编码副本（老师走它），
        抖了会让「引擎结算」与「老师的账」对不上。"""
        for _ in range(5):
            jitter.apply(3, 0.2)
            for name, u in game.UNIT_TYPES.items():
                self.assertEqual(u["supply"], jitter._TRUE["units"][name]["supply"])

    def test_yields_stay_integers(self):
        """产出/投料保持**整数**：引擎里到处是 `//`、`<= 0` 的整数口味假设。"""
        for s in range(20):
            jitter.apply(s, 0.2)
            for b in game.BUILDINGS.values():
                for f in ("outputs", "inputs", "fuel"):
                    for v in (b.get(f) or {}).values():
                        self.assertIsInstance(v, int, f"{f} 变成了 {type(v)}")


class TestReachesTheModel(JitterCase):
    """★随机化必须**真的传到观测内容**，否则模型学的是假动力学。"""

    def test_content_vector_follows_jitter(self):
        base = F.building_vector("石油能源厂").copy()
        jitter.apply(42, 0.2)
        self.assertFalse((F.building_vector("石油能源厂") == base).all(),
                         "抖了规则表，内容向量却没变 —— 观测与引擎不同源")

    def test_env_reset_applies_and_eval_keeps_truth(self):
        env = ZhanguoEnv(map_size=12, max_turns=4, rules_jitter=0.2)
        env.reset(7)
        self.assertTrue(jitter.is_on())
        self.assertNotEqual(game.BUILDINGS["石油能源厂"]["cost"], self._true_cost)
        # 关掉的那个 env：reset 即还原真值
        env0 = ZhanguoEnv(map_size=12, max_turns=4)
        env0.reset(7)
        self.assertFalse(jitter.is_on())
        self.assertEqual(game.BUILDINGS["石油能源厂"]["cost"], self._true_cost)


if __name__ == "__main__":
    unittest.main()
