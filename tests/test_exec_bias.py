# -*- coding: utf-8 -*-
"""可执行性软加权（第三条路）：**采样侧与更新侧必须同分布**。

## 为什么这份测试是这条路的生死线

上一版把软加权**只加在采样侧**：`act()` 存的 `old_logp` 来自加权分布
（`log_softmax(logit + w)`），而 `PPO.update` 重算的 `logp_all` 来自**未加权**分布
⇒ 参数一动没动时 `ratio = π_raw/π_w ≠ 1`。后果是日志的 `kl` 含一个与学习无关的常数
偏移，且 **clip 作用在错位的比值上**（对 `w_a` 很负的动作变成"只罚不奖"的非对称更新）。
所以：

| 用例 | 不变量 |
|---|---|
| β=0 | `exec_bias` **逐位不动** logits（默认关 ⇒ 行为与开关存在前相同） |
| **β>0，参数未动** | `kl ≈ 0` **且** `clipfrac ≈ 0` —— 即 `ratio ≡ 1`。**这条挂了 = 又回到那个 bug** |
| β 单调 | 低 `p_exec` 的候选被压得更低（软加权真的在做它说的事） |

★第 2 条是**决定性的**：它不检查"加权有没有生效"，只检查"两侧一不一致"。
上一版正是在这里错的，而当时没有任何测试盯着它。
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

import numpy as np
import torch

import rl.ppo as ppo_mod
from rl.ppo import EXEC_BETA, PPO, Rollout, act, exec_bias

K = 5          # 候选数
D = 3          # 假特征维度
BETA = 0.5


class _TinyModel(torch.nn.Module):
    """产 logits / value / p_exec 的最小模型（同 `test_adv_norm` 的做法）。"""

    def __init__(self, k: int = K, d: int = D):
        super().__init__()
        self.k = k
        self.lin = torch.nn.Linear(d, k)      # logits
        self.vl = torch.nn.Linear(d, 1)       # value
        self.el = torch.nn.Linear(d, k)       # p_exec 的 logit（每候选一个）

    def forward(self, *_a, **_kw):
        raise AssertionError("测试里应该用 _fake_forward_batch")


def _fake_forward_batch(model, steps, wins=None, *, return_exec: bool = False):
    n = len(steps)
    x = torch.ones(n, D)
    logits = model.lin(x)
    value = model.vl(x).squeeze(-1)
    mask = torch.ones(n, model.k, dtype=torch.bool)
    if return_exec:
        return logits, value, mask, model.el(x)
    return logits, value, mask


def _obs(k: int = K):
    """`act()` 只用到 `grid/glob/cand` 三个属性（`_one_step` 就取这三样）。"""
    return SimpleNamespace(grid=np.zeros((1, 1, 1), np.float16),
                           glob=np.zeros(1, np.float32),
                           cand={"actions": [None] * k})


class ExecBiasCase(unittest.TestCase):

    def setUp(self):
        self._orig = ppo_mod.forward_batch
        ppo_mod.forward_batch = _fake_forward_batch

    def tearDown(self):
        ppo_mod.forward_batch = self._orig

    def _rollout(self, model, beta: float, n: int = 6):
        """**按训练时的样子**收集：`act(use_exec=β>0, exec_beta=β)` 存的 logp。"""
        r = Rollout(lam=1.0, normalize=False)
        obs = _obs()
        for i in range(n):
            idx, lp, val = act(model, obs, deterministic=False,
                               use_exec=beta > 0, exec_beta=beta)
            r.add(obs, idx, lp, val, reward=float(i + 1), done=False)
        return r


class TestExecBiasFormula(ExecBiasCase):

    def test_关时逐位不动(self):
        torch.manual_seed(0)
        lg = torch.randn(4, K)
        pe = torch.randn(4, K)
        self.assertTrue(torch.equal(exec_bias(lg, pe, 0.0), lg),
                        "β=0 必须逐位不变（默认关的契约）")

    def test_低可执行概率被压得更低(self):
        lg = torch.zeros(1, K)
        pe = torch.tensor([[-8.0, -1.0, 0.0, 1.0, 8.0]])   # 第 0 个最不可能执行
        out = exec_bias(lg, pe, BETA)
        self.assertLess(out[0, 0], out[0, 4], "p_exec 低的没被压下去")
        self.assertLess(out[0, 1], out[0, 3])
        self.assertAlmostEqual(float(out[0, 4]), 0.0, places=2,
                               msg="p_exec≈1 的候选不该被抬太多（这是**压制**，不是抬举）")

    def test_beta_越大压得越狠(self):
        lg = torch.zeros(1, 2)
        pe = torch.tensor([[-6.0, 6.0]])
        a = exec_bias(lg, pe, 0.5)[0, 0]
        b = exec_bias(lg, pe, 2.0)[0, 0]
        self.assertLess(float(b), float(a))


class TestSamplingMatchesUpdate(ExecBiasCase):
    """★生死线：两侧同分布 ⇒ 参数未动时 `ratio ≡ 1`。"""

    def _stats(self, beta: float) -> dict:
        torch.manual_seed(0)
        model = _TinyModel()
        # 先拿同一份权重收 rollout（模拟训练循环的顺序）
        r = self._rollout(model, beta)
        ppo = PPO(model, lr=1e-3, epochs=1, minibatch=len(r.steps),
                  adv_norm="minibatch", exec_beta=beta)
        return ppo.update(r, last_value=0.0)

    def test_关着时本来就一致(self):
        s = self._stats(0.0)
        self.assertAlmostEqual(s["kl"], 0.0, places=6)

    def test_开着也必须一致(self):
        """★核心：β>0 时 `kl ≈ 0`（比值恒为 1）。若更新侧漏了加权，这条会挂。"""
        s = self._stats(BETA)
        self.assertAlmostEqual(
            s["kl"], 0.0, places=6,
            msg=f"kl={s['kl']} ≠ 0 —— 采样侧与更新侧不是同一个分布（错位 bug 回来了）")
        self.assertAlmostEqual(s["clipfrac"], 0.0, places=6,
                               msg=f"clipfrac={s['clipfrac']} ≠ 0 —— ratio 偏离 1")

    def test_开着时确实改了分布(self):
        """反向钉住：β>0 不能是空操作，否则上面那条"一致"是平凡成立的。"""
        torch.manual_seed(0)
        model = _TinyModel()
        obs = _obs()
        with torch.no_grad():
            p0 = torch.softmax(_fake_forward_batch(model, [None])[0], -1)
            lg, _v, _m, pe = _fake_forward_batch(model, [None], return_exec=True)
            p1 = torch.softmax(exec_bias(lg, pe, 8.0), -1)   # 用大 β 让差异明显
        self.assertGreater(float((p0 - p1).abs().sum()), 1e-3,
                           "β 很大时分布居然没变 —— 加权没接上")


if __name__ == "__main__":
    unittest.main()
