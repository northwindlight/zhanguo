# -*- coding: utf-8 -*-
"""v11 难度评估守卫：`combat.py` 的数字必须是**引擎的数字**，不是长得像的数字。

钉四件事：

  1. **与引擎逐字对拍** —— 把子按期望钉死（`_die` 返回 mod 0），跑一场真战斗，
     `combat.simulate` 预测的掉血必须与引擎实际结算**完全相同**。这一条是关键：
     自己重写一遍伤害公式（v10 就是重写的）迟早会与引擎漂开，而"漂开"这件事
     没有任何别的测试抓得到。
  2. **减伤方向不能反** —— 减伤归**守方**（进攻方在格子上，守方不在）。
  3. **按实际兵力估值** —— 民兵（攻 20）要比步兵（攻 50）多派几支；v10 用
     "全军最大值"估，三个民兵和一个步兵在它眼里一样强。
  4. **看得见才算数** —— 看不见的守军按一支满血野人**保守**估；看得见又没守军
     = **真空**（走进去就占地，只要 1 支）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ruleai import combat  # noqa: E402
import mp  # noqa: E402
from balance import TERRAIN_STATS, UNIT_TYPES  # noqa: E402
from game import building_effect  # noqa: E402

CAP, RDS = 12, 12


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
        return w

    def _army(self, w, kind: str, x: int, y: int, owner: str = "秦", aid: int = 1,
              hp: int | None = None) -> dict:
        from game import unit_max_hp
        a = {"id": aid, "gid": aid, "name": f"{owner}·{kind}军{aid}", "type": kind,
             "hp": unit_max_hp({"type": kind}) if hp is None else hp,
             "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": False}
        w.armies.append(a)
        return a

    def _guard(self, w, x: int, y: int, hp: int = 100) -> dict:
        """野人守卫（**没有 `type` 键**，与引擎 `_spawn_guardian` 一致）。"""
        g = {"id": 999, "gid": 999, "name": "野人999", "hp": hp, "x": x, "y": y,
             "owner": "野人", "moved_turn": -1, "engaged": False}
        w.armies.append(g)
        return g

    def _ter(self, w, x, y, terrain: str) -> None:
        w.tiles[(x, y)]["terrain"] = terrain

    def _wild(self, w, x: int, y: int, terrain: str = "平原", guard_hp: int = 100):
        """(x,y) 变成**无主野地 + 一支野人守卫**：这才是有守军的目标。

        ★ 必须无主：引擎里野人**只守无主格**（`_defs_at`：`owner is None` 才算），
          在有主的地块上放野人不会参战 —— 第一版用例就栽在这儿（评估全成了"空目标"）。
        """
        t = w._new_tile(x, y, "秦")
        t["owner"] = None
        t["terrain"] = terrain
        w.tiles[(x, y)] = t
        return self._guard(w, x, y, hp=guard_hp)

    def _inf(self, n: int, atk: int | None = None) -> list[dict]:
        return [{"id": i, "type": "步", "hp": 100, "x": 0, "y": 0} for i in range(1, n + 1)]


class TestAgainstEngine(_Base):
    """① 与引擎逐字对拍（骰子钉成期望值）。"""

    def _battle(self, w, cell, n: int, terrain: str, rounds: int = 1):
        """真跑一场：n 支步兵 atk 一格无主野地（野人守军）→ `(守军掉血, 我方总掉血)`。

        ★ 只跑**战斗阶段**（`_resolve_battles`），不跑整个 `resolve_turn`：
          后者在战斗之后还有**非交战军队每回合 +25 回血**（`ARMY_HEAL_PER_TURN`），
          会把"这场仗掉了多少血"抹平（第一版用例就是这么错的：掉了 100 只量到 50）。
          要跟 `simulate` 对拍的是**战斗本身**。
        """
        x, y = cell
        g = self._wild(w, x, y, terrain)
        atk = [self._army(w, "步", x - 1, y, aid=i) for i in range(1, n + 1)]
        w._die = lambda: (1, 0)                       # 骰子钉成期望（mod 0）
        ok, msg = w.attack("秦", [a["id"] for a in atk], x, y)
        self.assertTrue(ok, msg)
        hp_before = {a["id"]: a["hp"] for a in atk}
        g_hp0 = g["hp"]
        for _ in range(rounds):
            if w.owned_by(x, y) == "秦":
                break                                 # 守军已清、地已拿下 ⇒ 战斗结束
            w._resolve_battles()
        return (g_hp0 - max(0, g["hp"]),
                sum(hp_before[a["id"]] - max(0, a["hp"]) for a in atk))

    def test_simulate_first_round_matches_engine_exactly(self):
        """第一轮的**双方掉血**逐一对上（守方吃地形、我方不吃）。

        ★ 这一条是整个 v11 里最要紧的测试：`combat.py` 的伤害公式若是"自己重写的一版"
          （v10 就是），它与引擎漂开时**没有任何别的测试抓得到**。
          这里把骰子钉成期望值（`_die` 返回 mod 0），让引擎的随机结算变成确定值，
          再与 `simulate` 逐点对拍。
        """
        for terrain in ("平原", "森林", "丘陵", "山地"):
            with self.subTest(terrain=terrain):
                w = self._world()
                self._wild(w, 6, 5, terrain)
                d_pct = w._defense_pct(6, 5, "野人")
                pred = combat.simulate(w, [(100, 50)], [(100, 50)], d_pct, rounds_cap=1)
                self.assertFalse(pred[0], "1 支步兵一轮打不完 100 血（50 伤害）")
                w2 = self._world()
                g_loss, my_loss = self._battle(w2, (6, 5), 1, terrain)
                self.assertEqual(pred[2], my_loss, f"{terrain}: 我方掉血与引擎不符")
                dmg = max(1, round(w2._round_damage(w2._combat_power(50, 0), 0)
                                   * (100 - d_pct) / 100))
                self.assertEqual(g_loss, dmg, f"{terrain}: 守军掉血与引擎不符")

    def test_simulate_multi_round_matches_engine(self):
        """打满多轮也对得上：2 支步兵 vs 山地野人（减伤 50% ⇒ 每轮只打掉 50 血 ⇒ 2 轮）。"""
        w = self._world()
        self._wild(w, 6, 5, "山地")
        d_pct = w._defense_pct(6, 5, "野人")
        pred = combat.simulate(w, [(100, 50), (100, 50)], [(100, 50)], d_pct, rounds_cap=12)
        self.assertTrue(pred[0])
        self.assertEqual(pred[1], 2, "山地减伤 50% ⇒ 50 伤害/轮 ⇒ 2 轮")
        w2 = self._world()
        g_loss, my_loss = self._battle(w2, (6, 5), 2, "山地", rounds=2)
        self.assertEqual(g_loss, 100, "野人守军该在两轮内被打光")
        self.assertEqual(my_loss, pred[2], "多轮累计掉血也要与引擎一致")


class TestDefensePct(_Base):
    """② 减伤口径与方向。"""

    def test_matches_engine_for_all_terrains(self):
        w = self._world()
        for ter, st in sorted(TERRAIN_STATS.items()):
            with self.subTest(terrain=ter):
                self._ter(w, 6, 5, ter)
                self.assertEqual(combat.defense_pct(w, 6, 5, None, visible=True),
                                 w._defense_pct(6, 5, None))

    def test_castle_counts_only_for_owner(self):
        w = self._world()
        self._ter(w, 6, 5, "丘陵")
        w.tiles[(6, 5)]["buildings"]["城堡"] = 3
        per = building_effect("城堡", "defense_per_level")
        self.assertEqual(combat.defense_pct(w, 6, 5, "秦", visible=True),
                         w._defense_pct(6, 5, "秦"))
        self.assertGreater(combat.defense_pct(w, 6, 5, "秦", visible=True),
                           combat.defense_pct(w, 6, 5, "楚", visible=True),
                           "城堡只在地主名下才算（别人占了这块地就拿不到这个加成）")

    def test_unseen_is_conservative(self):
        w = self._world()
        worst = max(st.get("defense", 0) for st in TERRAIN_STATS.values())
        self.assertEqual(combat.defense_pct(w, 6, 5, None, visible=False), worst,
                         "看不见 ⇒ 按当下最保守的地形减伤估，且城堡按 0 算")


class TestAssess(_Base):
    """③④ 按实际兵力估、看得见才算数。"""

    def test_guardian_on_plains_needs_two(self):
        """1 支步兵打不赢满血野人（同归于尽算输），2 支一轮拿下 —— 这就是 MIN_SQUAD=2 的来处。"""
        w = self._world()
        self._wild(w, 6, 5, "平原")
        units = self._inf(2)
        d1 = combat.assess(w, "秦", (6, 5), units[:1], visible=True, need_cap=CAP, rounds_cap=RDS)
        d2 = combat.assess(w, "秦", (6, 5), units, visible=True, need_cap=CAP, rounds_cap=RDS)
        self.assertFalse(d1.winnable, "单兵打野人：同归于尽 ⇒ 算输")
        self.assertTrue(d2.winnable)
        self.assertEqual(d2.need, 2)
        self.assertEqual(d2.rounds, 1)

    def test_defense_and_castle_raise_cost(self):
        w = self._world()
        units = self._inf(4)
        dmg = {}
        for ter in ("平原", "森林", "丘陵", "山地"):
            self._wild(w, 6, 5, ter)
            dmg[ter] = combat.assess(w, "秦", (6, 5), units, visible=True,
                                     need_cap=CAP, rounds_cap=RDS)
        for a, b in (("平原", "森林"), ("森林", "丘陵"), ("丘陵", "山地")):
            self.assertLessEqual(dmg[a].rounds, dmg[b].rounds, f"{a} 不该比 {b} 更难打")
        self.assertLess(dmg["平原"].rounds, dmg["山地"].rounds, "山地减伤 50% ⇒ 明显更慢")

    def test_castle_raises_cost(self):
        w = self._world()
        units = self._inf(4)
        self._wild(w, 6, 5, "平原")
        base = combat.assess(w, "秦", (6, 5), units, visible=True, need_cap=CAP, rounds_cap=RDS)
        w.armies = [a for a in w.armies if a["owner"] != "野人"]      # 换成敌国的城
        t = w.tiles[(6, 5)]
        t["owner"] = "楚"
        t["buildings"]["城堡"] = 5
        w.declare_war("秦", "楚")
        self._army(w, "步", 6, 5, owner="楚", aid=50)
        with_castle = combat.assess(w, "秦", (6, 5), units, visible=True,
                                    need_cap=CAP, rounds_cap=RDS)
        self.assertGreater(with_castle.rounds + with_castle.need,
                           base.rounds + base.need,
                           "城堡 L5（+50% 减伤）必须让攻城更贵")

    def test_militia_needs_more_than_infantry(self):
        """v10 用"全军最大 atk"估 ⇒ 民兵与步兵在它眼里一样强。这里必须不同。"""
        w = self._world()
        self._wild(w, 6, 5, "平原", guard_hp=200)
        inf = [{"id": i, "type": "步", "hp": 100, "x": 0, "y": 0} for i in range(1, 5)]
        mil = [{"id": i, "type": "民", "hp": 80, "x": 0, "y": 0} for i in range(1, 5)]
        d_inf = combat.assess(w, "秦", (6, 5), inf, visible=True, need_cap=CAP, rounds_cap=RDS)
        d_mil = combat.assess(w, "秦", (6, 5), mil, visible=True, need_cap=CAP, rounds_cap=RDS)
        self.assertGreater(d_mil.need, d_inf.need, "民兵攻 20、步兵攻 50 ⇒ 民兵要更多支")

    def test_visible_and_empty_is_truly_empty(self):
        w = self._world()
        d = combat.assess(w, "秦", (6, 5), self._inf(1), visible=True,
                          need_cap=CAP, rounds_cap=RDS)
        self.assertTrue(d.empty and d.winnable)
        self.assertEqual(d.need, 1, "看得见又没守军 ⇒ 走进去就占地，1 支就够")

    def test_invisible_defender_is_assumed_guardian(self):
        w = self._world()
        d = combat.assess(w, "秦", (6, 5), self._inf(1), visible=False,
                          need_cap=CAP, rounds_cap=RDS)
        self.assertFalse(d.empty)
        self.assertEqual(d.def_hp, UNIT_TYPES["步"]["hp"], "看不见按一支满血野人估")

    def test_unknown_defender_ignores_map_armies(self):
        """野人样本**一个都不在图上**时，估值也不许变（不许扫全图找样本）。"""
        w = self._world()
        a = combat.assess(w, "秦", (6, 5), self._inf(2), visible=False,
                          need_cap=CAP, rounds_cap=RDS)
        w.armies.append({"id": 5, "name": "野人5", "hp": 7, "x": 1, "y": 1,
                         "owner": "野人", "moved_turn": -1, "engaged": False})
        b = combat.assess(w, "秦", (6, 5), self._inf(2), visible=False,
                          need_cap=CAP, rounds_cap=RDS)
        self.assertEqual(a, b, "估值只该看兵种表，不该被某支残血野人的血量带偏")


class TestReadsLive(_Base):
    """⑤ 抗抖：引擎数值就地改 ⇒ 结论必须跟着变（v9 抄死在常量里的正是这个）。"""

    def test_terrain_table_read_live(self):
        w = self._world()
        self._wild(w, 6, 5, "平原", guard_hp=200)
        units = self._inf(4)
        before = combat.assess(w, "秦", (6, 5), units, visible=True, need_cap=CAP, rounds_cap=RDS)
        old = TERRAIN_STATS["平原"]["defense"]
        try:
            TERRAIN_STATS["平原"]["defense"] = 60          # 平原突然易守难攻
            after = combat.assess(w, "秦", (6, 5), units, visible=True,
                                  need_cap=CAP, rounds_cap=RDS)
        finally:
            TERRAIN_STATS["平原"]["defense"] = old
        self.assertGreater(after.rounds + after.need, before.rounds + before.need,
                           "地形表改了，评估必须跟着变（不许抄死）")

    def test_unit_table_read_live(self):
        w = self._world()
        self._wild(w, 6, 5, "平原", guard_hp=200)
        units = self._inf(2)
        before = combat.assess(w, "秦", (6, 5), units, visible=True, need_cap=CAP, rounds_cap=RDS)
        old = UNIT_TYPES["步"]["atk"]
        try:
            UNIT_TYPES["步"]["atk"] = 5                    # 步兵突然很弱
            after = combat.assess(w, "秦", (6, 5), units, visible=True,
                                  need_cap=CAP, rounds_cap=RDS)
        finally:
            UNIT_TYPES["步"]["atk"] = old
        self.assertGreater(after.need, before.need, "兵种表改了，评估必须跟着变")

    def test_need_cap_bounds_the_search(self):
        w = self._world()
        self._wild(w, 6, 5, "平原", guard_hp=200)
        d = combat.assess(w, "秦", (6, 5), self._inf(30), visible=True, need_cap=2, rounds_cap=RDS)
        self.assertLessEqual(d.need, 3, "need_cap 是搜索上限（防长局爆算）")


if __name__ == "__main__":
    unittest.main()