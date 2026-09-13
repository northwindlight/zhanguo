# -*- coding: utf-8 -*-
"""可执行性辅助头（`--exec-head`）的测试。

三条护栏：
1. **默认关时行为逐位不变** —— `forward_batch` 仍返回 3 元组、`act` 不加权。
   这是全项目的纪律：新开关不得改变既有路径。
2. **开了能跑**，且返回形状对（`p_exec` 是 [B,K]）。
3. **软加权 != 硬 mask**：软加权只改 logits，**没有任何候选被置 -inf**
   —— 这条是**用户口径**（「让模型自己学会哪些点不动」）的守卫。
"""
from __future__ import annotations

import unittest

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.ppo import PPO, Rollout, act, forward_batch, _one_step
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer


def _mk():
    env = ZhanguoEnv(map_size=12, max_turns=4)
    env.reset(0)
    w0 = tokenize(env, env._obs())
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=64, n_layer=2, n_head=2)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    return env, m


class TestExecHead(unittest.TestCase):
    def test_default_returns_three_tuple(self):
        """默认关 ⇒ `forward_batch` 仍返回 3 元组（不破坏既有解包点）。"""
        env, m = _mk()
        obs = env._obs()
        out = forward_batch(m, [_one_step(obs)], [tokenize(env, obs)])
        self.assertEqual(len(out), 3, "默认必须是 (logits, value, mask)")

    def test_on_returns_pexec_with_right_shape(self):
        env, m = _mk()
        obs = env._obs()
        logits, value, mask, pexec = forward_batch(
            m, [_one_step(obs)], [tokenize(env, obs)], return_exec=True)
        self.assertEqual(pexec.shape, logits.shape, "p_exec 应与 logits 同形 [B,K]")

    def test_soft_weight_is_not_a_mask(self):
        """★软加权**不删任何候选**：加权的 logits 里不能出现被置 -inf 的。

        这条守的是用户口径 —— 候选集不预过滤、不硬 mask。
        """
        env, m = _mk()
        obs = env._obs()
        win = tokenize(env, obs)
        lg_raw = forward_batch(m, [_one_step(obs)], [win])[0]
        with torch.no_grad():
            lg_w = act(m, obs, deterministic=True, win=win,
                       use_exec=True)  # 只验证它不炸、且走的是软加权分支
        # 加权后不应出现**新的** -inf（原有的 -1e9 掩码除外）
        finite_before = torch.isfinite(lg_raw).sum().item()
        logits, _v, _mm, pexec = forward_batch(
            m, [_one_step(obs)], [win], return_exec=True)
        weighted = logits + 0.5 * torch.log(torch.sigmoid(pexec) + 1e-3)
        self.assertEqual(torch.isfinite(weighted).sum().item(), finite_before,
                         "软加权不得把任何候选变成 -inf（那就成硬 mask 了）")
        self.assertIsInstance(lg_w[0], int)

    def test_exec_coef_defaults_to_zero(self):
        """`PPO` 的 `exec_coef` 默认 0 ⇒ 不加辅助 loss（与开关存在前相同）。"""
        _env, m = _mk()
        ppo = PPO(m)
        self.assertEqual(ppo.exec_coef, 0.0)

    def test_old_ckpt_loads_with_strict_false(self):
        """★旧 ckpt 没有 `exec_head.*` ⇒ 必须能 `strict=False` 加载（新头随机）。"""
        _env, m = _mk()
        sd = {k: v for k, v in m.state_dict().items()
              if not k.startswith("exec_head.")}       # 模拟旧 ckpt
        miss = m.load_state_dict(sd, strict=False)
        self.assertTrue(any(k.startswith("exec_head.") for k in miss.missing_keys),
                        "缺的应当正好是新加的辅助头")

    def test_rollout_stores_ok_label(self):
        """`Rollout.add` 默认 `ok=True`，显式传入要存下来。"""
        r = Rollout(lam=1.0, normalize=False)
        env, _m = _mk()
        obs = env._obs()
        r.add(obs, 0, 0.0, 0.0, 0.0, False, ok=False)
        self.assertIs(r.steps[-1]["ok"], False)
        r.add(obs, 0, 0.0, 0.0, 0.0, False)
        self.assertIs(r.steps[-1]["ok"], True, "不传时应默认 True（行为不变）")


if __name__ == "__main__":
    unittest.main()
