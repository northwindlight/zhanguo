"""Saved scripts on synthetic worlds, standard API review, and boundary saves."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from tests.test_parliament import CouncilBackend, FakeBackend, call, reply, start_with_plan


class TestScripts(unittest.TestCase):
    actor='scenario:han'
    def cfg(self,kind='rule'):
        return {'mods':'mods.experimental.toml','map_size':12,'seed':73,
                'nations':[{'id':self.actor,'name':'韩','client':kind,'model':'synthetic',
                            'api_key':'synthetic-secret','ctx_compact':'off'}]}
    def host(self,decisions=(True,),kind='rule'):
        h=GameHost.new(self.cfg(kind))
        h.services[self.actor]['council']._review_backend=CouncilBackend(list(decisions))
        return h,h.player(self.actor)
    def write(self,p,source):
        result=p.submit('script_write',{'source':source})
        self.assertTrue(result['ok'],result)
    def next_period(self,h,p):
        if p.observe()['can_act']:
            if not p.observe()['facts']['government']['plan']:
                p.submit('plan',{'text':'保持生产并记录本期真实行为'})
            p.submit('end_turn',{'summary':'本期工作完成，下一期继续检查'})
        h.cycle()
        return h.player(self.actor)

    def test_free_check_never_executes_or_consumes_slot_and_source_patch_is_exact(self):
        h,p=self.host()
        self.write(p,'api.call("economy:queries","buy", resource="native:food", quantity=2)')
        saved=h.runtime.save_mod_state(h.world)
        for _ in range(3):self.assertTrue(p.query('script_check')['valid'])
        self.assertTrue(p.query('script_check',{'source':'print(1 / 0)'})['valid'])
        self.assertFalse(p.query('script_check',{'source':'import socket'})['valid'])
        self.assertEqual(h.runtime.save_mod_state(h.world),saved)
        self.assertTrue(p.query('script_status')['available'])
        self.assertNotIn('source',p.query('script_status'))
        self.assertTrue(p.submit('script_patch',{'old':'quantity=2','new':'quantity=3'})['ok'])
        self.assertIn('quantity=3',p.query('script_status',{'include_source':True})['source'])
        before=p.query('script_status',{'include_source':True})
        self.assertFalse(p.submit('script_patch',{'old':'absent','new':'x'})['ok'])
        self.assertEqual(p.query('script_status',{'include_source':True}),before)

    def test_success_and_every_inner_action_audited_with_compact_return(self):
        h,p=self.host()
        self.write(p,'api.call("economy:queries","buy", resource="native:food", quantity=2)\napi.call("economy:queries","sell", resource="native:food", quantity=1)\nprint("采购完成")')
        before=h.world.resources.balance(self.actor,'native:food')
        result=p.submit('script_run')
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['data']['output'],'采购完成\n')
        self.assertEqual(result['data']['actions'],2)
        self.assertNotIn('receipts',result['data'])
        self.assertEqual(h.world.resources.balance(self.actor,'native:food'),before+1)
        self.assertEqual([r['action'] for r in h.audit],['script_write','buy','sell','script_run'])
        self.assertFalse(p.submit('script_run')['ok'])
        self.assertEqual(result['data'],p.query('script_status')['result'])

    def test_failed_script_keeps_prior_action_and_rewrite_does_not_allow_retry(self):
        h,p=self.host()
        self.write(p,'api.call("economy:queries","buy", resource="native:food", quantity=2)\nx = 1 / 0')
        before=h.world.resources.balance(self.actor,'native:food')
        result=p.submit('script_run')
        self.assertFalse(result['ok'])
        self.assertEqual(result['data']['error']['type'],'ZeroDivisionError')
        self.assertEqual(result['data']['error']['message'],'division by zero')
        self.assertEqual(result['data']['error']['line'],2)
        self.assertEqual(h.world.resources.balance(self.actor,'native:food'),before+2)
        self.write(p,'print("修好了")')
        self.assertFalse(p.submit('script_run')['ok'])
        p=self.next_period(h,p)
        self.assertTrue(p.submit('script_run')['ok'])

    def test_syntax_error_computation_timeout_and_domain_failure_consume_allowance(self):
        for source,error in (('if :','SyntaxError'),('while True:\n    pass','SandboxLimitError'),
                             ('api.call("economy:queries","buy", resource="native:food", quantity=99999)','SandboxActionError')):
            with self.subTest(source=source):
                h,p=self.host();self.write(p,source)
                result=p.submit('script_run')
                self.assertFalse(result['ok'])
                self.assertEqual(result['data']['error']['type'],error)
                self.assertFalse(p.query('script_status')['available'])
                self.assertFalse(p.submit('script_run')['ok'])
                if error=='SandboxActionError':
                    failed=result['data']['failed_action']
                    self.assertEqual(failed['message'],result['data']['error']['message'])
                    self.assertEqual(next(r['receipt'] for r in h.audit if r['action']=='buy'),failed)

    def test_nested_script_controls_and_foreign_identity_are_rejected(self):
        for source in ('api.call("sandbox:programs","claim")','api.call("sandbox:programs","write", source="pass")',
                       'api.call("sandbox:programs","check", source="pass")',
                       'api.call(1)', 'api.call([])',
                       'api.call("economy:queries","buy", actor="scenario:qin", resource="native:food", quantity=1)'):
            h,p=self.host();self.write(p,source)
            result=p.submit('script_run')
            self.assertFalse(result['ok'])
            self.assertFalse(p.query('script_status')['available'])

    def test_source_and_attempts_belong_to_country_not_global_or_controller(self):
        cfg=self.cfg();cfg['nations'].append({'id':'scenario:qin','name':'秦','client':'rule'})
        h=GameHost.new(cfg)
        for actor in h.actors():h.services[actor]['council']._review_backend=CouncilBackend([True])
        a,b=h.player(self.actor),h.player('scenario:qin')
        self.write(a,'print("韩")');self.write(b,'print("秦")')
        self.assertEqual(a.submit('script_run')['data']['output'],'韩\n')
        self.assertEqual(b.submit('script_run')['data']['output'],'秦\n')
        h.invalidate(self.actor,{'prompt':'replacement'})
        self.assertFalse(h.player(self.actor).submit('script_run')['ok'])
        self.assertEqual(b.query('script_status',{'include_source':True})['source'],'print("秦")')

    def test_script_plan_uses_same_council_and_end_turn_stops_llm_normally(self):
        h,p=self.host(kind='llm')
        source='api.call("native:government","plan", text="按真实库存发展生产")\napi.call("native:government","end_turn", summary="已提交国策并结束本期行动")'
        h.clients[self.actor]._backend=FakeBackend([reply(call('s','script',action='write',source=source),call('r','script',action='run'))])
        results=h.cycle()
        self.assertTrue(results[-1]['ok'],results)
        self.assertEqual(len(h.services[self.actor]['council']._review_backend.requests),0)
        self.assertEqual([r['action'] for r in h.audit],['script','plan','end_turn','script'])
        self.assertEqual(h.clock(),1)
        # 第二回合换一份新国策（同一份正文不再重复送审，见 plan 幂等规则）。
        changed=source.replace('按真实库存发展生产','按真实库存发展生产并补兵营')
        h.player(self.actor).submit('script_write',{'source':changed})
        h.clients[self.actor]._backend=FakeBackend([reply(call('r2','script',action='run'))])
        results=h.cycle()
        self.assertTrue(results[-1]['ok'],results)
        self.assertEqual(len(h.services[self.actor]['council']._review_backend.requests),2)

    def test_third_rejection_stops_old_script_and_replacement_has_no_extra_attempt(self):
        h,p=self.host(decisions=(False,False,False))
        start_with_plan(h,self.actor);p=h.player(self.actor)
        for i in range(2):
            self.assertFalse(p.submit('plan',{'text':'需要修正的国策'})['ok'])
            p.submit('explain_plan',{'text':'说明原因，下一期会重新核实并修订'})
            p.submit('end_turn',{'summary':'完成说明，等待下回合修订国策'})
            h.cycle();p=h.player(self.actor)
        self.write(p,'api.call("native:government","plan", text="第三份需要审核的国策")\napi.call("economy:queries","buy", resource="native:food", quantity=1)')
        before=h.world.resources.balance(self.actor,'native:food')
        result=p.submit('script_run')
        self.assertFalse(result['ok'])
        self.assertFalse(p.action_state()['active'])
        self.assertEqual(h.world.resources.balance(self.actor,'native:food'),before)
        self.assertFalse(h.player(self.actor).submit('script_run')['ok'])
        self.assertEqual(result['data']['failed_action']['code'],'plan_not_approved')

    def test_boundary_save_restores_code_attempts_and_results_without_credentials(self):
        h,p=self.host();self.write(p,'print(inputs.get("limit", 3))')
        p.submit('script_run',{'inputs':{'limit':5}})
        p=self.next_period(h,p)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.json';h.save(path)
            self.assertNotIn('synthetic-secret',path.read_text())
            loaded=GameHost.load(path,self.cfg())
            loaded.services[self.actor]['council']._review_backend=CouncilBackend([True])
            other=loaded.player(self.actor)
            self.assertEqual(other.query('script_status',{'include_source':True}),p.query('script_status',{'include_source':True}))
            a=p.submit('script_run',{'inputs':{'limit':7}})
            b=other.submit('script_run',{'inputs':{'limit':7}})
            self.assertEqual(a,b)
            self.assertEqual(h.runtime.save_mod_state(h.world),loaded.runtime.save_mod_state(loaded.world))
        before=h.world.scripts.to_save();bad=deepcopy(before);bad[self.actor]['last_period']=-1
        with self.assertRaises(ValueError):h.world.scripts.restore(bad)
        self.assertEqual(h.world.scripts.to_save(),before)

    def test_oversized_inputs_are_execution_failure_even_if_script_does_not_use_them(self):
        for inputs,error in (({'large':'x'*70000},'SandboxLimitError'),({'number':float('nan')},'OverflowError')):
            h,p=self.host();self.write(p,'pass')
            result=p.submit('script_run',{'inputs':inputs})
            self.assertFalse(result['ok'])
            self.assertEqual(result['data']['error']['type'],error)
            self.assertFalse(p.query('script_status')['available'])

    def test_saved_script_never_runs_automatically(self):
        h,p=self.host(decisions=(True,True));self.write(p,'print("只允许手动运行")')
        h.cycle();h.cycle()
        status=h.player(self.actor).query('script_status')
        self.assertIsNone(status['last_period'])
        self.assertIsNone(status['result'])

    def test_shipped_examples_make_bounded_decisions_and_calculations(self):
        folder=Path(__file__).resolve().parents[1]/'mods/experimental/examples'
        h,p=self.host();self.write(p,(folder/'logistics.py').read_text())
        result=p.submit('script_run',{'inputs':{'food_target':24,'budget':20}})
        self.assertTrue(result['ok'],result)
        self.assertEqual(h.world.resources.balance(self.actor,'native:food'),24)
        self.assertEqual(result['data']['actions'],1)
        h,p=self.host();self.write(p,(folder/'market_interpolation.py').read_text())
        result=p.submit('script_run',{'inputs':{'history':[[0,1.5],[2,1.7],[4,1.8]],'target':3}})
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['data']['output'],'线性估计 1.75\n')


if __name__=='__main__':unittest.main()
