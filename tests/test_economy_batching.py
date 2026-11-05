"""Length-bucketed batching: coverage, shuffle, and padding reduction."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from rl.economy.adapter import PROJECT_FINGERPRINT, WIDTH
from rl.economy.batching import LengthBucketedSampler
from rl.economy.data import Dataset, Writer
from rl.economy.model import collate


def make_world(path,seed,lengths):
    path.parent.mkdir(parents=True,exist_ok=True);writer=Writer(PROJECT_FINGERPRINT)
    for n in lengths:
        features=np.zeros((n,WIDTH),dtype=np.float32);features[:,5]=1
        writer.add(dict(context=np.zeros((4,WIDTH),dtype=np.float32),candidates=features,
                        legal=np.ones(n,dtype=bool),limits=np.zeros(n,dtype=np.int64),
                        target=0,quantity=0.))
    writer.save(path,dict(seed=seed,turns=150,source='synthetic',teacher_probability=1.))


class SamplerChecks(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.path=Path(self.temp.name)/'world-121.npz'
        make_world(self.path,121,[96,120,180,300,640,700,1500,1900]*3)

    def tearDown(self):self.temp.cleanup()

    def dataset(self):
        return Dataset([self.path],fingerprint=PROJECT_FINGERPRINT)

    def test_every_index_exactly_once_per_epoch(self):
        data=self.dataset();sampler=LengthBucketedSampler(data.candidate_counts(),6,seed=3)
        seen=[]
        for batch in sampler:
            self.assertLessEqual(len(batch),6)
            seen.extend(batch)
        self.assertEqual(sorted(seen),list(range(len(data))))

    def test_batches_are_random_between_epochs(self):
        data=self.dataset();sampler=LengthBucketedSampler(data.candidate_counts(),6,seed=3)
        first=[batch for batch in sampler]
        sampler.set_epoch(1);second=[batch for batch in sampler]
        self.assertEqual(len(first),len(second))
        self.assertNotEqual(first,second)

    def test_padding_is_far_smaller_than_random_batches(self):
        data=self.dataset()
        counts=data.candidate_counts()
        entries=[data[i] for i in range(len(data))]
        bucketed=LengthBucketedSampler(counts,4,seed=5)
        bucketed_padding=self._padding([entries[i] for i in batch] for batch in bucketed)
        rng=np.random.default_rng(5)
        random_batches=[rng.choice(len(entries),4,replace=False) for _ in range(len(bucketed))]
        random_padding=self._padding([[entries[i] for i in batch] for batch in random_batches])
        self.assertLess(bucketed_padding,random_padding)

    @staticmethod
    def _padding(batches):
        padded=0
        for batch in batches:
            if not batch:continue
            widest=max(len(row['candidates']) for row in batch)
            padded+=sum(widest-len(row['candidates']) for row in batch)
        return padded

    def test_no_candidate_is_dropped_by_batch_sampler(self):
        data=self.dataset();entries=[data[i] for i in range(len(data))]
        sampler=LengthBucketedSampler(data.candidate_counts(),3,seed=2)
        for batch in sampler:
            bounded=collate([entries[i] for i in batch])
            self.assertEqual(int(bounded['candidate_mask'].sum()),
                             sum(len(e['candidates']) for e in [entries[i] for i in batch]))


if __name__=='__main__':
    unittest.main()
