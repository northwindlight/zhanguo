# -*- coding: utf-8 -*-
"""结算测试：按「总消费」（累计建造+征兵+军费，市价折金）排名，不再做加权总分。
全合成存档，不碰真实 mp_save.json。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settlement  # noqa: E402


def _save(spend: dict | None = None) -> dict:
    """两国合成存档：甲有农场+金矿，乙有矿场。spend 缺省=空（模拟旧档）。"""
    tiles = {
        "0,0": {"owner": "甲", "buildings": {"农场": 2, "黄金矿场": 6}},
        "1,0": {"owner": "甲", "buildings": {}},
        "0,1": {"owner": "乙", "buildings": {"矿场": 1}},
    }
    return {
        "turn": 12,
        "order": ["甲", "乙"],
        "nations": {"甲": {}, "乙": {}},
        "tiles": tiles,
        "armies": [
            {"owner": "甲", "type": "步", "hp": 100, "x": 0, "y": 0},
            {"owner": "乙", "type": "骑", "hp": 50, "x": 0, "y": 1},
        ],
        "spend": spend if spend is not None else {},
    }


class TestSpendTotal(unittest.TestCase):
    def test_total_is_sum_of_three(self):
        save = _save({"甲": {"build": 1000, "recruit": 200, "supply": 300},
                      "乙": {"build": 10, "recruit": 0, "supply": 5}})
        r = settlement.settle(save)["甲"]
        self.assertEqual(r["spend_total"], 1500)
        self.assertEqual(r["spend"], {"build": 1000.0, "recruit": 200.0, "supply": 300.0})

    def test_missing_spend_field_is_zero(self):
        """旧档没有 spend 字段 → 总消费 0，不崩。"""
        r = settlement.settle(_save())["甲"]
        self.assertEqual(r["spend_total"], 0.0)
        board = settlement.scoreboard_text(_save(), settlement.settle(_save()))
        self.assertIn("本档没有消费记录", board)

    def test_rank_by_spend_not_by_dimensions(self):
        """军力/领土更小的国家，只要花得多就排前面。"""
        save = _save({"甲": {"build": 5000, "recruit": 0, "supply": 0},
                      "乙": {"build": 1, "recruit": 0, "supply": 0}})
        res = settlement.settle(save)
        self.assertGreater(res["甲"]["land"], res["乙"]["land"])   # 甲本来就大
        rank = sorted(res, key=lambda n: -res[n]["spend_total"])
        self.assertEqual(rank[0], "甲")
        board = settlement.scoreboard_text(save, res)
        self.assertLess(board.index("甲"), board.index("乙"))


class TestDeadNations(unittest.TestCase):
    """已亡国照样上榜：按累计消费排名，四维现状记 0。"""

    def _save_with_dead(self) -> dict:
        save = _save({"甲": {"build": 100, "recruit": 0, "supply": 0},
                      "丙": {"build": 9000, "recruit": 500, "supply": 500}})
        save["order"] = ["甲", "乙", "丙"]
        save["nations"] = {"甲": {}, "乙": {}}          # 丙 已亡（不在 nations 里）
        return save

    def test_dead_nation_ranked_by_spend(self):
        save = self._save_with_dead()
        res = settlement.settle(save)
        self.assertFalse(res["丙"]["alive"])
        self.assertTrue(res["甲"]["alive"])
        self.assertEqual(res["丙"]["spend_total"], 10000)
        self.assertEqual(res["丙"]["land"], 0)          # 亡国四维归零
        self.assertEqual(res["丙"]["asset"], 0)
        board = settlement.scoreboard_text(save, res)
        self.assertLess(board.index("丙"), board.index("甲"))   # 消费最高 → 榜首
        self.assertIn("（亡）", board)
        self.assertIn("参与到底也算数", board)


class TestScoreboardText(unittest.TestCase):
    def test_board_columns_and_order(self):
        save = _save({"甲": {"build": 300, "recruit": 50, "supply": 70},
                      "乙": {"build": 900, "recruit": 0, "supply": 0}})
        res = settlement.settle(save)
        board = settlement.scoreboard_text(save, res)
        for col in ("总消费", "建造", "征兵", "军费", "GDP", "军力", "领土", "资产"):
            self.assertIn(col, board)
        self.assertIn("按总消费排名", board)
        self.assertIn("支出法 GDP", board)                 # 口径说明
        self.assertNotIn("总分", board)                    # 加权总分已取消
        self.assertLess(board.index("乙"), board.index("甲"))   # 乙花得多 → 排前面


if __name__ == "__main__":
    unittest.main()
