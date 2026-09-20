# -*- coding: utf-8 -*-
"""**和约（休战）优先于盟约**——用户 2026-09-20 新增的两条口径：

  ① 「有和约的国家不能加入联盟」——**全局**：只要还有未到期休战，不论对手是谁，
     都不进军事实体。入盟、**发起结盟**、接受结盟邀约，三处都要拦（漏一处就是后门：
     "不让我入盟？那我自己开一个"）。
  ② 「有和约时，防御条约和独立保障应该无法执行」——指**已有的条约不触发**：
     休战期内不把签约方拖进与休战对手的战争（宣战时的传递闭包跳过它）。
     新签不受限（那是另一条口径，没做）。

两条都住在同一张表上：`World.truce[frozenset{a,b}] = 到期回合`。★ **亡国时引擎会给
全天下列国对压一条 10 回合强制休战**（`FALL_TRUCE_TURNS`，防雪球）——用户明确口径：
**那条也算"和约"**，所以有人亡国后的 10 回合里谁都入不了盟（`TestFallTruceCounts`）。

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
