# -*- coding: utf-8 -*-
"""★★ **截断处的自举值**（`Step.boot`）—— 截断不是终局，价值**不该按 0 算**。

用户 2026-09-25 问「一局没打完的…纳入 ppo 吗」时查出来的真问题：

    `gae` 里 `next_v = 0.0 if (t == n-1 or dones[t]) else values[t+1]`
    —— 对**真终局**是对的（局结束了，价值就是 0），
       但**截断**（超步数上限）**不是终局**，那个局面照样有值。

★ 后果不是"少学一点"，是**学错**：这套奖励是**势函数差分**，
  返回值近似 `Φ(s_T) − Φ(s_0)` ⇒ 用 0 自举等于宣称那个局面**一文不值**。
  而截断在这套配置下是**常态** ⇒ critic 被持续教成 `V≈0`
  ⇒ 优势退化成"当步的势函数差分" ⇒ **策略变近视，白扔 critic 的前瞻**。
  ★ 而 loss 会照常下降、日志上**什么异常都没有** —— 典型的静默学错。

⇒ 修法：截断处用 **`Φ(s_T)`（当时的分数）** 自举；真终局仍然是 0。

★ 钉三件事：
  1. 截断局的最后一步 `boot` **非空、且等于当时的分数**（不是 0、不是 None）；
  2. `gae` **真的用了它**（拿两个不同的 boot 算出不同的 return —— 反向对照）；
  3. 真终局那一步 `boot` 是 `None`（⇒ 按 0 自举，不能被我顺手改成别的）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.model import build_model                      # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402
from rl.train import _score, collect_episode, gae     # noqa: E402


def _sb(size=8, n=3, t_max=500, seed=3):
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=t_max,
                   halls_known=True).reset()


def _nets(sb):
    torch.manual_seed(0)              # ★ 钉种子：动作是 rng 从网络 probs 里采的
    return {p: build_model() for p in sb.players}


class TestTruncateBootstrap(unittest.TestCase):
    def test_truncated_tail_carries_the_score(self):
        sb = _sb()
        steps, info = collect_episode(_nets(sb), sb, max_steps=10,
                                      rng=np.random.default_rng(0))
        self.assertTrue(info["truncated"], "用例前提：这局该被截断")
        boot = steps[-1].boot
        self.assertIsNotNone(boot, "截断处没有自举值 ⇒ 会按 0 算")
        self.assertNotEqual(boot, 0.0, "自举值恰好是 0 —— 那正是要避免的那个值")
        # ★ 它必须**就是当时的分数**（不是随便一个数）
        self.assertAlmostEqual(boot, _score(sb, steps[-1].player), places=6,
                               msg="自举值不等于 `Φ(s_T)`")
        # ★ 反向对照：`Φ(s_T)` 确实不是 0（否则上面那条"非 0"是空的）
        self.assertGreater(abs(boot), 1e-6, "这局的势函数恰好为 0 ⇒ 用例测不出东西")

    def test_gae_actually_uses_the_bootstrap(self):
        """★★ **反向对照**：同一个 step，换一个 boot ⇒ return 必须跟着变。

        没有这一条，"`boot` 非空"可能只是存了个没人读的字段。
        """
        rew = [0.0, 0.0]
        val = [1.0, 1.0]
        done = [False, True]
        a, _ = gae(rew, val, done, boots=[None, 0.0])
        b, ret_b = gae(rew, val, done, boots=[None, 5.0])
        self.assertNotAlmostEqual(float(a[-1]), float(b[-1]), places=6,
                                  msg="`boots` 没被 `gae` 用上 ⇒ 字段是死的")
        # ★ `ret = adv + values`，边界步 `adv = r + γ·boot − V` ⇒ `ret = r + γ·boot`
        #   （此处 r=0、V=1、boot=5、γ=0.99 ⇒ **4.95**，不是 5.0 —— 我第一版就写错了）
        self.assertAlmostEqual(float(ret_b[-1]), 0.99 * 5.0, places=6,
                               msg="边界步的 return 该是 `r + γ·boot`")

    def test_true_terminal_has_no_boot(self):
        """★ 真终局那一步 `boot` 必须是 `None` ⇒ 仍按 **0** 自举。"""
        sb = _sb(size=8, n=3, t_max=12)
        steps, info = collect_episode(_nets(sb), sb, max_steps=None,
                                      rng=np.random.default_rng(0))
        if info["truncated"]:            # 万一没打完就跳过（别把用例做成假绿）
            self.skipTest("这局被截断了，测不到终局那一支")
        self.assertTrue(steps[-1].done)
        self.assertIsNone(steps[-1].boot,
                          "真终局不该带自举值（局已结束，价值就是 0）")


if __name__ == "__main__":
    unittest.main()