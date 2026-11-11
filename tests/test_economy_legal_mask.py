"""Extra executability mask + per-decision (long-horizon) advantage.

The 2026-11-11 failure audit showed projects mode masked padding only, so 74% of
PPO samples were unaffordable projects, and that every decision of one
(country, period) shared one constant Monte-Carlo advantage. These tests pin the
two switches that fix that: a legality mask inside the policy, and a TD(0)
per-decision advantage that still targets the critic's full-horizon returns.
"""
import math
import unittest

import torch

from rl.economy.adapter import WIDTH
from rl.economy.model import Policy
from rl.economy.online import advantage_report, decision_advantages


def batch(legal, candidates=4):
    return dict(context=torch.randn(1, 2, WIDTH), context_mask=torch.ones(1, 2, dtype=torch.bool),
                candidates=torch.randn(1, candidates, WIDTH),
                candidate_mask=torch.ones(1, candidates, dtype=torch.bool),
                legal=torch.tensor([legal], dtype=torch.bool),
                limits=torch.ones(1, candidates))


def projects(**kwargs):
    return Policy(width=16, depth=1, heads=2, action_mode='projects', critic=True, **kwargs)


class LegalMaskChecks(unittest.TestCase):
    def test_extra_mask_removes_unfinanceable_candidates(self):
        model = projects(legal_mask=True)
        logits, value, executable = model(batch([True, False, True, False]), return_value=True)
        self.assertEqual(float(logits[0, 1]), -1e9)
        self.assertEqual(float(logits[0, 3]), -1e9)
        self.assertTrue(math.isfinite(float(logits[0, 0])))
        self.assertTrue(math.isfinite(float(logits[0, 2])))

    def test_without_the_extra_mask_legality_stays_a_soft_prior_only(self):
        model = projects()
        logits, _, _ = model(batch([True, False, True, False]), return_value=True)
        self.assertTrue(math.isfinite(float(logits[0, 1])))

    def test_masked_distribution_never_samples_an_illegal_candidate(self):
        model = projects(legal_mask=True)
        logits, _, _ = model(batch([True, False, True, False]), return_value=True)
        distribution = torch.distributions.Categorical(logits=logits[0])
        sampled = {int(distribution.sample()) for _ in range(200)}
        self.assertTrue(sampled <= {0, 2}, sampled)

    def test_exec_prior_weight_is_configurable_and_survives_the_config_round_trip(self):
        weak, strong = projects(exec_prior_weight=0.), projects(exec_prior_weight=2.)
        strong.load_state_dict(weak.state_dict())
        with torch.no_grad():
            left, right = weak(batch([True] * 4))[0], strong(batch([True] * 4))[0]
        self.assertFalse(torch.allclose(left, right))
        self.assertEqual(strong.config['exec_prior_weight'], 2.)
        clone = Policy(**strong.config)
        self.assertEqual(clone.config['exec_prior_weight'], 2.)
        self.assertFalse(clone.config['legal_mask'])


class DecisionAdvantageChecks(unittest.TestCase):
    @staticmethod
    def stream():
        return [dict(reward=0., value=1., period=0, action=0, action_kind='finish', executed=None,
                     row=dict(legal=[True] * 4)),
                dict(reward=0., value=2., period=0, action=0, action_kind='finish', executed=None,
                     row=dict(legal=[True] * 4)),
                dict(reward=1., value=3., period=0, action=1, action_kind='build', executed=True,
                     row=dict(legal=[True] * 4)),
                dict(reward=0., value=.5, period=1, action=0, action_kind='finish', executed=None,
                     row=dict(legal=[True] * 4))]

    def test_td_advantage_is_r_plus_next_value_minus_value(self):
        values = decision_advantages([self.stream()], 1.)
        self.assertEqual(len(values), 4)
        for got, want in zip(values, [1., 1., -1.5, -.5]):
            self.assertAlmostEqual(got, want)

    def test_td_advantage_differs_inside_one_period_where_mc_does_not(self):
        stream = self.stream()
        td = decision_advantages([stream])
        self.assertGreater(max(td[:3]) - min(td[:3]), 0.)
        # The historical Monte-Carlo advantage is constant per (country, period):
        # a return carried backwards does not depend on where inside the turn we are.
        mc = []
        future = 0.
        for step in reversed(stream):
            future += step['reward']
            mc.append(future)
        mc = list(reversed(mc))
        self.assertEqual(max(mc[:3]) - min(mc[:3]), 0.)

    def test_report_aligns_with_steps_and_reports_where_signal_lives(self):
        stream = self.stream()
        report = advantage_report([stream], decision_advantages([stream]))
        self.assertEqual(report['steps'], 4)
        self.assertEqual(report['streams'], 1)
        self.assertEqual(report['sampled_legal_rate'], 1.)
        self.assertEqual(report['success_steps'], 1)
        self.assertEqual(report['executed_rate'], .25)
        self.assertLess(report['between_period_share'], .5)
        self.assertAlmostEqual(report['nonzero_reward_share'], .25)


if __name__ == '__main__':
    unittest.main()
