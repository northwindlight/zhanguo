# -*- coding: utf-8 -*-
"""BC 锚的守门测试（用户 2026-09-14 拍板）。

## 它是什么

冻一份 BC 策略 `π_BC`，PPO 更新时对**回合 ≤ `--bc-anchor-turns`** 的状态加
`bc_coef · KL(π_θ ‖ π_BC)`。理由：实测「学了忘」发生在**开局**
（`mkt` 切法 A：开局前 20 回合 7/8 局已在掉，而那里所有策略构成相同），
而 BC 教的正是开局那 70 回合 ⇒ 灾难性遗忘。
**只锚 ≤N**：后期（战争/外交）没有老师示范，不锚，留给 PPO 自己学。

## 本测试钉三件事

1. **默认关**（`bc_coef=0` 或 `bc_model=None`）⇒ 与没有这个开关时**逐位相同**。
2. **全是不该锚的步**（`turn > bc_turns`）⇒ 与关掉时**逐位相同**
   ★这条最要紧：锚一旦渗进后期，就等于把学生按在老师的滚雪球打法上。
3. **有该锚的步** ⇒ 权重确实变了（锚真的生效，不是死代码）。
"""
from __future__ import annotations

import unittest

import numpy as np
import torch

import rl.ppo as ppo_mod
from rl.ppo import PPO, Rollout

K, D = 4, 3
_NP_ZEROS = np.zeros((1, 1, 1), np.float32)


class _TinyModel(torch.nn.Module):
    def __init__(self, k: int = K, d: int = D):
        super().__init__()
        self.k = k
        self.lin = torch.nn.Linear(d, k)
        self.vl = torch.nn.Linear(d, 1)

    def forward(self, *_a, **_kw):
        raise AssertionError("测试里应该用 _fake_forward_batch")


def _fake_forward_batch(model, steps, wins=None, *, return_exec: bool = False):
    n = len(steps)
    x = torch.ones(n, D)
    logits = model.lin(x)
    value = model.vl(x).squeeze(-1)
    mask = torch.ones(n, model.k, dtype=torch.bool)
    if return_exec:
        return logits, value, mask, model.lin(x)
    return logits, value, mask


def _steps(n: int, turn: int, k: int = K):
    return [{
        "grid": None, "glob": None, "cand": None, "win": None,
        "act": i % k, "logp": -1.5, "val": 0.5,
        "rew": float(i + 1), "done": False, "ok": True, "turn": turn,
    } for i in range(n)]


class TestBCAnchor(unittest.TestCase):

    def setUp(self):
        self._orig = ppo_mod.forward_batch
        ppo_mod.forward_batch = _fake_forward_batch

    def tearDown(self):
        ppo_mod.forward_batch = self._orig

    def _run(self, *, turns, bc_coef, with_bc, bc_turns=70, n=9):
        # ★两个 RNG 都要播种：`PPO.update` 里 `np.random.shuffle` 用的是 **numpy 全局 RNG**
        #   （真训练里 `train.py` 有 `np.random.seed(args.seed)`，测试里得自己播，
        #    否则两次 run 的 minibatch 不同 ⇒ 逐位比较必然假红）。
        torch.manual_seed(0)
        np.random.seed(0)
        model = _TinyModel()
        bc = None
        if with_bc:
            torch.manual_seed(1)
            bc = _TinyModel()
            bc.eval()
            for p in bc.parameters():
                p.requires_grad = False
        ppo = PPO(model, lr=1e-3, epochs=1, minibatch=4, adv_norm="minibatch",
                  bc_model=bc, bc_coef=bc_coef, bc_turns=bc_turns)
        r = Rollout(lam=1.0, normalize=False)
        r.steps = _steps(n, turns)
        ppo.update(r, last_value=0.0)
        return [v.detach().clone() for v in model.state_dict().values()]

    def test_off_is_bitwise_identical(self):
        """默认关（`bc_coef=0` + 没有 bc_model）与不传锚完全一致。"""
        a = self._run(turns=10, bc_coef=0.0, with_bc=False)
        b = self._run(turns=10, bc_coef=0.0, with_bc=True)     # 给了模型但权重为 0
        for x, y in zip(a, b):
            self.assertTrue(torch.equal(x, y), "bc_coef=0 时不该有任何影响")

    def test_no_qualifying_turn_is_bitwise_identical(self):
        """★全部步 `turn > bc_turns` ⇒ 与关掉时逐位相同。

        这条挡住的是最危险的情形：**锚渗进后期**（等于把学生按在老师的
        滚雪球打法上，而那正是 PPO 该自己学的那段）。
        """
        a = self._run(turns=999, bc_coef=1.0, with_bc=False)
        b = self._run(turns=999, bc_coef=1.0, with_bc=True, bc_turns=70)
        for x, y in zip(a, b):
            self.assertTrue(torch.equal(x, y),
                            "turn > bc_turns 的步不该受锚影响（锚渗进后期了）")

    def test_qualifying_turn_actually_changes_weights(self):
        """有该锚的步 ⇒ 权重确实变了（锚不是死代码）。"""
        a = self._run(turns=10, bc_coef=0.0, with_bc=False)
        b = self._run(turns=10, bc_coef=1.0, with_bc=True, bc_turns=70)
        self.assertFalse(all(torch.equal(x, y) for x, y in zip(a, b)),
                         "bc_coef>0 且有 turn≤bc_turns 的步，权重该变")

    def test_rollout_stores_turn(self):
        """`Rollout.add` 的 `turn` 默认 0、显式传入要存下来。"""
        class _Obs:
            grid = _NP_ZEROS
            glob = _NP_ZEROS
            cand = {}
        r = Rollout(lam=1.0, normalize=False)
        r.add(_Obs(), 0, 0.0, 0.0, 0.0, False)
        self.assertEqual(r.steps[-1]["turn"], 0, "不传时应默认 0")
        r.add(_Obs(), 0, 0.0, 0.0, 0.0, False, turn=123)
        self.assertEqual(r.steps[-1]["turn"], 123)


if __name__ == "__main__":
    unittest.main()
