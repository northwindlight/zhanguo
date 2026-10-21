"""Council evidence, privacy, continuity and bounded requests; fake backends only."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from clients.llm import assert_paired
from host import GameHost
from mods.experimental.council import CouncilService, DEFAULT_REQUIREMENTS
from mods.experimental.council_records import trajectory_overview, update_tracking, tracking_view
from tests.test_parliament import CouncilBackend, reply, start_with_plan
from tests.test_player_clients import call


class ScriptedCouncil:
    def __init__(self, responses):
        self.responses=list(responses)
        self.requests=[]

    def chat_turn(self,messages,tools,cfg,on_retry=None):
        self.requests.append((deepcopy(messages),deepcopy(tools)))
        return self.responses.pop(0), {}


class TestCouncilTracking(unittest.TestCase):
    actor='scenario:han'

    def cfg(self,window=64000):
        return {'mods':'mods.experimental.toml','map_size':12,'seed':73,
                'nations':[{'id':self.actor,'name':'韩','client':'llm','ctx_window':window,
                            'ctx_fill':.5,'max_tokens':1024,'ctx_compact':'off'}]}

    def host(self,window=64000):
        host=GameHost.new(self.cfg(window))
        start_with_plan(host,self.actor)
        return host,host.services[self.actor]['council']

    def evidence(self,messages):
        for message in reversed(messages):
            text=message.get('content','')
            if message['role']=='user' and text.startswith('第一步：'):
                return json.JSONDecoder().raw_decode(text[text.index('{'):])[0]
        self.fail('missing evidence')

    def test_experimental_monarch_and_council_can_read_all_strategy_documents(self):
        host,service=self.host()
        player=host.player(self.actor)
        before=host.to_save()
        for title,doc in [('基础指南','basics'),('经济学手册','economics'),('战争手册','war'),('战略手册','strategy')]:
            actual=player.query('query',{'panel':'manual','doc':doc})
            self.assertEqual(service._manual(player,title)['body'],actual['body'])
            self.assertGreater(len(actual['body']),100)
        self.assertEqual(host.to_save(),before)
        self.assertNotIn('战略手册',[r['title'] for r in player.rules('all')])

    def test_open_premise_survives_approval_save_reload_and_requires_explicit_resolution(self):
        host,service=self.host()
        backend=ScriptedCouncil([
            reply(call('track','council_track',kind='premise',text='邻国全国军力未知，改变主攻方向前须评估。',references=['current'],due=3),
                  call('next','council_next_stage')),
            reply(call('approve','council_approve'))])
        service._review_backend=backend
        self.assertTrue(host.player(self.actor).submit('plan',{'text':'保持守势，第三期核查邻国再决定是否出兵。'})['ok'])
        item=service.council['tracking']['items'][0]
        self.assertEqual(item['status'],'open')
        self.assertEqual(service.council['dialogue'],[])
        self.assertTrue(item['references'][0].startswith('row/'))
        source_ref=item['references'][0]
        host.player(self.actor).submit('end_turn',{'summary':'保留情报待核事项，本期守势'})
        host.cycle()
        with TemporaryDirectory() as directory:
            path=Path(directory)/'session.json'
            host.save(path)
            restored=GameHost.load(path,self.cfg())
        service=restored.services[self.actor]['council']
        reviewer=ScriptedCouncil([
            reply(call('close','council_track',item_id=item['id'],kind='premise',text=item['text'],references=['current'],
                       status='superseded',resolution='本阶段取消进攻，以本国可兑现后勤制定守势，不再依赖弱敌判断。'),
                  call('next','council_next_stage')),
            reply(call('approve','council_approve'))])
        service._review_backend=reviewer
        self.assertTrue(restored.player(self.actor).submit('plan',{'text':'改为守势，取消依赖邻国弱小的主攻计划。'})['ok'])
        self.assertEqual(self.evidence(reviewer.requests[0][0])['tracking']['open'][0]['id'],item['id'])
        self.assertEqual(service.council['tracking']['items'][0]['status'],'superseded')
        pieces=[];offset=0
        while True:
            page=service._read(source_ref,offset=offset);pieces.append(page['text'])
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertEqual(json.loads(''.join(pieces))['state']['period'],1)

    def test_rejection_is_tracked_even_when_reviewer_does_not_create_a_note(self):
        host,service=self.host()
        service._review_backend=CouncilBackend([False])
        result=host.player(self.actor).submit('plan',{'text':'省略外部评估与实际承诺。'})
        self.assertFalse(result['ok'])
        issue=service.council['tracking']['items'][0]
        self.assertEqual(issue['kind'],'issue')
        self.assertEqual(issue['status'],'open')
        self.assertEqual(issue['due'],2)
        self.assertIn('国策声称',issue['text'])
        self.assertIn('decision',service._read(issue['references'][0])['text'])

    def test_paged_evidence_is_lossless_and_search_returns_real_references(self):
        host,service=self.host()
        text='此前缺失的关键回执'*3000+'FINAL-EVIDENCE'
        index=len(service.council['archive'])
        row={'kind':'action','period':1,'action':'intelligence','arguments':{'action':'spy','target':'s:neighbor'},
             'receipt':{'ok':True,'period':1,'data':{'text':text}}}
        service.council['archive'].append(row)
        found=service._search(query='FINAL-EVIDENCE',since=1,until=1)
        ref=found['items'][0]['ref']
        self.assertEqual(ref,f'row/{index}')
        pieces=[];offset=0
        while True:
            page=service._read(ref,offset=offset)
            pieces.append(page['text'])
            self.assertLessEqual(len(page['text']),12000)
            if page['next_offset'] is None:break
            self.assertGreater(page['next_offset'],offset)
            offset=page['next_offset']
        loaded=json.loads(''.join(pieces))
        self.assertEqual(loaded['receipt']['data']['text'],text)
        self.assertEqual(service.council['archive'][index],row)
        with self.assertRaises(ValueError):service._read('row/9999999')

    def test_working_notes_reject_missing_resolution_and_invalid_references(self):
        host,service=self.host()
        service._prepare(host.player(self.actor))
        service._sources={'current':service.council['last_snapshot']}
        original=deepcopy(service.council['tracking'])
        archive=deepcopy(service.council['archive'])
        for args in [dict(kind='issue',text='缺乏依据',references=['row/999999']),
                     dict(kind='issue',text='声称解决但没有说明',references=['current'],status='resolved')]:
            with self.assertRaises(ValueError):service._track(**args)
            self.assertEqual(service.council['tracking'],original)
            self.assertEqual(service.council['archive'],archive)

    def test_previous_snapshot_keeps_its_sampling_period_in_immutable_reference(self):
        host,service=self.host()
        service._prepare(host.player(self.actor))
        previous=deepcopy(service.council['last_plan']['state_at_submission'])
        service._sources={'previous':previous}
        row=service._track(kind='premise',text='旧资料只能证明当时情况',references=['previous'])
        index=int(row['references'][0][4:])
        self.assertEqual(service.council['archive'][index]['period'],0)
        self.assertEqual(service.council['archive'][index]['state']['period'],0)

    def test_retained_evidence_reference_does_not_follow_current_alias_next_period(self):
        host,service=self.host();player=host.player(self.actor)
        service._review_backend=CouncilBackend([False])
        self.assertFalse(player.submit('plan',{'text':'旧草案待纠正'})['ok'])
        old_ref=service.council['last_plan']['details_ref']
        old=json.loads(service._read(old_ref,section='bank')['text'])
        player.submit('explain_plan',{'text':'下期补齐依据'})
        player.submit('end_turn',{'summary':'已说明，下期纠正'})
        host.cycle();player=host.player(self.actor)
        backend=ScriptedCouncil([reply(call('next','council_next_stage')),reply(call('approve','council_approve'))])
        service._review_backend=backend
        self.assertTrue(player.submit('plan',{'text':'补齐目标推进及实际部署，按当前后勤执行'})['ok'])
        self.assertNotEqual(service.council['last_plan']['details_ref'],old_ref)
        self.assertEqual(json.loads(service._read(old_ref,section='bank')['text']),old)
        self.assertEqual(old['period'],1)
        self.assertEqual(json.loads(service._read('current',section='bank')['text'])['period'],2)

    def test_many_short_retained_messages_and_archive_notices_are_also_bounded(self):
        host,service=self.host(24000)
        service.council['dialogue']=[{'role':'system','content':DEFAULT_REQUIREMENTS}]
        for index in range(140):
            service.council['dialogue'].append({'role':'user','content':f'旧审核反馈{index}：'+('须核查具体承诺及日期。'*12)})
        backend=ScriptedCouncil([reply(call('next','council_next_stage')),reply(call('approve','council_approve'))])
        service._review_backend=backend
        self.assertTrue(host.player(self.actor).submit('plan',{'text':'按真实部署推进目标，第二期按实际阻力复核。'})['ok'])
        for messages,tools in backend.requests:
            self.assertLessEqual(service._request_tokens(messages,tools),service._review_input_budget())
            self.evidence(messages)
            assert_paired(messages)
        stored=[m['content'] for row in service.council['archive'] if row['kind']=='council.context'
                for m in row['messages'] if m.get('content','').startswith('旧审核反馈')]
        self.assertEqual(len(stored),140)

    def test_overview_bounds_long_intervals_without_losing_cash_or_information_counts(self):
        rows=[]
        for period in range(1000):
            rows.extend([{'kind':'after.action','period':period,'changes':[{'path':['facts','bank','cash'],'set':period}]},
                         {'kind':'action','arguments':{'action':'spy' if period%2 else 'buy_report'},
                          'receipt':{'period':period,'ok':True}}])
        overview=trajectory_overview(rows)
        self.assertLessEqual(len(overview),24)
        self.assertEqual(sum(r['spy'] for r in overview),500)
        self.assertEqual(sum(r['buy_report'] for r in overview),500)
        self.assertEqual(max(r['cash_max'] for r in overview),999)
        self.assertEqual(min(r['cash_min'] for r in overview),0)

    def test_small_window_preserves_current_facts_when_old_details_and_exchanges_are_huge(self):
        host,service=self.host(24000)
        player=host.player(self.actor)
        player.submit('economy',{'action':'buy','resource':'wood','quantity':2})
        cash=player.query('bank',{})['cash']
        for period in range(120):
            service.council['archive'].append({'kind':'after.action','period':period,
                'changes':[{'path':['facts','army'],'set':['旧状态'*2000]}],
                'events':{'items':[{'text':'旧事件'*1000}]}})
        backend=ScriptedCouncil([
            {'content':'独立核对意见。'*3000,'reasoning_content':'模型签名推理'*3000,
             'reasoning_signature':{'model':'fake','signature':'signed'},
             'tool_calls':[call('read','council_read',ref='current',section='army')]},
            reply(call('next','council_next_stage')),
            reply(call('approve','council_approve'))])
        service._review_backend=backend
        self.assertTrue(player.submit('plan',{'text':'先确认邻国情报范围；守势期间以当前现金与后勤约束支出。'})['ok'])
        for messages,tools in backend.requests:
            self.assertLessEqual(service._request_tokens(messages,tools),service._review_input_budget())
            assert_paired(messages)
            evidence=self.evidence(messages)
            current=evidence.get('current_summary')
            if current is None:
                snapshot=evidence['current_state']
                self.assertEqual(snapshot['period'],1)
                self.assertEqual(snapshot['facts']['bank']['cash'],cash)
                self.assertTrue(snapshot['facts']['view'])
            else:
                self.assertEqual(current['period'],1)
                self.assertEqual(current['bank']['cash'],cash)
                self.assertIn('全国总量未知',current['scope'])
                self.assertTrue(current['visible_tiles'])
        self.assertTrue(any(row['kind']=='council.context' for row in service.council['archive']))
        self.assertEqual(json.loads(service._read('current',section='army')['text'])['period'],1)

    def test_private_other_council_records_are_not_searchable(self):
        cfg=self.cfg();cfg['nations'].append({'id':'scenario:chu','name':'楚','client':'llm'})
        host=GameHost.new(cfg)
        host.services['scenario:chu']['council'].council['archive'].append(
            {'kind':'action','period':0,'arguments':{},'receipt':{'message':'OTHER-COUNTRY-SECRET'}})
        service=host.services[self.actor]['council']
        self.assertEqual(service._search(query='OTHER-COUNTRY-SECRET')['items'],[])

    def test_full_active_ledger_fits_small_window_and_remains_available_in_full(self):
        host,service=self.host(24000)
        player=host.player(self.actor)
        service._prepare(player)
        service._sources={'current':service.council['last_snapshot']}
        for index in range(24):
            service._track(kind='commitment',text=f'事项{index}：'+('按时兑现情报与行动承诺'*40),
                           references=['current']*8,due=2)
        original=deepcopy(service.council['tracking'])
        with self.assertRaises(ValueError):
            service._track(kind='issue',text='不静默挤掉尚未解决的问题',references=['current'])
        self.assertEqual(service.council['tracking'],original)
        backend=ScriptedCouncil([reply(call('next','council_next_stage')),reply(call('approve','council_approve'))])
        service._review_backend=backend
        self.assertTrue(player.submit('plan',{'text':'有限进攻与情报搜集并行；按现有后勤执行，第二期按实际阻力复核。'})['ok'])
        for messages,tools in backend.requests:
            self.assertLessEqual(service._request_tokens(messages,tools),service._review_input_budget())
            self.assertEqual(len(self.evidence(messages)['tracking']['open']),24)
            assert_paired(messages)
        self.assertEqual(service.council['tracking'],original)
        pieces=[];offset=0
        while True:
            page=service._read('tracking',offset=offset);pieces.append(page['text'])
            if page['next_offset'] is None:break
            offset=page['next_offset']
        self.assertEqual(json.loads(''.join(pieces)),original)

    def test_insufficient_window_fails_before_any_provider_request(self):
        host,service=self.host()
        service.review_cfg.update(ctx_window=1024,max_tokens=1024)
        backend=ScriptedCouncil([]);service._review_backend=backend
        with self.assertRaisesRegex(ValueError,'窗口不足'):
            host.player(self.actor).submit('plan',{'text':'仍须在本地检查输入预算。'})
        self.assertEqual(backend.requests,[])

    def test_fixed_turn_configuration_still_has_an_input_ceiling(self):
        host,service=self.host(24000)
        service.review_cfg.update(ctx_full_turns=8)
        self.assertGreater(service._review_input_budget(),0)
        self.assertLessEqual(service._review_input_budget(),24000-1024-2048)
        service.review_cfg.pop('ctx_window',None)
        self.assertGreater(service._review_input_budget(),0)
        self.assertLessEqual(service._review_input_budget(),200000-1024-2048)

    def test_prompt_requires_substantive_assessment_even_without_explicit_promises(self):
        host,service=self.host()
        for text in ('宁愿打回','君主缩短国策','不作承诺','可见0支不证明全国0支','有钱却反复不兑现','批准本身不代表问题解决'):
            self.assertIn(text,DEFAULT_REQUIREMENTS)
        self.assertIn('完整性',service.ui.text('experimental.review_second'))
        self.assertIn('反复延期',service.ui.text('experimental.review_first'))

    def test_unresolved_intelligence_risk_does_not_mechanically_veto_a_reasoned_offensive(self):
        host,service=self.host()
        service._prepare(host.player(self.actor))
        service._sources={'current':service.council['last_snapshot']}
        risk=service._track(kind='risk',text='敌军全国兵力未知；须把可见子集与全国总量分开。',references=['current'])
        service._review_backend=CouncilBackend([True])
        # The fake reviewer supplies the verdict. This checks that open risks
        # are not an engine veto; it does not measure real model preferences.
        plan=('目标兼并天下，本阶段准备夺取边境资源点。敌军全国兵力未知，不假定视野外无兵。'
              '本期盘点可征兵力和三期军需预算，第二期按真实目录与部署提交进攻方案；'
              '报表搜集与集结并行。按实际阻力决定增援、换轴或议和，不等待完整全国情报或绝对必赢。')
        self.assertTrue(host.player(self.actor).submit('plan',{'text':plan})['ok'])
        self.assertEqual(service.council['tracking']['items'][0]['id'],risk['id'])
        self.assertEqual(service.council['tracking']['items'][0]['status'],'open')
        for text in ('审核不得降低合理开战','信息不完整的进攻','对不行动使用同样严格的标准','放弃游戏目标'):
            self.assertIn(text,DEFAULT_REQUIREMENTS)
        self.assertIn('不准借审核无限延期',service.ui.text('experimental.review_second'))


if __name__=='__main__':
    unittest.main()
