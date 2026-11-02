"""Environment/seat failures cannot be hidden by aggregate teacher scores."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from tests.test_economy_bc import torch
if torch is not None:
    from rl.economy.acceptance import compare,summary,cases,regresses


def game(student=False,values=(100.,100.),tails=(10.,10.)):
    actors=('a','b');version='economy-project-bc'if student else 'v13plus'
    return dict(turns=150,history=[dict(countries={a:dict(version=version,profile='balanced',
        failures={},project_failures=0,truncated=0) for a in actors})],
        consumption=dict(zip(actors,values)),tail_consumption=dict(zip(actors,tails)),
        milestones={a:dict(first_recruit=4,first_expansion=6) for a in actors},supply_shortfalls={})


@unittest.skipIf(torch is None,'Optional training dependencies')
class AcceptanceChecks(unittest.TestCase):
    def test_exploratory_ppo_changes_training_only(self):
        from rl.economy.acceptance import training_stage
        self.assertEqual(training_stage(False,False),'dagger')
        self.assertEqual(training_stage(False,False,True),'ppo')
        self.assertEqual(training_stage(True,False),'dagger')
        self.assertEqual(training_stage(True,True),'ppo')
        self.assertFalse(summary([compare(game(),game(True))])['near'])

    def test_strong_seat_does_not_hide_weak_seat(self):
        result=compare(game(),game(True,(150,80)))
        self.assertGreater(result['total_ratio'],1.)
        self.assertFalse(result['near']);self.assertFalse(result['surpass'])

    def test_short_smoke_cannot_be_accepted_even_with_strong_scores(self):
        student=game(True,(200,200));student['turns']=8
        self.assertFalse(compare(game(),student)['near'])

    def test_missing_country_cannot_disappear_from_acceptance(self):
        student=game(True);del student['history'][-1]['countries']['b']
        with self.assertRaises(ValueError):compare(game(),student)

    def test_formal_suite_never_replaces_long_game_with_short_failure_check(self):
        from rl.economy.acceptance import suite
        def simulate(**kwargs):
            r=game(student=kwargs.get('model') is not None)
            if kwargs.get('model') is not None:
                r['history'][-1]['countries']['a']['project_failures']=5
            return r
        with TemporaryDirectory() as directory:
            root=Path(directory);out=root/'round';out.mkdir();cache=root/'teachers';cache.mkdir()
            with patch('rl.economy.acceptance.run',side_effect=simulate) as run:
                result=suite(object(),[cases()[0]],out,cache,float('inf'))
            self.assertEqual(run.call_count,2)
            self.assertTrue(all(call.kwargs['turns']==150 for call in run.call_args_list))
            self.assertEqual(result['results'][0]['seats'][0]['failures'],5)
            self.assertTrue(result['results'][0]['complete'])

    def test_late_failure_blocks_good_total_and_matching_teacher_passes(self):
        self.assertTrue(compare(game(),game(True))['surpass'])
        self.assertFalse(compare(game(),game(True,(110,110),(10,8)))['near'])
        self.assertTrue(compare(game(),game(True,(96,96),(9.6,9.6)))['near'])

    def test_every_environment_required_and_holdout_disjoint(self):
        good=compare(game(),game(True));bad=compare(game(),game(True,(80,100)))
        self.assertFalse(summary([good,bad])['near'])
        self.assertEqual(sorted({c['turns']for c in cases()}),[150,200,250])
        self.assertTrue({c['seed']for c in cases()}.isdisjoint(c['seed']for c in cases(True)))

    def test_regression_rejected_even_when_total_gain_hides_weak_seat(self):
        old=dict(seed=1,size=20,turns=150,swap=False,**compare(game(),game(True)))
        new=dict(seed=1,size=20,turns=150,swap=False,**compare(game(),game(True,(150,80))))
        self.assertTrue(regresses(summary([new]),summary([old])))
        self.assertFalse(regresses(summary([old]),summary([old])))

    def test_completed_prefix_progress_can_extend_to_next_environment(self):
        first=cases()[0];second=cases()[1]
        old=dict(**first,complete=True,**compare(game(),game(True,(80,100))))
        improved=dict(**first,complete=True,**compare(game(),game(True)))
        next_case=dict(**second,complete=True,**compare(game(),game(True,(80,100))))
        self.assertFalse(regresses(summary([improved,next_case]),summary([old])))
        self.assertTrue(regresses(summary([improved]),summary([improved,next_case])))

    def test_dagger_aggregates_recent_worlds_instead_of_forgetting_previous_corrections(self):
        import numpy as np
        from rl.economy.acceptance import aggregated_replay
        from rl.economy.adapter import WIDTH,PROJECT_FINGERPRINT
        from rl.economy.data import Writer
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for seed in range(6):
                folder=root/f'round-{seed:03d}';folder.mkdir()
                w=Writer(PROJECT_FINGERPRINT)
                w.add(dict(context=np.zeros((1,WIDTH)),candidates=np.zeros((1,WIDTH)),
                    legal=np.ones(1,bool),limits=np.zeros(1,dtype=np.int64),target=0,quantity=0.))
                w.save(folder/f'world-{seed}.npz',dict(seed=seed))
            original=[object()];rows,seeds=aggregated_replay(original,root)
            self.assertIs(rows[0],original[0]);self.assertEqual(len(rows),5)
            self.assertEqual(seeds,{2,3,4,5})
