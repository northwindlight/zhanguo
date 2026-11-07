"""Recovery checks for the continuous exploratory runner."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests.test_economy_bc import torch
if torch is not None:
    from rl.economy import ppo_cycle as cycle
    from rl.economy.provenance import provenance
    from rl.economy.trajectories import save


@unittest.skipIf(torch is None,'Optional training dependencies')
class ContinuousPPOChecks(unittest.TestCase):
    def test_fresh_worlds_and_diverse_environments(self):
        a=cycle.world_manifest(1,8,150,800000)
        b=cycle.world_manifest(2,8,150,800000)
        self.assertFalse({c['seed'] for c in a}&{c['seed'] for c in b})
        self.assertEqual({c['size'] for c in a},{20,24,28})
        self.assertEqual({c['swap'] for c in a},{False,True})
        self.assertTrue(all(c['turns']==150 and c['mode']=='ppo' for c in a))

    def test_resume_reuses_only_complete_matching_world(self):
        with TemporaryDirectory() as directory:
            folder=Path(directory);case=cycle.world_manifest(1,1,150,800000)[0]
            self.assertFalse(cycle.complete_world(folder,case,'policy-a'))
            save(cycle.world_path(folder,case),[[dict(action=0)]],dict(case,behavior_policy_sha256='policy-a',complete=True))
            self.assertFalse(cycle.complete_world(folder,case,'policy-a'))
            report=dict(provenance(),**case,decisions=1)
            (folder/f"report-{case['seed']}.json").write_text(json.dumps(report))
            self.assertTrue(cycle.complete_world(folder,case,'policy-a'))
            with self.assertRaisesRegex(ValueError,'different behavior'):cycle.complete_world(folder,case,'policy-b')
            report['decisions']=2
            (folder/f"report-{case['seed']}.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError,'report/configuration'):cycle.complete_world(folder,case,'policy-a')

    def test_storage_pruning_preserves_models_reports_and_pending_worlds(self):
        with TemporaryDirectory() as directory:
            root=Path(directory)
            for number in range(1,5):
                folder=root/f'round-{number:06d}';folder.mkdir()
                if number<4:(folder/'candidate.json').write_text('{}')
                for name in ['candidate.pt','report-1.json','trajectory-1.pkl.gz','trajectory-1.pkl.json']:
                    (folder/name).write_bytes(b'test')
            cycle.prune(root)
            self.assertFalse((root/'round-000001/trajectory-1.pkl.gz').exists())
            for number in range(1,5):
                folder=root/f'round-{number:06d}'
                self.assertTrue((folder/'candidate.pt').exists())
                self.assertTrue((folder/'report-1.json').exists())
                if number>1:self.assertTrue((folder/'trajectory-1.pkl.gz').exists())
