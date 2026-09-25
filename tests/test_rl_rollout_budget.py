# -*- coding: utf-8 -*-
"""**步数预算**（内存闸）的守卫。

★★ 为什么要有它：缓冲区 = `步数 × 单帧观测`，**全量held在内存里**。
   而 12-30 的图上单帧观测上百 KB ⇒ 无上限时**一局就能顶到 GB 级**。
   ★ 实测（2026-09-25，ECS）：12-30 的炉子第 1 个 iter 跑了 **31 分钟**、
     RSS 涨到 **1742MB 还在涨**，可用内存只剩 1697MB ⇒ 那是要 OOM 的。
     （8×8 上确实"不至于" —— 用户那句判断在小图上是对的。）

★ 钉三件事：
  1. **截得住**：`max_steps=N` ⇒ 步数 ≤ N，且 `info["truncated"]` 为真。
  2. ★★ **截断处必须是 GAE 的边界**（最后一步 `done=True`）——
     这是**最容易静默出错**的一条：`gae` 靠 `dones[t]` 重置，
     不标的话这一局的尾巴会去**借下一局的价值**（GAE 跨局串味），
     而**训练照样跑得下去、loss 也照样降**，只是学的东西是错的。
  3. **反向对照**：不截断时 `truncated` 为假、且真正终局那一局照常打完。
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
from rl.train import collect_episode                  # noqa: E402


def _sb(size=8, n=3, t_max=500, seed=3):
    return Sandbox(seed=seed, size=size, n_nations=n, t_max=t_max,
                   halls_known=True).reset()


def _nets(sb):
    """★ **必须先钉 torch 的全局种子**：`build_model()` 是随机初始化，而动作是
    `rng.choice(p=net 给的 probs)` ⇒ 权重一变、**采出来的动作就变、整局结果就变**。
      不钉种子的话，这些用例的结果会**随测试执行顺序变**（实测踩到：
      同一个用例单独跑绿、进全量套件跑红）。"""
    torch.manual_seed(0)
    return {p: build_model() for p in sb.players}


class TestStepBudget(unittest.TestCase):
    def test_truncates_at_the_budget(self):
        sb = _sb()
        steps, info = collect_episode(_nets(sb), sb, max_steps=7,
                                      rng=np.random.default_rng(0))
        self.assertEqual(len(steps), 7, "没在预算上停住")
        self.assertTrue(info["truncated"], "截断了却没标出来")

    def test_truncation_is_a_gae_boundary(self):
        """★★ 截断处最后一步必须 `done=True`（否则 GAE 跨局串味，且**不报错**）。"""
        sb = _sb()
        steps, _ = collect_episode(_nets(sb), sb, max_steps=9,
                                   rng=np.random.default_rng(0))
        self.assertTrue(steps[-1].done, "截断处不是 GAE 边界 ⇒ 会去借下一局的价值")
        # ★ 反向对照：边界**只该有一处**（前面那些步不许被顺手标成 done）
        self.assertFalse(any(s.done for s in steps[:-1]),
                         "把中间的步也标成 done 了 ⇒ 优势估计被切碎")

    def test_zero_means_unlimited(self):
        """`0` 是"不限"，不是"走一步就停"（这个错法很隐蔽：还能跑，只是全被截）。"""
        sb = _sb(size=8, n=3, t_max=40)
        steps, info = collect_episode(_nets(sb), sb, max_steps=0,
                                      rng=np.random.default_rng(0))
        self.assertGreater(len(steps), 1, "`0` 被当成「走一步就停」了")
        self.assertFalse(info["truncated"], "没截断却标了截断")

    def test_untruncated_episode_finishes(self):
        """★ 反向对照：给足预算 ⇒ 照常**打到终局**、`truncated=False`、末步是 done。

        ★ 胜方**不保证有**：打到 `t_max` 判平局时 `winner is None`（那是引擎口径）。
          所以这里断言的是"**打完了**"（`is_terminal`），不是"有胜方"——
          原来写成断言有胜方，一跑就红，红的是**测试**不是实现。
        """
        sb = _sb(size=8, n=3, t_max=25)          # ★ 别开太大：8×8 上 60 回合要跑 2 分钟
        steps, info = collect_episode(_nets(sb), sb, max_steps=None,
                                      rng=np.random.default_rng(1))
        # ★★ 先钉**契约**（与具体结局无关）：标了截断 ⟺ 没打完。
        #   只断言"没截断"是脆的 —— 它对"实现把 truncated 恒置 False"没有牙齿，
        #   而且会随网络初始化的不同而变（见 `_nets`）。
        self.assertEqual(info["truncated"], not sb.is_terminal(),
                         "`truncated` 和 `is_terminal()` 对不上（契约破了）")
        self.assertFalse(info["truncated"], "给足预算却没打完")
        self.assertTrue(sb.is_terminal(), "给足预算却没打完")
        self.assertTrue(steps[-1].done, "终局那一局最后一步该是 done")
        self.assertGreater(len(steps), 0)

    def test_budget_bounds_the_real_buffer(self):
        """★ 真正的目的：**缓冲区字节数**被步数封住（大图上尤其重要）。

        这里用 12×12（`n_nations_for` ⇒ 3 国）比 8×8 大一档，
        预算 12 步 ⇒ 总字节数不该随图变大而失控。
        """
        sb = _sb(size=12, n=3)
        steps, info = collect_episode(_nets(sb), sb, max_steps=12,
                                      rng=np.random.default_rng(2))
        self.assertEqual(len(steps), 12)
        self.assertTrue(info["truncated"])

        def nbytes(o):
            n = 0
            for v in o.values():
                if isinstance(v, dict):
                    n += sum(x.nbytes for x in v.values() if hasattr(x, "nbytes"))
                elif hasattr(v, "nbytes"):
                    n += v.nbytes
            return n

        per = max(nbytes(s.obs) for s in steps)
        self.assertLess(per * len(steps), 40 * 2 ** 20,
                        f"12 步就占了 {per*len(steps)/2**20:.0f}MB ⇒ 预算拦不住内存")


if __name__ == "__main__":
    unittest.main()