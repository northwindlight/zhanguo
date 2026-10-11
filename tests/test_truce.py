# -*- coding: utf-8 -*-
"""**和约（休战）拦宣战、不拦条约**——下面两条口径**都改过一轮**，别看旧笔记。

  ① ~~「有和约的国家不能加入联盟」~~（2026-09-20 定的，**2026-10-07 推翻**）：
     当时是**全局**禁令——只要还有未到期休战，不论对手是谁都不进军事实体（入盟 /
     发起结盟 / 接受邀约三处都拦）。用户 2026-10-07 拍板改成：
     **「停战期可以缔结条约；如果是建立联盟，合并和平条约而不是阻止」**。
     旧口径的病灶在**亡国后的天下强制休战**上：全世界两两被压上一条休战，
     于是整段休战期里**谁都结不了盟**——而全天下同时停火，恰恰是唯一一段
     谁都打不了谁、最该谈条约的窗口。见 `TestBlocDespiteTruce` / `TestFallTruceCounts`。
  ② 「有和约时，防御条约和独立保障应该无法执行」（2026-09-20，**仍然有效**）：
     指**已有的条约不触发**——休战期内不把签约方拖进与休战对手的战争（宣战时守侧那一跳
     跳过它）。**和约优先于盟约**没变，见 `TestTruceBlocksPactCallUp`。

「合并」落在 `World.active_truce` 上：**同属一个外交实体（联盟）的两国之间不存在独立的
和约**（联盟自带的"盟内互不攻击"比和约更强）⇒ 返回 None。★ 但**表里那条不删**：
联盟解散/成员退盟 ⇒ 原来的休战**按原到期回合自动恢复**。删掉就成了一扇后门——
「邀对手入盟 → 当场解散 → 立刻偷袭」，对方还以为那纸和约在。

两条都住在同一张表上：`World.truce[frozenset{a,b}] = 到期回合`。★ **亡国时引擎会给
全天下列国对压一条 10 回合强制休战**（`FALL_TRUCE_TURNS`，防雪球）——用户明确口径：
**那条也算"和约"**（拦宣战、按同一套规则参与合并）。

★★ 2026-10-07 另一条拍板：那条休战**字面就是停战**——`_eliminate_if_dead` 会把
**所有战线一并终止**（不只是与亡国者有关的那几条），并解除全军交战；
判据与护栏见 `TestFallCeasefireActuallyStopsWars`。

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


class TestBlocDespiteTruce(unittest.TestCase):
    """★ 2026-10-07：**休战期能结盟**，与盟友之间的和约被联盟**合并**（不是拦下来）。"""

    def _world(self):
        """秦、燕有和约（至第 20 回合）；楚、齐在「连横」里；**赵干净**（无和约、无盟）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐", "燕", "赵"])
        w.turn = 5
        _bloc(w, "楚", "连横", "齐")            # 楚齐先立一个盟，供"申请入盟"用
        w.truce[mp._pair("秦", "燕")] = 20      # 秦与燕休战至第 20 回合
        return w

    def test_can_join_bloc(self):
        w = self._world()
        ok, msg = w.bloc_join("秦", "连横")
        self.assertTrue(ok, f"有和约就入不了盟（旧口径的残留）：{msg}")

    def test_can_found_bloc(self):
        """发起结盟同样不受和约阻拦（旧口径"自己开一个也算入盟"的那条已废）。"""
        w = self._world()
        ok, msg = w.propose_bloc("秦", "新盟", ["赵"])
        self.assertTrue(ok, f"有和约就不让发起结盟：{msg}")

    def test_can_accept_founding_invite(self):
        """发起时还没和约、接受时刚议和了 ⇒ 接受这一刻**也**不拦。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐"])
        w.turn = 5
        w.propose_bloc("楚", "连横", ["秦"])
        w.truce[mp._pair("秦", "齐")] = 30      # 提议之后、接受之前议和了
        ok, msg = w._accept_bloc_founding(w.proposals[-1], "秦")
        self.assertTrue(ok, f"有和约就不让接受结盟：{msg}")

    def test_join_vote_executes(self):
        w = self._world()
        v = w._new_vote("入盟", "连横", "秦", {"candidate": "秦"})
        ok, msg = w._execute_vote(v)
        self.assertTrue(ok, f"入盟投票被和约拦下了：{msg}")
        self.assertIn("秦", w.bloc_of("秦")["members"])

    def test_pact_can_be_signed_during_truce(self):
        """★ 「停战期**可以缔结条约**」：和约拦宣战，不拦缔约（旧文档只写了"不得入盟"，
        条约这条其实一直是放行的——钉死它，别哪天被"休战＝外交冻结"的直觉改回去）。"""
        w = self._world()
        ok, msg = w.propose_pact("共同防御", "秦", "燕")
        self.assertTrue(ok, f"休战期签不了共同防御：{msg}")
        ok, msg = w.accept_pact("燕", w.proposals[-1]["id"])
        self.assertTrue(ok, msg)
        self.assertTrue(w.has_pact("共同防御", w.entity_of("秦"), w.entity_of("燕")),
                        "休战期的条约没落下来")
        self.assertEqual(w.active_truce("秦", "燕"), 20, "签条约不该动那纸和约")


class TestTruceMergedIntoBloc(unittest.TestCase):
    """**合并**的确切含义：盟内那纸和约不再独立成立；联盟一散，它按原到期回合回来。"""

    def _world(self, members=("秦", "燕")):
        w = mp.World(size=20, seed=7, nations=["秦", "燕", "赵"])
        w.turn = 5
        w.truce[mp._pair("秦", "燕")] = 20       # 秦燕休战至第 20 回合
        w.truce[mp._pair("秦", "赵")] = 20       # 秦与**盟外**的赵也休战至第 20 回合
        if len(members) > 1:
            _bloc(w, members[0], "北盟", members[1])
        return w

    def test_merged_while_allied(self):
        w = self._world()
        self.assertIsNone(w.active_truce("秦", "燕"), "纳入联盟后和约还独立成立——没被合并")
        self.assertFalse([o for o, _u in w.truces_of("秦") if o == "燕"],
                         "truces_of 还列着盟友（面板会显示成「仍在休战中」）")
        self.assertIsNone(w.active_truce("燕", "秦"), "反方向也该是合并的（对称）")

    def test_outsider_truce_is_untouched(self):
        """**阴性对照**：与**盟外**国家的和约不受影响——合并只发生在实体内部。"""
        w = self._world()
        self.assertEqual(w.active_truce("秦", "赵"), 20, "盟外的和约被顺手清掉了")
        ok, msg = w.declare_war("秦", "赵")
        self.assertFalse(ok, f"与盟外国家的休战期内还能宣战：{msg}")
        self.assertIn("休战", msg)

    def test_ally_cannot_be_attacked_and_says_the_right_thing(self):
        """同实体本来就打不了——**理由要说"同属联盟"**，不是"休战中"（后者读起来像
        还有一纸能过期的和约在保它）。"""
        w = self._world()
        ok, msg = w.declare_war("秦", "燕")
        self.assertFalse(ok)
        self.assertIn("同属", msg)
        self.assertNotIn("休战", msg)

    def test_truce_comes_back_when_bloc_dissolves(self):
        """★★ 护栏：联盟解散 ⇒ 原和约**按原到期回合恢复**。

        不恢复的话就是一条后门：秦 邀燕入盟 → 当场解散 → 立刻开战，
        燕还以为那纸和约在（它确实还在表里，却已经不约束了）。"""
        w = self._world()
        ok, msg = w.bloc_dissolve("秦")
        self.assertTrue(ok, msg)
        self.assertEqual(w.active_truce("秦", "燕"), 20, "联盟散了，和约没回来")
        ok, msg = w.declare_war("秦", "燕")
        self.assertFalse(ok, f"解散联盟就把和约抹了：{msg}")
        self.assertIn("休战", msg)

    def test_truce_comes_back_when_member_leaves(self):
        """成员退盟同样恢复（走的是同一个 `entity_of` 判定，不是特判）。"""
        w = self._world(members=("秦", "燕"))
        ok, msg = w.bloc_leave("燕")
        self.assertTrue(ok, msg)
        self.assertEqual(w.active_truce("秦", "燕"), 20, "退盟后和约没回来")

    def test_proclamation_tells_the_parties(self):
        """暂停要说出来（"结果不告诉当事人"是这套引擎反复踩的洞）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "燕", "赵"])
        w.turn = 5
        w.truce[mp._pair("秦", "燕")] = 20
        _bloc(w, "秦", "北盟", "燕")
        self.assertTrue(any("在盟内暂停" in h.get("text", "") for h in w.history),
                        "结盟时没说和约被暂停了")
        self.assertTrue(any("原休战至第 20 回合" in h.get("text", "") for h in w.history),
                        "没说清是哪两家的和约、到哪一回合")


class TestEntityTruceFollowsTheChief(unittest.TestCase):
    """★★ 2026-10-11 用户口径：「**联盟和平条约改成由盟主的条约决定，成员和平条约丧失，
    然后退出自动恢复**」。

    实盘病根（八国局 T76）：**一个成员的和约能给整个联盟挡刀**。韩 与 楚 白和休战到第 94
    回合，楚 一入「周天下」，韩 连对周宣战都发不出去（`_declare_war_internal` 逐国检查，
    撞上 韩↔楚 那条直接拒）——四国联盟白得一张免战牌，而盟主 周 跟韩 根本没签过任何东西。
    韩 因此在回合小结里写下「打不出去 ⇒ 让魏替我扛 ⇒ 等它亡国换 10 回合休战期」。
    """

    def _world(self):
        """韩、魏、楚、周；周天下＝周（盟主）＋楚。**不预置任何和约**——各用例自带。"""
        w = mp.World(size=20, seed=7, nations=["韩", "魏", "楚", "周"])
        w.turn = 70
        _bloc(w, "周", "周天下", "楚")
        return w

    def test_members_truce_no_longer_shields_the_bloc(self):
        """★ 核心回归：成员的和约**不再**替全盟挡刀——韩 打得出去。"""
        w = self._world()
        w.truce[mp._pair("韩", "楚")] = 94          # 韩 与**成员**楚 白和（旧病现场）
        self.assertIsNone(w.active_truce("韩", "周"), "成员的和约竟然还算在全盟头上")
        ok, msg = w.declare_war("韩", "周")
        self.assertTrue(ok, f"成员的私约把整个联盟护住了（旧病）：{msg}")
        self.assertTrue(w.war_between("韩", "周"))
        self.assertTrue(w.war_between("韩", "楚"), "打盟员=打全盟，楚 该在守侧")

    def test_chiefs_truce_is_the_entitys(self):
        """**阴性对照的另一半**：盟主签的约就是全盟的约（这时才该挡住）。"""
        w = self._world()
        w.truce[mp._pair("韩", "周")] = 94          # 盟主 周 与韩 签约
        self.assertEqual(w.active_truce("韩", "楚"), 94, "盟主的约没有变成全盟的约")
        ok, msg = w.declare_war("韩", "周")
        self.assertFalse(ok, f"与盟主签约期内还能宣战：{msg}")
        self.assertIn("休战", msg)

    def test_truces_of_reports_the_entitys(self):
        """面板口径：在盟国家看到的是**全盟的**和约（盟主那份），不是自己那份。"""
        w = self._world()
        w.truce[mp._pair("韩", "楚")] = 94          # 成员私约：不列
        self.assertEqual(w.truces_of("楚"), [], "成员还列着自己那份暂停中的私约")
        w.truce[mp._pair("韩", "周")] = 94          # 盟主的约：全盟都该看到
        self.assertEqual(w.truces_of("楚"), [("韩", 94)], "成员没看到盟主签的那份")

    def test_suspended_truce_comes_back_on_leaving(self):
        """★「退出自动恢复」：楚 退盟 ⇒ 它自己那份和约按原到期回合回来（表里没删过）。"""
        w = self._world()
        w.truce[mp._pair("韩", "楚")] = 94
        ok, msg = w.bloc_leave("楚")
        self.assertTrue(ok, msg)
        self.assertEqual(w.active_truce("韩", "楚"), 94, "退盟后私约没恢复")
        ok, msg = w.declare_war("楚", "韩")
        self.assertFalse(ok, f"退盟后还能打自己的休战对手：{msg}")
        self.assertIn("休战", msg)

    def test_chief_transfer_swaps_the_entitys_truce(self):
        """移交盟主 ⇒ 实体的和约**随之换成新盟主那一份**（这是"以盟主为准"的必然结果，
        钉在这里免得日后当成 bug 顺手"修"掉）。"""
        w = self._world()
        w.truce[mp._pair("韩", "周")] = 94          # 盟主周 与韩 有约 ⇒ 全盟与韩 休战
        self.assertEqual(w.active_truce("韩", "楚"), 94)
        ok, msg = w.bloc_transfer("周", "楚")
        self.assertTrue(ok, msg)
        self.assertIsNone(w.active_truce("韩", "周"), "换盟主后旧盟主那份还在生效")

    def test_fall_truce_still_covers_every_entity(self):
        """**阴性对照**：亡国压给全天下的强制休战是按**每一对国家**压的 ⇒
        无论谁当盟主，实体之间都还有约（别把这条规则读成"全天下休战对联盟失效"）。"""
        w = mp.World(size=20, seed=7, nations=["秦", "魏", "韩", "赵"],
                     starts={"秦": (3, 3), "魏": (6, 6), "韩": (9, 9), "赵": (12, 12)})
        w.armies = []
        _bloc(w, "赵", "合纵", "韩")
        for (x, y) in list(w.own_tiles("魏")):
            w.tiles[(x, y)]["buildings"]["市政厅"] = 0
        self.assertTrue(w._eliminate_if_dead("魏"))
        for other in ("秦", "韩", "赵"):
            self.assertTrue(w.truces_of(other), f"{other} 没被压上全天下休战")
        ok, msg = w.declare_war("秦", "赵")
        self.assertFalse(ok, f"全天下休战期内还能对联盟宣战：{msg}")


class TestFallTruceCounts(unittest.TestCase):
    """亡国压给全天下的强制休战**也算"和约"**（用户 2026-09-20 拍板）——
    它拦宣战、参与合并，但**不再拦结盟**（2026-10-07 改）。"""

    def _world(self):
        """秦楚齐燕赵；楚齐在「连横」；**燕 亡国** ⇒ 全天下两两压上强制休战。
        **赵 留在盟外**，用来验"盟外的和约一条都不能少"。"""
        w = mp.World(size=20, seed=7, nations=["秦", "楚", "齐", "燕", "赵"])
        w.turn = 5
        _bloc(w, "楚", "连横", "齐")
        for (x, y) in list(w.own_tiles("燕")):  # 燕 亡国 ⇒ 天下强制休战 10 回合
            w._conquer(x, y, "秦", "攻陷")
        # ★ 2026-10-09：亡国改为**回合末统一判定**（`_settle_deaths`）——这里手工收口，
        #   等价于"这一回合走到了结算收尾"。
        w._settle_deaths()
        self.assertNotIn("燕", w.nations)
        return w

    def test_can_join_bloc_after_a_nation_falls(self):
        """★ 旧口径下这里是**全天下十回合谁也结不了盟**——正是用户要废掉的那条。"""
        w = self._world()
        ok, msg = w.bloc_join("秦", "连横")
        self.assertTrue(ok, f"亡国后的天下休战期内还入不了盟：{msg}")
        self.assertTrue(w.truces_of("秦"), "前提：秦 还和盟外的对手有休战")

    def test_joining_merges_only_the_internal_truce(self):
        w = self._world()
        w.bloc_join("秦", "连横")
        w._execute_vote(w.votes[-1])
        self.assertIsNone(w.active_truce("秦", "楚"), "入盟没能把与楚齐的和约合并掉")
        # 天下休战是**两两**压的：与盟外国家（赵）的那条一条都不能少
        self.assertEqual({o for o, _u in w.truces_of("秦")}, {"赵"},
                         "合并越界了——把盟外的和约也吞了")

    def test_world_still_cannot_declare_war(self):
        """**阴性对照**：休战期内不得宣战这条**没动**。"""
        w = self._world()
        ok, msg = w.declare_war("秦", "齐")
        self.assertFalse(ok, f"天下休战期内还能宣战：{msg}")
        self.assertIn("休战", msg)


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


class TestFallCeasefireVoidsPending(unittest.TestCase):
    """★★ 2026-10-11 用户报的洞：「**强制和平后，还没有敲定的和平条约没有自动作废，
    可能覆盖风险**」。

    强制和平那一刻**所有战线已经终止** ⇒ 在途的求和提议已经没仗可停；留着它，落地那一刻
    就会把「全天下强制休战」**覆盖**成它自己那条短的——而那条强制休战是防雪球的地基
    （2026-10-07 立的），不该被一纸短约抹掉。两道闸：① 强制和平即作废在途事项；
    ② 议和写休战期一律 `max`（只许延长、不许缩短）。
    """

    def _world(self):
        """秦、魏、韩、赵；**秦↔魏 交战**（另一条战线，与死者无关）；赵 有厅可拔。"""
        w = mp.World(size=20, seed=7, nations=["秦", "魏", "韩", "赵"],
                     starts={"秦": (3, 3), "魏": (6, 6), "韩": (9, 9), "赵": (12, 12)})
        w.armies = []
        w.turn = 30
        self.assertTrue(w.declare_war("秦", "魏")[0])
        return w

    def _kill(self, w, name):
        for (x, y) in list(w.own_tiles(name)):
            w.tiles[(x, y)]["buildings"]["市政厅"] = 0
        return w._eliminate_if_dead(name)

    def test_inflight_peace_offer_is_voided(self):
        w = self._world()
        ok, msg = w.offer_peace("秦", "魏", "white", truce=3)
        self.assertTrue(ok, msg)
        self.assertEqual(len(w.peace_offers), 1, "前提：提议在桌上")
        self.assertTrue(self._kill(w, "韩"))                 # 第三国亡国 ⇒ 全天下强制休战
        self.assertEqual(w.peace_offers, [], "强制和平后，在途的求和提议还挂着")
        # 拿着那纸条回来也没用
        ok, msg = w.accept_peace("魏", 1)
        self.assertFalse(ok, f"作废的求和提议还能接受：{msg}")

    def test_both_parties_are_told(self):
        w = self._world()
        w.offer_peace("秦", "魏", "white", truce=3)
        self._kill(w, "韩")
        for who in ("秦", "魏"):
            self.assertTrue(any("求和提议" in e and "作废" in e
                                for e in w.events_for(who, limit=30)),
                            f"{who} 没收到「提议作废」的通知")

    def test_a_short_peace_cannot_shorten_the_fall_truce(self):
        """★ **覆盖**的正面护栏：议和写休战期必须 `max`。

        正常流程走不到"既在打仗、又已有更长休战"（开战会被休战拦下、强制和平又会清空所有
        战争），所以这里**手工构造**那个状态——防雪球的地基不该靠"走不到"来保护。
        """
        w = self._world()
        w.truce[mp._pair("秦", "魏")] = w.turn + 50      # 手上已有一条更长的休战
        ok, msg = w.offer_peace("秦", "魏", "white", truce=2)
        self.assertTrue(ok, msg)
        _ok, _msg = w.accept_peace("魏", w.peace_offers[-1]["id"])
        self.assertEqual(w.active_truce("秦", "魏"), w.turn + 50,
                         "一纸短休战把原有的长休战覆盖掉了")


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
