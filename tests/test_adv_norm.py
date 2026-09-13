# -*- coding: utf-8 -*-
"""优势归一化的**退化批守卫** —— 一个杀掉整炉的边界 bug 的回归测试。

## 病是什么（2026-09-13 实测现场）

`PPO.update` 的 `adv_norm="minibatch"` 路径原来是：

    adv = (adv - adv.mean()) / (adv.std() + 1e-8)

`torch.std()` **默认无偏**（`ddof=1`），**`n == 1` 时返回 nan**。
而切批是 `for start in range(0, n, self.minibatch)`：

    n % minibatch == 1   ⇒   最后一个 minibatch 只有 1 个样本

于是 `adv` 全 nan ⇒ `pg` nan ⇒ `loss` nan ⇒ **权重 nan** ⇒
**下一块** `act()` 里 `torch.multinomial(logp.exp(), 1)` 直接抛

    RuntimeError: probability tensor contains either `inf`, `nan` or element < 0

**实测现场**：500 局长炉跑到第 16 块，`env_steps` 8161 = 255×32 + 1。
`n` 每块都在变（≈8000），`n % 32` 近似均匀 ⇒ **每块约 1/32 概率踩中**，
125 块期望踩 ~4 次。表现就是**"随机时刻崩"** —— 很容易被误当成
"熵增"/"超参没调好"去治，所以必须钉一条测试。

**崩前可判**：产生 nan 的那一块 `pg` 印成 nan，而 **`vf` 仍是有限值**
（vf 不含 adv）。这是区分"这个 bug"和"价值网炸了"的判据。

## 测试怎么做的

`PPO.update` 内部直接调模块级的 `collate`，没法从外面注入。
所以这里**替换 `rl.ppo.forward_batch`**：给它一个只依赖模型参数的假前向，
于是 `update` 的**全部逻辑**（含被改的优势归一化）照跑，却不惊动
观测/tokenize（那条链另有 `test_tokenize.py` 盯着）。
"""
from __future__ import annotations

import unittest

import torch

import rl.ppo as ppo_mod
from rl.ppo import PPO, Rollout

K = 4          # 候选数
D = 3          # 假特征维度


class _TinyModel(torch.nn.Module):
    """产 logits 与 value 的最小模型。参数真实存在 ⇒ 能 `backward`、能查 nan。"""

    def __init__(self, k: int = K, d: int = D):
        super().__init__()
        self.k = k
        self.lin = torch.nn.Linear(d, k)      # logits
        self.vl = torch.nn.Linear(d, 1)       # value

    def forward(self, *_a, **_kw):            # 假前向不走它，留着防万一
        raise AssertionError("测试里应该用 _fake_forward_batch，不该走模型 forward")


def _fake_forward_batch(model, steps, wins=None, *, return_exec: bool = False):
    """只依赖模型参数的假前向 —— 形状与真 `forward_batch` 对齐。"""
    n = len(steps)
    x = torch.ones(n, D)
    logits = model.lin(x)
    value = model.vl(x).squeeze(-1)
    mask = torch.ones(n, model.k, dtype=torch.bool)
    if return_exec:
        return logits, value, mask, model.lin(x)
    return logits, value, mask


def _steps(n: int, k: int = K):
    """`adv`/`ret` 不在这里设 —— 交给 `Rollout.gae()` 算，那才是真路径。

    ★奖励**逐 step 不同**：全相等时优势全相等、`std=0`，走的是守卫的
      另一条分支，测不出"n=1 的 nan"这条真病。
    """
    return [{
        "grid": None, "glob": None, "cand": None, "win": None,
        "act": i % k, "logp": -1.5, "val": 0.5,
        "rew": float(i + 1), "done": False, "ok": True,
    } for i in range(n)]


class TestAdvNormDegenerateBatch(unittest.TestCase):

    def setUp(self):
        self._orig = ppo_mod.forward_batch
        ppo_mod.forward_batch = _fake_forward_batch

    def tearDown(self):
        ppo_mod.forward_batch = self._orig

    def _run(self, n_steps: int, minibatch: int):
        torch.manual_seed(0)
        model = _TinyModel()
        ppo = PPO(model, lr=1e-3, epochs=1, minibatch=minibatch,
                  adv_norm="minibatch")
        r = Rollout(lam=1.0, normalize=False)
        r.steps = _steps(n_steps)
        ppo.update(r, last_value=0.0)
        return model

    def _bad(self, model):
        return [k for k, v in model.state_dict().items()
                if not torch.isfinite(v).all()]

    def test_single_sample_minibatch_does_not_nan(self):
        """★核心回归：`n % minibatch == 1` 时权重必须仍然有限。

        旧代码在这里产出 nan（`torch.std()` 无偏、n=1 ⇒ nan）⇒ 下一块崩。
        """
        n, mb = 9, 4                  # 9 = 2×4 + 1 ⇒ 最后一个 minibatch 只有 1 个
        self.assertEqual(n % mb, 1, "用例本身要满足 n % minibatch == 1")
        self.assertEqual(self._bad(self._run(n, mb)), [],
                         "退化批把权重搞成 nan 了 —— 守卫失效")

    def test_normal_batch_still_finite(self):
        """正常切分（整除）也要有限 —— 守卫不能把正常路径弄坏。"""
        self.assertEqual(self._bad(self._run(8, 4)), [])

    def test_all_equal_advantages_still_finite(self):
        """全等优势（`std==0`，守卫的另一条分支）也不能出 nan。"""
        torch.manual_seed(0)
        model = _TinyModel()
        ppo = PPO(model, lr=1e-3, epochs=1, minibatch=4, adv_norm="minibatch")
        r = Rollout(lam=1.0, normalize=False)
        r.steps = _steps(8)
        for s in r.steps:             # 抹平奖励 ⇒ GAE 出来的优势全相等
            s["rew"] = 1.0
        ppo.update(r, last_value=0.0)
        self.assertEqual(self._bad(model), [])


if __name__ == "__main__":
    unittest.main()
