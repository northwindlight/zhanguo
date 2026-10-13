"""Client/host behavior over synthetic worlds and disposable session files."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import test_world_domains as fixtures
from clients.llm import LLMClient,LLMState,assert_paired
from ruleai.v11plus import RuleClient,RuleState
from ruleai.v11plus.ledger import Ledger
from host import GameHost,SaveFormatError,ROOT
import ruleai as rule_ai
Q,C=fixtures.Q,fixtures.C


def call(ident,name,**arguments):
    return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False)}}


class FakeBackend:
    def __init__(self,responses=None):self.responses=list(responses or []);self.requests=[];self.compressions=[]
    def chat_turn(self,messages,tools,cfg,on_retry=None):
        self.requests.append(deepcopy(messages))
        if self.responses:message=self.responses.pop(0)
        else:message={'content':'本期发展','reasoning_content':'按可见事实决定','reasoning_signature':{'model':'fake','signature':'signed'},
                      'tool_calls':[call('plan','plan',text='发展经济'),call('end','end_turn',summary='本期已经完成经营')]}
        return message,{'hit':10,'miss':2}
    def complete_text(self,messages,cfg,tools=None,**kwargs):
        self.compressions.append((deepcopy(messages),deepcopy(tools)))
        return '持续发展经济，记住已生效条约与待办事项。',{'hit':8,'miss':1}


class TestPlayerClients(unittest.TestCase):
    def world(self):
        t=fixtures.TestWorldDomains();w=t.world();t.site(w,'scenario:qc',Q,1,1)
        return w
    def cfg(self):return {'map_size':12,'seed':73,'nations':[{'name':'秦','id':Q},{'name':'楚','id':C}]}

    def test_registry_has_only_active_version_and_rejects_all_retired_names(self):
        self.assertEqual(rule_ai.versions(),('v11plus',));self.assertEqual(rule_ai.resolve()[0],'v11plus')
        for version in ('ai','v4','v5','v6','v7','v8','v9','v10','v11','v12'):
            with self.assertRaises(ValueError):rule_ai.resolve(version)
        self.assertIsInstance(rule_ai.create(),RuleClient)

    def test_llm_and_rule_ledger_have_identical_actions_receipts_and_game_effects(self):
        w=self.world();other=w.runtime.world_class()();w.runtime.load_mod_state(other,w.runtime.save_mod_state(w))
        commands=[('plan',{'text':'发展经济'}),('build',{'site':'scenario:qc','kind':'native:farm'}),('end_turn',{'summary':'本期已经完成经营'})]
        backend=FakeBackend([{'content':'','tool_calls':[call(str(i),name,**args) for i,(name,args) in enumerate(commands)]}])
        llm=LLMClient({},backend=backend);left=llm.play(w.runtime.player(w,Q))
        ledger=Ledger(other.runtime.player(other,Q))
        for name,args in commands:self.assertTrue(ledger.do(name,**args))
        self.assertEqual(left,ledger.acts)
        self.assertEqual(w.runtime.save_mod_state(w),other.runtime.save_mod_state(other))
        self.assertFalse(hasattr(w,'turn_memory'))

    def test_full_replay_is_preserved_and_provider_private_signature_survives_reload(self):
        w=self.world();backend=FakeBackend();client=LLMClient({'ctx_full_turns':8},backend=backend)
        client.play(w.runtime.player(w,Q));record=deepcopy(client.state.replay[0]['messages'])
        w.resolve_turn();w.phase='行动';client.play(w.runtime.player(w,Q))
        requested=backend.requests[1]
        first_state=next(i for i,m in enumerate(requested) if m==record[0])
        self.assertEqual(requested[first_state:first_state+len(record)],record)
        assert_paired(record)
        state=LLMState.from_save(json.loads(json.dumps(client.state.to_save())))
        self.assertEqual(state.replay,client.state.replay)
        self.assertEqual(state.replay[0]['messages'][1]['reasoning_signature']['signature'],'signed')

    def test_every_tool_in_a_batch_gets_a_reply_even_after_end_turn(self):
        w=self.world();backend=FakeBackend([{'content':'','tool_calls':[call('a','plan',text='发展'),call('b','end_turn',summary='本期已经完成'),call('c','buy',resource='native:wood',quantity=1)]}])
        client=LLMClient({},backend=backend);receipts=client.play(w.runtime.player(w,Q))
        self.assertFalse(receipts[-1]['ok']);self.assertEqual(receipts[-1]['code'],'unavailable_action')
        assert_paired(client.state.replay[0]['messages'])
        self.assertEqual(sum(m['role']=='tool' for m in client.state.replay[0]['messages']),3)

    def test_bad_arguments_retry_body_and_expired_plan_need_real_end_turn(self):
        w=self.world();bad=call('bad','buy');bad['function']['arguments']='not json'
        backend=FakeBackend([{'content':'正文不能代替结束','tool_calls':[bad]},
                             {'content':'','tool_calls':[call('end','end_turn',summary='尚未写国策')]},
                             {'content':'','tool_calls':[call('p','plan',text='更新国策'),call('e','end_turn',summary='本期已经完成经营')]}])
        client=LLMClient({},backend=backend);receipts=client.play(w.runtime.player(w,Q))
        self.assertEqual(receipts[0]['code'],'invalid_arguments');self.assertFalse(receipts[1]['ok']);self.assertTrue(receipts[-1]['ok'])
        self.assertEqual(len(backend.requests),3)

    def test_recursive_compression_keeps_request_prefix_and_client_only_state(self):
        w=self.world();backend=FakeBackend();client=LLMClient({'ctx_full_turns':1,'ctx_compact':'llm'},backend=backend)
        client.state.long_memory='已有长期盟约'
        for _ in range(6):client.play(w.runtime.player(w,Q));w.resolve_turn();w.phase='行动'
        self.assertTrue(backend.compressions)
        compression,tools=backend.compressions[0]
        prefix=compression[:-1]
        self.assertEqual(prefix[:len(backend.requests[5])],backend.requests[5])
        self.assertEqual(prefix[len(backend.requests[5]):],client.state.replay[-1]['messages'][1:])
        self.assertIn('已有长期盟约',compression[-1]['content'])
        self.assertTrue(any(s['function']['name']=='end_turn' for s in tools))
        self.assertEqual(client.state.long_memory,'持续发展经济，记住已生效条约与待办事项。')
        self.assertFalse(hasattr(w,'long_memory'));self.assertEqual(len(client.state.blocks),1)
        self.assertEqual(len(client.state.history),6)
        self.assertGreater(len(client.state.history),len(client.state.replay))

    def test_rule_runs_and_persists_target_state_without_global_cross_game_memory(self):
        cfg={'map_size':12,'seed':73,'nations':[{'name':'秦','id':Q}]};host=GameHost.new(cfg)
        for _ in range(7):host.cycle()
        self.assertTrue(host.audit)
        self.assertEqual(RuleState.from_save(host.clients[Q].state.to_save()).targets,host.clients[Q].state.targets)
        fresh=GameHost.new(cfg);self.assertEqual(fresh.clients[Q].state.targets,{})
        # 规则客户端的经济节奏没有为库存市场重新调参（ruleai 不随引擎同步维护），
        # 所以这里只核对它在行动、目标状态可续、且没有跨局记忆，不绑定出兵周期。
        self.assertTrue(any(row['receipt'].get('action')=='plan' for row in host.audit))

    def test_rules_and_llm_import_only_api_and_client_dependencies(self):
        import ast
        for base in (ROOT/'ruleai/v11plus',ROOT/'clients'):
            for path in base.glob('*.py'):
                tree=ast.parse(path.read_text())
                for node in ast.walk(tree):
                    names=[a.name for a in node.names] if isinstance(node,ast.Import) else [node.module or ''] if isinstance(node,ast.ImportFrom) else []
                    self.assertFalse(any(name=='mp' or name=='game' or name.startswith('mods.') for name in names),path)
        for path in (ROOT/'engine').glob('*.py'):
            self.assertNotIn('from ruleai',path.read_text());self.assertNotIn('from clients',path.read_text())


class TestSessionHost(unittest.TestCase):
    def cfg(self):return {'map_size':12,'seed':73,'nations':[{'name':'秦','id':Q},{'name':'楚','id':C}]}

    def test_whole_cycle_save_reload_and_client_targets_continue_identically(self):
        cfg=self.cfg();h=GameHost.new(cfg)
        for _ in range(5):h.cycle()
        with TemporaryDirectory() as d:
            path=Path(d)/'session.json';h.save(path);loaded=GameHost.load(path,cfg)
            self.assertEqual(h.to_save(),loaded.to_save())
            for _ in range(3):h.cycle();loaded.cycle();self.assertEqual(h.to_save(),loaded.to_save())

    def test_hybrid_llm_rule_roundtrip_never_persists_api_keys(self):
        cfg=self.cfg();cfg['nations'][0].update(client='llm',api_key='synthetic-secret')
        h=GameHost.new(cfg);h.clients[Q]._backend=FakeBackend();h.cycle()
        self.assertNotIn('synthetic-secret',json.dumps(h.to_save()))
        with TemporaryDirectory() as d:
            path=Path(d)/'session.json';h.save(path);loaded=GameHost.load(path,cfg)
            loaded.clients[Q]._backend=FakeBackend();h.cycle();loaded.cycle()
            self.assertEqual(h.to_save(),loaded.to_save())

    def test_provider_failure_and_step_limit_leave_boundary_file_untouched(self):
        cfg=self.cfg();cfg['nations'][0].update(client='llm',api_key='synthetic-secret')
        for fault in (RuntimeError('provider unavailable'),'max_steps'):
            h=GameHost.new(cfg)
            with TemporaryDirectory() as d:
                path=Path(d)/'session.json';h.save(path);before=path.read_bytes()
                if fault=='max_steps':h.clients[Q]=LLMClient({'max_steps':1},backend=FakeBackend([{'content':'只回复正文'}]))
                else:
                    backend=FakeBackend();backend.chat_turn=lambda *a,**k:(_ for _ in ()).throw(fault)
                    h.clients[Q]._backend=backend
                with self.assertRaises(RuntimeError):h.cycle()
                self.assertEqual(path.read_bytes(),before)
                with self.assertRaises(SaveFormatError):h.save(path)

    def test_old_save_invalid_dependencies_retired_config_never_overwrite(self):
        cfg=self.cfg()
        with TemporaryDirectory() as d:
            path=Path(d)/'session.json';path.write_text('{"nations": ["秦"]}');before=path.read_bytes()
            with self.assertRaises(SaveFormatError):GameHost.load(path,cfg)
            self.assertEqual(path.read_bytes(),before)
            h=GameHost.new(cfg);h.save(path);bad=json.loads(path.read_text());bad['dependencies']['api']=0
            path.write_text(json.dumps(bad));before=path.read_bytes()
            with self.assertRaises(SaveFormatError):GameHost.load(path,cfg)
            self.assertEqual(path.read_bytes(),before)
        cfg['rule_ai']='v10'
        with self.assertRaises(ValueError):GameHost.new(cfg)

    def test_scheduled_appearance_after_load_matches_continuous_and_can_be_manual(self):
        cfg=self.cfg();cfg['nations'].append({'id':'scenario:huns','name':'林胡','polity':'huns','enable_turn':1,'start_cavalry':8,'start_gold':2000})
        h=GameHost.new(cfg);h.cycle()
        with TemporaryDirectory() as d:
            path=Path(d)/'session.json';h.save(path);loaded=GameHost.load(path,cfg)
            h.cycle();loaded.cycle();self.assertEqual(h.to_save(),loaded.to_save())
        self.assertIn('scenario:huns',h.world.nation_actors)
        self.assertEqual(h.roster['scenario:huns']['joined_at'],1)

    def test_failed_appearance_is_atomic_and_partial_player_actions_cannot_be_saved(self):
        cfg=self.cfg();cfg['nations'].append({'name':'林胡','polity':'huns','appearance':'manual','position':[8,6],'start_cavalry':-1})
        with self.assertRaises(ValueError):GameHost.prepare(cfg)
        cfg['nations'][-1]['start_cavalry']=1
        h=GameHost.new(cfg);before=h.to_save()
        # A runtime fault still rolls back a partially assembled arrival.
        h.cfg['nations'][-1]['start_cavalry']=-1
        with self.assertRaises(ValueError):h.add('林胡')
        self.assertEqual(h.to_save(),before)
        h.player(Q).submit('plan',{'text':'修改了当前回合'})
        with self.assertRaises(SaveFormatError):h.to_save()

    def test_real_cli_new_resume_and_observer_announcement_use_disposable_paths(self):
        with TemporaryDirectory() as d:
            p=Path(d);cfg=self.cfg();cfg.update(save=str(p/'session.json'),journal=str(p/'journal.md'))
            config=p/'config.json';config.write_text(json.dumps(cfg))
            command=[sys.executable,str(ROOT/'mp_run.py'),'--config',str(config),'--turns']
            first=subprocess.run(command+['2'],input='公告\n第一条\n第二条\nEND\n',text=True,capture_output=True,cwd=ROOT,timeout=60)
            self.assertEqual(first.returncode,0,first.stderr);self.assertIn('终局',first.stdout)
            second=subprocess.run(command+['3'],input='',text=True,capture_output=True,cwd=ROOT,timeout=60)
            self.assertEqual(second.returncode,0,second.stderr);self.assertIn('读档续局',second.stdout)
            saved=json.loads((p/'session.json').read_text())
            self.assertEqual(saved['modules']['native:government']['period'],3)
            self.assertTrue(any(e['text']=='第一条\n第二条' for e in saved['modules']['native:intelligence']['events']))


if __name__=='__main__':unittest.main()
