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
