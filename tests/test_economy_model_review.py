import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.econ_model_review import review
from tests.test_economy_acceptance import game,torch


@unittest.skipIf(torch is None,'Optional training dependencies')
class ModelCheckpointReviewChecks(unittest.TestCase):
    def setup_run(self,root,improved=None,missing=None):
        from rl.economy.acceptance import compare
        (root/'live').mkdir();(root/'evaluations').mkdir()
        (root/'model-review-policy.json').write_text(json.dumps(dict(after_round=20,reference_round=10,
            reference_policy_sha256='policy-10')))
        for number in range(10,21):
            folder=root/'live'/f'round-{number:06d}';folder.mkdir()
            (folder/'candidate.json').write_text(json.dumps(dict(policy_sha256=f'policy-{number}')))
            if number==missing:continue
            values=(110,110) if number==improved else (100,100)
            cases=[dict(seed=seed,size=20,turns=150,swap=bool(i),
                        **compare(game(),game(True,values))) for i,seed in enumerate((8101,8102))]
            folder=root/'evaluations'/f'round-{number:06d}';folder.mkdir()
            (folder/'summary.json').write_text(json.dumps(dict(phase='complete',
                screen=dict(evaluations=[dict(policy_sha256=f'policy-{number}',complete=True,results=cases)]))))

    def test_twentieth_round_without_improvement_prepares_trials_but_does_not_stop(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);self.setup_run(root)
            result=review(root);self.assertEqual(result['status'],'prepare_model_change')
            plan=json.loads((root/'model-change-preparation/comparison-plan.json').read_text())
            self.assertTrue(plan['continue_exploratory_training'])
            self.assertFalse(plan['production_change']);self.assertFalse(result['accepted'])

    def test_intermediate_best_counts_even_if_round_twenty_regresses(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);self.setup_run(root,improved=18)
            result=review(root);self.assertEqual(result['status'],'continue_current_model')
            self.assertEqual(result['improved_rounds'],[18])

    def test_missing_evaluation_cannot_be_mistaken_for_no_improvement(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);self.setup_run(root,missing=19)
            result=review(root);self.assertEqual(result['status'],'waiting_for_evaluations')
            self.assertEqual(result['missing_rounds'],[19])
            self.assertFalse((root/'model-change-preparation').exists())

    def test_one_weak_seat_blocks_aggregate_only_improvement(self):
        from scripts.econ_model_review import improvement
        from rl.economy.acceptance import compare
        before=dict(seed=8101,size=20,turns=150,swap=False,**compare(game(),game(True)))
        after=dict(seed=8101,size=20,turns=150,swap=False,**compare(game(),game(True,(140,80))))
        self.assertFalse(improvement(dict(results=[before]),dict(results=[after])))
