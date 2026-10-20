"""The terminal journal must receive the final, post-parliament plan receipt."""
import contextlib
import io
import queue
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mp_run import Observer
from tests import test_parliament as parliament
from tests.test_player_clients import call


class CouncilObserverReceiptTests(unittest.TestCase):
    def test_review_status_reaches_panel_before_each_model_call(self):
        harness=parliament.TestParliament()
        for approved in (True,False):
            with self.subTest(approved=approved):
                responses=([parliament.reply(call('p','plan',text='按实际库存修订国策'),
                                            call('e','end_turn',summary='审核通过，完成经营行动'))]
                           if approved else harness.rejected_turn('按实际库存修订国策'))
                host,_=harness.host([approved],responses)
                class Panel:
                    panel_on=True
                    def __init__(self):self.queue=queue.Queue();self.notes=[]
                    def set_panel(self,head=None,note=None):
                        if note is not None:self.notes.append(note)
                    def clear_panel(self):pass
                    def write(self,text):pass
                panel=Panel();seen=[]
                class Reviewer(parliament.CouncilBackend):
                    def chat_turn(self,*args,**kwargs):
                        seen.append(panel.notes[-1])
                        return super().chat_turn(*args,**kwargs)
                host.services[harness.actor]['council']._review_backend=Reviewer([approved])
                with TemporaryDirectory() as directory,patch('mp_run.CONSOLE',panel):
                    observer=Observer(host,Path(directory)/'journal.md')
                    host.cycle(on_result=observer.result,emit=observer.model_output,
                               head=observer.context_head,on_call=observer.on_call)
                    journal=observer.path.read_text(encoding='utf-8')
                self.assertEqual(len(seen),2)
                self.assertIn('韩 议会审查 1/2：核对旧国策与行动记录',seen[0])
                self.assertIn('第 1/10 次调用',seen[0])
                self.assertIn('韩 议会审查 2/2：审查新版国策',seen[1])
                self.assertIn('第 2/10 次调用',seen[1])
                self.assertTrue(all(status in journal for status in seen))
                self.assertIn('议会审查结束：国策已通过' if approved else '议会审查结束：国策被打回',
                              '\n'.join(panel.notes))
                if not approved:
                    self.assertIn('正在等待模型，第 2 次调用',panel.notes[-1])

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
        self.assertEqual(sum(line.startswith('[') and '◇ 提交国策 → ✅ 国策已通过。' in line for line in journal.splitlines()),1)
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

    def test_full_council_exchange_is_journal_only_and_contains_no_provider_credentials(self):
        harness=parliament.TestParliament()
        host,_=harness.host([True],[parliament.reply(call('p','plan',text='候选国策：审查日志验收'),
                                                     call('e','end_turn',summary='日志验收后结束行动'))])
        class Reviewer(parliament.CouncilBackend):
            queried=False
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                if not self.queried:
                    self.queried=True
                    return {'reasoning_content':'议长独有思考：核对施工证据。\n逐项检查联盟状态。','content':'议长独有正文：先查建筑规则。',
                            'tool_calls':[call('read','player_rules',topic='建筑')]},{'wall':1.25,'out_tokens':123}
                if any(t['function']['name']=='council_next_stage' for t in tools):
                    return parliament.reply(call('next','council_next_stage')) ,{}
                return super().chat_turn(messages,tools,cfg,on_retry)
        host.services[harness.actor]['council']._review_backend=Reviewer([True])
        with TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as screen:
            observer=Observer(host,Path(directory)/'journal.md')
            host.cycle(on_result=observer.result,emit=observer.model_output,head=observer.context_head,on_call=observer.on_call)
            journal=observer.path.read_text(encoding='utf-8')
        for value in ('议长独有思考','议长独有正文','player_rules','[1/10]','[2/10]','[3/10]',
                      'council_approve','建筑与能源','out_tokens','decision'):
            self.assertIn(value,journal)
        self.assertEqual(journal.count('议长独有思考'),1)
        self.assertIn('议长独有思考：核对施工证据。\n逐项检查联盟状态。',journal)
        self.assertIn('候选国策：审查日志验收',journal)
        self.assertNotIn('议长独有思考',screen.getvalue());self.assertNotIn('议长独有正文',screen.getvalue())
        self.assertNotIn('📜',screen.getvalue())
        for secret in ('review-secret','monarch-secret'):self.assertNotIn(secret,journal)

    def test_council_request_and_error_are_logged_even_if_transport_raises(self):
        harness=parliament.TestParliament()
        host,_=harness.host([True],[parliament.reply(call('p','plan',text='传输失败前的候选国策'))])
        class Broken:
            def chat_turn(self,*args,**kwargs):raise RuntimeError('synthetic failure')
        host.services[harness.actor]['council']._review_backend=Broken()
        with TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as screen:
            observer=Observer(host,Path(directory)/'journal.md')
            with self.assertRaisesRegex(RuntimeError,'synthetic failure'):
                host.cycle(emit=observer.model_output)
            journal=observer.path.read_text(encoding='utf-8')
        self.assertIn('previous_plan',journal);self.assertIn('RuntimeError',journal)
        self.assertIn('[1/10]',journal);self.assertNotIn('📜',screen.getvalue())


if __name__=='__main__':
    unittest.main()
