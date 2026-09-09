# -*- coding: utf-8 -*-
"""写信计费测试：起步价联盟 10 / 非联盟 20（吃外交中心减免），超字费每 10 字 1 金且不吃减免。
全合成世界，不碰真实 mp_save.json。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import mp_ai  # noqa: E402
from game import (LETTER_CHARS_PER_GOLD, LETTER_COST, LETTER_COST_ALLY,  # noqa: E402
                  LETTER_COST_MIN, LETTER_FREE_CHARS, letter_cost)


class TestLetterCost(unittest.TestCase):
    def test_base_price_by_alliance(self):
        self.assertEqual(letter_cost("短"), LETTER_COST)                  # 非联盟 20
        self.assertEqual(letter_cost("短", allied=True), LETTER_COST_ALLY)  # 联盟 10

    def test_free_chars_then_per_ten(self):
        n = LETTER_FREE_CHARS
        self.assertEqual(letter_cost("x" * n), LETTER_COST)               # 恰好免费额
        self.assertEqual(letter_cost("x" * (n + 1)), LETTER_COST + 1)     # 超 1 字 → 1 金
        self.assertEqual(letter_cost("x" * (n + 10)), LETTER_COST + 1)    # 超 10 字 → 1 金
        self.assertEqual(letter_cost("x" * (n + 11)), LETTER_COST + 2)    # 超 11 字 → 2 金
        self.assertEqual(letter_cost("x" * 242),
                         LETTER_COST + -(-(242 - n) // LETTER_CHARS_PER_GOLD))

    def test_center_discount_applies_to_base_only(self):
        n = LETTER_FREE_CHARS
        self.assertEqual(letter_cost("x" * n, diplo_centers=1), LETTER_COST - 5)
        self.assertEqual(letter_cost("x" * n, allied=True, diplo_centers=1), LETTER_COST_ALLY - 5)
        # 下限 5：4 座外交中心也只减到 5
        self.assertEqual(letter_cost("x" * n, diplo_centers=4), LETTER_COST_MIN)
        # 超字费不打折：联盟 + 1 座 = 5 + ceil(20/10) = 7（若打折会低于 7）
        self.assertEqual(letter_cost("x" * (n + 20), allied=True, diplo_centers=1), 7)


class TestSendLetterCharge(unittest.TestCase):
    def _world(self):
        w = mp.World(size=16, seed=9, nations=["秦", "楚", "齐"])
        w.cheat("秦", 黄金=1000)
        return w

    def test_charge_matches_formula_and_reports(self):
        w = self._world()
        text = "x" * 40                                     # 超 20 字 → +2
        before = w.res("秦", "黄金")
        msg = mp_ai.execute(w, "秦", "send_letter", {"to": "楚", "content": text})
        self.assertEqual(before - w.res("秦", "黄金"), LETTER_COST + 2)
        self.assertIn("40 字，-22 金", msg)

    def test_ally_base_is_ten(self):
        w = self._world()
        w.blocs.append({"name": "盟", "chief": "秦", "members": ["秦", "楚"], "turn": 1})
        self.assertTrue(w.allied_between("秦", "楚"))
        before = w.res("秦", "黄金")
        mp_ai.execute(w, "秦", "send_letter", {"to": "楚", "content": "x" * 20})
        self.assertEqual(before - w.res("秦", "黄金"), LETTER_COST_ALLY)   # 10，不再免费

    def test_diplomatic_center_discounts_base(self):
        w = self._world()
        t = next(iter(w.own_tiles("秦")))
        w.tiles[t]["buildings"]["外交中心"] = 1
        before = w.res("秦", "黄金")
        mp_ai.execute(w, "秦", "send_letter", {"to": "楚", "content": "x" * 20})
        self.assertEqual(before - w.res("秦", "黄金"), LETTER_COST - 5)    # 15

    def test_insufficient_gold_rejected_with_breakdown(self):
        w = self._world()
        w.add_res("秦", "黄金", -w.res("秦", "黄金"))          # 清空国库
        msg = mp_ai.execute(w, "秦", "send_letter", {"to": "楚", "content": "x" * 40})
        self.assertIn("国库不足", msg)
        self.assertIn("不打折", msg)
        self.assertEqual(w.res("秦", "黄金"), 0)               # 没扣钱


if __name__ == "__main__":
    unittest.main()
