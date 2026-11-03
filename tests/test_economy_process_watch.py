import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest

from scripts.economy_process_watch import health_failure,run_watched


class ProcessWatchChecks(unittest.TestCase):
    def test_missing_and_stale_health_do_not_depend_on_cli_exit(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'health.json'
            self.assertIsNone(health_failure(p,100,90,startup_grace=20))
            self.assertEqual(health_failure(p,111,90,startup_grace=20),'backup_health_missing')
            p.write_text(json.dumps(dict(checked_at=80,consecutive_failures=0)))
            self.assertEqual(health_failure(p,111,90,stale_after=30),'backup_health_stale')

    def test_hung_cli_is_terminated_after_repeated_backup_failure(self):
        with TemporaryDirectory() as directory:
            p=Path(directory)/'health.json'
            p.write_text(json.dumps(dict(checked_at=time.time(),consecutive_failures=2)))
            result=run_watched([sys.executable,'-c','import time; time.sleep(60)'],
                deadline=time.time()+30,health_path=p,poll=.01)
            self.assertEqual(result['reason'],'backup_failed')
            self.assertIsNotNone(result['returncode'])

    def test_deadline_prevents_start_and_normal_exit_remains_visible(self):
        self.assertEqual(run_watched(['this-command-must-not-start'],deadline=0,health_path='missing')['reason'],'deadline')
        result=run_watched([sys.executable,'-c','pass'],deadline=time.time()+10,health_path='missing',poll=.01)
        self.assertEqual(result,dict(reason='completed',returncode=0))
