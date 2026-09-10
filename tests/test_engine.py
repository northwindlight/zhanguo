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
        """在 (5,5) 野地让 1 支秦军打 covers 支野人守军（None=不撤退），返回守军剩余 HP。"""
        w = mp.World(size=16, seed=99, nations=["秦", "楚"])
        atk = {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
               "x": 5, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True}
        dfd = []
        for i, cv in enumerate(covers, 1):
            a = {"id": i, "gid": 100 + i, "name": f"野人{i}", "type": "步", "hp": 100,
                 "x": 5, "y": 5, "owner": "野人", "moved_turn": -1, "engaged": True}
            if cv is not None:
                a["retreat_cover"] = cv
            dfd.append(a)
        w.armies = [atk] + dfd
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

    def test_stranded_engaged_army_is_freed(self):
        """野人已被清空后，原格我方军队不该再背「交战中」——清扫立即解锁。"""
        w = mp.World(size=16, seed=5, nations=["秦", "楚"])
        stay = {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
                "x": 5, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True}
        w.armies = [stay]                      # 野人已阵亡（不在 armies 里）
        n = w._clear_disengaged()
        self.assertEqual(n, 1)
        self.assertFalse(stay["engaged"])
        # 野人还在时绝不误清
        stay["engaged"] = True
        w.armies.append({"id": 3, "gid": 3, "name": "野人3", "type": "步", "hp": 100,
                         "x": 5, "y": 5, "owner": "野人", "moved_turn": -1, "engaged": True})
        self.assertEqual(w._clear_disengaged(), 0)
        self.assertTrue(stay["engaged"])

    def test_retreat_cuts_attack_output(self):
        """撤退军输出 -80%：守军（野人）宣布撤退后，进攻方该回合吃的伤害降到约 1/5。"""
        def run(retreat: bool) -> int:
            w = mp.World(size=16, seed=99, nations=["秦", "楚"])
            atk = {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
                   "x": 5, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True}
            dfd = {"id": 2, "gid": 2, "name": "野人2", "type": "步", "hp": 100,
                   "x": 5, "y": 5, "owner": "野人", "moved_turn": -1, "engaged": True}
            if retreat:
                dfd["retreat_to"] = (5, 4)  # 宣布撤退（cover 不影响输出）
            w.armies = [atk, dfd]
            w.rng = random.Random(7)  # 同种子同骰 → 输出差异只来自撤退惩罚
            w._resolve_battles()
            return 100 - atk["hp"]

        full = run(False)
        part = run(True)
        self.assertGreater(full, 0)
        self.assertLessEqual(part, max(1, full // 4))

class TestHunsPolity(unittest.TestCase):
    """游牧政体只保留经济/兵种差异（本局无外交）：骑兵征召 8粮8装、建筑 +30% 惩罚。"""

    def test_cavalry_recruit_is_cheaper(self):
        w = mp.World(size=16, seed=3, nations=["秦", "林胡"])
        w.apply_polity("林胡", "huns", start={"骑": 0, "黄金": 500, "补给": 100})
        x, y = next(p for p, t in w.tiles.items() if t["owner"] == "林胡")
        w.tiles[(x, y)]["buildings"]["兵营"] = 1
        w.nations["林胡"].res.update({"粮食": 8, "装备": 8})
        ok, msg = w.recruit("林胡", x, y, 1, "骑")
        self.assertTrue(ok, msg)
        self.assertEqual(w.res("林胡", "粮食"), 0)   # 只吃 8 粮
        self.assertEqual(w.res("林胡", "装备"), 0)   # 只吃 8 装

    def test_build_penalty_applies(self):
        w = mp.World(size=16, seed=3, nations=["秦", "林胡"])
        w.apply_polity("林胡", "huns", start={"骑": 0, "黄金": 5000, "补给": 100})
        x, y = next(p for p, t in w.tiles.items() if t["owner"] == "林胡")
        w.tiles[(x, y)]["terrain"] = "平原"        # 排除地形惩罚干扰
        w.tiles[(x, y)]["resources"]["木头"] = 2
        w.add_res("林胡", "木头", 50)
        ok, msg = w.build("林胡", x, y, "林场")     # 基准 45 → 匈奴 ×1.3 = 58
        self.assertTrue(ok, msg)
        self.assertIn("-58金", msg)


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
        w.armies = [
            {"id": 1, "gid": 1, "name": "秦·步一军", "type": "步", "hp": 100,
             "x": 1, "y": 5, "owner": "秦", "moved_turn": -1, "engaged": True},
            {"id": 2, "gid": 2, "name": "野人2", "type": "步", "hp": 100,
             "x": 1, "y": 5, "owner": "野人", "moved_turn": -1, "engaged": True},
        ]
        w.guard_once.add((1, 5))
        w.rng = random.Random(7)
        w.resolve_turn()
        # 战场距塔 4 格（非相邻）：没有塔根本看不见；有塔必须看到战报
        self.assertTrue(any("⚔" in l for l in w.events_for("秦")))

    def test_militia_recruit_at_camp(self):
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["军屯"] = 1
        g0, f0 = w.res("秦", "黄金"), w.res("秦", "粮食")
        ok, msg = w.recruit("秦", x, y, 1, "民")
        self.assertTrue(ok, msg)
        mil = [a for a in w.nation_armies("秦") if a.get("type") == "民"]
        self.assertEqual(len(mil), 1)
        self.assertEqual((mil[0]["x"], mil[0]["y"]), (x, y))
        self.assertEqual(g0 - w.res("秦", "黄金"), 50)  # 50金/支
        self.assertEqual(f0 - w.res("秦", "粮食"), 5)   # +5粮/支
        # 每军屯每回合 1 支
        ok2, msg2 = w.recruit("秦", x, y, 1, "民")
        self.assertFalse(ok2)
        self.assertIn("产能", msg2)

    def test_militia_total_capped_by_camps(self):
        """民兵总数 ≤ 全国军屯总数：满编后换回合也征不出，阵亡后才能补员。"""
        w = self._world()
        (x1, y1), (x2, y2) = [p for p, t in w.tiles.items() if t["owner"] == "秦"][:2]
        w.tiles[(x1, y1)]["buildings"]["军屯"] = 1
        w.tiles[(x2, y2)]["buildings"]["军屯"] = 1
        w.add_res("秦", "黄金", 500)
        w.add_res("秦", "粮食", 100)
        # 全国 2 座军屯 → 满编 2 支
        ok1, m1 = w.recruit("秦", x1, y1, 1, "民")
        ok2, m2 = w.recruit("秦", x2, y2, 1, "民")
        self.assertTrue(ok1 and ok2, m1 + m2)
        ok3, m3 = w.recruit("秦", x1, y1, 1, "民")
        self.assertFalse(ok3)
        w.resolve_turn()
        ok4, m4 = w.recruit("秦", x2, y2, 1, "民")
        self.assertFalse(ok4)                        # 编制满：换回合也不行
        self.assertIn("编制", m4)
        # 阵亡一支 → 空出编制可补员
        w.armies.remove(next(a for a in w.armies if a["owner"] == "秦" and a.get("type") == "民"))
        ok5, m5 = w.recruit("秦", x2, y2, 1, "民")
        self.assertTrue(ok5, m5)

    def test_militia_camp_one_per_tile_and_needs_farmland(self):
        """军屯每地块限 1 座；本地无耕地则建不了。"""
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["resources"]["耕地"] = 2
        w.add_res("秦", "黄金", 2000)
        w.add_res("秦", "木头", 100)
        ok, msg = w.build("秦", x, y, "军屯")
        self.assertTrue(ok, msg)
        w.resolve_turn()                                # 落地
        ok2, msg2 = w.build("秦", x, y, "军屯")
        self.assertFalse(ok2)
        self.assertIn("上限", msg2)
        ox, oy = next(p for p, t in w.tiles.items() if t["owner"] == "秦" and p != (x, y))
        w.tiles[(ox, oy)]["resources"]["耕地"] = 0
        ok3, msg3 = w.build("秦", ox, oy, "军屯")
        self.assertFalse(ok3)

    def test_militia_camp_grows_food(self):
        """军屯屯田：每座每回合 +1 粮（不耗电）。"""
        w = self._world()
        x, y = self._own_tile(w)
        w.tiles[(x, y)]["buildings"]["军屯"] = 1
        w.tiles[(x, y)]["buildings"]["农场"] = 0
        f0 = w.res("秦", "粮食")
        w.resolve_turn()
        self.assertEqual(w.res("秦", "粮食") - f0, 1)

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
        w.armies = [{"id": 1, "gid": 901, "name": "秦·民一军", "type": "民", "hp": 80,
                     "x": x, "y": y, "owner": "秦", "moved_turn": -1, "engaged": False}]
        w.add_res("秦", "补给", -w.res("秦", "补给"))  # 补给仓清零
        w.resolve_turn()
        self.assertEqual(w.armies[0]["hp"], 80)  # 驻屯免补给 → 无断粮（满血 80）

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

    def test_militia_attack_is_weak(self):
        from game import unit_atk
        step = {"type": "步", "owner": "秦"}
        mil = {"type": "民", "owner": "秦"}
        legacy = {"owner": "野人"}  # 旧档无 type → 按步兵算
        self.assertEqual(unit_atk(step), 50)
        self.assertEqual(unit_atk(mil), 20)
        self.assertEqual(unit_atk(legacy), 50)
        self.assertEqual(mp.unit_max_hp(mil), 80)      # 民兵 80HP（步/骑 100）
        self.assertEqual(mp.unit_max_hp(step), 100)
        self.assertEqual(mp.unit_max_hp(legacy), 100)  # 旧档无 type → 按步兵

    # ---- 工程院 ----
    def test_academy_discounts_local_build(self):
        w = self._world()
        x, y = self._tile_with_slots(w, 4)
        w.tiles[(x, y)]["resources"]["矿石"] = 1
        ok, msg = w.build("秦", x, y, "工程院")
        self.assertTrue(ok, msg)
        w.resolve_turn()  # 落成
        # 同格再建矿场（70 金）：工程院 -25% → 52 金
        ok2, msg2 = w.build("秦", x, y, "矿场")
        self.assertTrue(ok2, msg2)
        self.assertIn("-52金", msg2)
        self.assertIn("工程院-25%", msg2)
        # 别的地块不享受（只惠及本地块）
        ox, oy = next(p for p, t in w.tiles.items() if t["owner"] == "秦" and p != (x, y))
        w.add_res("秦", "黄金", 5000)
        w.add_res("秦", "木头", 500)
        w.tiles[(ox, oy)]["resources"]["矿石"] = 1
        w.tiles[(ox, oy)]["terrain"] = "平原"
        ok3, msg3 = w.build("秦", ox, oy, "矿场")
        self.assertTrue(ok3, msg3)
        self.assertIn("-70金", msg3)


class TestWildernessClaims(unittest.TestCase):
    """野地（无主地）的 mv/atk 与归属：第三方驻守、打野、索取顺序、地形防御归属。"""

    WILD = (7, 7)

    def _army(self, aid, owner, x, y, engaged=False, hp=100, seq=0):
        a = {"id": aid, "gid": aid, "name": f"{owner}·步{aid}军", "type": "步", "hp": hp,
             "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": engaged}
        if seq:
            a["engage_seq"] = seq
        return a

    def _world(self, nations=("秦", "楚", "齐", "燕", "赵"), blocs=()):
        w = mp.World(size=16, seed=5, nations=list(nations))
        for name, members in blocs:
            w.blocs.append({"name": name, "members": list(members), "chief": members[0], "turn": 1})
        return w

    def _wild(self, w, guard_hp=100, armies=(), guard=True):
        x, y = self.WILD
        self.assertIsNone(w.owned_by(x, y), "测试用格必须是野地")
        w.armies = list(armies)
        if guard:
            w.armies.insert(0, self._army(900, "野人", x, y, hp=guard_hp))
        w.guard_once.add((x, y))
        return x, y

    def _squat(self, owner, aid, engaged=False, hp=100):
        """在测试用野地上放一支军队。"""
        return self._army(aid, owner, *self.WILD, engaged=engaged, hp=hp)

    def test_neutral_squatter_mv_free_no_effect(self):
        """中立驻守野地：mv 可进（旁观），结算互不伤害。"""
        w = self._world()
        x, y = self._wild(w, armies=[self._army(1, "秦", 6, 7), self._squat("齐", 2)])
        ok, msg = w.move("秦", 1, x, y)
        self.assertTrue(ok, msg)
        self.assertFalse(w._army("秦", 1)["engaged"])
        w.rng = random.Random(7)
        w._resolve_battles()
        self.assertEqual(w._army("秦", 1)["hp"], 100)
        self.assertEqual(w._army("齐", 2)["hp"], 100)
        self.assertIsNone(w.owned_by(x, y))          # 和平驻守不占地

    def test_neutral_squatter_atk_only_guards_then_claims(self):
        """中立驻守野地：我 atk 只打野人；野人清空后我拿地，驻守者回合末被遣返。"""
        w = self._world()
        x, y = self._wild(w, guard_hp=1, armies=[self._army(1, "秦", 6, 7), self._squat("齐", 2)])
        ok, msg = w.attack("秦", [1], x, y)
        self.assertTrue(ok, msg)
        w.rng = random.Random(7)
        lines = w._resolve_battles()
        self.assertEqual(w.owned_by(x, y), "秦")
        self.assertEqual(w._army("齐", 2)["hp"], 100)     # 中立未挨打（只打野人）
        self.assertIn("遣返", lines[0][2])
        w.resolve_turn()
        self.assertNotEqual((w._army("齐", 2)["x"], w._army("齐", 2)["y"]), (x, y))

    def test_empty_wild_with_neutral_squatter_atk_claims(self):
        """野人已清的空野地：中立驻守不构成障碍，atk 直接进驻占领。"""
        w = self._world()
        x, y = self._wild(w, guard=False, armies=[self._army(1, "秦", 6, 7), self._squat("齐", 2)])
        ok, msg = w.attack("秦", [1], x, y)
        self.assertTrue(ok, msg)
        self.assertEqual(w.owned_by(x, y), "秦")

    def test_neutral_attacking_guards_blocks_my_atk(self):
        """中立正在打野：不能 atk 插足（不抢别人的战斗），但可 mv 旁观。"""
        w = self._world()
        x, y = self._wild(w, armies=[self._army(1, "秦", 6, 7),
                                     self._squat("齐", 2, engaged=True)])
        ok, msg = w.attack("秦", [1], x, y)
        self.assertFalse(ok, msg)
        self.assertIn("插足", msg)
        ok2, msg2 = w.move("秦", 1, x, y)
        self.assertTrue(ok2, msg2)

    def test_defender_gets_terrain_defense(self):
        """谁挨打谁是守方：野人守军吃本地地形减伤，交战中的进攻方一律全额。"""
        def dmg_taken(terrain: str) -> int:
            w = self._world()
            spot = next(((x, y) for x in range(1, 15) for y in range(1, 15)
                         if w.tile_terrain(x, y) == terrain and w.owned_by(x, y) is None), None)
            self.assertIsNotNone(spot, f"种子里找不到{terrain}野地")
            x, y = spot
            w.armies = [self._army(1, "秦", x, y, engaged=True),
                        self._army(2, "野人", x, y, engaged=True)]
            w.guard_once.add((x, y))
            w.rng = random.Random(11)
            w._resolve_battles()
            return 100 - w._army("野人", 2)["hp"]
        plain = dmg_taken("平原")     # 守方无加成
        mountain = dmg_taken("山地")  # 守方 +50% 减伤
        self.assertGreater(plain, 0)
        self.assertLess(mountain, plain)

class TestMarket(unittest.TestCase):
    """市场定价（2026-09-09 改革）：沿曲线均价结算、买卖价差、分商品深度、供需均衡价。"""

    def _world(self, nations=("秦", "楚", "齐", "燕")):
        return mp.World(size=16, seed=13, nations=list(nations))

    def test_settlement_uses_walk_average_not_clearing_price(self):
        """整笔按「沿曲线均价」结算：实收 = (p0+p1)/2 × n × (1−卖价差/2)，而非旧的 p1×n。"""
        w = self._world()
        w.nations["秦"].res["粮食"] = 500
        p0 = w.prices["粮食"]
        n = 100
        p1 = p0 - w.market_tick("粮食") * n
        gold0 = w.nations["秦"].res["黄金"]
        ok, msg = w.sell("秦", "粮食", n)
        self.assertTrue(ok, msg)
        expect = int(round((p0 + p1) / 2 * n * (1 - mp.MARKET_SPREAD / 2)))
        self.assertEqual(w.nations["秦"].res["黄金"] - gold0, expect)
        self.assertAlmostEqual(w.prices["粮食"], p1, places=3)
        # 同一价差下，均价结算必须优于旧「整笔按清仓价 p1」结算
        self.assertGreater(expect, int(round(p1 * n * (1 - mp.MARKET_SPREAD / 2))))

    def test_spread_makes_round_trip_lose(self):
        """买卖价差 10%：同价买回再卖出必亏，翻转套利不成立。"""
        w = self._world()
        gold0 = w.nations["秦"].res["黄金"]
        self.assertTrue(w.buy("秦", "矿石", 20)[0])
        self.assertTrue(w.sell("秦", "矿石", 20)[0])
        self.assertLess(w.nations["秦"].res["黄金"], gold0)

    def test_depth_grows_with_nations(self):
        """国家越多市场越深：同样 4 国档为基准，5 国冲击更小。"""
        few = self._world(("秦", "楚"))
        many = self._world(("秦", "楚", "齐", "燕", "赵"))
        self.assertGreater(few.market_tick("矿石"), many.market_tick("矿石"), "国家少 → 市场浅 → 冲击大")
        self.assertEqual(many.market_depth("矿石"), mp.MARKET_DEPTH["矿石"] * 5 // 4)

    def test_per_good_depth(self):
        """分商品深度：军工（装备）比大路货（粮食）浅，同样的量冲击更大。"""
        w = self._world()
        self.assertGreater(w.market_tick("装备"), w.market_tick("粮食"))

    def test_split_order_across_turns_beats_one_big_dump(self):
        """跨回合分批卖比一次砸盘划算（同回合拆单无差别：均价结算下线性路径可加）。"""
        one, split = self._world(), self._world()
        for w in (one, split):
            w.nations["秦"].res["矿石"] = 500
        one.sell("秦", "矿石", 100)
        split.sell("秦", "矿石", 50)
        split.resolve_turn()      # 市价向均衡价回血后再卖剩下 50
        split.sell("秦", "矿石", 50)
        self.assertGreater(split.nations["秦"].res["黄金"], one.nations["秦"].res["黄金"])

    def test_equilibrium_from_world_flows(self):
        """产大于耗 → 均衡价低于基准，市价向均衡价回归。"""
        w = self._world()
        w.flow_in["粮食"] = 20
        w.flow_out["粮食"] = 5
        w.resolve_turn()
        self.assertLess(w.equilibrium["粮食"], mp.MARKET["粮食"])
        self.assertLess(w.prices["粮食"], mp.MARKET["粮食"])

    def test_equilibrium_scarcity_raises_price(self):
        """只有消耗没有产出（战时军需）→ 均衡价高于基准。"""
        w = self._world()
        w.flow_out["装备"] = 10
        w.resolve_turn()
        self.assertGreater(w.equilibrium["装备"], mp.MARKET["装备"])

    def test_equilibrium_clamped(self):
        """均衡价夹在 [基准×0.5, 基准×1.8] 内。"""
        w = self._world()
        w.flow_in["装备"], w.flow_out["装备"] = 1, 10 ** 6
        w.flow_in["粮食"], w.flow_out["粮食"] = 10 ** 6, 1
        w.resolve_turn()
        self.assertAlmostEqual(w.equilibrium["装备"], mp.MARKET["装备"] * mp.MARKET_EQ_MAX_RATIO, places=2)
        self.assertAlmostEqual(w.equilibrium["粮食"], mp.MARKET["粮食"] * mp.MARKET_EQ_MIN_RATIO, places=2)

    def test_price_floor_and_ceiling(self):
        """极端买卖被夹在 [基准×0.2, 基准×3]。"""
        w = self._world()
        w.nations["秦"].res["粮食"] = 10 ** 6
        w.nations["秦"].res["黄金"] = 10 ** 7
        w.sell("秦", "粮食", 10 ** 6)
        self.assertAlmostEqual(w.prices["粮食"], mp.MARKET["粮食"] * mp.PRICE_MIN_RATIO, places=2)
        w.buy("秦", "粮食", 10 ** 6)
        self.assertAlmostEqual(w.prices["粮食"], mp.MARKET["粮食"] * mp.PRICE_MAX_RATIO, places=2)

    def test_quote_matches_actual_sale(self):
        """面板试算与真实成交一致（AI 据试算决策，不能骗它）。"""
        w = self._world()
        w.nations["秦"].res["矿石"] = 200
        _unit, total = w.market_quote("矿石", 80, "sell")
        gold0 = w.nations["秦"].res["黄金"]
        w.sell("秦", "矿石", 80)
        self.assertEqual(w.nations["秦"].res["黄金"] - gold0, total)

    def test_flow_persists_across_save_load(self):
        """流量与均衡价随存档持久化（续档后市场状态不丢）。"""
        import tempfile
        from pathlib import Path as _P
        w = self._world()
        w.flow_out["补给"] = 7
        w.resolve_turn()
        eq = dict(w.equilibrium)
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        self.assertEqual(w2.equilibrium, eq)
        self.assertEqual(w2.flow_in, {g: 0 for g in mp.TRADEABLE})
        self.assertEqual(w2.flow_out, {g: 0 for g in mp.TRADEABLE})


class TestLoadNoGhostTiles(unittest.TestCase):
    """读档必须整体还原：不能留下存档里没有的地块。

    曾经的 bug：load() 内部 cls(size, seed) 不带 nations → 先按默认「秦楚齐」建了一遍地图与
    野人守卫，再逐格覆盖存档；默认三国里没被覆盖的格子就成了**幽灵地**（属于不存在的国家）。
    国家数恰好为 3 时位置重合，问题被掩盖。
    """

    def test_roundtrip_matches_tiles_exactly(self):
        import tempfile
        from pathlib import Path as _P
        for names in (["秦", "楚"], ["甲", "乙", "丙", "丁"], ["秦", "楚", "齐"]):
            w = mp.World(size=20, seed=3, nations=names)
            with tempfile.TemporaryDirectory() as d:
                p = _P(d) / "s.json"
                w.save(p)
                w2 = mp.World.load(p)
            self.assertEqual(set(w2.tiles), set(w.tiles), f"{names}: 地块集合应完全一致")
            self.assertEqual(w2.order, names)
            owners = {t["owner"] for t in w2.tiles.values()}
            self.assertEqual(owners - set(names), set(), f"{names}: 不应有幽灵地块")

    def test_load_does_not_build_default_nations(self):
        import tempfile
        from pathlib import Path as _P
        w = mp.World(size=20, seed=3, nations=["甲", "乙"])
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        for ghost in ("秦", "楚", "齐"):
            self.assertNotIn(ghost, w2.nations)
            self.assertNotIn(ghost, w2.order)


class TestGuardians(unittest.TestCase):
    """野人是地图的静态属性：开局全图无主地都有守卫，不随视野/扩张出现，杀了不重生。"""

    def _w(self):
        return mp.World(size=20, seed=5, nations=["秦", "楚"])

    @staticmethod
    def _guards(w) -> set:
        return {(a["x"], a["y"]) for a in w.armies if a["owner"] == "野人"}

    def test_every_wilderness_tile_guarded_at_start(self):
        w = self._w()
        wild = {(x, y) for x in range(20) for y in range(20) if (x, y) not in w.tiles}
        self.assertEqual(self._guards(w), wild)
        self.assertFalse(self._guards(w) & set(w.tiles), "国家格上不该有野人")

    def test_conquest_does_not_spawn_new_guardians(self):
        """占一格只清掉该格守卫，不会在边界外凭空冒出新的（旧的 lazy 行为）。"""
        w = self._w()
        tgt = next(iter(w.frontier_of("秦")))
        before = len(self._guards(w))
        ok, _msg = w._conquer(tgt[0], tgt[1], "秦", "测试")
        self.assertTrue(ok)
        after = self._guards(w)
        self.assertNotIn(tgt, after)
        self.assertEqual(len(after), before - 1)

    def test_add_nation_tiles_are_guardian_free(self):
        w = self._w()
        ok, _ = w.add_nation("齐")
        self.assertTrue(ok)
        self.assertFalse(self._guards(w) & set(w.tiles), "新加国的地块上不该有野人")

    def test_load_refills_guardians_for_old_save(self):
        """旧档（lazy 时代的野人）读档时一次性补齐全图。"""
        import tempfile
        from pathlib import Path as _P
        w = self._w()
        w.armies = [a for a in w.armies if a["owner"] != "野人"]
        w.guard_once.clear()
        with tempfile.TemporaryDirectory() as d:
            p = _P(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        wild = {(x, y) for x in range(20) for y in range(20) if (x, y) not in w2.tiles}
        self.assertEqual(self._guards(w2), wild)


if __name__ == "__main__":
    unittest.main()


class TestStandbySchedule(unittest.TestCase):
    """待登场国（匈奴）登场回合：每次读配置现算，不存存档。"""

    def _cfg(self, **over):
        hun = {"name": "林胡", "polity": "huns", "enable_turn": 120, "enable_turn_max": 150}
        hun.update(over)
        return {"nations": [{"name": "秦"}, {"name": "楚"}, hun]}

    def test_deterministic_per_seed_and_name(self):
        import mp_run
        w = mp.World(size=20, seed=20260910, nations=["秦", "楚"])
        a = mp_run.standby_schedule(self._cfg(), w)
        b = mp_run.standby_schedule(self._cfg(), w)
        self.assertEqual(a, b)                       # 同 seed 同配置 → 同回合
        self.assertIn(a["林胡"], range(120, 151))
        w2 = mp.World(size=20, seed=999, nations=["秦", "楚"])
        self.assertNotEqual(a, mp_run.standby_schedule(self._cfg(), w2))  # 换 seed 会变

    def test_config_change_takes_effect(self):
        import mp_run
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        self.assertIn(mp_run.standby_schedule(self._cfg(), w)["林胡"], range(120, 151))
        early = mp_run.standby_schedule(self._cfg(enable_turn=60, enable_turn_max=60), w)
        self.assertEqual(early["林胡"], 60)          # 改窗口立刻生效（不再被旧计划挡住）

    def test_spawned_nation_excluded_and_new_entries_picked_up(self):
        import mp_run
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.add_nation("林胡", "huns")
        self.assertNotIn("林胡", mp_run.standby_schedule(self._cfg(), w))
        cfg = self._cfg()
        cfg["nations"].append({"name": "楼烦", "polity": "huns", "enable_turn": 130})
        self.assertIn("楼烦", mp_run.standby_schedule(cfg, w))

    def test_removed_from_config_never_spawns(self):
        """从配置里删掉未登场的势力 → 永远不登场（旧机制下计划已写进存档，删了也没用）。"""
        import mp_run
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        self.assertEqual(mp_run.standby_schedule({"nations": [{"name": "秦"}, {"name": "楚"}]}, w), {})
        # 条目在但没 polity 标记 → 也不算待登场国
        cfg = {"nations": [{"name": "林胡", "enable_turn": 5}]}
        self.assertEqual(mp_run.standby_schedule(cfg, w), {})


class TestMapStaticResources(unittest.TestCase):
    """矿藏/耕地布局必须是**种子的静态属性**——和野人一样，是地图本身，不是"开图产物"。

    旧写法 `_new_tile` 里 `roll_resources(self.rng, terrain)` 用的是世界共享 RNG，
    于是同一格摇出什么，取决于轮到它时 RNG 已经走了多远（战斗掷骰、地块命名都在
    这条流上）。同一 seed 两局对不上，「种子可复现」形同虚设。
    """

    def test_same_tile_same_resources_regardless_of_rng_state(self):
        a = mp.World(size=16, seed=11, nations=["秦"])
        b = mp.World(size=16, seed=11, nations=["秦"])
        b.rng.random()                     # 模拟 b 那边先打过一仗 / 先拓过一格
        b.rng.randint(1, 6)
        for (x, y) in [(5, 5), (0, 0), (15, 3)]:
            self.assertEqual(a.tile_resources(x, y), b.tile_resources(x, y),
                             f"同 seed 的 ({x},{y}) 资源不该随 RNG 状态变")

    def test_same_tile_same_resources_regardless_of_expand_order(self):
        """真的按不同顺序建格，同格资源仍要一致（不只是 RNG 状态对齐）。"""
        a = mp.World(size=16, seed=23, nations=["秦"])
        b = mp.World(size=16, seed=23, nations=["秦"])
        ta = a._new_tile(9, 9, "秦")
        for _ in range(5):
            b._new_tile(0, 0, "秦")        # b 先建别的格子
        tb = b._new_tile(9, 9, "秦")
        self.assertEqual(ta["resources"], tb["resources"])
        self.assertEqual(ta["terrain"], tb["terrain"])

    def test_different_seed_different_layout(self):
        """别修过头变成"全图资源都一样"。"""
        a = mp.World(size=16, seed=11, nations=["秦"])
        b = mp.World(size=16, seed=12, nations=["秦"])
        spots = [(x, y) for x in range(6) for y in range(6)]
        self.assertNotEqual([a.tile_resources(*p) for p in spots],
                            [b.tile_resources(*p) for p in spots])
