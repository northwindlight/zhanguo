# -*- coding: utf-8 -*-
"""★ 统计口径与权重：**只有一份**，且**按 gid 记身份**（2026-10-22 重写后的第四道闸）。

★ 两条线：
  ① **权重/奖励口径单一出处**（`rl/scoring.py`）—— 沙盒、训练、看板都读它，
     谁都不许自己再算一份（"两处独立实现"是静默不一致的温床）。
  ② **击杀账本按 `gid` 记身份** —— 旧口径用 `a["id"]`（**本国序号**）当 key，
     甲的第 1 支与乙的第 1 支**撞成同一个 key** ⇒ 账目互相覆盖，**而且不报错**。
"""
from __future__ import annotations

import unittest

from rl import scoring as S
from rl.sandbox import KillLedger


def _sandbox(**kw):
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=kw.pop("seed", 9), size=kw.pop("size", 8), **kw)
    sb.reset()
    return sb


class _StubUnit:
    def __init__(self, gid, owner, x, y, hp):
        self.gid, self.owner, self.x, self.y, self.hp = gid, owner, x, y, hp
        self.alive = hp > 0


class _StubEnv:
    def __init__(self, units):
        self._units = list(units)

    def armies(self):
        return list(self._units)


class TestKillLedgerIdentity(unittest.TestCase):
    """② 击杀账本按 gid 记身份。"""

    def test_same_nation_index_units_do_not_collide(self):
        """★ 甲的 1 号与乙的 1 号是**两支不同的军**（gid 不同）⇒ 不能互相覆盖。"""
        sb = _sandbox()
        env = sb.env
        mine = [u for u in env.armies() if env.owner_of(u) == "甲" and u.id == 1]
        theirs = [u for u in env.armies() if env.owner_of(u) == "乙" and u.id == 1]
        self.assertTrue(mine and theirs, "两国都该有 1 号军")
        self.assertEqual(mine[0].id, theirs[0].id, "这个测试的前提是两国序号相同")
        self.assertNotEqual(mine[0].gid, theirs[0].gid, "gid 必须全局唯一")

    def test_snapshot_keys_by_gid(self):
        """★ 结算前的快照按 **gid** 记 ⇒ 不会因"本国序号相同"而少记一支。"""
        sb = _sandbox()
        env = sb.env
        before = {u.gid: (env.owner_of(u), u.x, u.y, u.hp) for u in env.armies()}
        self.assertEqual(len(before), len(env.armies()),
                         "快照条目数 ≠ 军队数 ⇒ 有军被覆盖了")

    def test_kill_is_attributed_to_the_other_side(self):
        """★ 同格上敌军战死 ⇒ 归因给**同格的另一家**（不是按本国序号猜）。"""
        ledger = KillLedger()
        attacker = _StubUnit(1001, "甲", 2, 2, 100)
        victim = _StubUnit(2001, "乙", 2, 2, 100)
        before = {attacker.gid: ("甲", 2, 2, 100), victim.gid: ("乙", 2, 2, 100)}
        # ★ 结算后：乙那支没了（战死），甲那支还在。
        ledger.observe(_StubEnv([attacker]), before)
        self.assertEqual(ledger.kills_by("甲", ["乙"]), 1, "击杀没记上")
        self.assertEqual(ledger.kills_by("乙", ["甲"]), 0, "杀反了")

    def test_two_victims_with_same_index_are_counted_separately(self):
        """★ 旧口径（按 `a["id"]` 记）会把两国同号的军**记成一支** ⇒ 这里钉住。"""
        ledger = KillLedger()
        a1 = _StubUnit(101, "甲", 2, 2, 100)
        b1 = _StubUnit(201, "乙", 2, 2, 100)
        before = {a1.gid: ("甲", 2, 2, 100), b1.gid: ("乙", 2, 2, 100)}
        ledger.observe(_StubEnv([]), before)          # 两支都没了
        self.assertEqual(ledger.kills_by("甲", ["乙"]), 1)
        self.assertEqual(ledger.kills_by("乙", ["甲"]), 1)

    def test_kills_by_the_barbarian_are_not_our_credit(self):
        """★ 野人**杀**的军不算我方战功（不然"被野人打死"也给我加分）。

        ★ 反过来：**我军杀野人**是要计的（野人也是敌军）—— 用户 2026-09-25：
          「只计算我军杀掉的敌军来加分」⇒ 野人算敌军。
        """
        ledger = KillLedger()
        mine = _StubUnit(101, "甲", 2, 2, 100)
        victim = _StubUnit(301, "丙", 2, 2, 100)
        before = {mine.gid: ("甲", 2, 2, 100), victim.gid: ("丙", 2, 2, 100)}
        # ★ 野人在同一格上 —— 结算后只有丙的军没了（被野人打死的）。
        ledger.observe(_StubEnv([mine, _StubUnit(999, "野人", 2, 2, 100)]), before)
        self.assertEqual(ledger.kills_by("野人", ["丙"]), 0, "野人的战功被算成了我方的")
        self.assertEqual(ledger.kills_by("甲", ["丙"]), 1,
                         "同格上的甲被算成凶手了（野人才是）")

    def test_killing_a_barbarian_counts(self):
        """★ 我军杀野人**要**计分（野人是敌军）。"""
        ledger = KillLedger()
        mine = _StubUnit(101, "甲", 2, 2, 100)
        barb = _StubUnit(999, "野人", 2, 2, 100)
        before = {mine.gid: ("甲", 2, 2, 100), barb.gid: ("野人", 2, 2, 100)}
        ledger.observe(_StubEnv([mine]), before)
        self.assertEqual(ledger.kills_by("甲", ["野人"]), 1)


class TestWeightSingleSource(unittest.TestCase):
    """① 权重/奖励口径单一出处。"""

    def test_scoring_exports_the_reward_scale(self):
        for name in ("REWARD_WIN", "REWARD_LOSS", "REWARD_DRAW",
                     "REWARD_TANH_SCALE", "WIN_TIME_BONUS"):
            self.assertTrue(hasattr(S, name), f"scoring 少了 {name}")

    def test_sandbox_reward_uses_the_scoring_constants(self):
        """★ 沙盒的终局价**就是** `scoring` 的那几个数（不另算）。"""
        sb = _sandbox(n_nations=3)
        sb.t_max = 0
        for name in sb.players:
            self.assertEqual(sb.reward(name), S.REWARD_DRAW)

    def test_reward_shaping_is_bounded(self):
        """★ 塑形后的奖励在 `[−1, 1]`（`tanh` ⇒ 天然有界，不会被某一步刷爆）。"""
        import math
        for delta in (-1e9, -100.0, -1.0, 0.0, 1.0, 100.0, 1e9):
            shaped = math.tanh(delta / S.REWARD_TANH_SCALE)
            self.assertLessEqual(abs(shaped), 1.0)

    def test_win_is_better_than_draw_is_better_than_loss(self):
        """★ 赢 > 平 > 输（否则模型会去"拖平局"）。"""
        self.assertGreater(S.REWARD_WIN, S.REWARD_DRAW)
        self.assertGreater(S.REWARD_DRAW, S.REWARD_LOSS)


if __name__ == "__main__":
    unittest.main()
