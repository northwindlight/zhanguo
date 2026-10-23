# -*- coding: utf-8 -*-
"""★★ **截断处的自举值**（`Step.boot`）—— 截断不是终局，价值**不该按 0 算**。

用户 2026-09-25 问「一局没打完的…纳入 ppo 吗」时查出来的真问题：

    `gae` 里 `next_v = 0.0 if (t == n-1 or dones[t]) else values[t+1]`
    —— 对**真终局**是对的（局结束了，价值就是 0），
       但**截断**（超步数上限）**不是终局**，那个局面照样有值。

★ 后果不是"少学一点"，是**学错** ⇒ 截断在这套配置下是常态
  ⇒ critic 被持续教成 `V≈0` ⇒ 优势退化成"当步的势函数差分"
  ⇒ **策略变近视，白扔 critic 的前瞻**。
  ★ 而 loss 会照常下降、日志上**什么异常都没有** —— 典型的静默学错。

★★★ **2026-10-22 改口径（这一版把上一版钉错的东西改回来了）。**
  上一版（本文件的老内容）写的是「截断处用 **`Φ(s_T)`（当时的分数）** 自举」，
  还专门加了一条"单位守卫"（`boot` 必须等于 `Φ / REWARD_TANH_SCALE`）——
  **那一条把错误钉成了"正确"** ⇒ 后人照着它写代码只会继续错。

  为什么 `Φ/200` 是错的：
    · `Φ` 是**势函数**（打分器给的"局面分"），**不是价值**（"从这里往后能拿多少"）。
      两者量纲不同、口径不同 ⇒ 拿势函数当价值灌进 critic，
      critic 学的是"这局面分多高"，而策略梯度要的是"这局面能赚多少"。
    · 势函数**可以被人为抬**（`scoring` 里那些权重就是可调的）⇒
      自举值跟着权重漂 ⇒ **同一个截断点，不同权重下有不同的 boot** ⇒
      价值目标与真实回报脱钩，而且**不报错**。
    · 还有更硬的：**截断的那一刻，对手还没动的那些回合**根本不在 `Φ` 里，
      而它们照样会影响最终结果 ⇒ `Φ` 对"后继状态"是**不完整**的估计。

  ⇒ 现在的口径：**截断处的自举值 = 网络对「合法边界观测」的价值预测**
    （当前策略 + 该玩家当前的记忆状态），见 `rl/train.py::_value_at`。
    真终局仍然是 `boot=None`（后继价值 0）。

★★ 钉四件事：
  1. 截断局的最后一步 `boot` **非空**，且**等于网络的价值预测**
     （用**可控价值函数**返回 0.25 ⇒ 就该是 0.25 —— 不是 `Φ/200`、不是 0、不是 None）；
  1b. ★ **`_score` 不参与**：把它打成很大值（甚至禁止调用），
      截断处的 `boot` **一点不变** ⇒ 势函数与价值彻底解耦；
  2. `gae` **真的用了它**（拿两个不同的 boot 算出不同的 return —— 反向对照）；
  3. 真终局那一步 `boot` 是 `None`（⇒ 按 0 自举，不能被我顺手改成别的）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl.model import build_model                      # noqa: E402
    from rl.sandbox import Sandbox                        # noqa: E402
    from rl.train import collect_episode, gae             # noqa: E402
    from rl import scoring as SC                          # noqa: E402
else:
    Sandbox = build_model = scoring = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口（见上面的说明）。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _sb(size=8, n=3, t_max=500, seed=3):
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=t_max,
                   halls_known=True).reset()


def _nets(sb):
    torch.manual_seed(0)              # ★ 钉种子：动作是 rng 从网络 probs 里采的
    return {p: build_model() for p in sb.players}


class _FixedValueNet:
    """★ **可控价值函数**：把 value 钉成固定值（其余照常走真网络）。

    ★ 报告 §5.3 的验收口径就是这个：「用可控价值函数返回 0.25 ⇒
      截断样本使用对应的 0.25」—— 断言的是**网络说了算**，
      而不是"某个势函数除以某个常数"。
    """

    VALUE = 0.25

    def __init__(self, net):
        self._net = net
        self.mem_slots = getattr(net, "mem_slots", 0)

    def mem_init(self, *a, **kw):
        return self._net.mem_init(*a, **kw)

    def __call__(self, batch):
        logits, v = self._net(batch)
        return logits, torch.full_like(v, self.VALUE)

    def forward_state(self, batch, mem):
        logits, v, mo = self._net.forward_state(batch, mem)
        return logits, torch.full_like(v, self.VALUE), mo


class TestTruncateBootstrap(unittest.TestCase):
    def test_truncated_tail_carries_the_value_estimate(self):
        """★ 截断处的 `boot` = **网络对边界观测的价值预测**（不是 `Φ/200`）。"""
        sb = _sb()
        steps, info = collect_episode(_nets(sb), sb, max_steps=10,
                                      rng=np.random.default_rng(0))
        self.assertTrue(info["truncated"], "用例前提：这局该被截断")
        boot = steps[-1].boot
        self.assertIsNotNone(boot, "截断处没有自举值 ⇒ 会按 0 算")
        self.assertNotEqual(boot, 0.0, "自举值恰好是 0 —— 那正是要避免的那个值")

    def test_boot_is_the_network_value_not_the_score(self):
        """★★ **可控价值函数**：返回 0.25 ⇒ 截断样本就必须是 0.25。

        ★ 这一条是本文件存在的理由：**自举值由网络说了算**，
          不是由势函数（`_score`）说了算 —— 量纲与口径都对不上。
        """
        sb = _sb()
        raw = _nets(sb)
        nets = {p: _FixedValueNet(n) for p, n in raw.items()}
        steps, info = collect_episode(nets, sb, max_steps=10,
                                      rng=np.random.default_rng(0))
        self.assertTrue(info["truncated"], "用例前提：这局该被截断")
        self.assertAlmostEqual(float(steps[-1].boot), _FixedValueNet.VALUE, places=5,
                               msg="截断处的自举值不是网络的价值预测")

    def test_score_does_not_affect_the_bootstrap(self):
        """★★ **`_score` 不参与自举**：把它打成巨大值，结果一点不变。

        ★ 老口径（`Φ/200`）下这一条必然红 —— 那正是它把错误钉成"正确"的地方。
        """
        sb = _sb()
        raw = _nets(sb)
        nets = {p: _FixedValueNet(n) for p, n in raw.items()}
        with mock.patch("rl.train._score", side_effect=AssertionError(
                "截断自举不许调用 `_score`（势函数 ≠ 价值）")):
            steps, info = collect_episode(nets, sb, max_steps=10,
                                          rng=np.random.default_rng(0))
        self.assertTrue(info["truncated"], "用例前提：这局该被截断")
        self.assertAlmostEqual(float(steps[-1].boot), _FixedValueNet.VALUE, places=5,
                               msg="`_score` 一变，自举值就变了 ⇒ 它还在参与")

    def test_unfinished_episode_has_no_fabricated_outcome(self):
        """★ **未完局不伪造 outcome**：`t_max` 判平才是 −0.5，截断不是。

        ★ 截断时局没打完 ⇒ 谁赢谁输**还不知道** ⇒ 不得凭空给 ±1/−0.5。
        """
        sb = _sb()
        steps, info = collect_episode(_nets(sb), sb, max_steps=10,
                                      rng=np.random.default_rng(0))
        self.assertTrue(info["truncated"], "用例前提：这局该被截断")
        for st in steps:
            if st.terminal:
                self.fail("截断局里出现了带最终结果的步 ⇒ 伪造了 outcome")
            self.assertIsNone(st.outcome, "截断局里出现了 outcome")

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
