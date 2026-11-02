"""Environment/seat failures cannot be hidden by aggregate teacher scores."""
import copy
import unittest
from rl.economy.acceptance import compare,summary,cases,regresses


def game(student=False,values=(100.,100.),tails=(10.,10.)):
    actors=('a','b');version='economy-project-bc'if student else 'v13plus'
    return dict(history=[dict(countries={a:dict(version=version,profile='balanced',
        failures={},project_failures=0,truncated=0) for a in actors})],
        consumption=dict(zip(actors,values)),tail_consumption=dict(zip(actors,tails)),
        milestones={a:dict(first_recruit=4,first_expansion=6) for a in actors},supply_shortfalls={})


class AcceptanceChecks(unittest.TestCase):
    def test_strong_seat_does_not_hide_weak_seat(self):
        result=compare(game(),game(True,(150,80)))
        self.assertGreater(result['total_ratio'],1.)
        self.assertFalse(result['near']);self.assertFalse(result['surpass'])

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
