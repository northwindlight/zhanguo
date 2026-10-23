# -*- coding: utf-8 -*-
"""★★ **协议口径**（报告 §7）：温度一致性 / 对手的 hold 口径 / 学习统计的范围。

用户 2026-10-21 的复盘报告（`~/rl-tactics-repair-upgrade-report.md` §7）指出的三件事：

  §7.1 采样用 `softmax(logits) ** (1/T)`，存的 logp 却来自**原始** softmax，
        更新时也按**未除温度**的 logits 算 ⇒ 非 1 温度下 **PPO 的行为概率与记录对不上**。
  §7.2 `ScorerGreedy` 把 `hold` 记成 **0**，其余候选记**绝对分** ⇒ 两个量纲混着比
        ⇒ 当前局面分 100、某 move 之后 99 时，它选了**让局面变差**的那个 move。
  §7.3 `ppo_update()` 返回的是**循环末次**的统计，却被当成"整个 iter 的均值"看。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl.model import build_model                      # noqa: E402
    from rl.sandbox import Sandbox                        # noqa: E402
    from rl.train import (_ppo_logprobs, _tempered_probs,  # noqa: E402
                          collect_episode, ppo_update)
    from rl import opponents as OPP                       # noqa: E402
else:
    Sandbox = build_model = opponents = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _nets(sb):
    torch.manual_seed(0)
    return {p: build_model() for p in sb.players}


class TestTemperatureContract(unittest.TestCase):
    """§7.1：采样 / 存的 logp / PPO 重算 —— **同一份策略定义**。"""

    def test_tempered_probs_is_the_tempered_policy(self):
        """★ 温度后的分布 = `softmax(logits/T)`。

        ★ 顺带记一条**容易搞混的事实**：`softmax(logits) ** (1/T)` 归一化后
          **与** `softmax(logits/T)` **是同一个分布**（都 ∝ `exp(x_i/T)`）⇒
          旧采样式子**本身没错**，错的是**存的 logp 用了原始 softmax**
          ⇒ 采样分布与记录/更新用的分布**对不上**（那才是报告 §7.1 的病灶）。
        """
        logits = torch.tensor([[1.0, 2.0, 3.0]])
        p = _tempered_probs(logits, 0.5).numpy()[0]
        expect = torch.softmax(logits / 0.5, -1).numpy()[0]
        self.assertTrue(np.allclose(p, expect))
        raw = torch.softmax(logits, -1).numpy()[0]
        power = raw ** (1.0 / 0.5)
        power = power / power.sum()
        self.assertTrue(np.allclose(p, power),
                        "「概率开方」与「logits 除以 T」不是同一个分布？")
        # ★ **关键**：非 1 温度下，温度后的分布**必须**与原始 softmax 不同
        self.assertFalse(np.allclose(p, raw),
                         "温度没起作用 ⇒ 这条测不出东西")

    def test_collected_logp_matches_the_behavior_distribution(self):
        """★★ 存的 logp **必须**是**行为分布**（含温度）的对数概率。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=3).reset(random_starts=False)
        nets = _nets(sb)
        temp = 0.5
        steps, _info = collect_episode(nets, sb, temperature=temp,
                                       rng=np.random.default_rng(0))
        self.assertTrue(steps)
        for st in steps:
            self.assertAlmostEqual(st.temperature, temp, places=9,
                                   msg="样本没记下有效温度")
            batch = st.obs["cand"]
            from rl.train import collate, to_dev
            b = to_dev(collate([st.obs]), "cpu")
            with torch.inference_mode():
                logits, _v = nets[st.player](b)
            probs = _tempered_probs(logits[0], temp).numpy()
            self.assertAlmostEqual(st.logp, float(np.log(max(1e-12, probs[st.aidx]))),
                                   places=5,
                                   msg="存的 logp 与行为分布对不上")

    def test_ratio_is_one_before_the_update_at_non_unit_temperature(self):
        """★★★ **验收**：相同参数、更新前，非 1 温度下 `old/new ratio` 也应为 **1**。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=3).reset(random_starts=False)
        nets = _nets(sb)
        temp = 0.5
        steps, _info = collect_episode(nets, sb, temperature=temp,
                                       rng=np.random.default_rng(0))
        self.assertTrue(steps)
        from rl.train import collate, to_dev
        for st in steps:
            b = to_dev(collate([st.obs]), "cpu")
            with torch.inference_mode():
                logits, _v = nets[st.player](b)
            logp_all = _ppo_logprobs(logits, temp)
            new_logp = float(logp_all[0, st.aidx])
            ratio = float(np.exp(new_logp - st.logp))
            self.assertAlmostEqual(ratio, 1.0, places=4,
                                   msg=f"更新前 ratio = {ratio}（应为 1）")

    def test_ppo_update_uses_the_same_temperature(self):
        """★ PPO 重算的 logp 与采集时**同一份温度**（否则比的是两个分布）。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=2).reset(random_starts=False)
        nets = _nets(sb)
        temp = 0.5
        steps, _info = collect_episode(nets, sb, temperature=temp,
                                       rng=np.random.default_rng(0))
        net = nets[sb.players[0]]
        before = [p.detach().clone() for p in net.parameters()]
        # ★★ `ppo_update` 的契约是"**只喂这一方的步**"（见它的 docstring）——
        #   把两家的步一起喂进去，KL 拿的是"这一家的网"在"另一家的观测"上的 logp
        #   ⇒ 那个数**本来就没有意义**（不是更新坏了，是喂错了）。
        #   ⇒ 这里按契约只喂本方的步，KL 才有"更新前应为 0"这个含义。
        mine = [s for s in steps if s.player == sb.players[0]]
        self.assertTrue(mine, "用例前提：本方得有步")
        st = ppo_update(net, mine, epochs=1, lr=0.0, temperature=temp)
        self.assertIn("kl", st)
        self.assertAlmostEqual(float(st["kl"]), 0.0, places=4,
                               msg="更新前（lr=0）KL 应为 0 ⇒ 两边口径一致")
        for a, b in zip(before, net.parameters()):
            self.assertTrue(torch.equal(a, b), "lr=0 却改了参数")


class _FakeSb:
    """给 `ScorerGreedy.act` 用的**假沙盒**（只实现它用到的那两个方法）。"""

    def __init__(self, base: float, log: list | None = None):
        self.base = base
        self.log = log if log is not None else []

    def clone(self):
        c = _FakeSb(self.base, self.log)
        self.log.append(c)
        return c

    def step(self, action):
        return (True, "")


class TestOpponentHoldCaliber(unittest.TestCase):
    """§7.2：`hold` 与其它候选**必须同一个量纲**。"""

    def test_hold_wins_when_moving_lowers_the_score(self):
        """★★ **验收 fixture**：当前局面分 100、某 move 之后 99 ⇒ **选 hold**。

        ★ 旧口径（hold 记 0、move 记绝对分 99）会选 move ⇒ 选了让局面变差的着法。
        """
        calls: list = []
        fake = _FakeSb(100.0, calls)

        def fake_score(sbm, me):
            # ★ 原来的局面 = 100，克隆出来的（= 动过之后）= 99
            return 100.0 if sbm is fake else 99.0

        opp = OPP.make("scorer_greedy", k=4)
        acts = [(1, "hold", 0, 0), (1, "move", 1, 1)]
        probs = np.array([0.5, 0.5])
        with mock.patch("rl.train._score", side_effect=fake_score):
            idx = opp.act(fake, "甲", acts, probs)
        self.assertEqual(acts[idx][1], "hold",
                         "局面分 100、move 之后 99，却选了 move ⇒ 量纲混着比了")

    def test_opponent_declares_a_caliber_version(self):
        """★ 口径改过 ⇒ **版本号**要能看见（旧胜率曲线不再可比）。"""
        opp = OPP.make("scorer_greedy", k=4)
        self.assertGreaterEqual(int(opp.version), 2,
                                "口径改过了却没升版本号 ⇒ 旧曲线会被混用")


class TestLearningStatsHaveAScope(unittest.TestCase):
    """§7.3：学习统计**按明确范围汇总**（不是最后一个 minibatch 的快照）。"""

    def _buffer(self, sb, nets, n=4):
        steps, _info = collect_episode(nets, sb, greedy=True,
                                       rng=np.random.default_rng(0))
        return steps

    def test_stats_report_how_many_minibatches_were_aggregated(self):
        """★ 统计必须**说明自己汇总了多少**（范围显式）。"""
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=2).reset(random_starts=False)
        nets = _nets(sb)
        steps = self._buffer(sb, nets)
        n = len(steps)
        if n < 2:
            self.skipTest("样本太少，测不了分批")
        net = nets[sb.players[0]]
        st = ppo_update(net, steps, epochs=2, minibatch=1, lr=0.0)
        self.assertIn("minibatches", st)
        self.assertEqual(int(st["minibatches"]), n * 2,
                         "汇总的 minibatch 数不对（= 样本数 × epoch 数）")

    def test_aggregate_equals_the_single_batch_value(self):
        """★★★ 汇总（分批求均值）**必须等于**"整批一次算"的值。

        ★ 这是"它是均值、不是末次快照"的判据：`lr=0` ⇒ 参数不变 ⇒
          每个 minibatch 的统计与批的划分无关 ⇒ 两种切法的均值必须相同。
          （旧口径返回**末次**快照 ⇒ 两者差很多。）
        """
        sb = Sandbox(seed=0, size=8, n_nations=2, t_max=2).reset(random_starts=False)
        nets = _nets(sb)
        steps = self._buffer(sb, nets)
        n = len(steps)
        if n < 2:
            self.skipTest("样本太少，测不了分批")
        net = nets[sb.players[0]]
        one = ppo_update(net, steps, epochs=1, minibatch=n, lr=0.0)
        many = ppo_update(net, steps, epochs=1, minibatch=1, lr=0.0)
        self.assertAlmostEqual(float(one["loss"]), float(many["loss"]), places=4,
                               msg="分批汇总 ≠ 整批均值 ⇒ 它取的是末次快照，不是均值")
        self.assertAlmostEqual(float(one["pg"]), float(many["pg"]), places=4)


if __name__ == "__main__":
    unittest.main()
