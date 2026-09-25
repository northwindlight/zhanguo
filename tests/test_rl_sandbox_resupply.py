# -*- coding: utf-8 -*-
"""沙盒**补员**规则的守卫。

用户 2026-09-25：「以前民兵是**无上限增长**的，改成**上限是步兵 2 倍**」。
★ 这是**训练环境**的规则，不是引擎的（引擎里民兵另有"只能军屯征召、
  总数 ≤ 军屯数"那一套，沙盒为了不让模型被经济层卡住而绕过了它）。

★★ 为什么这条值得一个守卫（不只是"改个数"）：
   原来"不缺员 ⇒ 出民兵"那一支是 `spawn(quota)` **无上限**，于是
   **只要不打仗，民兵每 5 回合净增 `厅×1` 支，永远收支为正** ——
   那不是"守家增益"，是一台**不需要对手配合的印钞机**：
   打分器里 `W_ARMY` 那一项会一路涨 ⇒ 模型学到的是"别打仗、苟着刷民兵"。

钉的四件事：
  1. **上限真的封在 步兵×2**（跑够多轮也不越线）；
     ★ 带**反向对照**：把常量调大，它就该继续出 —— 证明"被封住"是**这个常量**干的。
  2. **满编后一支都不出**（不是"慢慢出"，是**停**）。
  3. **步兵减员 ⇒ 上限跟着降，但已编民兵不被裁**（只限增长，不裁撤）。
  4. **缺员分支不受影响**（步兵没满就补步兵，不出民兵）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import sandbox as SB                       # noqa: E402
from rl.sandbox import Sandbox                     # noqa: E402


def _sb(seed=3, size=8, n=3):
    return Sandbox(seed=seed, size=size, n_nations=n).reset()


def _fill_foot(sb, me):
    """把步兵拉满 ⇒ 补员走「不缺员 ⇒ 出民兵」那一支。返回步兵数。

    ★ 开局本来就是 `BASE_CAP` 支、而 `cap_of` 开局恰好也是 5 ⇒ **本来就满**；
      这里只是把它写实（免得将来 BASE_CAP/cap_of 一改，用例悄悄变成空跑）。
    """
    foot = sb.cap_of(me)
    have = sb.count_of(me, "步")
    if have < foot:
        sb.spawn(me, foot - have, kind="步")
    sb.turn = SB.RESUPPLY_EVERY          # ★ 必须落在补员回合上，否则 `resupply` 直接返回
    assert sb.count_of(me, "步") == foot, f"步兵没拉满：{sb.count_of(me, '步')} vs {foot}"
    return foot


def _kill_foot(sb, me, n):
    """打掉 `n` 支步兵（直接改 `world`，形状等价于战损）—— 用来造"缺员"。"""
    ids = [id(a) for a in sb.world.armies
           if a["owner"] == me and a.get("type", "步") == "步"][:n]
    sb.world.armies = [a for a in sb.world.armies if id(a) not in set(ids)]


class TestMilitiaCap(unittest.TestCase):
    def test_capped_at_2x_foot(self):
        sb = _sb()
        me = sb.players[0]
        foot = _fill_foot(sb, me)
        over = 0
        for _ in range(20):                       # 每轮 厅×1=2 支，20 轮足够撞上限
            sb.resupply()
            over += max(0, sb.count_of(me, "民") - SB.MILITIA_PER_FOOT * foot)
        self.assertEqual(over, 0, "中途越过上限了")
        self.assertEqual(sb.count_of(me, "民"), SB.MILITIA_PER_FOOT * foot,
                         "跑够多轮后应当**恰好**停在 步兵×2 上")

    def test_constant_is_what_binds(self):
        """★ **反向对照**：把常量调大 ⇒ 它就该出更多。

        没有这一条，上面那条"停在 10 支"可能只是"跑得还不够多"的假象。
        """
        sb = _sb()
        me = sb.players[0]
        foot = _fill_foot(sb, me)
        with mock.patch.object(SB, "MILITIA_PER_FOOT", 5):
            for _ in range(20):
                sb.resupply()
        self.assertEqual(sb.count_of(me, "民"), 5 * foot,
                         "常量调大后上限**必须**跟着动 —— 否则被别的东西卡住了")

    def test_full_roster_spawns_nothing(self):
        """满编后是**停**，不是"慢慢出"。"""
        sb = _sb()
        me = sb.players[0]
        foot = _fill_foot(sb, me)
        for _ in range(20):
            sb.resupply()
        n_at_cap = sb.count_of(me, "民")
        mine_before = len(sb.armies_of(me))
        notes = sb.resupply()
        self.assertEqual(sb.count_of(me, "民"), n_at_cap, "满编后还在出民兵")
        self.assertEqual(len(sb.armies_of(me)), mine_before, "满编后我这国的军队还在涨")
        mine = [t for t in notes if t.startswith(me)]
        self.assertTrue(any("满编" in t for t in mine),
                        f"满编时该说清楚为什么没出（拿到的是 {mine}）")

    def test_attrition_lowers_the_cap_but_does_not_cull(self):
        """步兵减员 ⇒ 上限跟着降；**已编民兵保留**（只限增长，不裁撤）。"""
        sb = _sb()
        me = sb.players[0]
        foot = _fill_foot(sb, me)
        for _ in range(20):
            sb.resupply()
        mil = sb.count_of(me, "民")
        _kill_foot(sb, me, foot // 2)             # 打掉一半步兵
        now_foot = sb.count_of(me, "步")
        self.assertLess(now_foot, foot, "减员没生效，这个用例是空跑的")
        sb.resupply()
        self.assertEqual(sb.count_of(me, "民"), mil,
                         "现役民兵**不许**因为步兵死了就被裁掉（只限增长）")

    def test_understrength_still_gets_infantry(self):
        """缺员分支不受影响：步兵没满 ⇒ 补步兵，民兵一支不出。"""
        sb = _sb()
        me = sb.players[0]
        # ★ 开局**本来就满员**（`BASE_CAP` == `cap_of` 的开局值）⇒ 得先打掉几支
        #   才谈得上"缺员"。不这么做，这个用例会**静默走成民兵分支**（假绿）。
        _kill_foot(sb, me, 2)
        sb.turn = SB.RESUPPLY_EVERY
        before = sb.count_of(me, "步")
        self.assertLess(before, sb.cap_of(me), "没造出缺口，这个用例是空跑的")
        notes = sb.resupply()
        self.assertEqual(sb.count_of(me, "民"), 0, "缺员时不该出民兵")
        self.assertGreater(sb.count_of(me, "步"), before, "缺员时该补步兵")
        self.assertTrue(any("缺员" in t for t in notes if t.startswith(me)))


if __name__ == "__main__":
    unittest.main()