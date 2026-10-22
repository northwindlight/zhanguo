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
        if messages[-1]['content'].startswith('第一步：'):
            return {'content':'','tool_calls':[{'id':'council-next-stage','type':'function','function':{
                'name':'council_next_stage','arguments':'{}'}}]},stats
        if messages[-1]['content'].startswith('第二步：'):
            try:verdict=json.loads(content)
            except (TypeError,ValueError):return {'content':content},stats
            if type(verdict.get('approved')) is not bool or not isinstance(verdict.get('reason'),str):
                return {'content':content},stats
            name='council_approve' if verdict['approved'] else 'council_reject'
            args={} if verdict['approved'] else {'reason':verdict['reason']}
            return {'content':'','tool_calls':[{'id':'council-verdict','type':'function','function':{
                'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]},stats
        return {'content':content},stats


def reply(*calls,reasoning=''):
    return {'content':'','reasoning_content':reasoning,'tool_calls':list(calls)}


def start_with_plan(host,actor):
    """Establish a real first plan before exercising revision-only behavior."""
    player=host.player(actor)
    for action,args in (('plan',{'text':'INITIAL-BASELINE 按实际库存发展生产'}),
                        ('end_turn',{'summary':'第一版国策生效，下一期按实际回执修订'})):
        receipt=player.submit(action,args)
        if not receipt['ok']:raise AssertionError(receipt)
    host.cycle()


class TestParliament(unittest.TestCase):
    actor='scenario:han'
    def cfg(self):
        return {'mods':'mods.experimental.toml','map_size':12,'seed':73,
                'parliament':{'reviewer':{'model':'synthetic-council','api_key':'review-secret'}},
                'nations':[{'name':'韩','id':self.actor,'client':'llm','model':'synthetic-monarch',
                            'api_key':'monarch-secret','ctx_full_turns':1,'ctx_compact':'off'}]}
    def host(self,decisions,responses,*,revision=True):
        h=GameHost.new(self.cfg());c=h.clients[self.actor]
        c._backend=FakeBackend(responses);h.services[self.actor]['council']._review_backend=CouncilBackend(decisions)
        if revision:start_with_plan(h,self.actor)
        return h,c
    def rejected_turn(self,text):
        return [reply(call('p','plan',text=text),call('e','end_turn',summary='国策尚待解释')),
                reply(call('x','explain_plan',text='确实仍在施工，我将纠正表述并于下回合重提'),call('e2','end_turn',summary='完成解释等待下回合修订'))]

    def test_rejected_end_turn_shows_required_explanation_and_then_really_ends(self):
        h,c=self.host([False],self.rejected_turn('修订国策时错误声称工厂已经落成'))
        p=h.player(self.actor);states=[]
        receipts=c.play(p,on_call=lambda *args:states.append(p.action_state()['can_act']))
        failed=receipts[1];finished=receipts[-1]
        self.assertFalse(failed['ok'])
        self.assertIn('explain_plan',failed['message'])
        request=c._backend.requests[1]
        failed_text=next(m['content'] for m in request if m.get('role')=='tool' and m.get('tool_call_id')=='e')
        self.assertTrue(failed_text.startswith('❌'),failed_text)
        self.assertIn('explain_plan',failed_text)
        self.assertNotIn('回合小结',failed_text)
        self.assertNotIn('国策尚待解释',failed_text)
        h.ui.console.remember(p.observe())
        displayed=h.ui.console.action(h.clock(),self.actor,'end_turn',{'summary':'国策尚待解释'},failed,{self.actor:'韩'})
        self.assertIn('❌',displayed)
        self.assertIn('explain_plan',displayed)
        self.assertNotIn('回合小结',displayed)
        self.assertEqual(states,[True,False])
        self.assertTrue(finished['ok'])
        self.assertFalse(p.observe()['can_act'])
        self.assertEqual(len(c._backend.requests),2)
        self.assertEqual(len(c.state.summaries),1)
        self.assertEqual(c.state.summaries[0]['text'],'完成解释等待下回合修订')
        assert_paired(c.state.replay[-1]['messages'])

    def test_two_steps_use_actual_receipts_maps_economy_and_hide_new_plan_from_first(self):
        site=self.actor+'/land-6-6'
        h,c=self.host([True],[reply(call('b','build',site=site,kind='native:farm'),
                                      call('p','plan',text='NEW-CANDIDATE 农場仍在施工，下一期确认产出'),
                                      call('e','end_turn',summary='动工农场并制定国策'))])
        results=h.cycle();first,second=h.services[self.actor]['council']._review_backend.requests
        raw=json.dumps(first,ensure_ascii=False)
        self.assertNotIn('NEW-CANDIDATE',raw)
        for token in ('build','receipt','building_sites','econ','turn.start'):self.assertIn(token,raw)
        self.assertIn('NEW-CANDIDATE',json.dumps(second,ensure_ascii=False))
        self.assertEqual(second[:len(first)],first)
        self.assertEqual(second[-2]['role'],'tool')
        self.assertEqual(h.services[self.actor]['council'].council['dialogue'],[])
        self.assertIn('议会通过了国策',results[1]['message'])
        self.assertNotIn('reason',results[1]['data'])
        self.assertNotIn('已核对回执',json.dumps(results[1],ensure_ascii=False))
        self.assertTrue(any(row['kind']=='settlement' for row in h.services[self.actor]['council'].council['archive']))
        self.assertEqual(h.player(self.actor).observe()['facts']['government']['plan']['text'],'NEW-CANDIDATE 农場仍在施工，下一期确认产出')

    def test_review_has_one_authoritative_previous_plan_without_older_snapshot_prose(self):
        h,c=self.host([True],[reply(call('p','plan',text='新国策按最新事实纠正'),call('e','end_turn',summary='完成修订并结束本期'))])
        service=h.services[self.actor]['council'];prior=service.council['last_plan']
        prior['text']='AUTHORITATIVE-PREVIOUS-PLAN'
        prior['state_at_submission']['facts']['government']['plan']={'text':'OLDER-SNAPSHOT-PLAN','period':0}
        prior['state_at_submission']['facts']['government']['parliament'].update(
            reason='OLDER-SNAPSHOT-REJECTION',explanation='OLDER-SNAPSHOT-EXPLANATION')
        service.council['archive'].append({'kind':'after.action','state':deepcopy(prior['state_at_submission']),
            'changes':[{'path':['facts','government','plan','text'],'set':'OLDER-DELTA-PLAN'},
                       {'path':['facts','government','parliament','explanation'],'set':'OLDER-DELTA-EXPLANATION'}]})
        old_record=deepcopy(service.council['archive'][-1]);old_index=len(service.council['archive'])-1
        service.council['archive'].append({'kind':'action','action':'script_run',
            'arguments':{'inputs':{'facts':'SCRIPT-INPUT-MUST-STAY','changes':[1,2]}}})
        h.cycle()
        first=json.dumps(service._review_backend.requests[0],ensure_ascii=False)
        self.assertIn('AUTHORITATIVE-PREVIOUS-PLAN',first)
        self.assertNotIn('OLDER-SNAPSHOT-PLAN',first);self.assertNotIn('OLDER-DELTA-PLAN',first)
        for token in ('OLDER-SNAPSHOT-REJECTION','OLDER-SNAPSHOT-EXPLANATION','OLDER-DELTA-EXPLANATION'):
            self.assertNotIn(token,first)
        self.assertIn('SCRIPT-INPUT-MUST-STAY',first)
        self.assertNotIn('新国策按最新事实纠正',first)
        self.assertEqual(prior['state_at_submission']['facts']['government']['plan']['text'],'OLDER-SNAPSHOT-PLAN')
        self.assertEqual(service.council['archive'][old_index],old_record)

    def test_rejection_keeps_dialogue_explanation_and_retries_only_next_period(self):
        rows=self.rejected_turn('第一版错误事实')+[
            reply(call('p2','plan',text='第二版已纠正事实'),call('e3','end_turn',summary='修订国策并完成经营'))]
        h,c=self.host([False,True],rows)
        receipts=h.cycle()
        self.assertFalse(receipts[0]['ok']);self.assertFalse(receipts[1]['ok'])
        self.assertIn('要求君主解释',receipts[0]['message'])
        self.assertIn('要求纠正事实',receipts[0]['data']['reason'])
        self.assertEqual(len(h.services[self.actor]['council'].council['dialogue']),7)
        first_dialogue=deepcopy(h.services[self.actor]['council'].council['dialogue'])
        self.assertIn('INITIAL-BASELINE',h.player(self.actor).observe()['facts']['government']['plan']['text'])
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
            reply(call('p3','plan',text='三版'),call('cancel','economy',action='buy',resource='native:wood',quantity=1),
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
        self.assertEqual(len(h.services[self.actor]['council'].council['dialogue']),19) # Survives even succession.
        gov=h.player(self.actor).observe()['facts']['government']
        self.assertEqual(gov['parliament']['reign'],2);self.assertIsNone(gov['plan'])
        self.assertEqual([s['period'] for s in gov['summaries']],[3])
        self.assertTrue(any('罢免' in text for text in events))
        assert_paired(c.state.replay[0]['messages'])

    def test_approval_breaks_rejection_streak_and_council_restarts_clean(self):
        rows=self.rejected_turn('OLDER-REJECTED-A')+self.rejected_turn('OLDER-REJECTED-B')+[
            reply(call('p','plan',text='LATEST-APPROVED-C'),call('e','end_turn',summary='国策已纠正通过审核'))]+self.rejected_turn('NEXT-CANDIDATE-D')
        h,c=self.host([False,False,True,False],rows)
        for _ in range(4):h.cycle()
        gov=h.player(self.actor).observe()['facts']['government']['parliament']
        self.assertEqual(gov['streak'],1);self.assertEqual(gov['reign'],1)
        service=h.services[self.actor]['council'];request=service._review_backend.requests[6]
        self.assertEqual(len(request),2)
        raw=json.dumps(request,ensure_ascii=False)
        self.assertIn('LATEST-APPROVED-C',raw)
        for token in ('INITIAL-BASELINE','OLDER-REJECTED-A','OLDER-REJECTED-B','NEXT-CANDIDATE-D'):
            self.assertNotIn(token,raw)
        self.assertIn('NEXT-CANDIDATE-D',json.dumps(service._review_backend.requests[7],ensure_ascii=False))
        # 独立档案仍完整，但已通过前的审核不回灌给下一次议长。
        self.assertIn('OLDER-REJECTED-A',json.dumps(service.council['archive'],ensure_ascii=False))
        self.assertEqual(c.state.long_memory,'')

    def test_full_archive_survives_monarch_replay_compression(self):
        h,c=self.host([True]*6,[reply(call('p'+str(i),'plan',text='发展经济'+str(i)),call('e'+str(i),'end_turn',summary='完成本回合经营'+str(i))) for i in range(7)],revision=False)
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

    def test_council_can_think_in_plain_text_then_end_with_verdict_tool(self):
        h,c=self.host([True],[reply(call('p','plan',text='发展')),
                              reply(call('e','end_turn',summary='审核格式修正后完成行动'))])
        class RepairingCouncil:
            def __init__(self):self.calls=[];self.schemas=[];self.turn=0
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                self.calls.append(deepcopy(messages));self.schemas.append(deepcopy(tools));self.turn+=1
                if self.turn==1:return {'content':'先梳理了修订前的完整轨迹。'},{}
                if self.turn==2:return {'content':'','tool_calls':[{'id':'next-stage','type':'function','function':{
                    'name':'council_next_stage','arguments':'{}'}}]},{}
                if self.turn==3:return {'content':'周国已兑现占领与建造承诺；伤兵安排合理。'},{}
                return {'content':'','tool_calls':[{'id':'final-verdict','type':'function','function':{
                    'name':'council_approve','arguments':'{}'}}]},{}
        reviewer=RepairingCouncil();h.services[self.actor]['council']._review_backend=reviewer
        results=h.cycle()
        self.assertTrue(results[0]['ok'])
        self.assertIn('议会通过了国策',results[0]['message'])
        self.assertEqual(len(reviewer.calls),4)
        feedback=reviewer.calls[3][-1]['content']
        self.assertIn('未能正确提交裁决',feedback)
        self.assertIn('council_approve',feedback)
        self.assertEqual(reviewer.calls[3][-1]['role'],'user')
        self.assertIn('council_next_stage',{tool['function']['name'] for tool in reviewer.schemas[0]})
        approve_schema=next(tool['function'] for tool in reviewer.schemas[2] if tool['function']['name']=='council_approve')
        reject_schema=next(tool['function'] for tool in reviewer.schemas[2] if tool['function']['name']=='council_reject')
        self.assertEqual(approve_schema['parameters'],{'type':'object','properties':{},'additionalProperties':False})
        self.assertEqual(reject_schema['parameters']['required'],['reason'])
        self.assertEqual(reject_schema['parameters']['properties']['reason']['type'],'string')

    def test_first_stage_warns_after_five_calls_and_rejects_incomplete_at_ten(self):
        h,c=self.host([True],[reply(call('p','plan',text='发展')),
                              reply(call('x','explain_plan',text='下期补充未核验的战略依据与承诺'),call('e','end_turn',summary='说明未解决问题后完成本回合'))])
        class StalledCouncil:
            def __init__(self):self.calls=[];self.schemas=[]
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                self.calls.append(deepcopy(messages));self.schemas.append(deepcopy(tools))
                return {'content':'我还在核对旧国策与历史回执。'},{}
        reviewer=StalledCouncil();h.services[self.actor]['council']._review_backend=reviewer
        emitted=[]
        results=h.cycle(emit=emitted.append)
        self.assertIn('保守打回',results[0]['message'])
        self.assertFalse(results[0]['ok'])
        self.assertEqual(len(reviewer.calls),10)
        for number,request in enumerate(reviewer.calls,1):
            self.assertIn(f'[{number}/10]',request[-1]['content'])
            self.assertEqual('催办：' in request[-1]['content'],number>5)
        progress=[line for line in emitted if '议会审查 1/2' in line]
        self.assertEqual(len(progress),10)
        for index,line in enumerate(progress,1):
            self.assertIn(f'第 {index}/10 次调用',line)
        self.assertTrue(any('国策保守打回' in line for line in emitted))
        self.assertIn('两阶段累计已调用 5 轮',reviewer.calls[5][-1]['content'])
        self.assertIn('council_next_stage',{tool['function']['name'] for tool in reviewer.schemas[0]})
        self.assertEqual(reviewer.schemas[0][-1]['function']['name'],'council_next_stage')
        self.assertNotEqual(h.player(self.actor).observe()['facts']['government']['plan']['text'],'发展')
        review=h.services[self.actor]['council'].council['archive']
        self.assertTrue(any(row.get('auto_rejected_due_to_paralysis') for row in review))

    def test_five_and_ten_round_limits_count_across_both_stages(self):
        h,c=self.host([True],[reply(call('p','plan',text='发展')),
                              reply(call('x','explain_plan',text='下期补齐必要评估并提交新国策'),call('e','end_turn',summary='说明后完成本回合'))])
        class SplitCouncil:
            def __init__(self):self.calls=[]
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                self.calls.append(deepcopy(messages))
                if len(self.calls)==5:
                    return {'content':'','tool_calls':[{'id':'next','type':'function','function':{
                        'name':'council_next_stage','arguments':'{}'}}]},{}
                return {'content':'仍在核对国策与证据。'},{}
        reviewer=SplitCouncil();h.services[self.actor]['council']._review_backend=reviewer
        results=h.cycle()
        self.assertEqual(len(reviewer.calls),10)
        self.assertIn('两阶段累计已调用 5 轮',json.dumps(reviewer.calls[5],ensure_ascii=False))
        for number,request in enumerate(reviewer.calls,1):
            self.assertIn(f'[{number}/10]',request[-1]['content'])
            self.assertEqual('催办：' in request[-1]['content'],number>5)
        self.assertIn('第二步：',reviewer.calls[5][-1]['content'])
        self.assertIn('保守打回',results[0]['message'])
        self.assertFalse(results[0]['ok'])

    def test_verdict_on_tenth_total_call_is_not_overridden_by_auto_approval(self):
        h,c=self.host([True],[reply(call('p','plan',text='发展')),
                              reply(call('e','end_turn',summary='第十轮裁决后完成行动'))])
        class DeadlineCouncil:
            def __init__(self):self.calls=[]
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                self.calls.append(deepcopy(messages));round_no=len(self.calls)
                if round_no==5:
                    return {'content':'','tool_calls':[{'id':'next','type':'function','function':{
                        'name':'council_next_stage','arguments':'{}'}}]},{}
                if round_no==10:
                    return {'content':'','tool_calls':[{'id':'verdict','type':'function','function':{
                        'name':'council_approve','arguments':'{}'}}]},{}
                return {'content':'继续核对。'},{}
        reviewer=DeadlineCouncil();h.services[self.actor]['council']._review_backend=reviewer
        results=h.cycle()
        self.assertEqual(len(reviewer.calls),10)
        self.assertTrue(results[0]['ok'])
        self.assertNotIn('议会瘫痪',results[0]['message'])

    def test_invalid_verdict_reaches_total_cap_and_rejects(self):
        for output in ('not-json','{"approved":"yes","reason":"ok"}'):
            h,c=self.host([True],[reply(call('p','plan',text='发展')),
                                  reply(call('x','explain_plan',text='下期补齐必要评估并提交新国策'),call('e','end_turn',summary='说明后完成本回合'))])
            original=h.services[self.actor]['council']._review_backend.complete_text
            def complete(messages,cfg,tools=None):
                if messages[-1]['content'].startswith('第二步：'):
                    if isinstance(output,Exception):raise output
                    return output,{}
                return original(messages,cfg,tools)
            h.services[self.actor]['council']._review_backend.complete_text=complete
            results=h.cycle()
            self.assertIn('保守打回',results[0]['message'])
            self.assertFalse(results[0]['ok'])

    def test_failed_review_transport_preserves_boundary_file(self):
        h,c=self.host([True],[reply(call('p','plan',text='发展'))])
        original=h.services[self.actor]['council']._review_backend.complete_text
        def complete(messages,cfg,tools=None):
            if messages[-1]['content'].startswith('第二步：'):raise RuntimeError('review transport failure')
            return original(messages,cfg,tools)
        h.services[self.actor]['council']._review_backend.complete_text=complete
        with TemporaryDirectory() as d:
            p=Path(d)/'synthetic.json';h.save(p);before=p.read_bytes()
            with self.assertRaises(RuntimeError):h.cycle()
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
                                 call('guide','read_manual',topic='军队'),call('forged','economy',action='buy',resource='native:wood',quantity=1)),{}
                if any(m['role']=='tool' for m in messages[-2:]) and any(t['function']['name']=='council_next_stage' for t in tools):
                    return reply(call('next','council_next_stage')) ,{}
                return super().chat_turn(messages,tools,cfg,on_retry)
        reviewer=ReadingCouncil();h.services[self.actor]['council']._review_backend=reviewer
        h.cycle()
        names={s['function']['name'] for s in reviewer.calls[0][1]}
        self.assertEqual(names,{'player_rules','read_manual','council_search','council_read','council_track','council_next_stage'})
        queries=[r for r in h.services[self.actor]['council'].council['archive'] if r['kind']=='council.query']
        self.assertEqual([r['result']['ok'] for r in queries],[True,True,True,False])
        self.assertIn('buildings',queries[1]['result']['data'])
        manual_rules=queries[1]['result']['data']['rules']
        self.assertGreater(len(manual_rules),1)
        self.assertIn('建筑与能源',{r['title'] for r in manual_rules})
        self.assertNotIn('规则目录',{r['title'] for r in manual_rules})
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

    def test_review_requirements_check_dereliction_only_when_actionable(self):
        text=(Path(__file__).resolve().parents[1]/'mods/experimental/requirements.md').read_text(encoding='utf-8')
        for phrase in ('失职检查','GDP 停滞','总兵力占优','情报严重失真','丢失首都',
                       '没有有效动员','可被君主改变','实力不对称','被迫卷入防守战争','不可预见'):
            self.assertIn(phrase,text)
        self.assertIn('写明失职类别、证据和下一期可执行的纠正',text)

    def test_first_plan_is_effective_without_creating_or_calling_reviewer(self):
        h,c=self.host([False],[reply(call('p','plan',text='FIRST-PLAN 工厂已经落成'),
                                    call('e','end_turn',summary='第一版国策制定完成'))],revision=False)
        service=h.services[self.actor]['council']
        def fail():raise AssertionError('The first plan must not invoke a reviewer')
        service._reviewer=fail
        results=h.cycle()
        self.assertTrue(results[0]['ok']);self.assertTrue(results[-1]['ok'])
        self.assertIn('本国设有议会',results[0]['message'])
        self.assertIn('从第一次修订起',results[0]['message'])
        gov=h.player(self.actor).observe()['facts']['government']
        self.assertEqual(gov['plan']['text'],'FIRST-PLAN 工厂已经落成')
        self.assertEqual(gov['parliament']['streak'],0)
        self.assertEqual(service.council['dialogue'],[])
        self.assertEqual(service.council['usage'],{})
        self.assertTrue(any(row['kind']=='plan.initial' for row in service.council['archive']))

    def test_initial_exemption_does_not_allow_second_submission_or_invalid_input(self):
        h,c=self.host([],[],revision=False);p=h.player(self.actor)
        self.assertFalse(p.submit('plan',{'text':''})['ok'])
        self.assertIsNone(h.services[self.actor]['council'].council['last_plan'])
        self.assertTrue(p.submit('plan',{'text':'第一版正常国策'})['ok'])
        self.assertFalse(p.submit('plan',{'text':'同回合再次提交'})['ok'])
        self.assertEqual(h.services[self.actor]['council']._review_backend.requests,[])

    def test_first_revision_after_reload_can_be_rejected_and_uses_initial_baseline(self):
        h,c=self.host([False],[])
        with TemporaryDirectory() as folder:
            path=Path(folder)/'first-plan.json';h.save(path)
            loaded=GameHost.load(path,self.cfg())
            service=loaded.services[self.actor]['council'];service._review_backend=CouncilBackend([False])
            receipt=loaded.player(self.actor).submit('plan',{'text':'REVISED-CANDIDATE 工厂已落成'})
            self.assertFalse(receipt['ok']);self.assertEqual(receipt['data']['streak'],1)
            self.assertEqual(len(service._review_backend.requests),2)
            first=json.dumps(service._review_backend.requests[0],ensure_ascii=False)
            self.assertIn('INITIAL-BASELINE',first)
            self.assertNotIn('REVISED-CANDIDATE',first)
            self.assertIn('state_at_submission',first)
            self.assertTrue(service.council['dialogue'])

    def test_first_plan_exemption_is_per_country(self):
        cfg=self.cfg();cfg['nations'].append({'name':'秦','id':'scenario:qin','client':'rule'})
        h=GameHost.new(cfg)
        for actor in h.actors():
            service=h.services[actor]['council'];service._review_backend=CouncilBackend([False])
            self.assertTrue(h.player(actor).submit('plan',{'text':'本国第一版国策'})['ok'])
            self.assertEqual(service._review_backend.requests,[])
            self.assertIsNotNone(service.council['last_plan'])


if __name__=='__main__':unittest.main()
