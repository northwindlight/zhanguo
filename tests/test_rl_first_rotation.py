# -*- coding: utf-8 -*-
"""`sb.first`（先手）必须**管满一整局**，两人局也不例外 —— 2026-09-28 那一刀的守卫。

★ 病灶（PLAN §12.24）：`_turn_order()` 两人局写的是 `return alive`
  —— 直接返回固定的 `self.players` 顺序，**把 `self.first` 丢了**。
  而 `first` 只在 `reset()` 里建过 `pending`（turn 0），回合边界重建全靠本函数
  ⇒ **一局里只有第 1 个回合认先手**。
  ★ 实测（`size=12`、`first='乙'` 的一整局 60 回合）：**乙先动 1 次、甲先动 59 次**。

★ 它为什么会咬人（不是"不公平"这么轻）：
  ① docstring 自己写的补偿「两人局局内固定顺序，先手优势由**跨局**轮换 `first` 摊平」
     ≈ 失效 —— 只覆盖 1/40 个回合；
  ② **每一回合都是同一个座位先亮牌**（后手每回合都能看着已落子的局面应对）
     ⇒ 席位胜率实测 甲 44.1% vs 乙 55.9%（487 局，z=−2.58）；
  ③ ★ **连带**：`info["first"] = sb.first` ⇒ 日志的 `先手胜` 和 `_streak`
     （"先手连赢 N 局就炸"）闸门**都在看一个不控制胜负的变量** ——
     实测 `先手胜` 47.8%（z=−0.95，"很健康"）是**构造出来的假象**，
     闸门结构上抓不到它本来要抓的东西。

★★ 这里钉**四件事**，缺一不可：
  ① 两人局**每个回合**都认 `first`（这是本 bug 本身）；
  ② 两人局**局内固定**、**不许**变成每回合轮换（用户 2026-09-27 的口径，别修过头）；
  ③ 跨局轮换**真的改变了谁先动**（端到端跑真局，不是只看 `_turn_order()`）；
  ④ **≥3 人那条路一步都不许动**（`mp_run.py` 的 `(turn-1)%len(alive)` 口径）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox                          # noqa: E402


def _mk(*, first=None, n=2, size=10, t_max=20, seed=5) -> Sandbox:
    return Sandbox(seed=seed, size=size, t_max=t_max, n_nations=n, first=first,
                   halls_known=True, territory=True, alliances="none").reset()


def _turns(sb: Sandbox, k: int) -> list[list[str]]:
    """推进 `k` 个回合，返回每回合的行动顺序。"""
    out = []
    for _ in range(k):
        sb.end_turn()
        sb.pending = sb._turn_order()
        out.append(list(sb._turn_order()))
    return out


def _play(sb: Sandbox, max_steps=20000) -> dict:
    """把一局走完（每步挑第一个合法动作），返回「每个回合第一个**真正动过**的人」。"""
    turn_first = {}
    n = 0
    while not sb.is_terminal() and n < max_steps:
        n += 1
        sb._auto_advance()
        if sb.is_terminal():
            break
        name = sb.current_player()
        if name is None:
            break
        turn_first.setdefault(sb.turn, name)
        acts = sb.legal()
        if not acts:
            sb.pending.pop(0)
            continue
        sb.step(acts[0])
    return turn_first


class TestFirstGovernsTheWholeGame(unittest.TestCase):

    def test_two_player_order_honours_first_on_every_turn(self):
        """① 本 bug 本身：`first='乙'` ⇒ **每个回合**都该是乙先动。"""
        sb = _mk(first="乙")
        for i, order in enumerate(_turns(sb, 6), start=1):
            self.assertEqual(
                order[0], "乙",
                f"第 {i} 个回合的先手变成了 {order[0]}（`_turn_order()` 又把 "
                f"`self.first` 丢了 —— 只有第 1 个回合认先手，就是 PLAN §12.24 那个 bug）")

    def test_two_player_order_is_fixed_within_the_game(self):
        """② 别修过头：两人局是**局内固定**，不是每回合来回换（用户口径）。"""
        for who in ("甲", "乙"):
            sb = _mk(first=who)
            other = "乙" if who == "甲" else "甲"
            for i, order in enumerate(_turns(sb, 6), start=1):
                self.assertEqual(
                    order, [who, other],
                    f"first='{who}' 第 {i} 个回合的顺序是 {order} —— 两人局必须"
                    f"**局内固定** [{who!r}, {other!r}]，不许变成 (turn-1)%2 的轮换")

    def test_the_rotation_is_not_vacuous(self):
        """③ 非空泛性：换一个 `first`，行动顺序必须**真的**不一样。

        没有这条，一个把 `_turn_order()` 写死成常量的实现也能让上面两条全绿。
        """
        a = _turns(_mk(first="甲"), 3)
        b = _turns(_mk(first="乙"), 3)
        self.assertNotEqual(a, b, "换 `first` 顺序却一模一样 —— 用例或接线有问题")
        self.assertEqual(a[0][0], "甲")
        self.assertEqual(b[0][0], "乙")

    def test_whole_game_actually_flips_who_moves_first(self):
        """③（端到端）跑**真局**，数每个回合第一个动的人。

        ★ 为什么还要这一条：`_turn_order()` 对了，未必等于**真回路**用的是它
          （`_auto_advance` 里还有一层 `pending` 搬运）。实测 bug 版是
          「甲 59 / 乙 1」（**1.7%**），修好后应接近满值；阈值取 80% 留出
          「乙这一步没动作可做、被 `_auto_advance` 跳掉」的余量。
        """
        sb = _mk(first="乙", size=12, t_max=25)
        tf = _play(sb)
        self.assertGreater(len(tf), 4, "用例前提：得跑出几个回合来")
        n_b = sum(1 for v in tf.values() if v == "乙")
        frac = n_b / len(tf)
        self.assertGreaterEqual(
            frac, 0.8,
            f"`first='乙'` 的一整局 {len(tf)} 个回合里，乙只在 {n_b} 个回合先动 "
            f"（{frac:.1%}）—— `first` 没管满全局（PLAN §12.24 那个 bug 回来）")


class TestThreePlayerRotationIsUntouched(unittest.TestCase):
    """④ 我的修复**只该动两人局那条分支** —— ≥3 人走的是 `mp_run.py` 的轮换公式。"""

    def test_three_player_firsts_rotate(self):
        sb = _mk(first="丙", n=3, size=12)
        alive = [n for n in sb.players if sb.alive(n)]
        self.assertEqual(len(alive), 3, "用例前提：这几个回合里不该有人死")
        seq = _turns(sb, 4)
        for order in seq:
            self.assertEqual(sorted(order), sorted(alive), "顺序必须是全部活人")
        firsts = [o[0] for o in seq]
        self.assertEqual(
            len(set(firsts)), 3,
            f"三人局 4 个回合的先手是 {firsts} —— 必须是 (turn-1)%len(alive) 的"
            f"**轮换**（每回合换人），不许被 `self.first` 钉死"
            f"（那是我修两人局时最容易漏掉的越界）")
