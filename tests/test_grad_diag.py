# -*- coding: utf-8 -*-
"""`--grad-diag` 的**分解正确性** —— 量出来的数必须先证明是真的。

## 为什么要有这份测试

用户 2026-09-18：「**方差主导了？比价值头更高？**」—— 要回答就得把
`loss = pg + vf_coef·vf − ent_coef·ent + bc_coef·bc_kl` 拆成四项**各自的梯度**，
再看哪一项在 minibatch 之间**一致**（Adam 的 `m/√v` 会压掉零均值噪声，
只有一致分量推得动权重）。

但"分解"这件事本身很容易**悄悄错**：求错项、带上别项的系数、符号反了 ——
这些都不会报错，只会给出一组**看起来很像结论**的数。所以这里不测"数好不好看"，
只钉三条**能被证伪的不变量**：

| 用例 | 不变量 | 错了会怎样 |
|---|---|---|
| 优势全零 | `pg` 项梯度**恒等于 0** | 若 `_diag_step` 实际求的是整条 loss，这条必挂 |
| `ent_coef=0` | `ent` 项梯度**恒等于 0** | 钉住"每项只装自己那一项" |
| 系数线性 | `ent_coef` 加倍 ⇒ 该项 `‖mean g‖` 精确加倍 | 钉住"系数与符号照 loss 取" |

★第 3 条只在**单 minibatch**（`epochs=1`、`minibatch=n`）下是精确的：
多批时优化器在批间已经改过权重，后面的批落在另一个点上，线性不再成立。
"""
from __future__ import annotations

import unittest

import torch

import rl.ppo as ppo_mod
from rl.ppo import PPO, Rollout

K = 4          # 候选数
D = 3          # 假特征维度


class _TinyModel(torch.nn.Module):
    """产 logits 与 value 的最小模型（同 `test_adv_norm` 的做法）。"""

    def __init__(self, k: int = K, d: int = D):
        super().__init__()
        self.k = k
        self.lin = torch.nn.Linear(d, k)      # logits
        self.vl = torch.nn.Linear(d, 1)       # value

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


def _steps(n: int, k: int = K, rew: float = 1.0):
    """★奖励**逐 step 不同**：全相等时优势全相等、`std=0`，走的是守卫的另一条分支。"""
    return [{
        "grid": None, "glob": None, "cand": None, "win": None,
        "act": i % k, "logp": -1.5, "val": 0.5,
        "rew": float(i + 1) * rew, "done": False, "ok": True,
    } for i in range(n)]


class GradDiagCase(unittest.TestCase):

    def setUp(self):
        self._orig = ppo_mod.forward_batch
        ppo_mod.forward_batch = _fake_forward_batch

    def tearDown(self):
        ppo_mod.forward_batch = self._orig

    def _diag(self, n: int = 8, *, ent_coef: float = 0.01, vf_coef: float = 0.5,
              rew: float = 1.0):
        torch.manual_seed(0)
        model = _TinyModel()
        ppo = PPO(model, lr=1e-3, epochs=1, minibatch=n, adv_norm="minibatch",
                  vf_coef=vf_coef, ent_coef=ent_coef, grad_diag=True)
        r = Rollout(lam=1.0, normalize=False)
        r.steps = _steps(n, rew=rew)
        ppo.update(r, last_value=0.0)
        return ppo.report_grad_diag()


class TestGradDiagOffByDefault(GradDiagCase):
    """★默认关 ⇒ 一行诊断代码都不进，行为与开关存在前一致。"""

    def test_关时不留状态(self):
        torch.manual_seed(0)
        ppo = PPO(_TinyModel(), lr=1e-3, epochs=1, minibatch=8)
        self.assertIsNone(ppo._diag, "默认关时不该分配诊断缓冲")
        r = Rollout(lam=1.0, normalize=False)
        r.steps = _steps(8)
        stats = ppo.update(r, last_value=0.0)          # 照常跑，不炸
        self.assertIn("pg", stats)
        self.assertEqual(ppo.report_grad_diag(), {}, "关着也该能安全取报告")


class TestGradDiagDecomposition(GradDiagCase):

    def test_四项齐全且一致性落在零到一之间(self):
        rep = self._diag(16)
        for k in ("pg", "vf", "ent"):
            self.assertIn(k, rep, f"缺了 {k} 项")
            d = rep[k]
            self.assertGreaterEqual(d["n_minibatch"], 1)
            self.assertTrue(0.0 <= d["coh"] <= 1.0 + 1e-6,
                            f"{k} 的 coh={d['coh']} 越界 —— 一致性是个比值，不可能 >1")
            self.assertTrue(torch.isfinite(torch.tensor(d["snr"])))
        self.assertNotIn("bc", rep, "没给 bc_model 就不该有 bc 项")

    def test_优势全零时策略项梯度恒为零(self):
        """★决定性：`pg = -adv·ratio`，adv 全零 ⇒ 该项梯度**必须严格是 0**。

        若 `_diag_step` 求错了对象（例如求成了整条 loss 的梯度），这条会立刻挂。
        """
        rep = self._diag(8, rew=0.0)
        self.assertLess(rep["pg"]["norm_mean"], 1e-12,
                        f"优势全零却有非零梯度 {rep['pg']['norm_mean']} —— 项求错了")

    def test_熵系数为零时熵项梯度恒为零(self):
        """钉住"每项只装自己那一项、且带自己的系数"。"""
        rep = self._diag(8, ent_coef=0.0)
        self.assertLess(rep["ent"]["norm_mean"], 1e-12)
        self.assertGreater(rep["pg"]["norm_mean"], 0.0, "别的项不该跟着一起没了")

    def test_熵项随系数精确线性(self):
        """★系数只在项里生效：`ent_coef` ×3 ⇒ 该项 `‖mean g‖` 精确 ×3。

        单 minibatch ⇒ 批间权重点不动 ⇒ 线性是精确的（见模块说明）。
        """
        a = self._diag(8, ent_coef=0.01)["ent"]["norm_mean"]
        b = self._diag(8, ent_coef=0.03)["ent"]["norm_mean"]
        self.assertGreater(a, 0.0, "熵项梯度是 0 就测不出线性了")
        self.assertAlmostEqual(b / a, 3.0, places=5,
                               msg=f"熵项没跟着系数线性变：{a} → {b}")


if __name__ == "__main__":
    unittest.main()
