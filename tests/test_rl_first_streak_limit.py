# -*- coding: utf-8 -*-
"""「先手连赢」闸门的**阈值校准** —— 2026-09-28 从 5 改成 8 的守卫。

★ 为什么 5 不对（不是"感觉松"，是**算得出来**）：
  实测 `先手胜 ≈ 0.45`（修好 `sb.first` 之后才有意义，之前那个数**量的是 turn 0**）。
  若各局独立，N 连的概率是 `0.45^N`：

  | 阈值 | 单局概率 | 528 局期望误报 | |
  |---|---|---|---|
  | **5** | 1.8% | **~9 次** | 全是巧合 ⇒ 闸门响了说明不了任何事 |
  | **8** | 0.17% | ~0.9 次 | 才像个红旗 |

  实测对得上：528 局里 5 连的闸门响了 **2 次**，而当时 `先手胜` 是 47.8%（z=−0.95，
  即**根本没有先手优势**）⇒ 那两次**纯噪声**，代价是**炸进程 + 重启 + 重跑那一段**。

★ 用户 2026-09-28 拍板：「2 可以改成 8，当时拍脑袋定的，5 偶然性太高，那么改成 8 也合适」。

★★ 这个文件钉**三件事**：
  ① 函数签名与 CLI 的默认值**都是 8**，且**两者不许各自漂**（它们是两个独立的字面量，
     只改一个是最容易犯的错）；
  ② 闸门**真的会响**（非空泛性）—— 一个把 `_streak` 写死返回 0 的实现也能让 ① 全绿；
  ③ **8 这个数是按误报率算出来的**，不是魔数：把 500 局期望误报压在 2 次以下 ⇒
     谁要是改回 5，③ 会红并告诉他为什么。
"""
from __future__ import annotations

import inspect
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import train as T                              # noqa: E402

# ★ 500 局（≈ 一炉受控实验的规模）上，期望误报次数不许超过这个数。
#   按 `先手胜` 实测 0.45 算 —— 这是"闸门响了值不值得信"的唯一判据。
MAX_FALSE_ALARMS = 2.0
FIRST_WIN_P = 0.45
GAMES = 500


class TestFirstStreakLimitIsCalibrated(unittest.TestCase):

    def test_signature_default_is_eight(self):
        got = inspect.signature(T.train).parameters["first_streak_limit"].default
        self.assertEqual(got, 8, f"`train()` 的缺省是 {got} —— 用户 2026-09-28 拍的是 8")

    def test_cli_default_matches_the_signature(self):
        """★ 两个独立的字面量，**只改一个**是最容易犯的错。"""
        src = inspect.getsource(T)
        m = re.search(r'"--first-streak-limit",\s*type=int,\s*default=(\d+)', src)
        self.assertIsNotNone(m, "找不到 `--first-streak-limit` 的 argparse 定义 —— "
                                "是不是被改名/挪走了？这个守卫要跟着改")
        cli = int(m.group(1))
        sig = inspect.signature(T.train).parameters["first_streak_limit"].default
        self.assertEqual(cli, sig,
                         f"CLI 缺省 {cli} ≠ 函数缺省 {sig} —— 两个必须一致，"
                         f"否则「命令行不给参数」和「直接调 train()」跑的是两套阈值")

    def test_the_threshold_keeps_false_alarms_under_control(self):
        """③ 8 是**算出来的**，不是魔数。改回 5 会在这里红，并看到为什么。"""
        sig = inspect.signature(T.train).parameters["first_streak_limit"].default
        expect = GAMES * FIRST_WIN_P ** sig
        self.assertLess(
            expect, MAX_FALSE_ALARMS,
            f"阈值 {sig}：{GAMES} 局里期望误报 {expect:.1f} 次（上限 {MAX_FALSE_ALARMS}）—— "
            f"`先手胜≈{FIRST_WIN_P}` 时 {sig} 连基本是巧合，闸门响了说明不了任何事，"
            f"却要炸进程重跑。这正是不用 5 的原因（5 ⇒ 期望 ~9 次）")

    def test_the_gate_actually_fires(self):
        """② 非空泛性：`_streak` 得真的数得出来。"""
        infos = [{"first": "甲", "winner_members": ("甲",)} for _ in range(8)]
        state: dict = {}
        self.assertEqual(T._streak(infos, state, "__first__"), 8,
                         "8 局先手全赢，`_streak` 该返回 8")
        # ★ 反向：**没赢的那一局必须把连击清零**（平局同理 —— 没有胜方就不是先手赢）
        state2: dict = {}
        T._streak(infos[:3] + [{"first": "甲", "winner_members": ()}], state2, "__first__")
        self.assertEqual(state2["__first__"], 0, "平局/先手没赢没把连击清零")

    def test_the_gate_counts_the_games_own_first_mover(self):
        """★ 判据是**该局自己的先手**，不是写死的某一方（`_streak` 的 docstring）。"""
        infos = [{"first": "乙", "winner_members": ("乙",)} for _ in range(4)]
        self.assertEqual(T._streak(infos, {}, "__first__"), 4,
                         "先手是乙、赢的也是乙 ⇒ 该算连击")
        # 先手是乙、赢的是甲 ⇒ 不算
        self.assertEqual(
            T._streak([{"first": "乙", "winner_members": ("甲",)}], {}, "__first__"), 0,
            "先手乙而甲赢 ⇒ 不是「先手赢」，该清零")
