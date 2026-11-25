import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'cpp'))
from global_returns import global_targets,sequence_indices


class GlobalReturnsTests(unittest.TestCase):
    def fixture(self):
        rows=[dict(kind='decision',event=0,snapshot=0,turn=0,seat=0,index=0,type=4),
              dict(kind='decision',event=1,snapshot=0,turn=0,seat=1,index=0,type=4),
              dict(kind='decision',event=2,snapshot=2,turn=0,seat=2,index=0,type=4),
              dict(kind='settlement',event=3,turn=0,nominal_gdp=[1,100,1000,10000],land=[6,5,5,5],initial_land=[5]*4),
              dict(kind='decision',event=4,snapshot=4,turn=1,seat=0,index=1,type=4),
              dict(kind='decision',event=5,snapshot=4,turn=1,seat=2,index=1,type=4),
              dict(kind='settlement',event=6,turn=1,nominal_gdp=[2,200,2000,20000],land=[7,5,5,5],initial_land=[5]*4)]
        episodes=[]
        for seat,gdp in ((0,[1,2]),(2,[1000,2000])):
            samples=[dict(ti=t,a=0,obs=dict(cand_type=np.array([4]),globals=np.array([float(t)]))) for t in range(2)]
            episodes.append((samples,0,dict(seed=1,seat=seat,nominal_gdp=gdp),[],[]))
        return rows,episodes

    def test_separate_country_rewards_and_training_only_land_bonus(self):
        rows,episodes=self.fixture();before=copy.deepcopy(episodes)
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'1.jsonl';p.write_text('\n'.join(json.dumps(r) for r in rows))
            targets,stats=global_targets(d,episodes,1.)
        np.testing.assert_allclose(targets[1,0],[28,17]);np.testing.assert_allclose(targets[1,2],[3000,2000])
        self.assertEqual(stats['real_gdp_sum'],3003);self.assertEqual(stats['territory_bonus_sum'],25)
        for a,b in zip(before,episodes):
            for x,y in zip(a[0],b[0]):np.testing.assert_array_equal(x['obs']['globals'],y['obs']['globals'])

    def test_rejects_future_snapshot(self):
        rows,episodes=self.fixture();rows[0]['snapshot']=100
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'1.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            with self.assertRaises(ValueError):global_targets(d,episodes)

    def test_sequences_use_only_one_country_decision_indices(self):
        self.assertEqual(sequence_indices(6,1,4),[1,2,3,4])
        with self.assertRaises(ValueError):sequence_indices(6,5,2)


if __name__=='__main__':unittest.main()
