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
    """忠实模拟真 `collate`：**候选维 K = 批内最大候选数**，逐 step 用 mask 标有效位。

    ★为什么不能图省事用常数 K：真环境里每个状态的候选数是变的（160~400），
      `collate` 按**批内最大**补齐；对**子集**单独 collate 会得到更小的 K。
      用常数 K 的假前向会把「BC 前向跑了子集」这个 bug 放过去
      —— 2026-09-14 实测：测试全绿、真跑崩（`378 vs 196`）。
    """
    n = len(steps)
    k = max(s["kk"] for s in steps)          # ← 批内最大，与真 collate 同口径
    x = torch.ones(n, D)
    logits = model.lin(x)                     # [n, model.k]
    logits = logits[:, :k] if k <= model.k else logits.repeat(1, (k // model.k) + 1)[:, :k]
    value = model.vl(x).squeeze(-1)
    mask = torch.zeros(n, k, dtype=torch.bool)
    for i, s in enumerate(steps):
        mask[i, :s["kk"]] = True              # 补出来的位置不算有效位
    if return_exec:
        return logits, value, mask, logits
    return logits, value, mask


def _steps(n: int, turn: int, k: int = K):
    """★让**"该锚的步"恰好都是候选数少的那批** —— 这是复现形状 bug 的关键。

    真环境里候选数逐状态变（160~400），`collate` 按**批内最大**补齐。
    所以「该锚的子集」与「整个 minibatch」的 K 通常不同；而对子集单独 collate
    会拿子集的最大值 ⇒ `_p` 与 `_q` 形状不等（实测 `378 vs 196`）。

    这里：偶数步 kk=k（turn=999，**不锚**）、奇数步 kk=k//2（turn=turn，**该锚**）。
    ⇒ 整批 K=k，该锚子集 K=k//2 ⇒ 形状不同，bug 必现。
    """
    out = []
    for i in range(n):
        anchored = (i % 2 == 1)
        kk = max(1, k // 2) if anchored else k
        out.append({
            "grid": None, "glob": None, "cand": None, "win": None,
            "act": i % kk, "logp": -1.5, "val": 0.5,
            "rew": float(i + 1), "done": False, "ok": True,
            "turn": turn if anchored else 999,
            "kk": kk,
        })
    return out


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
