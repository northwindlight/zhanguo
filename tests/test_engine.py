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

    def test_retreat_cuts_attack_output(self):
        """撤退军输出 -80%：守军宣布撤退后，进攻方该回合吃的伤害降到约 1/5。"""
        def run(retreat: bool) -> int:
            w = mp.World(size=16, seed=99, nations=["秦", "楚"])
            w.tiles[(5, 5)] = w._new_tile(5, 5, "楚")
            w.tiles[(5, 5)]["owner"] = "楚"
            atk = {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
                   "x": 5, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True}
            dfd = {"id": 2, "gid": 2, "name": "楚·步一军", "type": "步", "hp": 100,
                   "x": 5, "y": 5, "owner": "楚", "moved_turn": -1, "engaged": True}
            if retreat:
                dfd["retreat_to"] = (5, 4)  # 宣布撤退（cover 不影响输出）
            w.armies = [atk, dfd]
            w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [], "turn": 1}]
            w.rng = random.Random(7)  # 同种子同骰 → 输出差异只来自撤退惩罚
            w._resolve_battles()
            return 100 - atk["hp"]

        full = run(False)
        part = run(True)
        self.assertGreater(full, 0)
        self.assertLessEqual(part, max(1, full // 4))


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


class TestStarvation(unittest.TestCase):
    """断粮扣血：35×缺口/需求 按比例分摊（交战中也照扣），HP≤0 饿毙。"""

    @staticmethod
    def _army(w, i, hp=100):
        p = next(p for p, t in w.tiles.items() if t["owner"] == "秦")
        return {"id": i, "gid": 900 + i, "name": f"秦·步{i}军", "type": "步", "hp": hp,
                "x": p[0], "y": p[1], "owner": "秦", "moved_turn": -1, "engaged": False}

    def _run(self, supply, hps):
        w = mp.World(size=16, seed=3, nations=["秦", "楚"])
        w.armies = [self._army(w, i, hp) for i, hp in enumerate(hps, 1)]
        w.add_res("秦", "补给", -w.res("秦", "补给") + supply)  # 新国无补给建筑，产出恒 0
        w.resolve_turn()
        return w, [a["hp"] for a in w.armies]

    def test_partial_shortage_proportional(self):
        # 3 军 2 补给：缺口 1/3 → 每军 100 - 35*1//3 = 89
        w, hps = self._run(2, [100, 100, 100])
        self.assertEqual(hps, [89, 89, 89])

    def test_full_starvation_kills(self):
        # 完全断供：每军 -35；hp≤0 者饿毙（剩 2 支，各 65）
        w, hps = self._run(0, [100, 100, 30])
        self.assertEqual(len(w.armies), 2)
        self.assertEqual(sorted(hps), [65, 65])

    def test_no_shortage_no_damage(self):
        w, hps = self._run(50, [80, 80, 80])
        self.assertEqual(hps, [100, 100, 100])
        self.assertFalse(any("断粮" in h["text"] for h in w.history))  # 无断粮日志
        self.assertTrue(any("断粮" in h["text"] for h in self._run(0, [100])[0].history))


class TestNewBuildings(unittest.TestCase):
    """特殊建筑：瞭望塔视野 / 军屯民兵与补给 / 外交中心费用 / 工程院折扣（全合成数据）。"""

    def _world(self):
        w = mp.World(size=16, seed=3, nations=["秦", "楚"])
        return w

    def _own_tile(self, w, name="秦"):
        return next(p for p, t in w.tiles.items() if t["owner"] == name)

    # ---- 瞭望塔 ----
    def test_watchtower_extends_vision(self):
        w = self._world()
        w.tiles = {}
        t = w._new_tile(0, 0, "秦")
        t["owner"] = "秦"
        w.tiles[(0, 0)] = t
        # 无塔：距离 4 看不见
        self.assertFalse(w.visible_to("秦", 4, 0))
        t["buildings"]["瞭望塔"] = 1
        self.assertTrue(w.visible_to("秦", 4, 0))    # dx²+dy²=16 ≤ r²
        self.assertFalse(w.visible_to("秦", 5, 0))   # 25 > 16，圆外
        self.assertTrue(w.visible_to("秦", 0, 4))
        self.assertTrue(w.visible_to("秦", 2, 3))    # 4+9=13 ≤ 16（圆形而非方形）

    def test_watchtower_sees_battle_reports(self):
        """战报带坐标 → 瞭望塔圈内的战斗事件能收到（修「战报无人可见」的不一致）。"""
        w = mp.World(size=16, seed=9, nations=["秦", "楚"])
        w.tiles = {}
        t = w._new_tile(1, 1, "秦")
        t["owner"] = "秦"
        t["buildings"]["瞭望塔"] = 1
        w.tiles[(1, 1)] = t
        bt = w._new_tile(1, 5, "楚")
        bt["owner"] = "楚"
        w.tiles[(1, 5)] = bt
        w.armies = [
            {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
             "x": 1, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True},
            {"id": 2, "gid": 2, "name": "楚·步一军", "type": "步", "hp": 100,
             "x": 1, "y": 5, "owner": "楚", "moved_turn": -1, "engaged": True},
        ]
        w.wars = [{"id": 1, "atk": "秦", "def": "楚", "followers": [], "turn": 1}]
        w.rng = random.Random(7)
        w.resolve_turn()
        # 战场距塔 4 格（非相邻）：没有塔根本看不见；有塔必须看到战报
        self.assertTrue(any("⚔" in l for l in w.events_for("秦")))

    # ---- 军屯：民兵兵营 ----
    def test_militia_recruit_at_camp(self):
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["军屯"] = 1
        g0 = w.res("秦", "黄金")
        ok, msg = w.recruit("秦", x, y, 1, "民")
        self.assertTrue(ok, msg)
        mil = [a for a in w.nation_armies("秦") if a.get("type") == "民"]
        self.assertEqual(len(mil), 1)
        self.assertEqual((mil[0]["x"], mil[0]["y"]), (x, y))
        self.assertEqual(g0 - w.res("秦", "黄金"), 50)  # 50金/支
        # 每军屯每回合 1 支
        ok2, msg2 = w.recruit("秦", x, y, 1, "民")
        self.assertFalse(ok2)
        self.assertIn("产能", msg2)

    def test_militia_quota_is_total_camps(self):
        w = self._world()
        (x1, y1), (x2, y2) = [p for p, t in w.tiles.items() if t["owner"] == "秦"][:2]
        w.tiles[(x1, y1)]["buildings"]["军屯"] = 1
        w.tiles[(x2, y2)]["buildings"]["军屯"] = 1
        w.add_res("秦", "黄金", 500)
        # 全国 2 座军屯 → 本回合共可征 2 支（两格各吃各的配额）
        ok1, m1 = w.recruit("秦", x1, y1, 1, "民")
        ok2, m2 = w.recruit("秦", x2, y2, 1, "民")
        self.assertTrue(ok1 and ok2, m1 + m2)
        # 同格第 3 支：每军屯 1 支/回合 卡住；全国配额也满了
        ok3, m3 = w.recruit("秦", x1, y1, 1, "民")
        self.assertFalse(ok3)
        # 回合结算后配额刷新
        w.resolve_turn()
        ok4, m4 = w.recruit("秦", x2, y2, 1, "民")
        self.assertTrue(ok4, m4)

    def test_militia_camp_capped_by_farmland(self):
        w = self._world()
        x, y = self._own_tile(w)
        t = w.tiles[(x, y)]
        t["resources"]["耕地"] = 1
        t["buildings"]["军屯"] = 1
        w.add_res("秦", "黄金", 2000)
        w.add_res("秦", "木头", 100)
        ok, msg = w.build("秦", x, y, "军屯")
        self.assertFalse(ok)
        self.assertIn("上限", msg)

    def test_militia_needs_camp(self):
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["兵营"] = 1  # 只有兵营也不行
        ok, msg = w.recruit("秦", x, y, 1, "民")
        self.assertFalse(ok)
        self.assertIn("军屯", msg)

    # ---- 军屯：民兵驻格免补给 ----
    def test_militia_on_camp_eats_no_supply(self):
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["军屯"] = 1
        w.armies = [{"id": 1, "gid": 901, "name": "秦·民一军", "type": "民", "hp": 100,
                     "x": x, "y": y, "owner": "秦", "moved_turn": -1, "engaged": False}]
        w.add_res("秦", "补给", -w.res("秦", "补给"))  # 补给仓清零
        w.resolve_turn()
        self.assertEqual(w.armies[0]["hp"], 100)  # 驻屯免补给 → 无断粮

    def test_second_militia_on_camp_eats_supply(self):
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["军屯"] = 1
        w.armies = [{"id": i, "gid": 900 + i, "name": f"秦·民{i}军", "type": "民", "hp": 100,
                     "x": x, "y": y, "owner": "秦", "moved_turn": -1, "engaged": False}
                    for i in (1, 2)]
        w.add_res("秦", "补给", -w.res("秦", "补给"))  # 0 补给：1 免费 1 断供
        w.resolve_turn()
        self.assertEqual(sorted(a["hp"] for a in w.armies), [65, 65])  # 全军按缺口 -35

    def test_militia_off_camp_eats_supply(self):
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["军屯"] = 1
        w.armies = [{"id": 1, "gid": 901, "name": "秦·民一军", "type": "民", "hp": 100,
                     "x": x + 1, "y": y, "owner": "秦", "moved_turn": -1, "engaged": False}]
        w.add_res("秦", "补给", -w.res("秦", "补给"))
        w.resolve_turn()
        self.assertEqual(w.armies[0]["hp"], 65)  # 离格照常吃 → 断供 -35

    def test_militia_on_enemy_camp_eats_supply(self):
        w = self._world()
        x, y = self._own_tile(w)
        ex, ey = self._own_tile(w, "楚")
        w.tiles[(ex, ey)]["buildings"]["军屯"] = 1  # 别国的军屯，秦的民兵蹭不到
        w.armies = [{"id": 1, "gid": 901, "name": "秦·民一军", "type": "民", "hp": 100,
                     "x": ex, "y": ey, "owner": "秦", "moved_turn": -1, "engaged": False}]
        w.add_res("秦", "补给", -w.res("秦", "补给"))
        w.resolve_turn()
        self.assertEqual(w.armies[0]["hp"], 65)

    # ---- 外交中心 ----
    def _tile_with_slots(self, w, n_slots):
        x, y = self._own_tile(w)
        t = w.tiles[(x, y)]
        t["buildings"]["农场"] = n_slots
        w.add_res("秦", "黄金", 5000)
        w.add_res("秦", "木头", 500)
        return x, y

    def test_diplomatic_center_halves_fee_and_is_unique(self):
        import mp_ai
        w = self._world()
        x, y = self._tile_with_slots(w, 5)
        ok, msg = w.build("秦", x, y, "外交中心")
        self.assertTrue(ok, msg)
        self.assertEqual(w.diplo_built["秦"], 1)  # 自建名额已用
        # 第二座：自建全国限 1（换一块秦地、凑足建筑位再建）
        ox, oy = next(p for p, t in w.tiles.items() if t["owner"] == "秦" and p != (x, y))
        w.tiles[(ox, oy)]["buildings"]["农场"] = 5
        ok2, msg2 = w.build("秦", ox, oy, "外交中心")
        self.assertFalse(ok2)
        self.assertIn("自建全国限", msg2)
        w.resolve_turn()  # 落地
        self.assertEqual(mp_ai._diplo_cost(w, "秦", "楚"), 5)       # 10 减半
        self.assertEqual(mp_ai._diplo_cost(w, "楚", "秦", incoming=True), 0)  # 向它提议免费
        self.assertEqual(mp_ai._diplo_cost(w, "楚", "秦"), 10)       # 楚没有中心，自己付全价

    def test_militia_attack_is_weak(self):
        from game import unit_atk
        step = {"type": "步", "owner": "秦"}
        mil = {"type": "民", "owner": "秦"}
        legacy = {"owner": "野人"}  # 旧档无 type → 按步兵算
        self.assertEqual(unit_atk(step), 50)
        self.assertEqual(unit_atk(mil), 30)
        self.assertEqual(unit_atk(legacy), 50)

    # ---- 工程院 ----
    def test_academy_discounts_local_build(self):
        w = self._world()
        x, y = self._tile_with_slots(w, 6)
        w.tiles[(x, y)]["resources"]["矿石"] = 1
        ok, msg = w.build("秦", x, y, "工程院")
        self.assertTrue(ok, msg)
        w.resolve_turn()  # 落成
        # 同格再建矿场（80 金）：工程院 -20% → 64 金
        ok2, msg2 = w.build("秦", x, y, "矿场")
        self.assertTrue(ok2, msg2)
        self.assertIn("-64金", msg2)
        self.assertIn("工程院-20%", msg2)


if __name__ == "__main__":
    unittest.main()
