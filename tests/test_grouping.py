# -*- coding: utf-8 -*-
"""编组守卫：**全局最优**、**三指标**、**只在目标消失时重编**、**永远有解**。

对着 `docs/v11编组模型.md` 逐条钉（每条都对着旧做法或本模块踩过的坑）：

  1. **全局最优** —— 小规模与手算/穷举对拍（不是"我觉得对"）；
  2. **三指标** —— 组内距离进代价、全体到目标用 `max`（引擎的 atk 是原子的）、
     **人数由目标定**（`n_j` 是判定式算的，不是常数）；
  3. **状态机** —— 目标消失 ⇒ 那一组解散；有目标的军不被改派；交战中的军钉在脚下；
  4. **永远有解** —— 每支军都能拿到目标（"待命"不存在）。
     用户 2026-09-15：「全体无解是求解器的问题，而不是应该兜底」⇒ 模型是
     **指派**（每军认领一个目标、目标够 `n_j` 支才打得下来），结构上必然有解。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ruleai.v11 import grouping  # noqa: E402
import mp  # noqa: E402
from ruleai.v11 import pathfind  # noqa: E402

# 一块"无主野地"目标：在自家块（2..11）之外、(11,·) 的邻格之内 ⇒ 看得见、又可攻
WILD = (12, 6)


def mask_of(w) -> frozenset:
    return pathfind.vision_mask(w, "秦")


class _Base(unittest.TestCase):
    def setUp(self):
        grouping.clear()
        self.addCleanup(grouping.clear)

    def _world(self, size: int = 20) -> mp.World:
        w = mp.World(size=size, seed=5, nations=["秦", "楚"],
                     starts={"秦": (5, 5), "楚": (18, 18)})
        w.armies = []
        for x in range(2, 12):
            for y in range(2, 12):
                t = w._new_tile(x, y, "秦")
                t["terrain"] = "平原"
                w.tiles[(x, y)] = t
        return w

    def _army(self, w, x, y, aid, hp=100, kind="步"):
        a = {"id": aid, "gid": aid, "name": f"秦·{kind}{aid}", "type": kind,
             "hp": hp, "x": x, "y": y, "owner": "秦", "moved_turn": -1, "engaged": False}
        w.armies.append(a)
        return a

    def _cand(self, w, cell, need):
        return grouping.Candidate(cell, need, 0, True, True, "平原", ())

    def _solve(self, w, armies, cands, cap=10 ** 6):
        return grouping._solve(w, "秦", cands, armies, mask_of(w), {}, cap, 4)


class TestSolverIsGlobal(_Base):
    """① 全局最优（与手算对拍）② 永远有解。"""

    def test_matches_hand_computed_optimum(self):
        """3 支军、两个目标（A 要 1 支、B 要 2 支）—— 三种划分手算得出来，必须挑中最优。

        布点：A=(5,2)、B=(5,9)；军 1(5,4)、2(5,7)、3(5,8)。三种划分：
          A={1} → reach(A)=2；B={2,3} reach=max(2,1)=2、spread=1 ⇒ **合计 5**  ← 最优
          A={2} → reach(A)=3；B={1,3} reach=5、spread=3           ⇒ 合计 11
          A={3} → reach(A)=4；B={1,2} reach=5、spread=3           ⇒ 合计 12
        """
        w = self._world()
        armies = [self._army(w, 5, 4, 1), self._army(w, 5, 7, 2), self._army(w, 5, 8, 3)]
        picked, exact = self._solve(w, armies, [self._cand(w, (5, 2), 1),
                                               self._cand(w, (5, 9), 2)])
        self.assertTrue(exact)
        self.assertEqual(picked, {1: (5, 2), 2: (5, 9), 3: (5, 9)},
                         "求解器没挑中手算的最优划分 ⇒ 不是全局最优")
        by: dict = {}
        for aid, cell in picked.items():
            by.setdefault(cell, []).append(aid)
        total = 0.0
        for cell, ids in by.items():
            mem = [a for a in armies if a["id"] in ids]
            total += grouping._spread(mem) + max(
                max(abs(a["x"] - cell[0]), abs(a["y"] - cell[1])) for a in mem)
        self.assertAlmostEqual(total, 5.0, msg="代价公式（spread + reach）与手算不符")

    def test_always_covers_every_army(self):
        """★ 永远有解：任意局面下，每支军都拿到目标（用户：无解是求解器的问题）。

        构造最容易无解的局面：目标**都要 2 支**（有守军），军队数是奇数
        —— "每组恰好 n_j 人"的写法在这里必然无解，指派写法必须有解。
        """
        for n in (3, 5, 7):
            with self.subTest(armies=n):
                grouping.clear()
                w = self._world()
                armies = [self._army(w, 6, 6, i) for i in range(1, n + 1)]
                cands = [self._cand(w, c, 2) for c in ((12, 3), (12, 6), (12, 9), (3, 12))]
                picked, exact = self._solve(w, armies, cands)
                self.assertTrue(exact)
                self.assertEqual(sorted(picked), list(range(1, n + 1)),
                                 f"{n} 支军里有军没拿到目标 ⇒ 求解器没给出解")

    def test_beats_first_come_greedy(self):
        """★ 全局解不劣于"按目标逐个取最近的"（旧 `formation.allocate` 的抢兵病）。"""
        w = self._world()
        armies = [self._army(w, 5, 4, 1), self._army(w, 5, 5, 2), self._army(w, 5, 9, 3)]
        picked, _ = self._solve(w, armies, [self._cand(w, (5, 2), 1),
                                            self._cand(w, (5, 9), 2)])

        def cost_of(assign: dict) -> float:
            by: dict = {}
            for aid, cell in assign.items():
                by.setdefault(cell, []).append(aid)
            tot = 0.0
            for cell, ids in by.items():
                mem = [a for a in armies if a["id"] in ids]
                tot += grouping._spread(mem) + max(
                    max(abs(a["x"] - cell[0]), abs(a["y"] - cell[1])) for a in mem)
            return tot

        greedy = {1: (5, 2), 2: (5, 9), 3: (5, 9)}      # 先 A、A 取最近的那支
        self.assertLessEqual(cost_of(picked), cost_of(greedy) + 1e-9,
                             "全局解不该比先到先得还差")


class TestThreeMetrics(_Base):
    """② 三指标。"""

    def test_group_size_comes_from_the_target(self):
        """空目标 1 支、有野人守的目标 2 支 —— **判定式算的，不是常数**。"""
        w = self._world()
        [self._army(w, 6, 6, i) for i in (1, 2, 3, 4)]
        t2 = w._new_tile(*WILD, "秦")
        t2["owner"] = None                           # 无主野地 + 野人守卫
        t2["terrain"] = "平原"
        w.tiles[WILD] = t2
        w.armies.append({"id": 99, "name": "野人99", "hp": 100, "x": WILD[0], "y": WILD[1],
                         "owner": "野人", "moved_turn": -1, "engaged": False})
        cands = grouping.candidates(w, "秦", mask_of(w), w.nation_armies("秦"),
                                   radius=10, need_cap=12, rounds_cap=12, cache={})
        need = {c.cell: c.need for c in cands}
        self.assertEqual(need.get((12, 3)), 1, "无主空格：1 支就够（走进去即占）")
        self.assertEqual(need.get(WILD), 2, "满血野人：2 支（判定式算出来的）")

    def test_reach_is_the_slowest_member(self):
        """组的到达时刻 = **最慢**那支（引擎的 atk 原子：一支到不了整通全废）。"""
        w = self._world()
        near = self._army(w, 6, 6, 1)
        far = self._army(w, 10, 6, 2)
        cand = self._cand(w, (6, 4), 1)
        cost = grouping._atom_cost(w, "秦", [near, far], cand, mask_of(w), {}, 0.0)
        self.assertGreaterEqual(cost, max(abs(10 - 6), abs(6 - 4)),
                                "两支一起走时，代价按最慢那支算")

    def test_spread_enters_the_cost(self):
        """同样两支军，抱团的组代价更低（组内距离是目标函数的一项）。"""
        w = self._world()
        near = [self._army(w, 6, 6, 1), self._army(w, 6, 7, 2)]
        far = [self._army(w, 3, 3, 3), self._army(w, 10, 10, 4)]
        self.assertLess(grouping._spread(near), grouping._spread(far))

    def test_shortfall_is_penalized(self):
        """派的人不够 `n_j` ⇒ 目标函数里吃亏（那一仗打不下来）。"""
        w = self._world()
        a = self._army(w, 6, 6, 1)
        cand = self._cand(w, (6, 4), 2)
        short = grouping._atom_cost(w, "秦", [a], cand, mask_of(w), {}, 100.0)
        full = grouping._spread([a]) + grouping._army_cost(w, "秦", a, (6, 4), mask_of(w), {})
        self.assertAlmostEqual(short, full + 100.0, msg="缺员该按缺几个记罚")


class TestStateMachine(_Base):
    """③ 状态机：只在目标消失时重编，且只重编无目标的军。"""

    def test_group_keeps_target_until_it_vanishes(self):
        w = self._world()
        a = self._army(w, 6, 6, 1)
        grouping._STATE["秦"] = {1: WILD}
        freed = grouping.refresh(w, "秦", mask_of(w), [a])
        self.assertEqual(freed, [], "目标还在（无主野地、可攻）⇒ 不该解散")
        self.assertEqual(grouping.target_of("秦", 1), WILD)

    def test_target_captured_frees_the_group(self):
        w = self._world()
        a = self._army(w, 6, 6, 1)
        t = w._new_tile(*WILD, "秦")                  # 已经变成自家的了
        t["terrain"] = "平原"
        w.tiles[WILD] = t
        grouping._STATE["秦"] = {1: WILD}
        self.assertEqual(grouping.refresh(w, "秦", mask_of(w), [a]), [1],
                         "目标变自家的 ⇒ 该解散")
        self.assertIsNone(grouping.target_of("秦", 1))

    def test_target_becomes_neutral_frees_the_group(self):
        w = self._world()
        a = self._army(w, 6, 6, 1)
        t = w._new_tile(*WILD, "楚")
        t["terrain"] = "平原"
        w.tiles[WILD] = t                            # 楚的地，但没宣战 ⇒ 不可攻
        grouping._STATE["秦"] = {1: WILD}
        self.assertEqual(grouping.refresh(w, "秦", mask_of(w), [a]), [1],
                         "变成不可攻（中立国领土）⇒ 解散")

    def test_engaged_army_is_pinned_to_its_tile(self):
        w = self._world()
        a = self._army(w, 6, 6, 1)
        a["engaged"] = True
        grouping._STATE["秦"] = {1: (7, 7)}
        grouping.refresh(w, "秦", mask_of(w), [a])
        self.assertEqual(grouping.target_of("秦", 1), (6, 6), "交战中：目标钉在脚下那格")

    def test_regroup_only_touches_targetless_armies(self):
        """有目标的军**不被改派**：重编只覆盖无目标的军。"""
        w = self._world()
        a1 = self._army(w, 5, 5, 1)
        a2 = self._army(w, 5, 6, 2)
        grouping._STATE["秦"] = {1: WILD}
        grouping.regroup(w, "秦", mask_of(w), [a1, a2],
                         radius=10, need_cap=12, rounds_cap=12, cache={})
        self.assertEqual(grouping.target_of("秦", 1), WILD, "有目标的军不许被改派")
        self.assertIsNotNone(grouping.target_of("秦", 2), "无目标的军必须拿到目标")

    def test_regroup_gives_everyone_a_target(self):
        w = self._world()
        for i in range(6):
            self._army(w, 3 + i, 3, i + 1)
        grouping.regroup(w, "秦", mask_of(w), w.nation_armies("秦"),
                         radius=10, need_cap=12, rounds_cap=12, cache={})
        st = grouping.targets_of("秦")
        self.assertEqual(sorted(st), [a["id"] for a in w.nation_armies("秦")],
                         "编组后有军没目标")
        for cell in st.values():
            self.assertIn(cell, set(mask_of(w)), "目标必须在看得见的范围内")


if __name__ == "__main__":
    unittest.main()