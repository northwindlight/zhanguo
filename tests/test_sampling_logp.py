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

## 两条路，同一件事

要满足「两侧同分布」，只有两条路：**(a) 两侧都不加权** 或 **(b) 两侧同一个 β**。

- **2026-09-14 选了 (a)**：`SAMPLING_USE_EXEC = False`。依据是 §V.3d 实测
  "软加权对**行为**是空操作"（12 局配对，撞墙 33.2% vs 33.8%）⇒ 删掉零行为损失。
- **2026-09-18 用户改选 (b)**（"第三条路"）。因为那条"空操作"的前提是**头没训过**
  （`p_exec≈0.5` ⇒ 压制≈0），而实测冻结表征里 `ok` **线性可分**
  （`probe_exec_head_learn.py`：留出 AUC 0.457→**0.880**）⇒ 头训得出来、压制才有力。
  于是 β 成了可调/可退火的正常超参，**但两侧必须同值**。

★无论走哪条，**本文件钉的不变量都不变**：采样存的 `logp` 与更新算的 `logp`
必须来自**同一个分布**。变的是实现（(a) 靠"都不加权"，(b) 靠"同一处赋值"）。

## 本测试钉什么

1. 默认（`β=0`）时采样 logp == 原始 logits 的 `log_softmax`（与更新侧逐位相同）；
2. **β 只有一个赋值点**（`_sync_exec_sampling`），采样三处与更新侧同源；
3. worker（spawn 子进程）**从 `args` 现取**，不靠父进程的模块全局。

行为侧的那条（β>0 时 `kl≈0` 且 `clipfrac≈0`）在 `tests/test_exec_bias.py`。
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

    def test_train_py_sampling_uses_the_same_beta_as_the_update(self):
        """★静态守卫：采样三处与更新侧**同源**，且 β 只有一个赋值点。

        旧口径是"采样一律不加权"；(b) 之后加权合法，但**两侧必须同值** ——
        所以这里钉的是"只有一个地方能设定它"，而不是"它必须是 False"。
        """
        import rl.train as T
        src = inspect.getsource(T)
        self.assertNotIn("use_exec=args.exec_head > 0", src,
                         "采样侧又出现了自成一体的 `use_exec=args.exec_head > 0`")
        self.assertEqual(src.count("use_exec=SAMPLING_USE_EXEC"), 3,
                         "train.py 里应有 3 处采样/评估调用走 SAMPLING_USE_EXEC")
        self.assertEqual(src.count("exec_beta=SAMPLING_EXEC_BETA"), 3,
                         "那 3 处必须同时带上同一个 β（否则两侧不同分布）")
        self.assertIn("exec_beta=args.exec_beta,", src,
                      "PPO(更新侧) 没拿到 --exec-beta")
        self.assertIn("_sync_exec_sampling(args.exec_beta)", src,
                      "没把 --exec-beta 同步到采样侧")
        self.assertEqual(src.count("SAMPLING_EXEC_BETA = "), 2,
                         "SAMPLING_EXEC_BETA 应当只有「模块默认 + 函数内赋值」两处")
        self.assertEqual(src.count("SAMPLING_USE_EXEC = "), 2,
                         "SAMPLING_USE_EXEC 应当只有「模块默认 False + 函数内赋值」两处")

    def test_workers_take_beta_from_args(self):
        """并行 worker 同理，但它是 `spawn` 子进程 ⇒ **父进程的模块全局带不过来**，
        必须从 `args` 现取（`HORIZON` 那族坑：别用 import 时刻的快照）。"""
        import rl.workers as W
        src = inspect.getsource(W)
        self.assertNotIn("use_exec=False", src,
                         "worker 采样又被硬编码成不加权了 —— 那会与更新侧不同分布")
        self.assertNotIn("use_exec=(args.exec_head > 0)", src,
                         "worker 采样侧又出现了自成一体的加权")
        self.assertIn('getattr(args, "exec_beta"', src,
                      "worker 没从 args 现取 β")
        self.assertIn("use_exec=_beta > 0, exec_beta=_beta", src,
                      "worker 的开关与强度必须出自同一个 _beta")


if __name__ == "__main__":
    unittest.main()
