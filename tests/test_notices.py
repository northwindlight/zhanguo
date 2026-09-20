# -*- coding: utf-8 -*-
"""**当事人必须被告知结果**——看海纪事的可见性口径（用户 2026-09-20 报的两个洞）。

背景：纪事条目有三种可见性（见 `World._stamp_seen` / `World.log`）
  · 有坐标 → 按**落盘时刻**的视野快照（`seen`）；
  · `nation=X` → **只有 X**；
  · 都没有 → 谁也看不见（只有看海台/人类看得到，`observer` 不打视野过滤）。
于是"**把结果告诉对面**"这件事没有通道，结果就掉在两个缝里：

  ① **外交被拒没人告诉提议方**（用户：「外交拒绝不会回应到对应国家」）——
     `reject_peace` 原先一条纪事都不写；`reject_pact` 只写 `nation=me`（拒绝方自己）。
     提议方于是**一直等**（面板里"你提的求和#N"下回合无声消失）。
  ② **丢地不报**（用户：「丢地不报消息」）—— 夺地那条只写攻方视角
     （"秦 攻占「绥湾」"），失主从自己的近讯里认不出是自己丢了地；更糟的是
     **按视野快照它可能根本收不到**（那块地是飞地 / 失去后四周再无自家地）。

正解是 `log(..., parties=[...])`：**并进**视野快照（有坐标时）或直接作为可见者
（无坐标时）。口径不变——**"怎么谈的"不广播**（那是给第三方的），但**桌上这两方必须知道结果**。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402


def seen_by(world, name: str, *keys: str) -> list[str]:
    """该国近讯里提到这些关键词的条目。"""
    return [e for e in world.events_for(name, limit=60) if any(k in e for k in keys)]


class TestPartyChannel(unittest.TestCase):
    """机制本身：`parties=` 保证当事人看得到，第三方看不到。"""

    def test_parties_see_it_third_party_does_not(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 3
        w.log("💔 楚 拒绝了 秦 的求和提议", phase="外交", parties=["秦", "楚"])
        self.assertTrue(seen_by(w, "秦", "拒绝了"), "提议方必须看得到")
        self.assertTrue(seen_by(w, "楚", "拒绝了"), "拒绝方自己也看得到")
        self.assertFalse(seen_by(w, "齐", "拒绝了"), "**不广播**：第三方不该看到（怎么谈的不公开）")

    def test_parties_union_with_fog_snapshot(self):
        """有坐标时 `parties` 是**并进**视野快照，不是替换——原来能看到的仍看得到。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 3
        x, y = sorted(w.own_tiles("秦"))[0]
        w.log("某条带坐标的纪事", phase="事件", x=x, y=y, parties=["楚"])
        entry = w.history[-1]
        self.assertIn("秦", entry["seen"], "坐标视野快照照旧")
        self.assertIn("楚", entry["seen"], "当事人被并进来")

    def test_no_coords_still_hides_from_others(self):
        """没坐标又没 parties ⇒ 行为不变：谁也不可见（`seen` 都不写）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 3
        w.log("只给自己看的一句", phase="事件", nation="秦")
        self.assertNotIn("seen", w.history[-1])
        self.assertTrue(seen_by(w, "秦", "只给自己看"))
        self.assertFalse(seen_by(w, "楚", "只给自己看"))


class TestDiplomacyRejectionReachesProposer(unittest.TestCase):
    """① 外交拒绝要回到提议方（用户 2026-09-20：「外交拒绝不会回应到对应国家」）。"""

    def test_peace_rejection_reaches_proposer(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        w.declare_war("秦", "楚")
        w.offer_peace("秦", "楚", "white")
        self.assertTrue(w.reject_peace("楚", w.peace_offers[0]["id"])[0])
        self.assertTrue(seen_by(w, "秦", "拒绝"), f"求和被拒没回到提议方：{w.events_for('秦', 60)}")
        self.assertFalse(seen_by(w, "齐", "拒绝"), "拒绝不该广播给第三方")

    def test_pact_rejection_reaches_proposer(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        w.propose_pact("共同防御", "秦", "楚")
        self.assertTrue(w.reject_pact("楚", w.proposals[0]["id"])[0])
        self.assertTrue(seen_by(w, "秦", "拒绝"), f"缔约被拒没回到提议方：{w.events_for('秦', 60)}")
        self.assertFalse(seen_by(w, "齐", "拒绝"))

    def test_bloc_offer_rejection_reaches_founder_and_invitees(self):
        """结盟提议流产：**发起人 + 其余创始成员**都该知道（他们也在等）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐", "燕"])
        w.turn = 5
        w.propose_bloc("秦", "合纵", ["楚", "齐", "燕"])
        pid = w.proposals[0]["id"]
        w.reject_pact("齐", pid)
        for who in ("秦", "楚", "燕"):
            self.assertTrue(seen_by(w, who, "拒绝"),
                            f"{who} 该知道结盟提议已流产：{w.events_for(who, 60)}")


class TestTerritoryLossNoticesLoser(unittest.TestCase):
    """② 丢地要报给失主（用户 2026-09-20：「丢地不报消息」）。"""

    def test_loser_is_told_and_line_names_it(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 5
        t = sorted(w.own_tiles("楚"))[0]
        w._conquer(t[0], t[1], "秦", "攻占")
        ev = seen_by(w, "楚", "攻占")
        self.assertTrue(ev, "失主没收到丢地消息")
        self.assertIn("原属 楚", ev[-1], f"消息该点名原属国（否则认不出是自己丢了地）：{ev[-1]}")

    def test_loser_still_told_when_blind(self):
        """★ 极端情形：那块地是飞地（失主四周已无自家地 ⇒ 按视野根本看不到该格）
        ——`parties` 必须把它捞回来，否则"丢了地"在它的近讯里**彻底不存在**。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        t = sorted(w.own_tiles("楚"))[0]
        for nb in w.neighbors(*t):          # 四周划给秦 ⇒ 楚 对该格的视野清零
            if w.tiles.get(nb, {}).get("owner") == "楚":
                w.tiles[nb]["owner"] = "秦"
        w._conquer(t[0], t[1], "秦", "攻占")
        self.assertFalse(w.visible_to("楚", t[0], t[1]), "前提：失主确实看不见这格了")
        self.assertTrue(seen_by(w, "楚", "攻占"), "看不见也得被告知：这块地是你的")

    def test_elimination_is_public(self):
        """亡国是公开事实：**第三方也该看到**（原先是 `log(nation=死者)`＝谁也看不见）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        for (x, y) in list(w.own_tiles("楚")):
            w._conquer(x, y, "秦", "攻占")
        self.assertNotIn("楚", w.nations)
        self.assertTrue(seen_by(w, "齐", "亡国"), "第三方该知道谁亡国了")
        self.assertTrue(seen_by(w, "秦", "亡国"), "灭它的那家更该知道")


if __name__ == "__main__":
    unittest.main()
