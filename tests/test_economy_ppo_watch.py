import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from scripts import econ_ppo_watch as watch


class PPOWatchChecks(unittest.TestCase):
    def test_complete_summary_for_another_policy_does_not_suppress_evaluation(self):
        summary=dict(phase='complete',screen=dict(evaluations=[dict(policy_sha256='old')]))
        self.assertTrue(watch.evaluation_matches(summary,'old'))
        self.assertFalse(watch.evaluation_matches(summary,'new'))
        summary['phase']='evaluating'
        self.assertFalse(watch.evaluation_matches(summary,'old'))

    def test_snapshot_roundtrip_and_incremental_backup(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);remote=root/'remote';run=remote/'rl/runs/ppo-cycle'
            run.mkdir(parents=True);(remote/'manager.pid').write_text(str(os.getpid()))
            (run/'state.json').write_text(json.dumps(dict(phase='collection')))
            folder=run/'round-000001';folder.mkdir();(folder/'behavior.pt').write_bytes(b'weights')
            archive=root/'snapshot.zip';output=io.StringIO()
            with patch.object(watch,'REMOTE',str(remote)),contextlib.redirect_stdout(output):
                code=watch.remote_code({}).replace('/content/econ-ppo-backup.zip',str(archive))
                exec(code,{})
            manifest=json.loads(output.getvalue().split('BACKUP ')[1]);self.assertTrue(manifest['alive'])
            live=root/'live';live.mkdir();watch.install_backup(archive,live,manifest)
            self.assertEqual((live/'round-000001/behavior.pt').read_bytes(),b'weights')
            output=io.StringIO()
            with patch.object(watch,'REMOTE',str(remote)),contextlib.redirect_stdout(output):
                code=watch.remote_code(manifest['files']).replace('/content/econ-ppo-backup.zip',str(archive))
                exec(code,{})
            second=json.loads(output.getvalue().split('BACKUP ')[1])
            self.assertEqual(second['changed'],[])
            watch.install_backup(archive,live,second)

    def test_ecs_snapshot_uses_explicit_paths_and_rejects_missing_run_before_copy(self):
        from scripts.econ_ecs_watch import snapshot
        from types import SimpleNamespace
        with TemporaryDirectory() as directory:
            root=Path(directory);archive=root/'backup.zip';output=io.StringIO()
            with contextlib.redirect_stdout(output):
                exec(watch.remote_code({},remote=str(root),backup_path=str(archive)),{})
            manifest=json.loads(output.getvalue().split('BACKUP ')[1])
            self.assertEqual(manifest['files'],{})
            args=SimpleNamespace(remote=str(root),out=root,host='owned-host')
            with patch('scripts.econ_ecs_watch.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=output.getvalue())) as run:
                with self.assertRaisesRegex(RuntimeError,'preserve local artifacts'):snapshot(args,{},False)
            self.assertEqual(run.call_count,1)

    def test_backup_rejects_traversal_and_corruption(self):
        with TemporaryDirectory() as directory:
            root=Path(directory);archive=root/'bad.zip';data=b'bad'
            with zipfile.ZipFile(archive,'w') as z:z.writestr('../escape',data)
            manifest=dict(bytes=archive.stat().st_size,sha256=watch.digest(archive),changed=['../escape'],
                          files={'../escape':dict(sha256=hashlib.sha256(data).hexdigest())})
            with self.assertRaisesRegex(ValueError,'Unexpected'):watch.install_backup(archive,root/'live',manifest)
            self.assertFalse((root/'escape').exists())
            manifest['sha256']='wrong'
            with self.assertRaisesRegex(ValueError,'checksum'):watch.install_backup(archive,root/'live',manifest)
