"""Opt-in monarch/minister turns over the real session and mock endpoints."""
from pathlib import Path
from queue import Queue
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from clients.llm import assert_paired
from clients.provider import EndpointError
from engine.config import ConfigError, require_ready
from host import GameHost
from mp_run import Observer
from tests.test_player_clients import FakeBackend, call


QIN = 'scenario:qin'
CHU = 'scenario:chu'
POLICY = {'direction':'先发展后择机', 'economy':'依据本国报告，先补电力与矿石',
          'gdp_growth_pct':15.5, 'diplomacy':'争取十回合边境缓冲',
          'military':'扩军前先锁定补给，保持当前防御兵力',
          'intelligence':'先核实对方是否集结，确认后再决定动员'}


def reply(*calls, content='', reasoning=''):
    return {'content':content,'reasoning_content':reasoning,'tool_calls':list(calls)}


def cfg(*, experimental=False):
    return {'mods':'mods.experimental.toml' if experimental else 'mods.toml','map_size':12,'seed':73,
            'ctx_window':120000,'experimental_features':{'model_orchestration':True},
            'nations':[{'id':QIN,'name':'秦','client':'llm','provider':'openai',
                        'model':'minister-a','base_url':'https://minister.example/v1','api_key':'local-test',
                        'monarch':{'provider':'anthropic','model':'monarch-b',
                                   'base_url':'https://monarch.example','api_key':'mock-key'}},
                       {'id':CHU,'name':'楚','client':'rule'}]}


class RecordingBackend(FakeBackend):
    def __init__(self,responses=None):
        super().__init__(responses)
        self.tools=[]

    def chat_turn(self,messages,tools,cfg,on_retry=None):
        self.tools.append({tool['function']['name']:tool['function'] for tool in tools})
        return super().chat_turn(messages,tools,cfg,on_retry=on_retry)


class TestOrchestration(unittest.TestCase):
    def setUp(self):
        self.minister=RecordingBackend()
        self.monarch=RecordingBackend()
        self.connections=[]

        def backend(settings):
            self.connections.append((settings['model'],settings.get('provider')))
            return self.monarch if settings['model']=='monarch-b' else self.minister

        self.addCleanup(patch.stopall)
        patch('clients.llm.make_backend',side_effect=backend).start()

    def test_minister_receives_game_rules_and_opening_guide_in_first_model_request(self):
        openings = {
            'zh-CN': ('回合制战略游戏', '只有君主有权制定和修订国策', '市政厅会亡国', '从下回合开始生产', '【开局指南】'),
            'en-US': ('turn-based strategy game', 'Only the monarch can issue or revise', 'no completed town hall', 'first produce next turn', 'OPENING GUIDE'),
        }
        for language, expected in openings.items():
            with self.subTest(language=language):
                options = cfg()
                options['language'] = language
                host = GameHost.new(options)
                self.minister.responses = [reply(call('ask','ask_monarch',reason='Request first policy')),
                                           reply(call('end','end_turn',summary='Ready to begin'))]
                self.monarch.responses = [reply(call('policy','plan',**POLICY))]
                host.cycle()
                first_prompt = self.minister.requests[-2][0]['content']
                for phrase in (*expected,'query(panel=manual, doc=basics)','ask_monarch'):
                    self.assertIn(phrase,first_prompt)
                self.assertNotIn(expected[-1],self.monarch.requests[-1][0]['content'])

    def test_observer_shows_deliberation_receipts_and_switches_model_protocol(self):
        host=GameHost.new(cfg())
        self.minister.responses=[
            reply(call('buy','economy',action='buy',resource='wood',quantity=1),
                  call('ask','ask_monarch',reason='请确定本国路线')),
            reply(call('accept','decide_control',accept=True),call('end','end_turn',summary='本期市场交易已经完成')),
        ]
        self.monarch.responses=[
            reply(call('army','query',panel='army'),call('bad','query',panel='missing')),
            reply(call('ask','request_control',reason='下期主持建设'),call('plan','plan',**POLICY)),
            reply(call('buy','economy',action='buy',resource='wood',quantity=2),
                  call('end','end_turn',summary='任内已实施采购')),
        ]

        class Panel:
            panel_on = True
            queue = Queue()
            def __init__(self):self.heads=[];self.notes=[];self.screen=[]
            def set_panel(self,*,head=None,note=None):
                if head:self.heads.append(head)
                if note:self.notes.append(note)
            def clear_panel(self):pass
            def write(self,text):self.screen.append(text)

        panel=Panel()
        with TemporaryDirectory() as directory,patch('mp_run.CONSOLE',panel):
            observer=Observer(host,Path(directory)/'journal.md')
            for _ in range(2):
                host.cycle(on_model=observer.model_event,on_call=observer.on_call,
                           on_result=observer.result,emit=observer.model_output)
            journal=observer.path.read_text(encoding='utf-8')
        self.assertIn('[首辅] 秦 · 模型 minister-a · 协议 OpenAI Chat Completions',panel.heads[0])
        self.assertIn('[君主] 秦 · 模型 monarch-b · 协议 Anthropic Messages',panel.heads[1])
        self.assertEqual(panel.heads[0],panel.heads[2])
        self.assertTrue(any('[君主] 秦 议事中，第 2/20 次调用' in note for note in panel.notes))
        self.assertIn('◈ [君主] 秦 第 1 次调用',journal)
        self.assertIn('[君主] [0·行动] 秦 ◇ 军队',journal)
        self.assertIn('[君主] [0·行动] 秦 ◇ query panel=missing',journal)
        self.assertIn('[首辅] [0·行动] 秦 ◇ 购入 木头×1',journal)
        self.assertIn('[君主] [1·行动] 秦 ◇ 购入 木头×2',journal)
        self.assertEqual(journal.count('先发展后择机'),2)  # Published receipt and the separate help result.

    def test_roles_share_a_view_and_audit_receipts_without_crossing_conversations(self):
        host=GameHost.new(cfg());player=host.player(QIN)
        host.player(CHU).submit('plan',{'text':'外国私有国策'})
        self.minister.responses=[
            reply(call('end-early','end_turn',summary='尝试结束但国策尚未颁布'),
                  call('forged','plan',text='首辅私自改国策'),
                  call('buy','economy',action='buy',resource='wood',quantity=5),
                  content='首辅的私有正文',reasoning='首辅的私有思考'),
            reply(call('ask','ask_monarch',reason='请决定十回合总路线')),
            reply(call('end','end_turn',summary='购入五单位木头并按新国策筹划')),
        ]
        self.monarch.responses=[
            reply(call('read','query',panel='econ'),call('log','action_log',limit=2),
                  call('badwrite','economy',action='buy',resource='wood',quantity=100),
                  call('partial','plan',direction='少了四项')),  # Must retry.
            reply(call('policy','plan',**POLICY),call('second','plan',**{**POLICY,'direction':'同批不应覆盖'}),
                  content='君主的私有正文',reasoning='君主的私有思考'),
        ]
        client=host.clients[QIN]
        seen=[]
        receipts=client.play(player,on_result=lambda action,args,receipt:seen.append((action,receipt)))
        self.assertEqual(self.connections,[('minister-a','openai'),('monarch-b','anthropic')])
        self.assertEqual(client.cfg['ctx_window'],client.monarch.cfg['ctx_window'])
        self.assertEqual(receipts[0]['code'],'domain_rejected')
        self.assertEqual(receipts[1]['code'],'tool_not_exposed')
        self.assertTrue(receipts[-1]['ok'])
        help_receipt=next(receipt for action,receipt in seen if action=='ask_monarch')
        policy=help_receipt['data']['plan']
        self.assertEqual(policy,player.query('government')['plan'])
        self.assertEqual(policy['valid_until'],9)
        for text in ('【总路线】','【经济定调】','【外交定调】','【军事定调】','【情报定调】','15.5%'):
            self.assertIn(text,policy['text'])
        self.assertNotIn('【具体计划】',policy['text'])
        self.assertIn('ask_monarch',self.minister.tools[0])
        self.assertNotIn('plan',self.minister.tools[0])
        self.assertEqual(set(self.monarch.tools[0]['plan']['parameters']['required']),set(POLICY))
        self.assertNotIn('economy',self.monarch.tools[0])
        self.assertNotIn('end_turn',self.monarch.tools[0])
        self.assertNotIn('memory_search',self.monarch.tools[0])
        brief=self.monarch.requests[0][-1]['content']
        self.assertIn('共享看板',self.monarch.requests[0][0]['content'])
        self.assertIn('"quantity":5',brief)
        self.assertIn('当前国策',brief)
        for secret in ('外国私有国策','首辅的私有正文','首辅的私有思考'):
            self.assertNotIn(secret,str(self.monarch.requests))
        for secret in ('君主的私有正文','君主的私有思考'):
            self.assertNotIn(secret,str(self.minister.requests))
        self.assertIn('先发展后择机',str(self.minister.requests[-1]))
        self.assertEqual([row['model'] for row in host.audit if row['actor']==QIN and row['action']=='plan'],['monarch-b'])
        for state in (client.state,client.monarch.state):
            assert_paired(state.replay[-1]['messages'])

    def test_twenty_rounds_quota_and_gate_survive_save_and_reload(self):
        options=cfg();host=GameHost.new(options)
        client=host.clients[QIN]
        self.monarch.responses=[reply(call('query','query',panel='bank')) for _ in range(19)]+[reply(call('plan','plan',**POLICY))]
        self.monarch.responses += [reply(call('plan','plan',**POLICY)),reply(call('plan','plan',**POLICY))]
        for _ in range(4):
            self.minister.responses.append(reply(call('ask','ask_monarch',reason='请求定调')))
        self.minister.responses.append(reply(call('end','end_turn',summary='执行国策并记录实际进展')))
        with patch('engine.session.AUDIT_LIMIT',2),TemporaryDirectory() as tmp:
            result=host.cycle()
            self.assertEqual([r['ok'] for r in result if r['action']=='ask_monarch'],[True,True,True,False])
            self.assertEqual(len(self.monarch.requests),22)
            self.assertEqual(len(client.monarch.state.replay),3)
            path=Path(tmp)/'session.json';host.save(path);host=GameHost.load(path,options)
            client=host.clients[QIN]
            self.assertEqual(client.requests,[0,0,0])
            self.assertEqual(len(client.monarch.state.replay),3)
            self.assertEqual(len(client.action_log),4)
            self.assertEqual(client.action_log[0]['action'],'plan')
            self.assertEqual(client._budget(9)['remaining'],0)
            self.minister.responses=[reply(call('end','end_turn',summary='沿原国策继续执行')) for _ in range(9)]
            for _ in range(9):host.cycle()
            self.assertEqual(host.clock(),10)
            self.assertEqual(len(self.monarch.requests),22)
            player=host.player(QIN)
            denied=player.submit('end_turn',{'summary':'仍需先获得有效国策'})
            self.assertFalse(denied['ok'])
            self.assertEqual(client._budget(10)['remaining'],3)
            self.minister.responses=[reply(call('ask','ask_monarch',reason='国策已到期')),reply(call('end','end_turn',summary='按新国策继续实施'))]
            self.monarch.responses=[reply(call('p','plan',**POLICY))]
            host.cycle()
            self.assertEqual(host.player(QIN).query('government')['plan']['valid_until'],19)
            self.assertIn('"valid_until": 9',self.monarch.requests[-1][-1]['content'])

    def test_planner_failure_and_exhaustion_do_not_create_a_policy_or_end_turn(self):
        host=GameHost.new(cfg());player=host.player(QIN);client=host.clients[QIN]
        self.minister.responses=[reply(call('ask','ask_monarch',reason='国策尚未制定')),reply(call('end','end_turn',summary='试图完成回合'))]
        self.monarch.responses=[reply() for _ in range(20)]
        seen=[]
        with self.assertRaises(RuntimeError):
            client.play(player,max_steps=2,on_result=lambda _,__,receipt:seen.append(receipt))
        self.assertEqual(len(self.monarch.requests),20)
        self.assertEqual(seen[0]['code'],'monarch_no_plan')
        self.assertEqual(client.requests,[0])
        self.assertTrue(player.action_state()['can_act'])
        self.assertIsNone(player.query('government')['plan'])
        self.minister.responses=[reply(call('ask','ask_monarch',reason='再次请求定调'))]
        with patch.object(self.monarch,'chat_turn',side_effect=EndpointError('私有错误正文',status_code=429)):
            with self.assertRaises(RuntimeError):client.play(player,max_steps=1,on_result=lambda _,__,receipt:seen.append(receipt))
        self.assertEqual(seen[-1]['code'],'monarch_endpoint')
        self.assertNotIn('私有错误正文',str(seen))
        self.assertEqual(client.requests,[0,0])

    def test_interrupted_turn_keeps_paid_requests_without_saving_partial_actions(self):
        options=cfg();host=GameHost.new(options)
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'session.json'
            host.save(path)
            self.minister.responses=[reply(call('ask','ask_monarch',reason='请定国策')) for _ in range(3)]
            with patch.object(self.monarch,'chat_turn',side_effect=EndpointError('rate limited',status_code=429)) as endpoint:
                with self.assertRaises(RuntimeError):host.clients[QIN].play(host.player(QIN),max_steps=3)
                self.assertEqual(endpoint.call_count,3)
                restored=GameHost.load(path,options)
                self.assertEqual(restored.clock(),0)
                self.assertIsNone(restored.player(QIN).query('government')['plan'])
                self.assertEqual(restored.clients[QIN].requests,[0,0,0])
                self.minister.responses=[reply(call('ask','ask_monarch',reason='重试求助'))]
                with self.assertRaises(RuntimeError):restored.clients[QIN].play(restored.player(QIN),max_steps=1)
                self.assertEqual(endpoint.call_count,3)

    def test_accepted_control_starts_next_turn_last_five_and_returns_action_history(self):
        options=cfg();host=GameHost.new(options)
        self.minister.responses=[
            reply(call('premature','decide_control',accept=True)),
            reply(call('ask','ask_monarch',reason='请决定如何优先扩产'),call('blind','decide_control',accept=True)),
            reply(call('accept','decide_control',accept=True),call('again','decide_control',accept=True),
                  call('buy','economy',action='buy',resource='wood',quantity=1),
                  call('end','end_turn',summary='首辅完成当前回合')),
        ]
        self.monarch.responses=[reply(call('request','request_control',reason='亲自推进建设和采购'),call('plan','plan',**POLICY))]
        first=host.cycle()
        decisions=[r for r in first if r['action']=='decide_control']
        self.assertEqual([r['ok'] for r in decisions],[False,False,True,False])
        offer=next(r['data']['control_request'] for r in first if r['action']=='ask_monarch')
        self.assertEqual((offer['start'],offer['end']),(1,5))
        self.assertEqual([row['model'] for row in host.audit if row['actor']==QIN and row['action']=='economy'],['minister-a'])
        self.assertEqual(host.clock(),1)

        self.monarch.responses=[reply(call('revise','plan',**{**POLICY,'details':'推进建设和采购'}))]
        for period in range(1,6):
            self.monarch.responses.append(reply(call('buy','economy',action='buy',resource='wood',quantity=period),
                                               call('end','end_turn',summary=f'君主任内第 {period} 回合买木'),
                                               content='只给君主重播的正文',reasoning='只给君主重播的思考'))
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'session.json'
            for period in range(1,6):
                self.assertEqual(host.clock(),period)
                receipts=host.cycle()
                self.assertTrue(any(r['actor']==QIN and r['action']=='end_turn' and r['ok'] for r in receipts))
                if period==2:
                    host.save(path)
                    host=GameHost.load(path,options)
                    self.assertTrue(host.clients[QIN].monarch_in_control(3))
            host.save(path)
            host=GameHost.load(path,options)
        self.assertEqual(host.clock(),6)
        self.assertEqual(len(self.minister.requests),3)
        self.assertEqual(len(self.monarch.requests),7)
        self.assertIn('economy',self.monarch.tools[-1]);self.assertNotIn('decide_control',self.monarch.tools[-1])
        self.assertNotIn('request_control',self.monarch.tools[-1]);self.assertNotIn('ask_monarch',self.monarch.tools[-1])
        self.assertEqual(host.clients[QIN].requests,[0])
        self.assertEqual([row['model'] for row in host.audit if row['actor']==QIN and row['action']=='economy'],
                         ['minister-a']+['monarch-b']*5)

        self.minister.responses=[reply(call('history','control_history',after=0,limit=2)),
                                 reply(call('end','end_turn',summary='首辅接手并核实任内回执'))]
        resumed=host.cycle()
        handover=self.minister.requests[-2][-1]['content']
        self.assertIn('【首辅接手】',handover)
        self.assertIn('【任内行动和回执】',handover)
        self.assertIn('"quantity":5',handover)
        for private in ('只给君主重播的正文','只给君主重播的思考'):
            self.assertNotIn(private,handover)
        history=next(r['data'] for r in resumed if r['action']=='control_history')
        self.assertEqual([row['period'] for row in history['records']],[1,1])
        self.assertEqual(history['next_after'],2)
        self.assertTrue(history['has_more'])
        self.assertEqual(next(row['model'] for row in reversed(host.audit) if row['actor']==QIN),'minister-a')

    def test_decline_or_silence_does_not_grant_control(self):
        for answer in (None,False):
            with self.subTest(answer=answer):
                options=cfg();host=GameHost.new(options)
                self.minister.responses=[reply(call('ask','ask_monarch',reason='确定首期国策')),
                                         reply(call('end','end_turn',summary='本期按国策筹备'))]
                self.monarch.responses=[reply(call('plan','plan',**POLICY))]
                host.cycle()
                tools=[] if answer is None else [call('decline','decide_control',accept=answer)]
                self.minister.responses=[reply(call('ask','ask_monarch',reason='请复核当前路线')),
                                         reply(*tools,call('end','end_turn',summary='继续完成筹备'))]
                self.monarch.responses=[reply(call('request','request_control',reason='希望亲自安排矿场'))]
                receipts=host.cycle()
                self.assertTrue(next(r for r in receipts if r['action']=='ask_monarch')['ok'])
                self.assertIsNone(host.clients[QIN].control_term)
                self.minister.responses=[reply(call('late','decide_control',accept=True),
                                              call('end','end_turn',summary='首辅继续执行国策'))]
                later=host.cycle()
                self.assertEqual(next(r for r in later if r['action']=='decide_control')['code'],'no_control_request')
                self.assertEqual(next(row['model'] for row in reversed(host.audit) if row['actor']==QIN),'minister-a')
                self.assertIsNone(host.clients[QIN].control_term)

    def test_previous_orchestration_save_loads_without_a_control_term(self):
        options=cfg();data=GameHost.new(options).to_save()
        old=data['clients'][QIN]
        old['version']='orchestration/1'
        old['state'].pop('control_term')
        restored=GameHost(GameHost.prepare(options))
        restored.session.restore(data)
        self.assertIsNone(restored.clients[QIN].control_term)

    def test_governing_monarch_renews_expired_policy_before_ending_turn(self):
        host=GameHost.new(cfg())
        self.minister.responses=[reply(call('ask','ask_monarch',reason='首次定策'))]
        self.minister.responses.extend(reply(call('end','end_turn',summary='继续按当前国策发展')) for _ in range(9))
        self.monarch.responses=[reply(call('plan','plan',**POLICY))]
        for _ in range(9):host.cycle()
        self.minister.responses=[reply(call('ask','ask_monarch',reason='国策将到期，请调整')),
                                 reply(call('consent','decide_control',accept=True),call('end','end_turn',summary='首辅完成最后一期'))]
        self.monarch.responses=[reply(call('offer','request_control',reason='亲自落实下阶段投资'))]
        host.cycle()
        self.assertEqual(host.clock(),10)
        self.monarch.responses=[reply(call('early','end_turn',summary='尝试用到期国策结束')),
                                reply(call('plan','plan',**POLICY)),
                                reply(call('end','end_turn',summary='颁布新国策并完成本回合'))]
        receipts=[r for r in host.cycle() if r['actor']==QIN]
        self.assertEqual([(r['action'],r['ok']) for r in receipts],[('end_turn',False),('plan',True),('end_turn',True)])
        self.assertEqual(host.player(QIN).query('government')['plan']['valid_until'],19)

    def test_council_stays_active_for_other_seats_and_executor_cannot_write_policy_by_api(self):
        options=cfg(experimental=True);host=GameHost.new(options)
        self.assertNotIn('council',host.services[QIN])
        self.assertIn('council',host.services[CHU])
        player=host.player(QIN)
        self.assertEqual(player.submit('plan',{'text':'伪造'})['code'],'monarch_required')
        denied=player.call_api('native:government','plan',{'text':'伪造'},action_label='script_plan')
        self.assertEqual((denied['code'],denied['action']),('monarch_required','script_plan'))
        self.minister.responses=[reply(call('ask','ask_monarch',reason='请颁布国策')),reply(call('end','end_turn',summary='按国策完成当前准备'))]
        self.monarch.responses=[reply(call('plan','plan',**POLICY))]
        self.assertTrue(host.clients[QIN].play(player)[-1]['ok'])
        self.assertTrue(host.player(CHU).submit('plan',{'text':'保障国内补给'})['data']['approved'])

    def test_opt_in_and_independent_endpoint_preflight(self):
        options=cfg();require_ready(GameHost.prepare(options))
        del options['nations'][0]['monarch']['api_key']
        with self.assertRaisesRegex(ConfigError,r'nations\[0\]\.monarch'):
            require_ready(GameHost.prepare(options))
        options['experimental_features']['model_orchestration']=False
        with self.assertRaisesRegex(ConfigError,'君臣编排'):
            GameHost.prepare(options)
        del options['nations'][0]['monarch']
        self.assertNotIn('model_orchestration',GameHost.prepare(options).runtime.dependency_manifest()['distribution']['Features'])


if __name__=='__main__':unittest.main()
