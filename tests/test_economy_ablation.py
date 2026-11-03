import gzip
import hashlib
import json
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory
import unittest

from tests.test_economy_bc import torch
if torch is not None:
    import numpy as np
    from rl.economy.ablation import load_streams,drift


@unittest.skipIf(torch is None,'Optional training dependencies')
class AblationChecks(unittest.TestCase):
    def test_frozen_trajectory_preserves_float32_and_rejects_corruption(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for seed in (1,2):
                path=root/f'world-{seed}.pkl.gz'
                streams=[[dict(row=dict(context=np.array([[1.0001234]],dtype=np.float32)),logp=-.1234567890123)]]
                with gzip.open(path,'wb') as f:pickle.dump(streams,f,protocol=5)
                manifest=dict(complete=True,turns=150,steps=1,sha256=hashlib.sha256(path.read_bytes()).hexdigest())
                (root/f'world-{seed}-manifest.json').write_text(json.dumps(manifest))
            loaded,_=load_streams(root)
            self.assertEqual(loaded[0][0]['row']['context'].dtype,np.float32)
            self.assertEqual(float(loaded[0][0]['row']['context'][0,0]),float(streams[0][0]['row']['context'][0,0]))
            self.assertEqual(loaded[0][0]['logp'],-.1234567890123)
            path.write_bytes(path.read_bytes()+b'corruption')
            with self.assertRaisesRegex(ValueError,'checksum'):load_streams(root)

    def test_drift_distinguishes_no_update_from_changed_action(self):
        candidates=np.zeros((2,96));candidates[0,5]=1;candidates[1,6]=1
        row=dict(candidates=candidates,legal=np.array([True,False]),target=0)
        old=[torch.tensor([2.,0.],dtype=torch.float64)]
        unchanged=drift([row],old,old)
        self.assertEqual(unchanged['kl'],0.);self.assertEqual(unchanged['argmax_changed'],0.)
        changed=drift([row],old,[torch.tensor([0.,2.],dtype=torch.float64)])
        self.assertGreater(changed['kl'],0.);self.assertEqual(changed['family_changed'],1.)
        self.assertEqual(changed['argmax_legal'],0.)
