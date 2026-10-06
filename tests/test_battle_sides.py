# -*- coding: utf-8 -*-
"""`World.battle_sides` —— 「谁是进攻方 / 吃几档减伤 / 输出基数」的**唯一口径**。

为什么专门测它：从前这套判断有两处独立实现（`_resolve_battles` 排除 `hp<=0`、
`retreat()` 不排除），而漂开的症状是**静默**的——结算按攻方算、撤退却按守方给
50% 减伤。本文件的重点是**把三处消费钉到同一个数上**，而不是逐个函数测行为。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import balance  # noqa: E402
import mp  # noqa: E402
from game import unit_atk  # noqa: E402


class _Base(unittest.TestCase):
    def _world(self, size: int = 16) -> mp.World:
        w = mp.World(size=size, seed=5, nations=["秦", "楚"],
                     starts={"秦": (5, 5), "楚": (12, 12)})
        w.armies = []
        for x in range(4, 11):
            for y in range(4, 11):
                t = w._new_tile(x, y, "秦")
                t["terrain"] = "平原"
                w.tiles[(x, y)] = t
        w.tiles[(6, 5)]["owner"] = "楚"          # 目标格：楚的地
        w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [], "turn": 1}]
        return w

    def _army(self, w, kind: str, x: int, y: int, owner: str, aid: int,
              hp: int = 100, engaged: bool = False) -> dict:
        a = {"id": aid, "gid": aid, "name": f"{owner}·{kind}{aid}军", "type": kind,
             "hp": hp, "x": x, "y": y, "owner": owner, "moved_turn": -1,
             "engaged": engaged}
        w.armies.append(a)
        return a


class TestBattleSides(_Base):
    """口径本身：角色、资格、输出基数、以及"没有战斗"的判定。"""

    def test_no_engaged_means_no_battle(self):
        w = self._world()
        self._army(w, "步", 6, 5, "楚", 1)       # 楚守军，没人打它
        self.assertIsNone(w.battle_sides(6, 5))

    def test_roles_and_eligibility(self):
        w = self._world()
        self._army(w, "步", 6, 5, "秦", 1, engaged=True)   # ★ 进攻方站在目标格上
        self._army(w, "步", 6, 5, "楚", 3)                 # 守方
        s = w.battle_sides(6, 5)
        self.assertEqual(s["attacker"], {"秦"})
        self.assertEqual(s["role"]["秦"], "攻")
        self.assertEqual(s["role"]["楚"], "守")
        self.assertEqual(s["defending"], {"楚"})
        # 减伤资格：攻方不吃；格主（楚）吃
        self.assertFalse(s["soak_elig"]["秦"])
        self.assertTrue(s["soak_elig"]["楚"])
        self.assertEqual(s["soak_pct"]["秦"], 0)
        self.assertEqual(s["soak_pct"]["楚"], w._defense_pct(6, 5, "楚"))

    def test_bystander_is_neither_attacker_nor_defender(self):
        """在场但既不攻也不挨打的第三方 = 旁观（引擎无「阵营」）。"""
        w = self._world()
        self._army(w, "步", 6, 5, "秦", 1, engaged=True)
        self._army(w, "步", 6, 5, "楚", 2)
        self._army(w, "步", 6, 5, "齐", 3)        # 齐与秦、楚都无战事
        s = w.battle_sides(6, 5)
        self.assertEqual(s["role"]["齐"], "旁观")
        self.assertNotIn("齐", s["defending"])
        self.assertEqual(s["enemies"]["齐"], [])
        # 旁观者**也吃本格地形**（引擎口径：未参战的驻军挨打时吃地形）
        self.assertTrue(s["soak_elig"]["齐"])

    def test_corpse_is_not_an_attacker(self):
        """★ 口径统一到 `hp > 0`：0 血军是尸体，不该替本方要来"防御撤退"的减伤。

        这正是从前两处实现会分歧的场景（`_resolve_battles` 排除、`retreat()` 不排除）。
        """
        w = self._world()
        self._army(w, "步", 6, 5, "秦", 1, hp=0, engaged=True)   # 阵亡未移尸的进攻军
        self._army(w, "步", 6, 5, "楚", 2)
        self.assertIsNone(w.battle_sides(6, 5), "尸体不该构成一场战斗")

    def test_output_base_uses_engine_discounts(self):
        """输出基数 = Σ unit_atk；撤退中的军 −RETREAT_ATK_PENALTY%。"""
        w = self._world()
        a = self._army(w, "步", 6, 5, "秦", 1, engaged=True)
        b = self._army(w, "步", 6, 5, "秦", 2, engaged=True)
        b["retreat_to"] = [5, 5]                 # 撤退中的军
        s = w.battle_sides(6, 5)
        full = unit_atk(a)
        disc = max(1, unit_atk(b) * (100 - balance.RETREAT_ATK_PENALTY) // 100)
        self.assertEqual(s["atk_base"]["秦"], full + disc)
        self.assertLess(disc, unit_atk(b))

    def test_faction_atk_reads_hp_independently(self):
        """`unit_atk` 与 hp 无关——输出基数不看剩多少血（引擎事实，见 combat_probs 注释）。"""
        w = self._world()
        a = self._army(w, "步", 6, 5, "秦", 1, hp=100)
        b = self._army(w, "步", 6, 5, "秦", 2, hp=7)
        self.assertEqual(w.faction_atk([a]), w.faction_atk([b]))


class TestDefenseBreakdown(_Base):
    """`defense_breakdown` 的三分量，以及"城堡只算格主"那条隐藏分支。"""

    def test_terrain_and_castle_multiply(self):
        w = self._world()
        t = w.tiles[(6, 5)]
        t["terrain"] = "丘陵"
        t["buildings"]["城堡"] = 2
        td, cd, total = w.defense_breakdown(6, 5, "楚")
        self.assertEqual((td, cd), (25, 2 * 10))            # 地形 25%、城堡 L2 → 20%
        self.assertEqual(total, 100 - (100 - 25) * (100 - 20) // 100)   # 相乘＝40%，不是 45%
        self.assertEqual(w._defense_pct(6, 5, "楚"), total)  # _defense_pct 就是它的 [2]

    def test_negative_terrain_is_not_clamped(self):
        """沙漠 −10%：守方反而**多挨打**，如实返回别夹到 0。"""
        w = self._world()
        w.tiles[(6, 5)]["terrain"] = "沙漠"
        td, cd, total = w.defense_breakdown(6, 5, "楚")
        self.assertEqual((td, cd, total), (-10, 0, -10))

    def test_castle_only_counts_for_the_tile_owner(self):
        """城堡分量只在**该格归属就是防御方**时计——非格主的守军只吃地形。"""
        w = self._world()
        w.tiles[(6, 5)]["terrain"] = "丘陵"
        w.tiles[(6, 5)]["buildings"]["城堡"] = 2
        self.assertEqual(w.defense_breakdown(6, 5, "楚"), (25, 20, 40))   # 楚是格主
        self.assertEqual(w.defense_breakdown(6, 5, "齐"), (25, 0, 25))    # 齐不是格主


class TestPanelSoakEqualsSettlement(_Base):
    """★★ 本文件最重要的一条：**面板报的减伤 == 结算真吃的减伤**。

    面板与结算一旦各自算减伤，症状是"面板说减伤 40%、实际按 0% 打"——静默。
    这里把骰子钉成 `(1, 0)`（mod 0），于是伤害有闭式解，可以逐点对拍。
    """

    def _fight(self, terrain: str, castle: int):
        w = self._world()
        w.tiles[(6, 5)]["terrain"] = terrain
        w.tiles[(6, 5)]["buildings"]["城堡"] = castle
        w.tiles[(6, 5)]["owner"] = "楚"
        self._army(w, "步", 6, 5, "秦", 1, engaged=True)     # ★ 攻方站在目标格上
        defender = self._army(w, "步", 6, 5, "楚", 2, hp=100)
        w._die = lambda: (1, 0)                              # 骰子钉死：mod = 0
        soak = w.battle_sides(6, 5)["soak_pct"]["楚"]
        w._resolve_battles()
        return soak, 100 - max(0, defender["hp"])

    def test_panel_soak_reproduces_actual_damage(self):
        for terrain, castle in (("平原", 0), ("丘陵", 0), ("丘陵", 2),
                                ("沙漠", 0), ("森林", 1)):
            with self.subTest(terrain=terrain, castle=castle):
                w = self._world()
                w.tiles[(6, 5)]["terrain"] = terrain
                w.tiles[(6, 5)]["buildings"]["城堡"] = castle
                w.tiles[(6, 5)]["owner"] = "楚"
                atk = self._army(w, "步", 6, 5, "秦", 1, engaged=True)
                dfd = self._army(w, "步", 6, 5, "楚", 2, hp=1000)
                w._die = lambda: (1, 0)
                sides = w.battle_sides(6, 5)
                power = w._round_damage(w._combat_power(sides["atk_base"]["秦"], 0), 0)
                expected = max(1, round(power / len(sides["enemies"]["秦"])
                                        * (100 - sides["soak_pct"]["楚"]) / 100))
                w._resolve_battles()
                self.assertEqual(1000 - dfd["hp"], expected,
                                 f"面板 soak={sides['soak_pct']['楚']} 与实际掉血不符")
                self.assertEqual(sides["soak_pct"]["楚"], w._defense_pct(6, 5, "楚"))
                self.assertGreater(atk["hp"], 0)

    def test_no_corpse_left_after_resolution(self):
        """引擎不变量：结算后不该有 hp≤0 的军留在 `armies` 里（口径统一的前提）。"""
        w = self._world()
        self._army(w, "步", 6, 5, "秦", 1, engaged=True)
        for i in range(2, 6):
            self._army(w, "步", 6, 5, "楚", i, hp=10)
        w.rng = random.Random(3)
        w._resolve_battles()
        self.assertTrue(all(a["hp"] > 0 for a in w.armies))


class TestRetreatRole(_Base):
    """撤退的攻/防档位：与 `soak_elig` 同源，且不靠 `cover` 反推。"""

    def _retreat(self, attacker_side: bool):
        w = self._world()
        w.tiles[(5, 5)]["owner"] = "秦"          # 秦自家地：撤退目标合法
        me = self._army(w, "步", 6, 5, "秦", 1, engaged=attacker_side)
        self._army(w, "步", 6, 5, "楚", 2)       # 楚守军
        ok, msg = w.retreat("秦", 1, 5, 5)
        self.assertTrue(ok, msg)
        return w, me, w.battle_sides(6, 5)

    def test_attacker_retreat_is_full_damage(self):
        w, me, sides = self._retreat(attacker_side=True)
        self.assertEqual(me["retreat_role"], "攻")
        self.assertEqual(me["retreat_cover"], 100)
        self.assertFalse(sides["soak_elig"]["秦"])
        self.assertIn("进攻撤退", "进攻撤退，本回合结算无减伤")

    def test_defender_retreat_gets_cover(self):
        w = self._world()
        w.tiles[(5, 5)]["owner"] = "楚"
        w.tiles[(6, 5)]["owner"] = "楚"
        self._army(w, "步", 6, 5, "秦", 1, engaged=True)     # 秦来打
        me = self._army(w, "步", 6, 5, "楚", 2)              # 楚是被打的一方
        ok, msg = w.retreat("楚", 2, 5, 5)
        self.assertTrue(ok, msg)
        self.assertEqual(me["retreat_role"], "守")
        self.assertEqual(me["retreat_cover"], balance.RETREAT_DEF_COVER)
        self.assertIn("防御撤退", msg)

    def test_role_cover_and_eligibility_never_drift(self):
        """三者必须同源：`retreat_role == "守"` ⟺ `cover == RETREAT_DEF_COVER` ⟺ `soak_elig`。"""
        w, me, sides = self._retreat(attacker_side=True)
        for a in w.armies:
            if "retreat_role" not in a:
                continue
            is_def = a["retreat_role"] == "守"
            self.assertEqual(is_def, a["retreat_cover"] == balance.RETREAT_DEF_COVER)
            self.assertEqual(is_def, sides["soak_elig"][a["owner"]])


if __name__ == "__main__":
    unittest.main()
