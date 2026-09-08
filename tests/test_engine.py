# -*- coding: utf-8 -*-
"""引擎级回归测试：撤退减伤、强制遣返（全合成数据，不碰真实存档）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402


class TestRetreatCover(unittest.TestCase):
    """防御方撤退减伤 50%：按「该军自己该吃的那份」打折，不转嫁给同格友军。"""

    def _battle(self, covers: list) -> list[int]:
        """在 (5,5) 让 1 支秦军打 covers 支楚守军（None=不撤退），返回守军剩余 HP。"""
        w = mp.World(size=16, seed=99, nations=["秦", "楚"])
        w.tiles[(5, 5)] = w._new_tile(5, 5, "楚")
        w.tiles[(5, 5)]["owner"] = "楚"
        atk = {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
               "x": 5, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True}
        dfd = []
        for i, cv in enumerate(covers, 1):
            a = {"id": i, "gid": 100 + i, "name": f"楚·步{i}军", "type": "步", "hp": 100,
                 "x": 5, "y": 5, "owner": "楚", "moved_turn": -1, "engaged": True}
            if cv is not None:
                a["retreat_cover"] = cv
            dfd.append(a)
        w.armies = [atk] + dfd
        w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [], "turn": 1}]
        w.rng = random.Random(7)          # 同种子 → 同骰，便于对比
        w._resolve_battles()
        return [a["hp"] for a in dfd]

    def test_lone_retreater_takes_half_damage(self):
        full = self._battle([None])
        half = self._battle([50])
        dmg_full, dmg_half = 100 - full[0], 100 - half[0]
        self.assertGreater(dmg_full, 0)
        self.assertAlmostEqual(dmg_half, dmg_full // 2, delta=1)

    def test_cover_does_not_inflate_allies(self):
        normal = self._battle([None, None])
        mixed = self._battle([None, 50])
        self.assertEqual(normal[0], mixed[0])        # 未撤退的友军伤害不变
        self.assertGreaterEqual(mixed[1], normal[1])  # 撤退那支受伤更少（HP 更高）

    def test_attacker_has_no_reduction(self):
        """进攻方撤退是全额：cover=100（默认）与显式 100 等价。"""
        a = self._battle([None])
        b = self._battle([100])
        self.assertEqual(a, b)


class TestForcedWithdrawal(unittest.TestCase):
    """断盟/停战后滞留他国腹地的军队必须能走回家（否则永远困死）。"""

    def _world(self):
        w = mp.World(size=16, seed=5, nations=["秦", "楚"])
        w.tiles[(0, 0)] = w._new_tile(0, 0, "秦")
        w.tiles[(0, 0)]["owner"] = "秦"
        for x in range(4, 10):
            for y in range(4, 10):
                w.tiles[(x, y)] = w._new_tile(x, y, "楚")
                w.tiles[(x, y)]["owner"] = "楚"
        return w

    def test_stranded_army_walks_home_through_neutral_land(self):
        w = self._world()
        a = {"id": 1, "gid": 1, "name": "秦·骑一军", "type": "骑", "hp": 100,
             "x": 6, "y": 6, "owner": "秦", "moved_turn": -1, "engaged": False}
        w.armies = [a]

        def _nearest_home():
            return min(max(abs(tx - a["x"]), abs(ty - a["y"])) for tx, ty in w.own_tiles("秦"))

        before = _nearest_home()
        w._withdraw_illegal()
        self.assertNotEqual((a["x"], a["y"]), (6, 6), "滞留军队必须能挪动")
        self.assertLess(_nearest_home(), before, "每回合应朝最近的自家地走")

    def test_legal_position_is_left_alone(self):
        w = self._world()
        a = {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
             "x": 0, "y": 0, "owner": "秦", "moved_turn": -1, "engaged": False}
        w.armies = [a]
        w._withdraw_illegal()
        self.assertEqual((a["x"], a["y"]), (0, 0))


class TestHunsDiplomacy(unittest.TestCase):
    """游牧政体不接受盟约——提议直接失败，别让提议悬空、白扣外交费。"""

    def test_propose_to_huns_is_rejected(self):
        w = mp.World(size=16, seed=3, nations=["秦", "林胡"])
        w.apply_polity("林胡", "huns")
        ok, msg = w.propose_pact("共同防御", "秦", "林胡")
        self.assertFalse(ok)
        self.assertIn("游牧", msg)

    def test_bloc_founding_with_huns_is_rejected(self):
        w = mp.World(size=16, seed=3, nations=["秦", "楚", "林胡"])
        w.apply_polity("林胡", "huns")
        ok, msg = w.propose_bloc("秦", "北盟", ["楚", "林胡"])
        self.assertFalse(ok)
        self.assertIn("游牧", msg)


if __name__ == "__main__":
    unittest.main()
