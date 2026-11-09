import copy
import math
import random
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from rl.economy.reward_shaping import objective,shape_trajectories
from tests.test_economy_bc import torch


class InvalidProjectPenaltyChecks(unittest.TestCase):
    def test_every_failed_attempt_is_charged_without_mutating_raw_data(self):
        row=object()
        failed=dict(row=row,reward=.2,action_kind='build',executed=False,logp=-1.,value=0.)
        streams=[[failed.copy(),failed.copy(),dict(failed,action_kind='recruit',executed=True),
                  dict(failed,action_kind='finish',executed=None)]]
        shaped,metrics=shape_trajectories(streams,.05)
        self.assertEqual(metrics['invalid_actions'],2)
        self.assertAlmostEqual(metrics['penalty_total'],.1)
        self.assertAlmostEqual(metrics['raw_reward_sum'],.8)
        self.assertAlmostEqual(metrics['training_reward_sum'],.7)
        self.assertAlmostEqual(shaped[0][0]['reward'],.15)
        self.assertEqual(shaped[0][2]['reward'],.2)
        self.assertEqual(shaped[0][3]['reward'],.2)
        self.assertTrue(all(s['reward']==.2 for s in streams[0]))
        self.assertIs(shaped[0][0]['row'],row)
        self.assertEqual(shaped[0][0]['logp'],-1.)

    def test_default_is_identity_and_does_not_require_old_data_labels(self):
        streams=[[dict(reward=1.)]]
        same,metrics=shape_trajectories(streams)
        self.assertIs(same,streams);self.assertFalse(metrics['objective']['enabled'])
        self.assertEqual(metrics['penalty_total'],0.)
        with self.assertRaisesRegex(ValueError,'action labels'):shape_trajectories(streams,.1)
        with self.assertRaisesRegex(ValueError,'execution outcomes'):
            shape_trajectories([[dict(reward=1.,action_kind='build')]],.1)

    def test_penalty_requires_finite_nonnegative_coefficient(self):
        for value in (-.1,math.inf,math.nan):
            with self.assertRaises(ValueError):objective(value)


@unittest.skipIf(torch is None,'Optional training dependencies')
class PenaltyPPOIntegrationChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):torch.set_num_threads(1)

    def test_checkpoint_keeps_raw_S_and_training_objective_distinct(self):
        from rl.economy.model import Policy,load_policy
        from rl.economy.ppo_cycle import save_model
        model=Policy(width=16,depth=1,heads=4,action_mode='projects',critic=True)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'candidate.pt'
            save_model(path,model,{},set(),invalid_action_penalty=.003)
            loaded,saved=load_policy(path)
            self.assertEqual(saved['reward']['name'],'paper_S_consumption_increment')
            self.assertEqual(saved['training_objective']['invalid_action_penalty'],.003)
            self.assertTrue(saved['training_objective']['enabled'])
            for name,value in model.state_dict().items():
                torch.testing.assert_close(value,loaded.state_dict()[name],rtol=0.,atol=0.)

    def test_shaped_rewards_reach_ppo_but_raw_rollouts_remain_unchanged(self):
        from tests.test_economy_ppo_updates import Tiny,PPOUpdateChecks
        from rl.economy.online import ppo_update
        initial=Tiny();anchor=copy.deepcopy(initial)
        streams=PPOUpdateChecks().trajectories(initial,16)
        for i,stream in enumerate(streams):stream[0].update(action_kind='build',executed=bool(i%2))
        raw=[s[0]['reward'] for s in streams]
        random.seed(44)
        report=ppo_update(initial,streams,anchor,epochs=1,batch_size=2,update_batch_size=8,
                          invalid_action_penalty=.1)
        self.assertEqual(report['coverage'],1.)
        self.assertEqual(report['reward_shaping']['invalid_actions'],8)
        self.assertAlmostEqual(report['reward_shaping']['penalty_total'],.8)
        self.assertAlmostEqual(report['return_mean'],sum(raw)/16-.05,places=6)
        self.assertEqual([s[0]['reward'] for s in streams],raw)
        self.assertTrue(all('return' not in s[0] for s in streams))

    def test_explicit_zero_matches_default_optimizer_update(self):
        from tests.test_economy_ppo_updates import Tiny,PPOUpdateChecks
        from rl.economy.online import ppo_update
        initial=Tiny();anchor=copy.deepcopy(initial)
        streams=PPOUpdateChecks().trajectories(initial,16)
        models=[copy.deepcopy(initial),copy.deepcopy(initial)]
        for model,options in zip(models,({},dict(invalid_action_penalty=0.))):
            random.seed(53)
            ppo_update(model,streams,anchor,epochs=1,batch_size=2,update_batch_size=8,**options)
        for a,b in zip(models[0].parameters(),models[1].parameters()):
            torch.testing.assert_close(a,b,rtol=0.,atol=0.)
