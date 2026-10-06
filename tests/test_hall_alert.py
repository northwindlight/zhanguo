# -*- coding: utf-8 -*-
"""【国祚警报】：**后方安全假设已经破了**的时候点名——不是"哪座厅没有兵"。

来由（用户 2026-10-07，实盘秦魏之战）：秦把国祚连同全部战争工业堆在晴桥一格，
又为一场"闪击战"把兵全压到前线，自家南翼一兵未留。魏一支步军 `atk` 进空格：
**敌人为 0、零战斗直接进驻**，秦当场丢了工业首都，电网跟着停摆。

★★ 触发口径被用户当场驳回改了两次，这三句就是本文件要钉住的东西：

  ① 「**不是不堆到一格，不集中意味着你根本无法防守**」
     ⇒ 集中是对的；不能劝它摊开。
  ② 「**不是更要有兵，是你的安全假设必须成立**」
     ⇒ 警报的语义是"假设**已经**破了"，不是"你该加兵"。
  ③ 「**而且不是什么常驻，为什么要把根本不可能被进攻的地守一堆军队，等于学燕，
     被运动战早晚打烂**」
     ⇒ **远处没有敌人的空厅，一律不报**——要求每座厅常驻，是把兵钉死在家里，
       正面挨运动战。（这一条是第一版的反面：第一版"没驻军就报"，是错的。）

所以判据是：**那格没人守 ∧ 看得见的敌军本回合就够得着**（`World._reachable(
for_attack=True)`，用引擎自己的移动规则，不另抄一份相邻判定）。

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

HALL = (5, 5)          # 秦的核心格（开局自带，四周视野成立）
NEXT_TO_HALL = (6, 5)  # 与厅相邻的一格


def _world() -> mp.World:
    """空场子：**把开局白送的核心市政厅清掉**（否则每个国家一开始就有座厅，
    用例里的"有没有洞"全被那条底噪盖住）。"""
    w = mp.World(size=16, seed=23, nations=["秦", "楚"],
                 starts={"秦": (4, 4), "楚": (12, 12)})
    w.armies = []
    for t in w.tiles.values():
        t["buildings"]["市政厅"] = 0
    return w


def _tile(w, x: int, y: int, owner: str | None, name: str = "—", hall: int = 0,
          pending_hall: int = 0, terrain: str = "平原") -> dict:
    """物化一格（引擎的 `tiles` 是**惰性**的，不建就没有这格）。"""
    t = w._new_tile(x, y, owner or "秦")
    t["owner"] = owner
    t["name"] = name
    t["terrain"] = terrain
    if hall:
        t["buildings"]["市政厅"] = hall
    if pending_hall:
        t["pending"] = {"市政厅": pending_hall}
    w.tiles[(x, y)] = t
    return t


def _army(w, x: int, y: int, owner: str, aid: int, kind: str = "步") -> dict:
    a = {"id": aid, "gid": aid, "name": f"{owner}·{kind}{aid}军", "type": kind, "hp": 100,
         "x": x, "y": y, "owner": owner, "moved_turn": -1, "engaged": False}
    w.armies.append(a)
    return a


def _at_war(w, a: str, b: str) -> None:
    w.wars = [{"id": 1, "atk": a, "def": b, "followers": [], "turn": 1}]


def _arena(w, hall: bool = True, pending: bool = False) -> None:
    """秦的厅在 (5,5)，旁边 (6,5) 是**无主野地**（敌军的落脚点）。"""
    if hall or pending:
        _tile(w, *HALL, "秦", "晴桥", hall=1 if hall else 0,
              pending_hall=1 if pending else 0)
    _tile(w, *NEXT_TO_HALL, None, terrain="平原")


class TestSafetyAssumptionHolds(unittest.TestCase):
    """★ 假设成立 ⇒ **一个字都不说**（用户第 ③ 条：别教它守打不到的地方）。"""

    def test_far_away_hall_with_no_enemy_anywhere_is_silent(self):
        """空厅 + 附近根本没有敌军 ⇒ 不报。为这种地方驻军就是"学燕"。"""
        w = _world()
        _arena(w)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_enemy_out_of_vision_is_not_a_threat_you_can_compute(self):
        """交战中的敌军，但**不在我视野内** ⇒ 不报（迷雾就是迷雾，算不出来）。"""
        w = _world()
        _arena(w)
        _at_war(w, "楚", "秦")
        _army(w, 13, 13, "楚", 1)
        self.assertFalse(w.visible_to("秦", 13, 13), "前提：那格对秦不可见")
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_neutral_neighbour_cannot_walk_in(self):
        """隔壁**中立**国的军队够不着（引擎里中立不可入境、不能 atk）⇒ 不报。"""
        w = _world()
        _arena(w)
        _army(w, *NEXT_TO_HALL, "楚", 1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_garrisoned_hall_is_silent(self):
        w = _world()
        _arena(w)
        _at_war(w, "楚", "秦")
        _army(w, *NEXT_TO_HALL, "楚", 1)
        _army(w, *HALL, "秦", 2)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_pending_hall_is_not_a_guozuo_yet(self):
        """**在建**的厅还不是国祚（口径同 `nation_building_count`）⇒ 不报。"""
        w = _world()
        _arena(w, hall=False, pending=True)
        _at_war(w, "楚", "秦")
        _army(w, *NEXT_TO_HALL, "楚", 1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_other_nations_hall_is_not_my_business(self):
        w = _world()
        _tile(w, 12, 12, "楚", "郢", hall=1)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))


class TestSafetyAssumptionBroken(unittest.TestCase):
    """★ 假设破了 ⇒ 点名：哪几处、谁够得着。"""

    def _broken(self):
        w = _world()
        _arena(w)
        _at_war(w, "楚", "秦")
        e = _army(w, *NEXT_TO_HALL, "楚", 1)
        self.assertTrue(w.visible_to("秦", *NEXT_TO_HALL), "前提：那支军看得见")
        self.assertIn(HALL, w._reachable("楚", e, for_attack=True), "前提：本回合够得着")
        return w

    def test_reachable_enemy_over_an_empty_hall_is_reported(self):
        w = self._broken()
        s = mp_ai._fmt_hall_alert(w, "秦")
        self.assertIsNotNone(s, "空厅 + 够得着的敌军 = 假设已破")
        self.assertIn("【国祚警报】", s)
        self.assertIn("后方安全假设已经不成立", s)
        self.assertIn("晴桥", s)
        self.assertIn("(6,6)", s)                   # 1-based 呈现（引擎内部是 5,5）
        self.assertIn("楚·步1军", s)                # 谁够得着 —— 要能直接去截它
        self.assertIn("零战斗直接进驻", s)          # 讲清代价
        self.assertIn("被换家意味着战略完全失败", s)  # 用户口径原话

    def test_garrison_clears_it(self):
        """回兵即消——这是"假设此刻成不成立"，不是历史事件。"""
        w = self._broken()
        self.assertIsNotNone(mp_ai._fmt_hall_alert(w, "秦"))
        _army(w, *HALL, "秦", 2)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_wild_men_are_not_a_threat(self):
        """野人从不主动进攻（引擎里只守无主格）⇒ 不算威胁。"""
        w = _world()
        _arena(w)
        _at_war(w, "楚", "秦")
        g = {"id": 9, "gid": 9, "name": "野人9", "hp": 100, "x": NEXT_TO_HALL[0],
             "y": NEXT_TO_HALL[1], "owner": "野人", "moved_turn": -1, "engaged": False}
        w.armies.append(g)
        self.assertIsNone(mp_ai._fmt_hall_alert(w, "秦"))

    def test_only_the_reachable_halls_are_listed(self):
        """多座厅：够得着的那座报，够不着的**不报**（别把它变成"逐厅点名"）。"""
        w = self._broken()
        _tile(w, 3, 3, "秦", "雍城", hall=1)        # 另一座厅，附近没有任何敌军
        s = mp_ai._fmt_hall_alert(w, "秦")
        self.assertIn("晴桥", s)
        self.assertNotIn("雍城", s)
        self.assertIn("1 座", s)


class TestWiring(unittest.TestCase):
    def test_alert_sits_above_the_rest_of_the_state_panel(self):
        """钉在状态面板最前：在【威胁】之后、【国力】之前（和领地警报同一档紧迫度）。"""
        w = _world()
        _arena(w)
        _at_war(w, "楚", "秦")
        _army(w, *NEXT_TO_HALL, "楚", 1)
        state = mp_ai.full_state(w, "秦")
        self.assertIn("【国祚警报】", state)
        self.assertLess(state.index("【威胁】"), state.index("【国祚警报】"))
        self.assertLess(state.index("【国祚警报】"), state.index("【国力】"))

    def test_nothing_to_report_no_block(self):
        """假设成立 ⇒ 整段不出现（不白占 token）。"""
        w = _world()
        _arena(w)
        self.assertNotIn("【国祚警报】", mp_ai.full_state(w, "秦"))


if __name__ == "__main__":
    unittest.main()
