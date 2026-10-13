"""Synthetic council decisions; no network, live saves or credentials."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from clients.llm import assert_paired
from host import GameHost,SaveFormatError
from tests.test_player_clients import FakeBackend,call


class CouncilBackend:
    def __init__(self,decisions):
        self.decisions=list(decisions);self.requests=[]
    def complete_text(self,messages,cfg,tools=None):
        self.requests.append(deepcopy(messages))
        if messages[-1]['content'].startswith('第二步：'):
            approved=self.decisions.pop(0)
            return json.dumps({'approved':approved,'reason':'已核对回执，经济承诺合理' if approved else '国策声称工厂已落成，但回执仅为在建，要求纠正事实'},ensure_ascii=False),{'calls':1}
        if messages[-1]['content'].startswith('第一步：'):
            return '核对了实际地图、经济变化及前次经济、外交、军事承诺；施工在建不能写成已落成。',{'calls':1}
        return '开局以来曾建农场并作出错误的落成判断；议会三次否决。以当前实际地图和库存为准，新君主可以重新规划。',{'calls':1}
    def chat_turn(self,messages,tools,cfg,on_retry=None):
        content,stats=self.complete_text(messages,cfg)
        return {'content':content},stats


def reply(*calls,reasoning=''):
    return {'content':'','reasoning_content':reasoning,'tool_calls':list(calls)}


class TestParliament(unittest.TestCase):
    actor='scenario:han'
    def cfg(self):
        return {'mods':'mods.experimental.toml','map_size':12,'seed':73,
                'parliament':{'reviewer':{'model':'synthetic-council','api_key':'review-secret'}},
                'nations':[{'name':'韩','id':self.actor,'client':'llm','model':'synthetic-monarch',
                            'api_key':'monarch-secret','ctx_full_turns':1,'ctx_compact':'off'}]}
    def host(self,decisions,responses):
        h=GameHost.new(self.cfg());c=h.clients[self.actor]
        c._backend=FakeBackend(responses);h.services[self.actor]['council']._review_backend=CouncilBackend(decisions)
        return h,c
    def rejected_turn(self,text):
        return [reply(call('p','plan',text=text),call('e','end_turn',summary='国策尚待解释')),
                reply(call('x','explain_plan',text='确实仍在施工，我将纠正表述并于下回合重提'),call('e2','end_turn',summary='完成解释等待下回合修订'))]

    def test_two_steps_use_actual_receipts_maps_economy_and_hide_new_plan_from_first(self):
        site=self.actor+'/land-6-6'
        h,c=self.host([True],[reply(call('b','build',site=site,kind='native:farm'),
                                      call('p','plan',text='NEW-CANDIDATE 农場仍在施工，下一期确认产出'),
                                      call('e','end_turn',summary='动工农场并制定国策'))])
        results=h.cycle();first,second=h.services[self.actor]['council']._review_backend.requests
        raw=json.dumps(first,ensure_ascii=False)
        self.assertNotIn('NEW-CANDIDATE',raw)
        for token in ('build','receipt','building_sites','land','econ','turn.start'):self.assertIn(token,raw)
        self.assertIn('NEW-CANDIDATE',json.dumps(second,ensure_ascii=False))
        self.assertEqual(second[:len(first)],first)
        self.assertEqual(second[-2]['role'],'assistant')
        self.assertEqual(h.services[self.actor]['council'].council['dialogue'],[])
        self.assertIn('议会通过了国策',results[1]['message'])
        self.assertTrue(any(row['kind']=='settlement' for row in h.services[self.actor]['council'].council['archive']))
        self.assertEqual(h.player(self.actor).observe()['facts']['government']['plan']['text'],'NEW-CANDIDATE 农場仍在施工，下一期确认产出')

    def test_rejection_keeps_dialogue_explanation_and_retries_only_next_period(self):
        rows=self.rejected_turn('第一版错误事实')+[
            reply(call('p2','plan',text='第二版已纠正事实'),call('e3','end_turn',summary='修订国策并完成经营'))]
        h,c=self.host([False,True],rows)
        receipts=h.cycle()
        self.assertFalse(receipts[0]['ok']);self.assertFalse(receipts[1]['ok'])
        self.assertIn('要求君主解释',receipts[0]['message'])
        self.assertEqual(len(h.services[self.actor]['council'].council['dialogue']),5)
        first_dialogue=deepcopy(h.services[self.actor]['council'].council['dialogue'])
        self.assertIsNone(h.player(self.actor).observe()['facts']['government']['plan'])
        h.cycle()
        request=h.services[self.actor]['council']._review_backend.requests[2]
        self.assertEqual(request[:len(first_dialogue)],first_dialogue)
        self.assertIn('我将纠正表述',request[-1]['content'])
        self.assertEqual(h.services[self.actor]['council'].council['dialogue'],[])
        self.assertEqual(h.player(self.actor).observe()['facts']['government']['parliament']['streak'],0)

    def test_one_submission_per_period_enforced_without_extra_model_review(self):
        h,c=self.host([False],[reply(call('a','plan',text='第一版'),call('b','plan',text='第二版'),
                                   call('x','explain_plan',text='下一回合按事实重新提交'),call('e','end_turn',summary='已经解释并完成行动'))])
        results=h.cycle()
        self.assertFalse(results[1]['ok']);self.assertIn('每回合只能提交一次',results[1]['message'])
        self.assertEqual(len(h.services[self.actor]['council']._review_backend.requests),2)
        names=[row['function']['name'] for row in h.player(self.actor).actions()]
        for privileged in ('decide','replace','enroll'):self.assertNotIn(privileged,names)
        self.assertFalse(h.player(self.actor).submit('decide',{'approved':True})['ok'])

    def test_third_consecutive_rejection_replaces_monarch_without_second_submission(self):
        rows=self.rejected_turn('一版')+self.rejected_turn('二版')+[
            reply(call('p3','plan',text='三版'),call('cancel','buy',resource='native:wood',quantity=1),
                  call('old-end','end_turn',summary='旧君主想直接结束'),reasoning='OLD-MONARCH-TOXIC-REASONING'),
            reply(call('new-p','plan',text='新君主本回合不可重提'),call('new-e','end_turn',summary='新君主接管并完成本回合行动'))]
        h,c=self.host([False,False,False],rows)
        h.cycle();h.cycle();before=h.player(self.actor).query('econ')
        events=[];results=h.cycle(emit=events.append)
        self.assertFalse(results[1]['ok']);self.assertIn('未执行',results[1]['message'])
        self.assertFalse(results[3]['ok']);self.assertIn('每回合只能提交一次',results[3]['message'])
        self.assertTrue(results[-1]['ok'])
        self.assertEqual(c.state.blocks,[]);self.assertEqual(c.state.long_memory,'')
        self.assertEqual(len(c.state.replay),1);self.assertEqual(len(c.state.summaries),1)
        new_request=c._backend.requests[-1]
        content=json.dumps(new_request,ensure_ascii=False)
        self.assertIn('上一任君主被愤怒的贵族们罢免',content)
        self.assertNotIn('OLD-MONARCH-TOXIC-REASONING',content)
        self.assertIn('OLD-MONARCH-TOXIC-REASONING',json.dumps(h.services[self.actor]['council']._review_backend.requests[-1],ensure_ascii=False))
        self.assertEqual(len(h.services[self.actor]['council'].council['dialogue']),13) # Survives even succession.
        gov=h.player(self.actor).observe()['facts']['government']
        self.assertEqual(gov['parliament']['reign'],2);self.assertIsNone(gov['plan'])
        self.assertEqual([s['period'] for s in gov['summaries']],[2])
        self.assertTrue(any('罢免' in text for text in events))
        assert_paired(c.state.replay[0]['messages'])

    def test_approval_breaks_rejection_streak_and_council_restarts_clean(self):
        rows=self.rejected_turn('a')+self.rejected_turn('b')+[
            reply(call('p','plan',text='c'),call('e','end_turn',summary='国策已纠正通过审核'))]+self.rejected_turn('d')
        h,c=self.host([False,False,True,False],rows)
        for _ in range(4):h.cycle()
        gov=h.player(self.actor).observe()['facts']['government']['parliament']
        self.assertEqual(gov['streak'],1);self.assertEqual(gov['reign'],1)
        self.assertEqual(len(h.services[self.actor]['council']._review_backend.requests[6]),2)
        self.assertEqual(c.state.long_memory,'')

    def test_full_archive_survives_monarch_replay_compression(self):
        h,c=self.host([True]*7,[reply(call('p'+str(i),'plan',text='发展经济'+str(i)),call('e'+str(i),'end_turn',summary='完成本回合经营'+str(i))) for i in range(7)])
        for _ in range(7):h.cycle()
        self.assertLess(len(c.state.replay),7)
        actions=[r for r in h.services[self.actor]['council'].council['archive'] if r['kind']=='action']
        self.assertEqual(len(actions),14)
        self.assertEqual(actions[0]['receipt']['period'],0)
        self.assertEqual(len(h.services[self.actor]['council'].council['transcript']),21)

    def test_pending_dialogue_reload_and_succession_match_uninterrupted_run(self):
        rows=self.rejected_turn('a')+self.rejected_turn('b')+[
            reply(call('p','plan',text='c')),reply(call('e','end_turn',summary='新君主已经完成行动'))]
        h,c=self.host([False]*3,rows)
        h.cycle()
        with TemporaryDirectory() as d:
            path=Path(d)/'synthetic.json';h.save(path);loaded=GameHost.load(path,self.cfg());other=loaded.clients[self.actor]
            self.assertEqual(h.to_save(),loaded.to_save())
            other._backend=FakeBackend(deepcopy(c._backend.responses));loaded.services[self.actor]['council']._review_backend=CouncilBackend([False]*2)
            for _ in range(2):h.cycle();loaded.cycle();self.assertEqual(h.to_save(),loaded.to_save())
            self.assertNotIn('review-secret',json.dumps(h.to_save()))
            self.assertNotIn('monarch-secret',json.dumps(h.to_save()))
            with self.assertRaises(SaveFormatError):GameHost.load(path,{**self.cfg(),'mods':'mods.toml'})

    def test_invalid_or_failed_council_stops_and_cannot_replace_boundary_file(self):
        for output in ('not-json','{"approved":"yes","reason":"ok"}',RuntimeError('review transport failure')):
            h,c=self.host([True],[reply(call('p','plan',text='发展'))])
            original=h.services[self.actor]['council']._review_backend.complete_text
            def complete(messages,cfg,tools=None):
                if messages[-1]['content'].startswith('第二步：'):
                    if isinstance(output,Exception):raise output
                    return output,{}
                return original(messages,cfg,tools)
            h.services[self.actor]['council']._review_backend.complete_text=complete
            with TemporaryDirectory() as d:
                p=Path(d)/'synthetic.json';h.save(p);before=p.read_bytes()
                with self.assertRaises((ValueError,RuntimeError)):h.cycle()
                self.assertEqual(p.read_bytes(),before)
                with self.assertRaises(SaveFormatError):h.save(p)

    def test_council_can_read_current_manuals_but_cannot_execute_country_actions(self):
        h,c=self.host([True],[reply(call('p','plan',text='发展经济'),call('e','end_turn',summary='通过审核完成行动'))])
        class ReadingCouncil(CouncilBackend):
            def __init__(self):super().__init__([True]);self.calls=[];self.queried=False
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                self.calls.append((deepcopy(messages),deepcopy(tools)))
                if not self.queried:
                    self.queried=True
                    return reply(call('rules','player_rules',topic=''),call('manual','read_manual',topic='说明书'),
                                 call('guide','read_manual',topic='战争手册'),call('forged','buy',resource='native:wood',quantity=1)),{}
                if messages[-1]['role']=='tool':return {'content':'已经查说明书及战争手册，按施工与结算回执核对。'},{}
                return super().chat_turn(messages,tools,cfg,on_retry)
        reviewer=ReadingCouncil();h.services[self.actor]['council']._review_backend=reviewer
        h.cycle()
        names={s['function']['name'] for s in reviewer.calls[0][1]}
        self.assertEqual(names,{'player_rules','read_manual'})
        queries=[r for r in h.services[self.actor]['council'].council['archive'] if r['kind']=='council.query']
        self.assertEqual([r['result']['ok'] for r in queries],[True,True,True,False])
        self.assertIn('buildings',queries[1]['result']['data'])
        assert_paired(reviewer.calls[1][0])
        self.assertTrue(any('read_manual' in json.dumps(r['dialogue']) for r in h.services[self.actor]['council'].council['archive'] if r['kind']=='review'))

    def test_briefing_consumes_entire_oversized_history_including_final_receipt(self):
        h,c=self.host([],[])
        h.services[self.actor]['council'].review_cfg.update(ctx_window=4096,max_tokens=256)
        h.services[self.actor]['council'].council['archive']=[{'kind':'large','text':'事实'*12000+'LAST-RECEIPT-MARKER'}]
        summary=h.services[self.actor]['council']._briefing(h.player(self.actor),[{'role':'system','content':'完整上下文'}])
        self.assertTrue(summary)
        self.assertGreater(len(h.services[self.actor]['council']._review_backend.requests),2)
        self.assertIn('LAST-RECEIPT-MARKER',json.dumps(h.services[self.actor]['council']._review_backend.requests,ensure_ascii=False))

    def test_next_monarch_can_submit_next_turn_and_council_keeps_prior_rejections(self):
        rows=self.rejected_turn('a')+self.rejected_turn('b')+[
            reply(call('p','plan',text='c')),reply(call('e','end_turn',summary='接任君主完成行动')),
            reply(call('new-p','plan',text='纠正先前事实的新国策'),call('new-e','end_turn',summary='新君主的国策已经通过'))]
        h,c=self.host([False,False,False,True],rows)
        for _ in range(3):h.cycle()
        previous=deepcopy(h.services[self.actor]['council'].council['dialogue'])
        h.cycle()
        self.assertEqual(h.services[self.actor]['council']._review_backend.requests[-2][:len(previous)],previous)
        self.assertEqual(h.services[self.actor]['council'].council['dialogue'],[])
        self.assertEqual(h.player(self.actor).observe()['facts']['government']['plan']['text'],'纠正先前事实的新国策')


if __name__=='__main__':unittest.main()
