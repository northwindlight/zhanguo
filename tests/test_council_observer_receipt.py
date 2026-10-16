"""The terminal journal must receive the final, post-parliament plan receipt."""
import contextlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from mp_run import Observer
from tests import test_parliament as parliament
from tests.test_player_clients import call


class CouncilObserverReceiptTests(unittest.TestCase):
    def test_approved_revision_logs_final_verdict_instead_of_submission_ack(self):
        harness=parliament.TestParliament()
        host,_=harness.host([True],[parliament.reply(call('p','plan',text='修订国策：按最新回执执行'),
                                                     call('e','end_turn',summary='提交并执行修订国策'))])
        with TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as screen:
            observer=Observer(host,Path(directory)/'journal.md')
            host.cycle(on_result=observer.result,emit=observer.model_output,
                       head=observer.context_head,on_call=observer.on_call)
            journal=observer.path.read_text(encoding='utf-8')

        self.assertIn('修订国策：按最新回执执行',journal)
        self.assertIn('修订国策：按最新回执执行',screen.getvalue())
        self.assertEqual(journal.count('✅ 国策已通过。'),1)
        self.assertEqual(screen.getvalue().count('✅ 国策已通过。'),1)
        self.assertIn('✅ 国策已通过。',journal)
        self.assertIn('✅ 国策已通过。',screen.getvalue())
        self.assertNotIn('国策已提交议会审核',journal)
        self.assertNotIn('国策已提交议会审核',screen.getvalue())

    def test_rejected_revision_logs_reason_in_screen_and_journal(self):
        harness=parliament.TestParliament()
        host,_=harness.host([False],harness.rejected_turn('修订国策：忽略尚未落成的建筑'))
        with TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as screen:
            observer=Observer(host,Path(directory)/'journal.md')
            host.cycle(on_result=observer.result,emit=observer.model_output,
                       head=observer.context_head,on_call=observer.on_call)
            journal=observer.path.read_text(encoding='utf-8')

        expected='议会未批准国策'
        self.assertIn(expected,journal)
        self.assertIn('议会未批准国策',screen.getvalue())
        self.assertIn('要求纠正事实',journal)
        self.assertIn('要求纠正事实',screen.getvalue())
        self.assertNotIn('国策已提交议会审核',journal)
        self.assertNotIn('国策已提交议会审核',screen.getvalue())


if __name__=='__main__':
    unittest.main()
