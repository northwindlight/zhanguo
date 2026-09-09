# -*- coding: utf-8 -*-
"""结算打分测试：总分 = 四维原始值 × 权重（已取消按榜首归一化，权重按基准国标定）。
全合成存档，不碰真实 mp_save.json。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import settlement  # noqa: E402


def _save(extra_tiles: int = 0, gold_mines: int = 6) -> dict:
    """两国合成存档：甲有农场+金矿，乙有矿场；extra_tiles 给乙加空地（模拟对手发育）。"""
    tiles = {
        "0,0": {"owner": "甲", "buildings": {"农场": 2, "黄金矿场": gold_mines}},
        "1,0": {"owner": "甲", "buildings": {}},
        "0,1": {"owner": "乙", "buildings": {"矿场": 1}},
    }
    for i in range(extra_tiles):
        tiles[f"{i + 5},0"] = {"owner": "乙", "buildings": {}}
    return {
        "turn": 12,
        "nations": {"甲": {}, "乙": {}},
        "tiles": tiles,
        "armies": [
            {"owner": "甲", "type": "步", "hp": 100, "x": 0, "y": 0},
            {"owner": "乙", "type": "骑", "hp": 50, "x": 0, "y": 1},
        ],
    }


class TestRawWeightedTotal(unittest.TestCase):
    def test_total_is_raw_weighted_sum(self):
        """总分 = 各维原始值 × 权重，不再除以存活国最大值。"""
        r = settlement.settle(_save())["甲"]
        self.assertAlmostEqual(r["total"], sum(r[k] * settlement.W[k] for k in settlement.W),
                               places=6)
        self.assertGreater(r["gdp"], 0)
        self.assertGreater(r["asset"], 0)

    def test_score_is_absolute_not_relative_to_others(self):
        """对手发育（加地）不改变自己的分数——归一化时代会掉分，现在不会。"""
        base = settlement.settle(_save())
        grown = settlement.settle(_save(extra_tiles=5))
        self.assertAlmostEqual(base["甲"]["total"], grown["甲"]["total"], places=9)
        self.assertGreater(grown["乙"]["total"], base["乙"]["total"])

    def test_total_not_capped_at_100(self):
        """取消归一化后总分可以远超 100（榜首不再恒为 100）。"""
        self.assertGreater(settlement.settle(_save(gold_mines=60))["甲"]["total"], 100)

    def test_weights_calibrated_to_reference_nation(self):
        """权重 = 目标分 ÷ 基准值 → 基准国恰好 100 分，四维贡献 30/25/30/15。"""
        parts = {k: settlement.REF_NATION[k] * settlement.W[k] for k in settlement.W}
        self.assertAlmostEqual(sum(parts.values()), 100.0, places=6)
        for k, want in settlement.TARGET_PTS.items():
            self.assertAlmostEqual(parts[k], want, places=6)


class TestScoreboardText(unittest.TestCase):
    def test_board_shows_raw_values(self):
        save = _save()
        result = settlement.settle(save)
        board = settlement.scoreboard_text(save, result)
        self.assertIn(f"{result['甲']['total']:.1f}", board)     # 总分是加权后的原始值
        self.assertIn(f"{result['甲']['asset']:.0f}", board)     # 资产列是原始值
        self.assertNotIn(f"({result['甲']['gdp']:.0f})", board)  # 不再有「归一化(原始值)」双列
        self.assertNotIn("满分", board)

    def test_board_documents_weights(self):
        save = _save()
        board = settlement.scoreboard_text(save, settlement.settle(save))
        self.assertIn(f"GDP×{settlement.W['gdp']:g}", board)
        self.assertIn(f"资产×{settlement.W['asset']:g}", board)
        self.assertIn("基准国", board)

    def test_board_ranks_by_total(self):
        save = _save()
        result = settlement.settle(save)
        board = settlement.scoreboard_text(save, result)
        self.assertLess(board.index("甲"), board.index("乙"))    # 甲综合更强 → 排前面


if __name__ == "__main__":
    unittest.main()
