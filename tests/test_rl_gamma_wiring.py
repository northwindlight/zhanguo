# -*- coding: utf-8 -*-
"""`gae` 的折扣/平滑**必须从先验表读**，不许再写死在签名里。

★ 病灶（2026-09-28）：`gae(gamma=0.99, lam=0.95)` 原来是**签名里的默认值**，
  两个调用处从没传过、也没有 CLI 开关 ⇒ 「改 γ」在代码里**根本无从下手**。
  而实测（`rl/adv_probe.py`）：一局每方**中位 908 步**，`γ=0.99` 的视野只有 ~100 步
  ⇒ **前 ~89% 的决策拿不到终局信号** —— 与"188 个 iter `e/K` 贴在 1.00"直接对应。

★★ 为什么钉的是**接线**而不是值：值（1.0）是用户拍板的**决定**，会再变；
  会**静默退回去**的是接线 —— 有人把 `S.GAMMA` 换回字面量 0.99，
  结果一切照跑、日志上的 `先验` 一行还印着 1.0（因为那行读的是表），
  **只有实际算 GAE 时用的是 0.99**。所以这里用探针抓**调用时传了什么**。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch                                          # noqa: E402

from rl import scoring as S                           # noqa: E402
from rl import train as T                             # noqa: E402
from rl.model import build_model                      # noqa: E402
from rl.sandbox import Sandbox                        # noqa: E402


def _steps(n=40):
    torch.manual_seed(11)
    sb = Sandbox(seed=315, size=10, t_max=20, n_nations=2, halls_known=True,
                 territory=True, alliances="random2v2").reset()
    nets = {p: build_model(mem_slots=8) for p in sb.players}
    for m in nets.values():
        m.eval()
    for m in nets.values():
        m.train()
    steps, _info = T.collect_episode(nets, sb, rng=np.random.default_rng(0))
    return nets["甲"], steps


class TestGaeReadsThePriorTable(unittest.TestCase):

    def test_ppo_update_passes_S_GAMMA_into_gae(self):
        net, steps = _steps()
        self.assertGreater(len(steps), 8, "用例太小")
        seen = {}
        real = T.gae

        def spy(rewards, values, dones, **kw):
            seen.update(kw)
            return real(rewards, values, dones, **kw)

        T.gae = spy
        try:
            T.ppo_update(net, steps, epochs=1)
        finally:
            T.gae = real

        self.assertIn("gamma", seen, "`gae` 没收到 gamma ⇒ 又用回签名的默认值了")
        self.assertEqual(seen["gamma"], S.GAMMA,
                         f"传的是 {seen['gamma']}，而表里是 {S.GAMMA} —— 接线断了")
        self.assertEqual(seen["lam"], S.GAE_LAMBDA, "lam 同理")

    def test_the_wiring_is_not_vacuous(self):
        """★ 表改了，`gae` 的结果必须**真的跟着变**（否则接线是空的）。"""
        _net, steps = _steps()
        rw = [s.reward for s in steps]
        va = [s.value for s in steps]
        dn = [s.done for s in steps]
        bt = [s.boot for s in steps]
        a99, _ = T.gae(rw, va, dn, boots=bt, gamma=0.99, lam=0.95)
        a10, _ = T.gae(rw, va, dn, boots=bt, gamma=1.00, lam=0.95)
        self.assertFalse(np.allclose(a99, a10),
                         "γ=0.99 与 γ=1.0 算出来的优势一模一样 —— 用例或接线有问题")
        self.assertGreater(np.abs(a99 - a10).max(), 1e-6, "差异小到看不出来")

    def test_default_is_one(self):
        """用户 2026-09-28 拍板「改成 1」。钉住它，免得被谁顺手改回去。"""
        self.assertEqual(S.GAMMA, 1.0,
                         "`S.GAMMA` 不是 1.0 了 —— 这是用户拍过的决定，"
                         "要改请先确认")
