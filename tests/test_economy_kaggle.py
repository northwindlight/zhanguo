import json
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.econ_kaggle_watch import gpu_submission_available,prepare_input,status
from scripts.econ_ppo_watch import digest,write


class KaggleRecoveryChecks(unittest.TestCase):
    def test_ecs_owner_prevents_duplicate_gpu_training(self):
        with TemporaryDirectory() as directory:
            out=Path(directory);(out/'ECS_OWNER').touch();state=dict(round=17,version=20)
            with patch('scripts.econ_kaggle_watch.subprocess.run') as run:
                self.assertFalse(gpu_submission_available(out,state))
            run.assert_not_called()
            self.assertEqual(state['phase'],'delegated_to_ecs')

    def test_colab_owner_prevents_duplicate_kaggle_training_after_quota_refresh(self):
        with TemporaryDirectory() as directory:
            out=Path(directory);(out/'COLAB_OWNER').touch();state=dict(round=17,version=20)
            with patch('scripts.econ_kaggle_watch.subprocess.run') as run:
                self.assertFalse(gpu_submission_available(out,state))
            run.assert_not_called()
            self.assertEqual(state['phase'],'delegated_to_colab')

    def test_exhausted_gpu_quota_is_waiting_not_training_even_with_unused_tpu(self):
        quota=dict(gpu=dict(time_used=30.45,time_reserved=0.,total_time_allowed=30.),
                   tpu=dict(time_used=0.,time_reserved=0.,total_time_allowed=20.),
                   refresh='2026-11-14T00:00:00')
        with TemporaryDirectory() as directory:
            out=Path(directory);state=dict(round=17,version=20,phase='submitting',push_pending=True)
            with patch('scripts.econ_kaggle_watch.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(quota))):
                self.assertFalse(gpu_submission_available(out,state))
            saved=json.loads((out/'kaggle-watch-state.json').read_text())
            self.assertEqual(saved['phase'],'waiting_gpu_quota')
            self.assertEqual(saved['next_round'],18)
            self.assertEqual(saved['version'],20)
            quota['gpu']['time_used']=0.;quota['gpu']['time_reserved']=30.
            with patch('scripts.econ_kaggle_watch.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(quota))):
                self.assertFalse(gpu_submission_available(out,state))
            quota['gpu']['time_reserved']=0.
            with patch('scripts.econ_kaggle_watch.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(quota))):
                self.assertTrue(gpu_submission_available(out,state))

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

    def test_completed_round_upload_omits_old_rows_but_preserves_pending_worlds(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);live=root/'live';live.mkdir();(root/'kaggle-input').mkdir()
            (root/'kaggle-input/runtime.bin').write_bytes(b'runtime')
            (live/'run.json').write_text('{}');(live/'anchor.pt').write_bytes(b'anchor')
            for number in (1,2):
                folder=live/f'round-{number:06d}';folder.mkdir()
                (folder/'candidate.pt').write_bytes(str(number).encode())
                (folder/'candidate.json').write_text(json.dumps(dict(round=number,sha256=digest(folder/'candidate.pt'))))
                (folder/'trajectory-old.pkl.gz').write_bytes(b'old data')
            pending=live/'round-000003';pending.mkdir()
            (pending/'behavior.pt').write_bytes(b'pending behavior')
            (pending/'trajectory-new.pkl.gz').write_bytes(b'pending data')
            folder=prepare_input(root,live)
            manifest=json.loads((folder/'bundle-manifest.json').read_text())
            self.assertEqual(manifest['resume']['next_round'],3)
            with tarfile.open(folder/'recovery.bin') as archive:
                names=archive.getnames();prefix='rl/runs/ppo-cycle/'
                self.assertNotIn(prefix+'round-000001/candidate.pt',names)
                self.assertNotIn(prefix+'round-000002/trajectory-old.pkl.gz',names)
                self.assertEqual(archive.extractfile(prefix+'round-000002/candidate.pt').read(),b'2')
                self.assertEqual(archive.extractfile(prefix+'round-000003/trajectory-new.pkl.gz').read(),b'pending data')
            (live/'round-000002/candidate.pt').write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'checksum'):prepare_input(root,live)
