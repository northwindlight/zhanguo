"""Long-game screening keeps seat protection and checks cached teacher identity."""
import copy
import unittest
from unittest.mock import patch

from tests.test_economy_bc import torch

if torch is not None:
    from tests.test_economy_acceptance import game
    from rl.economy.acceptance import compare
    from rl.economy.benchmark import SCREEN, assessment, cache_identity_matches, check_teacher
    from rl.economy.provenance import contracts
    from rl.economy.scenarios import config


@unittest.skipIf(torch is None, 'Optional training dependencies')
class BenchmarkChecks(unittest.TestCase):
    def test_only_exact_audited_runtime_transition_can_reuse_cache(self):
        old=dict(runtime=dict(implementation='old',torch='version',threads=1),
                 case=SCREEN[0],policy_sha256='weights',contracts=dict(feature_contract='schema',teacher_contract='teacher'))
        new=copy.deepcopy(old);new['runtime']['implementation']='new'
        self.assertFalse(cache_identity_matches(old,new))
        registry={'raw':dict(feature_contract='schema',implementation_transition=dict(before='old',after='new'))}
        with patch('rl.economy.benchmark.feature_equivalences',return_value=registry):
            self.assertTrue(cache_identity_matches(old,new))
            for key,value in (('policy_sha256','other'),('case',SCREEN[1])):
                bad=copy.deepcopy(old);bad[key]=value
                self.assertFalse(cache_identity_matches(bad,new))
            for key,value in (('implementation','unknown'),('torch','other'),('threads',2)):
                bad=copy.deepcopy(old);bad['runtime'][key]=value
                self.assertFalse(cache_identity_matches(bad,new))
            bad=copy.deepcopy(new);bad['contracts']['teacher_contract']='other'
            self.assertFalse(cache_identity_matches(old,bad))

    def results(self, values=(100.,100.), tails=(10.,10.)):
        return [dict(**case, **compare(game(),game(True,values,tails))) for case in SCREEN]

    def test_aggregate_gain_cannot_hide_tail_or_seat_regression(self):
        baseline=self.results()
        new=self.results((180,96),(18,9))
        self.assertGreater(new[0]['total_ratio'],baseline[0]['total_ratio'])
        self.assertFalse(assessment(new,baseline)['baseline_protected'])

    def test_partial_results_never_qualify(self):
        self.assertFalse(assessment(self.results()[:1])['complete'])
        self.assertFalse(assessment(self.results()[:1])['baseline_protected'])
        self.assertFalse(assessment(self.results())['accepted'])

    def test_cross_environment_counterexample_is_reported(self):
        new=self.results((160,160),(16,16))
        new[1]=self.results((60,60),(6,6))[1]
        report=assessment(new,self.results())
        self.assertAlmostEqual(report['worst_environment_total'],.6)
        self.assertFalse(report['target_met_on_screen'])
        self.assertFalse(report['baseline_protected'])

    def test_cache_requires_same_environment_and_teacher(self):
        case=SCREEN[0];expected=contracts()
        teacher=dict(expected,**case,config=config(case['seed'],size=case['size'],swap=case['swap']))
        check_teacher(teacher,case,expected)
        for key,value in (('teacher_contract','unknown'),('turns',8),('swap',True)):
            bad=copy.deepcopy(teacher);bad[key]=value
            with self.assertRaises(ValueError):check_teacher(bad,case,expected)
        bad=copy.deepcopy(teacher);bad['config']['map_size']=28
        with self.assertRaises(ValueError):check_teacher(bad,case,expected)


if __name__=='__main__':
    unittest.main()
