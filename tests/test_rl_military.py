# -*- coding: utf-8 -*-
"""`rl/military.py`（沙盒里那个"会动的老师"）的**最小**守卫 —— 只有一条。

★★ **用户 2026-09-25 的裁决（别再拿它当对照）**：
    「其实这个测试也不是很需要，**因为这个老师不能代表任何东西，这个老师不会打仗，
      也不作为基线**」。
  ⇒ 它是沙盒自检/`rollout` 用的"能走两步的老师"，**不是基线**；
    PLAN §七 第 8 条里真正的对照线是**纯打分贪心**。**别拿它的成绩报数。**

那为什么还留一条？因为 2026-09-25 实测它**曾经静默返回零动作**
（`sandbox.rollout()` 200 回合 0 动作、判平，而**不报错**）—— 根因是
`targets(defend=True)` 把**自家核心**（一个**零代价**目标）放进候选池，
最小代价指派于是把**全军认领自家核心** ⇒ `best_step` 全 `None`。
"看着活着、其实死了"这一类正是最该钉的，代价又只有一行。
★ 它**只**钉"不是 no-op"，**不**钉水平（水平它没有）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.sandbox import Sandbox            # noqa: E402


class TestTeacherIsNotANoOp(unittest.TestCase):
    """★ 只此一条：老师**必须真的动手**（不保证打得好，保证不是死的）。"""

    def test_ai_turn_and_rollout_produce_actions(self):
        for n in (2, 3):
            sb = Sandbox(seed=0, size=8, n_nations=n).reset()
            self.assertTrue(sb.ai_turn(sb.players[0]),
                            f"n={n}：`ai_turn` 零动作 ⇒ 又退回「全军认领自家核心」了")
            out = sb.rollout()
            n_acts = sum(len(a) for _, _, a in out["actions"])
            self.assertGreater(n_acts, 10,
                               f"n={n}：整局只有 {n_acts} 个动作 ⇒ 这条路径又死了")


if __name__ == "__main__":
    unittest.main()