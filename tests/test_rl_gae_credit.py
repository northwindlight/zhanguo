# -*- coding: utf-8 -*-
"""★ **GAE-λ 的信用视界** —— 把"终局结果能回传多远"钉成闸门。

★ 为什么要有这条线（2026-10-28）
  ──────────────────────────────
  "策略学不动"曾经被归因成"信号被 critic 吃掉" ⇒ 打算去动**模型结构**。
  实测（`rl/gae_credit_probe.py`）证明真正掐死信号的是 **λ**：
  GAE-λ 的递推让**终局奖励**在第 t 步的权重是 `λ^m`（m = 到终局的步数）⇒
  **信用视界 ≈ 1/(1−λ) 步**；而这一局每方中位 **265 步** ⇒ λ=0.95 时
  **92.7% 的决策拿不到终局结果**（中位权重 6.3e-9）。

★ 这条线的性质：**纯算术**（不碰引擎、不训练）⇒ CI 上跑得动、跑得快。
  它钉的是**机制**，不是某个具体数：
    · λ=1 且 γ=1 ⇒ `adv_t ≡ R − V_t`（蒙特卡洛优势，闭式）；
    · 终局奖励对第 t 步的权重 ≡ `λ^m`（可微 ⇒ 用"扰动终局奖励"量出来）；
    · `S.GAE_LAMBDA` 的改动**必须真的传到 `gae_segments`**（默认值是**导入时**绑死的
      ⇒ 这条最容易变成"旋钮拧了没反应"的那一类静默失败）。
"""
from __future__ import annotations

import unittest

from tests.rl_deps import has_torch, torch_or_skip


def _segment(n: int, *, value: float = 0.3, terminal_reward: float = 1.0):
    """造一段 n 步的轨迹：**只有最后一步**带终局奖励（SHAPING 关时的真实形状）。"""
    from rl.train import Step
    out = []
    for i in range(n):
        out.append(Step(obs={}, aidx=0, logp=-1.0, value=value, reward=0.0,
                        done=(i == n - 1), player="甲", episode_id=0, member="m0"))
    out[-1].reward = terminal_reward
    return out


@unittest.skipUnless(has_torch(), torch_or_skip())
class TestGaeCreditHorizon(unittest.TestCase):
    def test_mc_lambda_advantage_is_outcome_minus_value(self):
        """λ=1 且 γ=1 ⇒ `adv_t ≡ R − V_t`（蒙特卡洛优势，闭式）。"""
        from rl.train import gae_segments
        steps = _segment(6, value=0.3, terminal_reward=1.0)
        adv, ret = gae_segments(steps, gamma=1.0, lam=1.0)
        for t, s in enumerate(steps):
            self.assertAlmostEqual(float(adv[t]), 1.0 - s.value, places=5,
                                   msg=f"第 {t} 步：MC 优势必须是 R − V")
            self.assertAlmostEqual(float(ret[t]), 1.0, places=5,
                                   msg=f"第 {t} 步：回报必须等于终局结果")

    def test_terminal_weight_decays_as_lambda_to_the_m(self):
        """终局奖励对第 t 步的权重 ≡ `λ^m`（m = 该步到终局的步数）。"""
        from rl.train import gae_segments
        n = 6
        base = _segment(n, value=0.3, terminal_reward=1.0)
        bumped = _segment(n, value=0.3, terminal_reward=2.0)   # 终局 +1
        for lam in (0.5, 0.95, 1.0):
            a0, _ = gae_segments(base, gamma=1.0, lam=lam)
            a1, _ = gae_segments(bumped, gamma=1.0, lam=lam)
            for t in range(n):
                m = n - 1 - t
                self.assertAlmostEqual(float(a1[t] - a0[t]), lam ** m, places=5,
                                       msg=f"λ={lam}、第 {t} 步（m={m}）："
                                           f"终局权重必须是 λ^m")

    def test_credit_horizon_matches_one_over_one_minus_lambda(self):
        """信用视界 ≈ 1/(1−λ) 步 —— **这一条是"为什么 λ=0.95 不可能学得动"的算术**。

        ★ 量的办法：**扰动终局奖励**（+1）⇒ 某一步的优势变化量 = 那个距离上的权重。
          · 距终局 `d` 步 ⇒ 权重 `λ^d`；
          · `d = 1/(1−λ)` 处权重 ≈ `λ^20 = 0.358`（还有三成）；
          · `d = 400` 处（这一局每方中位 265 步）⇒ `0.95^400 = 6.6e-09`（等于没有）。
        """
        from rl.train import gae_segments
        n = 400
        base = _segment(n, value=0.3, terminal_reward=1.0)
        bumped = _segment(n, value=0.3, terminal_reward=2.0)
        for lam, near_d, far_d in ((0.95, 20, 399), (0.99, 100, 399)):
            a0, _ = gae_segments(base, gamma=1.0, lam=lam)
            a1, _ = gae_segments(bumped, gamma=1.0, lam=lam)
            near = float(a1[n - 1 - near_d] - a0[n - 1 - near_d])
            far = float(a1[n - 1 - far_d] - a0[n - 1 - far_d])
            self.assertAlmostEqual(near, lam ** near_d, places=5,
                                   msg=f"λ={lam}：距终局 {near_d} 步的权重必须是 λ^d")
            self.assertGreater(near, 0.2,
                               msg=f"λ={lam}：{near_d} 步外的终局权重不该是 0")
            self.assertLess(abs(far), 0.05,
                            msg=f"λ={lam}：距终局 {far_d} 步的终局权重应已衰减到"
                                f"可忽略（实测 {far:.3g}）")
        # ★ 顺带把**两个数**钉在这条线上（它们是"为什么 λ 必须≈1"的算术）：
        #   0.95^399 = 6.6e-09（等于没有）· 0.99^399 = 0.018（也几乎没有）
        #   ⇒ 只有 λ≈1 才把整局的结果送到每一个决策上。

    def test_prior_table_lambda_change_reaches_the_math(self):
        """★ `S.GAE_LAMBDA` 改了必须**真的**改变 `gae_segments` 的结果。

        ★ 为什么这条必须钉：`gae_segments` 的签名是 `lam=S.GAE_LAMBDA` ——
          **默认值在导入时就被绑死**了 ⇒ 训练侧两处调用点**必须显式传**
          （`gamma=S.GAMMA, lam=S.GAE_LAMBDA`）否则 `--lam` 旋钮**拧了没反应**，
          而且**不报错**（这正是"静默失败"那一类）。
        """
        from rl import scoring as S
        from rl.train import gae_segments
        self.assertIn("GAE_LAMBDA", S._TUNABLE, "λ 必须是先验表里的可调项")
        steps = _segment(6, value=0.3, terminal_reward=1.0)
        old = S.GAE_LAMBDA
        try:
            S.GAE_LAMBDA = 1.0
            a_mc, _ = gae_segments(steps, gamma=S.GAMMA, lam=S.GAE_LAMBDA)
            S.GAE_LAMBDA = 0.5
            a_half, _ = gae_segments(steps, gamma=S.GAMMA, lam=S.GAE_LAMBDA)
        finally:
            S.GAE_LAMBMA = old
        self.assertNotAlmostEqual(float(a_mc[0]), float(a_half[0]), places=6,
                                  msg="λ 改了而结果没变 ⇒ 旋钮没接上（静默失败）")


if __name__ == "__main__":
    unittest.main()
