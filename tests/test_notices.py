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
import mp_ai  # noqa: E402


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
        w._conquer(t[0], t[1], "秦", "攻陷")
        ev = seen_by(w, "楚", "攻陷")  # 引擎真用的动词（`how` 值：攻陷/进驻/守军尽撤）
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
        w._conquer(t[0], t[1], "秦", "攻陷")
        self.assertFalse(w.visible_to("楚", t[0], t[1]), "前提：失主确实看不见这格了")
        self.assertTrue(seen_by(w, "楚", "攻陷"), "看不见也得被告知：这块地是你的")

    def test_elimination_is_public(self):
        """亡国是公开事实：**第三方也该看到**（原先是 `log(nation=死者)`＝谁也看不见）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        for (x, y) in list(w.own_tiles("楚")):
            w._conquer(x, y, "秦", "攻陷")
        self.assertNotIn("楚", w.nations)
        self.assertTrue(seen_by(w, "齐", "亡国"), "第三方该知道谁亡国了")
        self.assertTrue(seen_by(w, "秦", "亡国"), "灭它的那家更该知道")


class TestTerritoryAlertPanel(unittest.TestCase):
    """③ 领土变更/被侵略要**钉在面板最前**（用户 2026-09-21：「紧急性不够高……得强调」）。

    原先这些只落在面板**最末尾**的【近讯】里，前面压着几百行地图 ⇒ 没紧迫感。
    现在 `full_state` 顶部有【领土警报】：失地（含 ♥核心标记）/ 得地 / **境内敌军**，
    且只在**自上次行动以来**（上一轮结算 + 本回合）有事时出现。
    """

    def _world(self, **kw):
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 6
        return w

    def test_loss_is_at_top_of_panel(self):
        w = self._world()
        t = sorted(w.own_tiles("楚"))[0]
        w._conquer(t[0], t[1], "秦", "攻陷")
        lines = mp_ai.full_state(w, "楚").splitlines()
        self.assertEqual(lines[1], "⚠ 【领土警报】", f"警报该紧跟首行：{lines[:3]}")
        self.assertIn("失地", lines[2])
        self.assertIn(f"({t[0] + 1},{t[1] + 1})", lines[2])

    def test_non_core_loss_also_reported(self):
        """★ 用户问的：「只算核心？」——**不算**，任何地块易主都报；核心只多一个 ♥ 标。"""
        w = self._world()
        tiles = sorted(w.own_tiles("楚"))
        noncore, core = tiles[0], tiles[1]
        w.tiles[noncore]["core"] = "秦"        # 对楚是**非核心**（它早先从秦手里夺来的）
        w._conquer(*noncore, "秦", "攻陷")
        w._conquer(*core, "秦", "攻陷")
        alert = mp_ai._fmt_alerts(w, "楚")
        self.assertIn("失地 2 块", alert)
        self.assertIn("♥核心", alert, "核心那块要标出来（盟友夺回会自动归还）")
        self.assertEqual(alert.count("♥核心"), 1, "只有核心那块标 ♥")

    def test_intruding_army_reported(self):
        """**境内敌军**：敌人还没夺地、但已站在我的地上——这就是"被侵略"。"""
        w = self._world()
        t = sorted(w.own_tiles("楚"))[0]
        gid, seq = w._new_army("秦")
        w.armies.append({"id": seq, "gid": gid, "name": f"秦步{seq}", "type": "步",
                         "hp": 94, "x": t[0], "y": t[1], "owner": "秦",
                         "moved_turn": -1, "engaged": False})
        alert = mp_ai._fmt_alerts(w, "楚")
        self.assertIn("境内敌军 1 支", alert)
        self.assertIn("94HP", alert)

    def test_ally_army_is_not_intrusion(self):
        """**阴性对照**：盟军合法驻留在我的地上，不算被侵略。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        w.turn = 6
        w.propose_bloc("秦", "合纵", ["楚"])
        w._accept_bloc_founding(w.proposals[-1], "楚")
        t = sorted(w.own_tiles("楚"))[0]
        gid, seq = w._new_army("秦")
        w.armies.append({"id": seq, "gid": gid, "name": f"秦步{seq}", "type": "步",
                         "hp": 94, "x": t[0], "y": t[1], "owner": "秦",
                         "moved_turn": -1, "engaged": False})
        self.assertIsNone(mp_ai._fmt_alerts(w, "楚"), "盟军驻留不该报成境内敌军")

    def test_quiet_turn_has_no_alert_block(self):
        """没事就**整段不出现**（不白占 token）——面板第二行直接是【国力】。"""
        w = self._world()
        self.assertIsNone(mp_ai._fmt_alerts(w, "秦"))
        lines = mp_ai.full_state(w, "秦").splitlines()
        self.assertNotIn("领土警报", "\n".join(lines))
        self.assertEqual(lines[1], "【国力】")

    def test_only_since_last_action(self):
        """陈年旧账不报：只报 `turn >= 本回合-1`（上一轮结算 + 本回合）。"""
        w = self._world()
        t = sorted(w.own_tiles("楚"))[0]
        w.history.append({"turn": 1, "phase": "领土", "nation": "秦",
                          "x": t[0], "y": t[1], "text": "陈年旧事", "lost_by": "楚"})
        self.assertIsNone(mp_ai._fmt_alerts(w, "楚"), "上一轮之前的失地不该再报")
        w.turn = 2
        self.assertIn("失地", mp_ai._fmt_alerts(w, "楚") or "", "上一轮的失地要报")


if __name__ == "__main__":
    unittest.main()
