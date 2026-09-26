# -*- coding: utf-8 -*-
"""`FastLinear` 的守卫 —— 它救的是 **Pi**，但它有一条**会让训练静默停摆**的坑。

★★ 背景（2026-09-26）：Pi（aarch64）上 `F.linear` 比等价的连续权重 matmul 慢
   **6.6×**（x86 上只差 1.05×）⇒ 前向在 Pi 上占整条回路的 **70%**，而同一局里
   纯 Python 的战斗 DP 两边**一模一样**（17.4 ms/步）⇒ 差的全在 torch。
   换成本类之后 Pi 上前向 **2.00×**（实测）。

★ **为什么必须钉死**：快路用的是 `weight.detach().t().contiguous()` 这份**拷贝**
  ⇒ 若在**记梯度**的时候走了快路，梯度会流到那份拷贝上、`weight.grad` 恒为
  `None` ⇒ `opt.step()` 什么都不更新 ⇒ **训练完全不动，而 loss 照常打印**。
  这是"跑起来看着没事"里最坏的一种 ⇒ 每条守卫都对着它。
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import torch
import torch.nn as nn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.model import FastLinear, build_model           # noqa: E402
from rl.train import collate                           # noqa: E402
from rl.encode import obs_of                           # noqa: E402
from rl.sandbox import Sandbox                         # noqa: E402


def _batch(seed=3, size=8, t_max=10):
    sb = Sandbox(seed=seed, size=size, t_max=t_max, n_nations=3,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    me = sb.current_player()
    return collate([obs_of(sb, me, sb.legal())])


class TestBothPathsAgree(unittest.TestCase):
    """① 快路与慢路**逐元素一致**（这是"换了内核、没换数学"的判据）。"""

    def test_inference_matches_the_reference(self):
        torch.manual_seed(0)
        lin = FastLinear(12, 7)
        x = torch.randn(2, 5, 12)
        with torch.no_grad():                      # ← 快路
            fast = lin(x)
        ref = nn.functional.linear(x, lin.weight, lin.bias)   # ← 慢路（参考）
        self.assertLess((fast - ref).abs().max().item(), 1e-5)
        self.assertEqual(tuple(fast.shape), (2, 5, 7))

    def test_whole_model_inference_matches(self):
        """★ 不只是单层 —— **整个 PolicyNet** 两条路也要一致。"""
        torch.manual_seed(0)
        a = build_model(mem_slots=8).eval()
        b = copy.deepcopy(a)
        batch = _batch()
        with torch.no_grad():
            la, va, _ = a.forward_state(batch, None)
        # b 的每一层强制走慢路
        for m in b.modules():
            if isinstance(m, FastLinear):
                m.forward = lambda x, _m=m: nn.functional.linear(
                    x, _m.weight, _m.bias)
        with torch.no_grad():
            lb, vb, _ = b.forward_state(batch, None)
        self.assertLess((la - lb).abs().max().item(), 1e-5, "logits 两条路不一致")
        self.assertLess((va - vb).abs().max().item(), 1e-5, "value 两条路不一致")


class TestTrainingPathKeepsGradients(unittest.TestCase):
    """② ★★★ **记梯度时必须走慢路** —— 否则训练静默停摆。"""

    def test_grad_reaches_the_weight_when_grad_is_enabled(self):
        """★ 破坏方式：把 `forward` 里的 `if torch.is_grad_enabled()` 那道门删掉
        （永远走快路）⇒ 本条当场红（`weight.grad is None`）。
        """
        torch.manual_seed(0)
        lin = FastLinear(6, 4)
        x = torch.randn(3, 6)
        lin(x).sum().backward()
        self.assertIsNotNone(
            lin.weight.grad,
            "记梯度时走了快路 ⇒ 梯度流到那份 detach 拷贝上、weight.grad 是 None "
            "⇒ opt.step() 什么都不更新 ⇒ **训练完全不动而 loss 照常打印**")

    def test_optimizer_actually_moves_the_weights_when_training(self):
        """★ 不只是"grad 不是 None" —— **整步真的把权重改了**。

        （`SGD` 对 `grad is None` 的参数是**静默跳过**的，所以"grad 为 None"
          和"权重没动"是同一件事的两种查法，都测。）
        """
        torch.manual_seed(0)
        lin = FastLinear(6, 4)
        opt = torch.optim.SGD(lin.parameters(), lr=1.0)
        x = torch.randn(3, 6)
        before = lin.weight.detach().clone()
        lin(x).sum().backward()
        opt.step()
        self.assertFalse(torch.allclose(before, lin.weight.detach()),
                         "训练一步之后权重没动 ⇒ 静默停摆")

    def test_real_model_trains_under_grad(self):
        """★ 整个模型：`build_model` 之后每个 FastLinear 都要拿到梯度。"""
        torch.manual_seed(0)
        net = build_model(mem_slots=0).train()
        lg, v, _ = net.forward_state(_batch(), None)
        (lg.sum() + v.sum()).backward()
        missing = [n for n, m in net.named_modules()
                   if isinstance(m, FastLinear) and m.weight.grad is None]
        self.assertEqual(missing, [], f"这些层的权重没拿到梯度：{missing}")


class TestCacheInvalidation(unittest.TestCase):
    """③ `_version` 失效 —— 不靠"记得刷新"。"""

    def test_cache_refreshes_after_inplace_change(self):
        torch.manual_seed(0)
        lin = FastLinear(5, 3)
        x = torch.randn(1, 5)
        with torch.no_grad():
            a = lin(x).clone()
            lin.weight.add_(2.0)               # ★ 就地改（`opt.step()` 就是这么干的）
            b = lin(x)
        ref = nn.functional.linear(x, lin.weight, lin.bias)
        self.assertLess((b - ref).abs().max().item(), 1e-5,
                        "**就地改权重后缓存没失效** ⇒ 拿旧权重算（静默错）")
        self.assertFalse(torch.allclose(a, b), "改了权重结果却没变 ⇒ 缓存是旧的")

    def test_cache_refreshes_after_optimizer_step(self):
        """★ 走**真的** `opt.step()` 一次（不是手写 add_）。"""
        torch.manual_seed(0)
        lin = FastLinear(5, 3)
        opt = torch.optim.SGD(lin.parameters(), lr=0.5)
        x = torch.randn(1, 5)
        with torch.no_grad():
            lin(x)                                  # 先建缓存
        lin(x).sum().backward()
        opt.step()
        with torch.no_grad():
            got = lin(x)
        ref = nn.functional.linear(x, lin.weight, lin.bias)
        self.assertLess((got - ref).abs().max().item(), 1e-5,
                        "`opt.step()` 之后缓存没失效 ⇒ 训练一直在用旧权重")

    def test_cache_does_not_masquerade_after_device_change(self):
        """④ 副本是**普通属性**、不跟着 `.to()` 走 ⇒ 判有效时must连 device/dtype 一起看。"""
        lin = FastLinear(4, 2)
        with torch.no_grad():
            lin(torch.randn(1, 4))
        self.assertIsNotNone(lin._wt, "缓存没建起来 ⇒ 本用例空转")
        # 伪造一个"换了 dtype 的权重"（CPU 上没法真搬设备，用 dtype 走同一条判据）
        lin = lin.to(torch.float64)
        with torch.no_grad():
            out = lin(torch.randn(1, 4, dtype=torch.float64))
        self.assertEqual(out.dtype, torch.float64,
                         "换了 dtype 之后还在用 float32 的旧副本 ⇒ 静默算错")


class TestStateDictIsUntouched(unittest.TestCase):
    """⑤ ★★ **老 ckpt 必须照样读** —— 这是"不动 state_dict"那个承诺的守卫。

    （我原先以为要动 state_dict、所以说"别改"；能用本类的前提正是**不动它**。）
    """

    def test_keys_and_shapes_are_identical_to_plain_linear(self):
        class Ref(nn.Module):
            def __init__(self):
                super().__init__()
                self.a = nn.Linear(6, 4)
                self.seq = nn.Sequential(nn.Linear(4, 5), nn.ReLU())

        class Fast(nn.Module):
            def __init__(self):
                super().__init__()
                self.a = FastLinear(6, 4)
                self.seq = nn.Sequential(FastLinear(4, 5), nn.ReLU())

        r, f = Ref().state_dict(), Fast().state_dict()
        self.assertEqual(sorted(r), sorted(f), "state_dict 的键变了 ⇒ 老 ckpt 读不了")
        for k in r:
            self.assertEqual(tuple(r[k].shape), tuple(f[k].shape), k)

    def test_a_linear_state_dict_loads_into_fast_linear(self):
        """★ 真的把一份 `nn.Linear` 的档 load 进 `FastLinear`（老 ckpt 的形状）。"""
        torch.manual_seed(0)
        ref = nn.Linear(6, 4)
        fast = FastLinear(6, 4)
        fast.load_state_dict(ref.state_dict())
        x = torch.randn(2, 6)
        with torch.no_grad():
            self.assertLess((fast(x) - ref(x)).abs().max().item(), 1e-6,
                            "load 进来的权重没被用上")

    def test_cached_copy_never_enters_state_dict(self):
        lin = FastLinear(4, 2)
        with torch.no_grad():
            lin(torch.randn(1, 4))
        self.assertIsNotNone(lin._wt, "缓存没建起来 ⇒ 本用例空转")
        self.assertEqual(sorted(lin.state_dict()), ["bias", "weight"],
                         "那份缓存副本混进 state_dict 了 ⇒ 存档会变大且带冗余")

    def test_the_real_model_round_trips_through_a_checkpoint(self):
        """★ 端到端：**存一份再读回来**，推理结果必须一致。

        （顺带钉住"Pi 上产的档在 x86 上读得回来、反之亦然"这件事的形状。）
        """
        import io
        torch.manual_seed(0)
        net = build_model(mem_slots=8).eval()
        batch = _batch()
        with torch.no_grad():
            want, _, _ = net.forward_state(batch, None)
        buf = io.BytesIO()
        torch.save({"weights": net.state_dict()}, buf)
        buf.seek(0)
        fresh = build_model(mem_slots=8).eval()
        fresh.load_state_dict(torch.load(buf, weights_only=False)["weights"])
        with torch.no_grad():
            got, _, _ = fresh.forward_state(batch, None)
        self.assertLess((want - got).abs().max().item(), 1e-6,
                        "存读一轮之后前向结果变了")


if __name__ == "__main__":
    unittest.main()
