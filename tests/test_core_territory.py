# -*- coding: utf-8 -*-
"""核心领土（`tile["core"]`）的**归属口径**。

它唯一的消费者是 `_return_core`：**同联盟、同战线**的盟友占了你的核心地 ⇒ 立即归还
（驻军原地不动）。所以 core 记错 = 该还的不还；core 漏记 = **永远不还**。

★ 2026-10-07 修的 bug（实盘用户看到"有些地方没核心"）：`_conquer` 里"无主地"**有两条路**，
一条盖 core 一条不盖——

    if t is None:      t = self._new_tile(x, y, by)   # 里面写着 "core": owner ⇒ 有核心
    elif old is None:  t["owner"] = by                # 只改 owner ⇒ core 留着 None

而 `tiles` 是**惰性物化**的（面板/地图/视野查一次就建）⇒ **谁看过那格**会改变它将来
有没有 core：同一种动作两种结果，且同 seed 两局可能对不上。根源是那行注释把两件事
混成了一件：「无主 ⇒ 没有失主、**不归还**」（对）写成了「无主 ⇒ **也不记归属**」（错）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402


def _world(nations=("秦", "楚", "燕")):
    w = mp.World(size=16, seed=11, nations=list(nations),
                 starts={"秦": (3, 3), "楚": (8, 8), "燕": (12, 12)})
    w.armies = []
    return w


def _unowned_tile(w, x: int, y: int, name: str = "废墟") -> dict:
    """物化一格并置成**无主**（亡国废墟那种：格子在、名字在、建筑在，就是没主人）。"""
    t = w._new_tile(x, y, "秦")
    t["owner"] = None
    t["core"] = None
    t["name"] = name
    w.tiles[(x, y)] = t
    return t


class TestConquerUnownedKeepsCore(unittest.TestCase):
    """★ 核心不变量：**无主地谁占下，核心就归谁**——两条路必须给同一个答案。"""

    def test_both_paths_give_the_same_core(self):
        w = _world()
        # A：那格**从没被物化过**（没有谁查过它）⇒ 走 `_new_tile`
        w.tiles.pop((6, 6), None)
        w._conquer(6, 6, "秦", "进驻")
        # B：那格**已存在但无主**（有人查过 / 前朝废墟）⇒ 走 `elif old is None`
        _unowned_tile(w, 7, 7)
        w._conquer(7, 7, "秦", "进驻")
        a, b = w.tiles[(6, 6)], w.tiles[(7, 7)]
        self.assertEqual(a["owner"], b["owner"], "前提：两块地都归了秦")
        self.assertEqual(a["core"], b["core"],
                         "同样占一块无主地，两条路给出的核心不一样——"
                         "而走哪条路取决于**那格之前有没有被谁查过**")
        self.assertEqual(b["core"], "秦", "占下的无主地该有核心")

    def test_core_follows_the_second_conqueror_too(self):
        """前朝废墟反复易手：每次无主时被占，核心跟着最新的占领者走。"""
        w = _world()
        _unowned_tile(w, 8, 8)
        w._conquer(8, 8, "秦", "进驻")
        self.assertEqual(w.tiles[(8, 8)]["core"], "秦")
        w.tiles[(8, 8)]["owner"] = None            # 又变回无主（模拟再次沦为废墟）
        w._conquer(8, 8, "楚", "进驻")
        self.assertEqual(w.tiles[(8, 8)]["core"], "楚")

    def test_taking_from_a_living_nation_leaves_core_alone(self):
        """**阴性对照**：占**活着的**他国的地，core 一律不动——留给战后
        `_snapshot_cores` 按实占重算（那是现有设计，别被这次修动到）。"""
        w = _world()
        t = w.tiles[(12, 12)] if (12, 12) in w.tiles else w._new_tile(12, 12, "燕")
        t["owner"] = "燕"
        t["core"] = "燕"
        w.tiles[(12, 12)] = t
        w._conquer(12, 12, "秦", "攻陷")
        self.assertEqual(t["owner"], "秦")
        self.assertEqual(t["core"], "燕", "战时易手不该当场改核心")


class TestCoreIsReturnedToAllies(unittest.TestCase):
    """core 的**用途**：同联盟同战线的盟友占了它 ⇒ 立即归还。整条机制此前没有测试。"""

    def _same_front_war(self, w):
        """秦（盟主）对燕宣战 ⇒ 全盟落在攻方（楚是盟员，同战线）。"""
        w.propose_bloc("秦", "北盟", ["楚"])
        p = w.proposals[-1]
        w.accept_pact("楚", p["id"])
        self.assertIsNotNone(w.bloc_of("秦"))
        w.declare_war("秦", "燕")                  # 在盟 → 转联盟投票
        v = w.votes[-1]
        w.cast_vote("秦", v["id"], True)
        w.cast_vote("楚", v["id"], True)
        self.assertTrue(w.wars, "前提：开战了")
        atk, dfs = w._war_sides(w.wars[0])
        self.assertIn("秦", atk)
        self.assertIn("楚", atk, "前提：秦楚同侧")

    def test_ally_taking_my_core_returns_it(self):
        w = _world()
        self._same_front_war(w)
        t = w.tiles[(12, 12)] if (12, 12) in w.tiles else w._new_tile(12, 12, "燕")
        t["owner"], t["core"] = "燕", "秦"          # 燕 手里一格，核心是盟友秦的
        w.tiles[(12, 12)] = t
        w._conquer(12, 12, "楚", "攻陷")
        self.assertEqual(w.tiles[(12, 12)]["owner"], "秦", "盟友的核心地没被归还")
        self.assertEqual(w.tiles[(12, 12)]["core"], "秦")

    def test_non_ally_keeps_it(self):
        """**阴性对照**：不是盟友（没联盟）就不归还——核心只是"该是谁的"，不是护身符。"""
        w = _world()
        t = w._new_tile(12, 12, "燕")
        t["owner"], t["core"] = "燕", "秦"
        w.tiles[(12, 12)] = t
        w._conquer(12, 12, "楚", "攻陷")
        self.assertEqual(w.tiles[(12, 12)]["owner"], "楚")


if __name__ == "__main__":
    unittest.main()
