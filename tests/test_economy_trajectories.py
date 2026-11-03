from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.test_economy_bc import torch
if torch is not None:
    import numpy as np
    from rl.economy.trajectories import save,load


@unittest.skipIf(torch is None,'Optional training dependencies')
class TrajectoryChecks(unittest.TestCase):
    def test_lossless_data_survives_resume_but_wrong_behavior_is_rejected(self):
        row=dict(context=np.array([[1.0001234]],np.float32),candidates=np.array([[.00345678]],np.float32))
        streams=[[dict(row=row,logp=-.1234567890123,reward=.04,action=0,value=0.,period=3)]]
        with TemporaryDirectory() as directory:
            p=Path(directory)/'world.pkl.gz';save(p,streams,dict(seed=1,behavior_policy_sha256='old'))
            recovered=load(p,dict(seed=1,behavior_policy_sha256='old'))
            np.testing.assert_array_equal(recovered[0][0]['row']['context'],row['context'])
            self.assertEqual(recovered[0][0]['logp'],streams[0][0]['logp'])
            with self.assertRaisesRegex(ValueError,'behavior'):load(p,dict(seed=1,behavior_policy_sha256='new'))
            p.write_bytes(p.read_bytes()+b'bad')
            with self.assertRaisesRegex(ValueError,'checksum'):load(p,dict(seed=1,behavior_policy_sha256='old'))
