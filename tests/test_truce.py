# -*- coding: utf-8 -*-
"""**和约（休战）优先于盟约**——用户 2026-09-20 新增的两条口径：

  ① 「有和约的国家不能加入联盟」——**全局**：只要还有未到期休战，不论对手是谁，
     都不进军事实体。入盟、**发起结盟**、接受结盟邀约，三处都要拦（漏一处就是后门：
     "不让我入盟？那我自己开一个"）。
  ② 「有和约时，防御条约和独立保障应该无法执行」——指**已有的条约不触发**：
     休战期内不把签约方拖进与休战对手的战争（宣战时守侧那一跳跳过它）。
     新签不受限（那是另一条口径，没做）。

两条都住在同一张表上：`World.truce[frozenset{a,b}] = 到期回合`。★ **亡国时引擎会给
全天下列国对压一条 10 回合强制休战**（`FALL_TRUCE_TURNS`，防雪球）——用户明确口径：
**那条也算"和约"**，所以有人亡国后的 10 回合里谁都入不了盟（`TestFallTruceCounts`）。

★★ 2026-10-07 用户拍板：那条休战**字面就是停战**——`_eliminate_if_dead` 现在会把
**所有战线一并终止**（不只是与亡国者有关的那几条），并解除全军交战；
判据与护栏见 `TestFallCeasefireActuallyStopsWars`。旧口径只拦"新宣战"，于是出现
「全天下两两休战至第 N 回合」与「韩↔燕 照打不误」并存的荒谬状态。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402


def _bloc(world, founder: str, name: str, invitee: str) -> None:
    """直接立一个两人联盟（免去投票/往返，测试用）。"""
    world.propose_bloc(founder, name, [invitee])
    world._accept_bloc_founding(world.proposals[-1], invitee)


class TestNoBlocWhileTruce(unittest.TestCase):
    """① 有和约 ⇒ 入不了盟（入盟 / 发起结盟 / 接受邀约，三处都要拦）。"""

    def _world(self):
        """秦、燕有和约（至第 20 回合）；楚、齐在「连横」里；**赵干净**（无和约、无盟）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐", "燕", "赵"])
        w.turn = 5
        _bloc(w, "楚", "连横", "齐")            # 楚齐先立一个盟，供"申请入盟"用
        w.truce[mp._pair("秦", "燕")] = 20      # 秦与燕休战至第 20 回合
        return w

    def test_cannot_join_bloc(self):
        w = self._world()
        ok, msg = w.bloc_join("秦", "连横")
        self.assertFalse(ok, f"有和约还让入盟了：{msg}")
        self.assertIn("休战", msg)

    def test_cannot_found_bloc(self):
        """★ 发起结盟也算"入盟"——否则从"自己开一个"就绕过去了。"""
        w = self._world()
        ok, msg = w.propose_bloc("秦", "新盟", ["赵"])
        self.assertFalse(ok, f"有和约还让发起结盟：{msg}")
        self.assertIn("休战", msg)

    def test_cannot_accept_founding_invite(self):
        """发起时还没和约、接受时刚议和了 ⇒ 接受这一刻仍要拦（提议不悬空）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        w.propose_bloc("楚", "连横", ["秦"])
        w.truce[mp._pair("秦", "齐")] = 30      # 提议之后、接受之前议和了
        ok, msg = w._accept_bloc_founding(w.proposals[-1], "秦")
        self.assertFalse(ok, f"有和约还让接受结盟：{msg}")

    def test_invitee_with_truce_blocks_proposal(self):
        """创始成员里有"和约在身"的 ⇒ 直接拦住提议（别让它悬空挂着）。"""
        w = self._world()
        ok, msg = w.propose_bloc("赵", "再盟", ["秦"])       # 发起人赵干净，被邀的秦有和约
        self.assertFalse(ok, f"被邀方有和约还让提议成立：{msg}")
        self.assertIn("创始成员 秦", msg)

    def test_join_vote_reexamines_at_execution(self):
        """入盟投票通过时**再判一次**：投票期间刚议和 ⇒ 落空（不能靠投票绕过）。"""
        w = self._world()
        v = w._new_vote("入盟", "连横", "秦", {"candidate": "秦"})
        w.truce[mp._pair("秦", "燕")] = 30
        ok, msg = w._execute_vote(v)
        self.assertFalse(ok, f"投票期间议和了还让入盟：{msg}")

    def test_ok_after_truce_expires(self):
        """**阴性对照**：和约到期（turn ≥ 到期回合）就能入盟了——禁令不是永久的。"""
        w = self._world()
        w.turn = 21                             # 休战至第 20 回合 ⇒ 第 21 回合已过期
        ok, msg = w.bloc_join("秦", "连横")
        self.assertTrue(ok, f"和约过期后该能入盟：{msg}")

    def test_ok_without_truce(self):
        """**阴性对照**：干净的国家（赵：无和约、无盟）照旧能入盟。"""
        w = self._world()
        ok, msg = w.bloc_join("赵", "连横")
        self.assertTrue(ok, f"没和约的国家被误拦了：{msg}")


class TestFallTruceCounts(unittest.TestCase):
    """亡国压给全天下的强制休战**也算"和约"**（用户 2026-09-20 拍板）。"""

    def test_nobody_joins_bloc_after_a_nation_falls(self):
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐", "燕"])
        w.turn = 5
        _bloc(w, "楚", "连横", "齐")
        for (x, y) in list(w.own_tiles("燕")):  # 燕 亡国 ⇒ 天下强制休战 10 回合
            w._conquer(x, y, "秦", "攻陷")
        self.assertNotIn("燕", w.nations)
        ok, msg = w.bloc_join("秦", "连横")
        self.assertFalse(ok, f"亡国后的天下休战期内还让入盟：{msg}")
        self.assertTrue(w.truces_of("秦"), "前提：秦 全是对手的休战")
        for other in ("楚", "齐"):
            self.assertTrue(w.truces_of(other)[0][1] >= w.turn + 1, f"{other} 该也在休战期")


class TestFallCeasefireActuallyStopsWars(unittest.TestCase):
    """★★ 2026-10-07 用户拍板：**"全天下强制休战"字面就是停战**。

    旧口径只终止与亡国者有关的那几条战线，其余照打——实盘 T157 魏/周同日亡国，
    全天下两两"休战至第 167 回合"，而 韩↔燕（自 T128 的老战线）**照打不误、
    连一次宣战都不用**。用户当场判为 bug：「正在发生的战争必须强制停下」「包括战斗」。
    """

    def _world(self, n=4):
        w = mp.World(size=20, seed=7, nations=["秦", "魏", "韩", "赵"][:n],
                     starts={"秦": (3, 3), "魏": (6, 6), "韩": (9, 9), "赵": (12, 12)})
        w.armies = []
        return w

    def _kill(self, w, name):
        for (x, y) in list(w.own_tiles(name)):
            t = w.tiles[(x, y)]
            t["buildings"]["市政厅"] = 0
        return w._eliminate_if_dead(name)

    def test_unrelated_war_is_ended_too(self):
        """秦↔魏 在打，第三国（韩）亡国 ⇒ **秦↔魏 那条线也当场停**。"""
        w = self._world()
        self.assertTrue(w.declare_war("秦", "魏")[0])
        self.assertTrue(self._kill(w, "韩"))
        self.assertEqual(w.wars, [], "与亡国者无关的战线没停")
        self.assertFalse(w.war_between("秦", "魏"))

    def test_every_pair_gets_the_truce(self):
        w = self._world()
        w.turn = 5
        self.assertTrue(self._kill(w, "韩"))
        for other in ("秦", "魏", "赵"):
            got = dict(w.truces_of(other))
            self.assertTrue(got, f"{other} 没被压上休战")
            for o, until in got.items():
                self.assertGreaterEqual(until, 5 + 1, f"{other}↔{o} 的休战期不对")

    def test_world_is_told(self):
        """这是世界级事件，必须广播（否则各国不知道自己突然停战了）。"""
        w = self._world()
        self.assertTrue(w.declare_war("秦", "魏")[0])
        self.assertTrue(self._kill(w, "韩"))
        self.assertTrue(any("强制休战" in h.get("text", "") for h in w.history),
                        "停战的广播没发出去")

    def test_battle_stops_and_nobody_grabs_the_tile(self):
        """★ 战斗本身也要停：滞留在原敌方格上的攻方**不能白占一格、不能清掉守军**。

        不清 `engaged` 的话，`_resolve_battles` 会把这批人判成"唯一幸存者"
        （没敌人了）⇒ `_conquer("攻陷")` ⇒ 地归它、守军消失。实测复现过。
        """
        w = self._world()
        self.assertTrue(w.declare_war("秦", "魏")[0])
        x, y = 6, 6
        t = w.tiles[(x, y)]
        t["owner"] = "魏"
        t["buildings"]["市政厅"] = 0
        for i in range(3):
            w.armies.append({"id": 10 + i, "gid": 10 + i, "name": f"秦·步{10 + i}",
                             "type": "步", "hp": 100, "x": x, "y": y, "owner": "秦",
                             "moved_turn": -1, "engaged": True})
        for i in range(2):
            w.armies.append({"id": 20 + i, "gid": 20 + i, "name": f"魏·步{20 + i}",
                             "type": "步", "hp": 100, "x": x, "y": y, "owner": "魏",
                             "moved_turn": -1, "engaged": False})
        w._die = lambda: (1, 0)
        w._resolve_battles()
        self.assertEqual(w.owned_by(x, y), "魏", "前提：第一回合还没分出胜负")

        self.assertTrue(self._kill(w, "韩"))          # 停战
        self.assertFalse(any(a.get("engaged") for a in w.armies), "军队没解除交战")
        snap = {a["name"]: a["hp"] for a in w.armies}
        w._resolve_battles()                       # 再结算一回合
        self.assertEqual({a["name"]: a["hp"] for a in w.armies}, snap,
                         "停战之后还在掉血")
        self.assertEqual(w.owned_by(x, y), "魏",
                         "停战后攻方白占了这一格（战斗没真的停）")
        self.assertEqual(len([a for a in w.armies if a["owner"] == "魏"]), 2,
                         "守军被停战顺手清掉了")

    def test_battle_stops_on_the_dying_nations_own_tile_too(self):
        """★ 解除交战必须跑在**"余土变无主"之前**——这条用例的全部内容就是这个顺序。

        反过来（先归无主）：亡国者的地块 `owner` 先变成 None，站在上面正在打它的军队
        会被"只清国有地"那条过滤器漏掉、带着 `engaged` 留在无主地上 ⇒ 下一回合它就是
        "唯一幸存者" ⇒ `_conquer("攻陷")`，**白捡一格**。那格的主人刚亡国、谁都能占，
        但要占得**下命令**——停战不是白捡。
        """
        w = self._world()
        self.assertTrue(w.declare_war("秦", "韩")[0])
        x, y = 9, 9
        t = w.tiles[(x, y)]
        t["owner"] = "韩"
        t["name"] = "临都"
        t["buildings"]["市政厅"] = 0
        for i in range(3):
            w.armies.append({"id": 10 + i, "gid": 10 + i, "name": f"秦·步{10 + i}",
                             "type": "步", "hp": 100, "x": x, "y": y, "owner": "秦",
                             "moved_turn": -1, "engaged": True})
        for i in range(2):
            w.armies.append({"id": 20 + i, "gid": 20 + i, "name": f"韩·步{20 + i}",
                             "type": "步", "hp": 100, "x": x, "y": y, "owner": "韩",
                             "moved_turn": -1, "engaged": False})

        self.assertTrue(self._kill(w, "韩"))
        self.assertIsNone(w.owned_by(x, y), "前提：韩 的余土已成无主地")
        self.assertFalse(any(a.get("engaged") for a in w.armies),
                         "解除交战跑晚了（那一刻格子已经归无主，过滤掉了这批人）")
        w._die = lambda: (1, 0)
        w._resolve_battles()
        self.assertIsNone(w.owned_by(x, y), "停在原地的人白捡了一格")
        self.assertEqual([a["hp"] for a in w.armies], [100, 100, 100], "不该掉血")

    def test_clearing_barbarians_is_not_a_war_and_keeps_going(self):
        """**阴性对照**：清野地不是"战争"（野人只守无主格）⇒ 全天下休战不该掐掉它。"""
        w = self._world()
        t = w._new_tile(3, 4, "秦")          # 强行做成无主野地（开局生成的归属不可控）
        t["owner"] = None
        w.tiles[(3, 4)] = t
        self.assertIsNone(w.owned_by(3, 4), "前提：那格是无主地")
        w.armies.append({"id": 1, "gid": 1, "name": "秦·步1", "type": "步", "hp": 100,
                         "x": 3, "y": 4, "owner": "秦", "moved_turn": -1, "engaged": True})
        w.armies.append({"id": 99, "gid": 99, "name": "野人99", "hp": 100, "x": 3, "y": 4,
                         "owner": "野人", "moved_turn": -1, "engaged": False})
        self.assertTrue(self._kill(w, "韩"))
        self.assertTrue([a for a in w.armies if a["owner"] == "秦"][0].get("engaged"),
                        "清野地的军队被天下休战误伤了")


class TestTruceBlocksPactCallUp(unittest.TestCase):
    """② 已有的共同防御/保障在休战期内**不触发**（和约优先于盟约）。"""

    def _world(self, truce: bool):
        w = mp.World(size=20, seed=7, nations=["秦", "燕", "楚"])
        w.turn = 5
        w._add_pact("共同防御", w.entity_of("燕"), w.entity_of("楚"))   # 燕楚互卫
        if truce:
            w.truce[mp._pair("秦", "燕")] = 30    # 秦燕和约（燕是楚的防御盟友）
        return w

    def test_pact_ally_not_dragged_in(self):
        w = self._world(truce=True)
        ok, msg = w.declare_war("秦", "楚")
        self.assertTrue(ok, msg)
        atk, dfs = w._war_sides(w.wars[0])
        self.assertNotIn("燕", dfs, "有和约的盟友被拖进战争了（和约当场作废）")

    def test_without_truce_ally_is_dragged_in(self):
        """**阴性对照**：没有和约时共同防御照旧触发（别把整条机制关掉了）。"""
        w = self._world(truce=False)
        ok, msg = w.declare_war("秦", "楚")
        self.assertTrue(ok, msg)
        _atk, dfs = w._war_sides(w.wars[0])
        self.assertIn("燕", dfs, "没和约时盟友该照旧参战")

    def test_both_parties_are_told(self):
        """拦下来这件事要**通知当事双方**（否则又是"结果不告诉当事人"）。"""
        w = self._world(truce=True)
        w.declare_war("秦", "楚")
        self.assertTrue([e for e in w.events_for("燕", 40) if "未触发" in e],
                        "被拦下的盟友该知道自己没参战")
        self.assertTrue([e for e in w.events_for("楚", 40) if "未触发" in e],
                        "守方该知道预期的援军为什么没来")


if __name__ == "__main__":
    unittest.main()
