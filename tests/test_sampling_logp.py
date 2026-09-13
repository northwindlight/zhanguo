# -*- coding: utf-8 -*-
"""★守卫：**采样存的 `logp` 必须与更新算的 `logp` 同分布**（否则 `ratio` 起点 ≠ 1）。

## 病是什么（2026-09-14，ECS 侧 AI 发现，我在生产机上复现）

采样走 `act(..., use_exec=args.exec_head > 0)` → `policy_logits`，存进 `old_logp` 的是

    log_softmax(logit + 0.5·log(σ(p_exec)+1e-3))[a]      ← **加权后**

而 `PPO.update`（`rl/ppo.py`）里是

    logp_all = F.log_softmax(logits)                      ← **未加权**

⇒ **参数一动没动时** `ratio = π_raw(a)/π_w(a) = E_{π_raw}[e^w] / e^{w_a} ≠ 1`。

**两个后果**：
1. 日志里 `kl = mean(old_logp − logp)` **含一个与学习无关的常数偏移**
   ⇒ 「`kl≈0.015` 是健康值」这条读数部分/全部是假象；
2. **clip 作用在错位的比值上** —— 对 `w_a` 很负的动作（exec 头判"点不动"），
   `ratio ≫ 1` ⇒ `A>0` 时被截断没梯度、`A<0` 时不截断还被放大，
   变成**只罚不奖的非对称更新**。

**实测幅度**（单状态，零更新）：随机 exec 头 **0.0%** 候选出信任域、
训过的头 **1.9%** —— 不大，但是**系统性偏差**，且**随 exec 头变自信而放大**
（头的区分度越高，`w` 的离散度越大）。

## 修法为什么是"删采样侧的加权"

`rl/train.py` 的 `SAMPLING_USE_EXEC = False`（§V.3d 实测：软加权对**行为**是空操作
—— 12 局配对，撞墙 33.2% vs 33.8%）⇒ 删掉它**零行为损失**，还顺手消掉这个 bug。
`--exec-head` **保留**：辅助头仍经 `[h, q0]` 把梯度回流主干（那才是它有用的部分）。

## 本测试钉什么

**训练采样用的那一档（默认 `use_exec=False`）拿到的 logp，必须与
"对原始 logits 做 log_softmax 再 gather"逐位相同。**
—— 一旦有人把加权加回采样侧，这条会红。
"""
from __future__ import annotations

import inspect
import unittest

import torch
import torch.nn.functional as F

from rl.env import KINDS, ZhanguoEnv
from rl.ppo import act, forward_batch, _one_step
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer


def _mk():
    env = ZhanguoEnv(map_size=16, max_turns=10)
    env.reset(0)
    w0 = tokenize(env, env._obs())
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    m.eval()
    env.reset(900000)
    return env, m


class TestSamplingLogpMatchesUpdate(unittest.TestCase):

    def test_default_act_logp_is_the_raw_log_softmax(self):
        """★核心：默认（=训练采样用的那一档）的 logp == 原始 logits 的 log_softmax。

        这正是 `PPO.update` 里 `logp_all = log_softmax(logits)` 算的东西
        ⇒ 零更新时 `ratio ≡ 1`，信任域不再错位。
        """
        env, m = _mk()
        obs = env._obs()
        win = tokenize(env, obs)
        with torch.no_grad():
            idx, logp, _v = act(m, obs, win=win)          # 默认 use_exec=False
            lg, _v2, _mk_ = forward_batch(m, [_one_step(obs)], [win])
            want = float(F.log_softmax(lg, dim=-1)[0, idx])
        self.assertAlmostEqual(
            logp, want, places=6,
            msg="采样 logp 与更新侧的 log_softmax 不一致 —— ratio 起点不是 1，"
                "信任域会错位。别把软加权加回采样侧（见本文件头）。")

    def test_train_py_sampling_does_not_use_exec(self):
        """静态断言：`train.py` 里采样/评估那三处走的是 `SAMPLING_USE_EXEC` 常量，
        且该常量是 `False`。有人把它改成 True 时这条会红，逼他先读文件头。"""
        import rl.train as T
        self.assertIs(T.SAMPLING_USE_EXEC, False,
                      "SAMPLING_USE_EXEC 被改回 True 了 —— 那会重新引入 ratio 错位的 bug，"
                      "先读 tests/test_sampling_logp.py 文件头。")
        src = inspect.getsource(T)
        self.assertNotIn("use_exec=args.exec_head > 0", src,
                         "采样侧又出现了 `use_exec=args.exec_head > 0`")
        self.assertEqual(src.count("use_exec=SAMPLING_USE_EXEC"), 3,
                         "train.py 里应有 3 处采样/评估调用走 SAMPLING_USE_EXEC")

    def test_workers_sampling_does_not_use_exec(self):
        """并行 worker 的采样同理（`rl/workers.py`）。"""
        import rl.workers as W
        src = inspect.getsource(W)
        self.assertNotIn("use_exec=(args.exec_head > 0)", src,
                         "worker 采样侧又出现了加权")
        self.assertIn("use_exec=False", src)


if __name__ == "__main__":
    unittest.main()
