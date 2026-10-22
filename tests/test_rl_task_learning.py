# -*- coding: utf-8 -*-
"""★★ **小型可控任务：概率必须向正确选择集中**（报告 §9.1 第 2 条）。

★ 报告 §9.1 的退出条件第 ② 条：
  「**小型可控任务** —— 无随机战斗**或答案可枚举**的
    集结 / 增援 / 回防 / 攻取目标，**确认概率向正确选择集中**。」

★ 为什么要有这一条（报告 §10 的纪律）：
  「**全量测试绿**」只说明**数据流**是对的，**不说明模型能学**。
  一个**答案可枚举**的小任务，能把"学不到"**定位**到具体环节
  （奖励没进样本 / 优势没信号 / 动作分布被偏置 / 温度口径不一致…）。

★ 走的是**真家伙**：观测走 `encode.obs_of`、网络是真网、更新走 `rl.train.ppo_update`
  ⇒ 测的是**真实训练方法**，不是另写一套。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl import tasks                              # noqa: E402
    from rl.model import build_model                  # noqa: E402
    from rl import train as T                         # noqa: E402
else:
    tasks = build_model = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


class TestDefenseTaskIsWellFormed(unittest.TestCase):
    """任务本身：答案**唯一**、局面**确定**、奖励**可枚举**。"""

    def test_correct_candidate_is_unique(self):
        """★ 「回防」的正确候选**唯一**（⇒ 答案可枚举）。"""
        sb, me, cell = tasks.defense_fixture()
        correct = tasks.correct_candidates(sb, me, cell)
        self.assertEqual(len(correct), 1,
                         f"正确候选应当唯一，实际 {correct}")
        self.assertEqual(sb.legal()[correct[0]][1], "attack")

    def test_fixture_is_deterministic(self):
        """★ 同一个 seed ⇒ **同一个局面**（没有骰子）。"""
        a = tasks.defense_fixture(seed=3)
        b = tasks.defense_fixture(seed=3)
        self.assertEqual(a[0].legal(), b[0].legal())
        self.assertEqual(a[2], b[2])

    def test_threat_is_real(self):
        """★ 威胁**真的存在**：敌国军队**紧贴**我的核心、且那一格**不可走**。"""
        sb, me, cell = tasks.defense_fixture()
        acts = sb.legal()
        self.assertTrue(any(a[1] == "attack" and (a[2], a[3]) == cell for a in acts),
                        "用例前提：应当能攻击那支敌军")
        # ★ 敌占格**不可走** ⇒ "走过去挡一下"这种着法**不存在**（答案唯一的依据）
        self.assertFalse(any(a[1] == "move" and (a[2], a[3]) == cell for a in acts),
                         "敌占格竟然可以走进去 ⇒ 答案就不唯一了")

    def test_task_reward_has_no_combat_randomness(self):
        """★ 任务奖励只说「**选对没有**」⇒ 不掺战斗随机数。"""
        self.assertEqual(tasks.task_reward(0, [0]), 1.0)
        self.assertEqual(tasks.task_reward(1, [0]), 0.0)
        self.assertEqual(tasks.task_reward(0, []), 0.0)


class TestProbabilityConcentratesOnTheRightChoice(unittest.TestCase):
    """★★ 报告 §9.1 第 ② 条的**主验收**。"""

    def test_probability_mass_moves_to_the_correct_candidate(self):
        """★★★ 短训之后，**正确候选上的概率质量**必须明显上升。

        ★ 判据（两条都要）：
          ① 训练后 **> 0.8**（"向正确选择**集中**"）；
          ② **明显高于**未训练网（**同一个** net 的训练前读数）——
             免得"恰好初始就很高"蒙混过关。
        """
        out = tasks.train_task(iters=60, batch=24, seed=0)
        trace = out["trace"]
        self.assertTrue(trace)
        first, last = trace[0], trace[-1]
        self.assertGreater(last, 0.8,
                           f"训完正确候选的概率质量只有 {last:.3f} ⇒ 没学会")
        self.assertGreater(last, first + 0.3,
                           f"训练前 {first:.3f} → 训练后 {last:.3f} ⇒ 几乎没有变化")

    def test_untrained_net_is_not_already_right(self):
        """★ **对照**：未训练网在这个局面上**不该**已经全押正确候选
        （否则上一条就是假绿）。"""
        sb, me, cell = tasks.defense_fixture()
        acts = sb.legal()
        correct = tasks.correct_candidates(sb, me, cell)
        torch.manual_seed(0)
        net = build_model()
        with torch.inference_mode():
            logits, _v = net(T.collate([tasks.E.obs_of(sb, me, acts)]))
            probs = torch.softmax(logits[0], -1).numpy()
        self.assertLess(float(probs[correct].sum()), 0.8,
                        "未训练网就已经全押正确候选 ⇒ 对照失效")


if __name__ == "__main__":
    unittest.main()
