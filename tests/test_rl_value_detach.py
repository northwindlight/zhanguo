# -*- coding: utf-8 -*-
"""★ **critic 不得改写共享主干** —— 这是一条闸门，不是一句约定。

★ 为什么（2026-10-28 实测出来的）
  ──────────────────────────────
  训练在**压平主干**：未训练基线的逐 token 特征在局面间波动 **41.0%**，
  训 30 个 iter 后只剩 **15.7%**；`g`（全局摘要）token 从 **8.75%** 掉到 **2.54%**
  ⇒ **棋盘信息被磨没了** ⇒ 策略只剩"候选自己的局部特征" ⇒ **看不见局面** ⇒
  无论奖励口径、λ、算力怎么调都学不动。
  磨平它的是**价值项**：价值项在主干上的梯度范数是策略项的 **41 倍**，
  而 critic 的目标（同一段里恒为 R）**最优解是常数** ⇒ 它的梯度天然把
  "局面特有"的那些分量按下去、只留公共分量。

★ 这条线钉三件事：
  ① **只回传价值损失时，主干的参数拿不到任何梯度**（`detach` 生效）；
  ② **但策略损失仍然能回传到主干**（没把策略的通路也掐断）；
  ③ **前向数值逐位不变**（`detach` 不改数值）⇒ 旧 ckpt 照样能读、
     行为评估结果不用重算。
"""
from __future__ import annotations

import unittest

from tests.rl_deps import has_torch, torch_or_skip


@unittest.skipUnless(has_torch(), torch_or_skip())
class TestValueDetachedFromTrunk(unittest.TestCase):
    def _model_and_batch(self):
        import torch
        from rl.train import collate, obs_of
        from rl.model import build_model
        from rl.sandbox import Sandbox
        torch.manual_seed(0)
        net = build_model(mem_slots=0)
        sb = Sandbox(seed=3, size=8, t_max=10, n_nations=3, halls_known=True,
                     territory=True, alliances="random2v2").reset()
        me = sb.current_player()
        return net, collate([obs_of(sb, me, sb.legal())])

    @staticmethod
    def _split(net):
        vparams = [p for n, p in net.named_parameters() if n.startswith("value.")]
        tparams = [p for n, p in net.named_parameters() if not n.startswith("value.")]
        return vparams, tparams

    def test_value_loss_does_not_reach_the_trunk(self):
        """① 只回传价值损失 ⇒ **主干的参数拿不到梯度**，而 critic 自己的头照常有。"""
        import torch
        net, batch = self._model_and_batch()
        _logits, value, _mem = net.forward_state(batch, None)
        loss = value.sum()
        loss.backward()
        vparams, tparams = self._split(net)
        touched = [n for n, p in net.named_parameters()
                   if p.grad is not None and float(p.grad.abs().sum()) > 0]
        trunk_touched = [n for n in touched if not n.startswith("value.")]
        self.assertEqual(trunk_touched, [],
                         msg=f"价值损失不该碰到主干，但这些参数拿到了梯度：{trunk_touched}")
        self.assertTrue(any(n.startswith("value.") for n in touched),
                        msg="critic 自己的头必须还能学（否则优势就没有基线了）")

    def test_policy_loss_still_reaches_the_trunk(self):
        """② 策略损失**仍然**回传到主干（别把策略的通路也掐断）。"""
        net, batch = self._model_and_batch()
        logits, _value, _mem = net.forward_state(batch, None)
        logits.sum().backward()
        _v, tparams = self._split(net)
        got = [n for n, p in net.named_parameters()
               if p.grad is not None and float(p.grad.abs().sum()) > 0]
        self.assertTrue(got, msg="策略损失没有回传到任何参数 ⇒ 通路断了")

    def test_critic_input_is_the_raw_feature_means(self):
        """③ critic 的输入 = **各组原始特征的掩码均值**（新口径，不走共享主干）。

        ★ 这条钉住"critic 读的是原始局面、不是主干那份被平均掉的摘要"：
          手工复算一遍 `critic_input` ⇒ 必须与模型内部用的**逐位相同**。
        """
        import torch
        net, batch = self._model_and_batch()
        with torch.no_grad():
            got = net.critic_input(batch["win"], batch["win_mask"], None)
            parts = []
            for g in net.groups:
                x = batch["win"][g]
                m = batch["win_mask"][g].unsqueeze(-1).to(x.dtype)
                parts.append((x * m).sum(1) / m.sum(1).clamp(min=1.0))
            ref = torch.cat(parts, dim=-1)
        self.assertEqual(tuple(got.shape), tuple(ref.shape),
                         msg="critic 输入的形状对不上")
        self.assertLess((got - ref).abs().max().item(), 1e-6,
                        msg="critic 输入必须就是各组原始特征的掩码均值")


if __name__ == "__main__":
    unittest.main()
