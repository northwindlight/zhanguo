# -*- coding: utf-8 -*-
"""【国祚警报】：自己**空着的市政厅**每回合点名。

来由（用户 2026-10-07，实盘秦魏之战）：秦把国祚连同**全部战争工业**（补给厂×7 /
装备厂×3 / 兵营 / 能源厂 / 市政厅）堆在晴桥(4,12)一格，又为一场"闪击战"把兵全压到
前线——自家南翼一兵未留。魏一支步军 `atk` 进空格：**敌人为 0、零战斗直接进驻**，
秦当场丢了工业首都；那两格能源厂一掉**电网停摆**，补给厂/装备厂/兵营/市政厅
全部停产（耗电建筑是全有全无的）。

手册讲透了「拔厅是唯一得分动作」，却从没讲**己方的厅也是对手唯一的得分动作**；
而空厅最阴的地方在于：**在被走进来之前，什么事件都不会发生**——`_fmt_alerts`
那种"上次行动以来的变更"口径永远抓不到它。所以这条必须常驻。

口径（用户原话）：「**不是常驻[多少兵]，是你应该时刻保证你的后方安全，
被换家意味着战略完全失败**」⇒ **只在一兵都没有时响**。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import mp_ai  # noqa: E402


def _world() -> mp.World:
    """空场子：**把开局白送的核心市政厅清掉**（否则每个国家一开始就有一座厅，
    用例里的"有没有洞"全被那条底噪盖住）。"""
    w = mp.World(size=16, seed=23, nations=["秦", "楚"],
                 starts={"秦": (4, 4), "楚": (12, 12)})
    w.armies = []
    for t in w.tiles.values():
        t["buildings"]["市政厅"] = 0
    return w


def _tile(w, x: int, y: int, owner: str, name: str, hall: int = 0,
          pending_hall: int = 0) -> dict:
    """物化一格（引擎的 `tiles` 是**惰性**的，不建就没有这格）。"""
    t = w._new_tile(x, y, owner)
    t["owner"] = owner
    t["name"] = name
    if hall:
        t["buildings"]["市政厅"] = hall
    if pending_hall:
        t["pending"] = {"市政厅": pending_hall}
    w.tiles[(x, y)] = t
    return t


def _army(w, x: int, y: int, owner: str, aid: int) -> dict:
    a = {"id": aid, "gid": aid, "name": f"{owner}·步{aid}军", "type": "步", "hp": 100,
         "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": False}
    w.armies.append(a)
    return a


class TestHallAlert(unittest.TestCase):
    def test_naked_hall_is_named_with_coords_and_count(self):
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        s = mp_ai._fmt_hall_alert(w, "秦")
        self.assertIsNotNone(s, "空着的厅必须报警")
        self.assertIn("【国祚警报】", s)
        self.assertIn("1 座", s)
        self.assertIn("晴桥", s)
        self.assertIn("(5,13)", s)                 # 1-based 呈现（引擎内部是 4,12）
        self.assertIn("零战斗直接进驻", s)          # 讲清"为什么白送"
        self.assertIn("被换家意味着战略完全失败", s)  # 用户口径原话

    def test_garrisoned_hall_is_silent(self):
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        _army(w, 4, 12, "秦", 1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_army_back_and_the_alert_clears(self):
        """回兵即消——这是"常驻直到你补上"的行为，不是历史事件。"""
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        self.assertIsNotNone(mp_ai._fmt_hall_alert(w, "秦"))
        _army(w, 4, 12, "秦", 1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_only_the_naked_ones_are_listed(self):
        """多座厅：有兵的那座不报，没兵的照报（别把"有守军"也算成洞）。"""
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        _tile(w, 3, 4, "秦", "雍城", hall=1)
        _army(w, 3, 4, "秦", 1)
        s = mp_ai._fmt_hall_alert(w, "秦")
        self.assertIn("晴桥", s)
        self.assertNotIn("雍城", s)
        self.assertIn("1 座", s)

    def test_others_army_is_not_a_garrison(self):
        """站在我厅上的**别人**的兵（或野人）不算驻军——那格照样是白送的。"""
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        _army(w, 4, 12, "楚", 1)
        self.assertIsNotNone(mp_ai._fmt_hall_alert(w, "秦"),
                             "敌军的兵不能替我守厅")

    def test_pending_hall_is_not_a_guozuo_yet(self):
        """**在建**的厅还不是国祚（口径同 `nation_building_count`）⇒ 不报。"""
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", pending_hall=1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_no_hall_no_alert(self):
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥")
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_other_nations_hall_is_not_my_business(self):
        w = _world()
        _tile(w, 12, 12, "楚", "郢", hall=1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_list_is_capped_but_count_is_whole(self):
        w = _world()
        for i in range(6):
            _tile(w, 2 + i, 2, "秦", f"城{i}", hall=1)
        s = mp_ai._fmt_hall_alert(w, "秦")
        self.assertIn("6 座", s)
        self.assertIn("等 6 处", s)
        self.assertLessEqual(len(s.splitlines()[0]), 120, "标题行要有界")


class TestWiring(unittest.TestCase):
    def test_alert_sits_above_the_rest_of_the_state_panel(self):
        """钉在状态面板最前：在【威胁】之后、【国力】之前（和领地警报同一档紧迫度）。"""
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        state = mp_ai.full_state(w, "秦")
        self.assertIn("【国祚警报】", state)
        self.assertLess(state.index("【国祚警报】"), state.index("【国力】"))
        self.assertLess(state.index("【威胁】"), state.index("【国祚警报】"))

    def test_no_hall_no_block_in_the_state_panel(self):
        """没洞 ⇒ 整段不出现（不白占 token）。"""
        w = _world()
        _tile(w, 4, 12, "秦", "晴桥", hall=1)
        _army(w, 4, 12, "秦", 1)
        self.assertNotIn("【国祚警报】", mp_ai.full_state(w, "秦"))


if __name__ == "__main__":
    unittest.main()
