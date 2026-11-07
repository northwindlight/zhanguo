import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.econ_kaggle_watch import prepare_input,status
from scripts.econ_ppo_watch import digest,write


class KaggleRecoveryChecks(unittest.TestCase):
    def test_recovery_bundle_preserves_behavior_worlds_and_resume_settings(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);live=root/'live';folder=live/'round-000001';folder.mkdir(parents=True)
            input_folder=root/'kaggle-input';input_folder.mkdir()
            (input_folder/'runtime.bin').write_bytes(b'runtime')
            files={'round-000001/behavior.pt':b'behavior','round-000001/trajectory-800008.pkl.gz':b'lossless float32 data',
                   'run.json':b'{"settings":{"worlds":8,"turns":150}}'}
            for name,data in files.items():(live/name).write_bytes(data)
            prepare_input(root,live)
            manifest=json.loads((input_folder/'bundle-manifest.json').read_text())
            for name,record in manifest.items():
                self.assertEqual(record['sha256'],digest(input_folder/name))
                self.assertEqual(record['bytes'],(input_folder/name).stat().st_size)
            with tarfile.open(input_folder/'recovery.bin') as archive:
                for name,data in files.items():
                    self.assertEqual(archive.extractfile('rl/runs/ppo-cycle/'+name).read(),data)

    def test_status_distinguishes_running_missing_and_network_failure(self):
        with patch('scripts.econ_kaggle_watch.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='has status "KernelWorkerStatus.RUNNING"',stderr='')):
            self.assertEqual(status('owner/kernel/1'),'RUNNING')
        with patch('scripts.econ_kaggle_watch.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='404 Not Found')):
            self.assertIsNone(status('owner/kernel/2',allow_missing=True))
        with patch('scripts.econ_kaggle_watch.subprocess.run',return_value=SimpleNamespace(returncode=1,stdout='',stderr='Network timeout')):
            with self.assertRaisesRegex(RuntimeError,'request failed'):status('owner/kernel/2',allow_missing=True)

    def test_restart_can_rewrite_a_state_with_existing_timestamp(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'state.json';write(p,dict(at=0,version=1,phase='running'))
            saved=json.loads(p.read_text());self.assertGreater(saved['at'],0)
            write(p,saved);self.assertEqual(json.loads(p.read_text())['version'],1)
