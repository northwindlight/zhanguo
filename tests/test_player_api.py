"""The same actor-bound contract for all clients, with domain privacy and quotes."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from tests import test_world_domains as fixtures
Q,C,Z,Y=fixtures.Q,fixtures.C,fixtures.Z,fixtures.Y
from engine.player import API_VERSION
from engine.runtime import Runtime,ModError


class TestPlayerAPI(unittest.TestCase):
    def world(self):
        t=fixtures.TestWorldDomains();w=t.world();t.site(w,'scenario:qc',Q,1,1);t.site(w,'scenario:cc',C,6,6)
        return w

    def test_observation_detached_private_fields_filtered_and_schema_matches_actor(self):
        w=self.world();p=w.runtime.player(w,Q)
        before=json.dumps(w.runtime.save_mod_state(w),sort_keys=True)
        obs=p.observe();self.assertEqual(obs['api'],API_VERSION);self.assertEqual(obs['actor'],Q)
        self.assertNotIn(C,{s['owner'] for s in obs['facts']['building_sites']['sites']})
        self.assertNotIn([6,6],[t['position'] for t in obs['facts']['view']])
        w.military.seed(C,'native:infantry',6,6)
        self.assertFalse(any(u['owner']==C for u in p.observe()['facts']['army']))
        obs['facts']['econ']['stocks'][0]['amount']=999999
        self.assertNotEqual(p.observe()['facts']['econ']['stocks'][0]['amount'],999999)
        self.assertEqual(len(p.actions()),13)
        self.assertTrue({'player_state','player_rules','player_events','military','economy','relations','intelligence'}
                        <={s['function']['name'] for s in p.actions()})
        self.assertFalse(any('_'==key[:1] for u in p.query('army') for key in u))

    def test_bound_actor_cannot_forge_identity_query_mutation_or_read_other_inventory(self):
        w=self.world();p=w.runtime.player(w,Q);before=w.runtime.save_mod_state(w)
        for action,args in [('buy',{'actor':C,'resource':'native:wood','quantity':1}),('econ',{'owner':C}),('_stock',{}),('build',{'site':'scenario:cc','kind':'native:farm'})]:
            self.assertFalse(p.submit(action,args)['ok'])
        self.assertEqual(w.runtime.save_mod_state(w),before)
        with self.assertRaises(ValueError):p.query('buy',{'resource':'native:wood','quantity':1})
        with self.assertRaises(AttributeError):p.actor=C
        for name,args in [('player_state',{'actor':C}),('player_events',{'cursor':-1}),('player_rules',{'topic':[]})]:
            self.assertEqual(p.submit(name,args)['code'],'invalid_arguments')

    def test_end_gate_and_retirement_have_structured_rejections(self):
        w=self.world();p=w.runtime.player(w,Q)
        self.assertTrue(p.submit('plan',{'text':'发展经济'})['ok'])
        self.assertTrue(p.submit('end_turn',{'summary':'本期已经完成'})['ok'])
        receipt=p.submit('economy', {'action': 'buy', 'resource':'native:wood','quantity':1})
        self.assertEqual(receipt['code'],'unavailable_action');self.assertFalse(p.observe()['can_act'])
        self.assertNotIn('buy',{s['function']['name'] for s in p.actions()})
        self.assertTrue(p.submit('player_state')['ok'])
        w.nations.extinguish(Y,'synthetic')
        with self.assertRaises(ValueError):w.runtime.player(w,Y)
        retired=w.runtime.player(w,Z);w.nations.extinguish(Z,'synthetic')
        self.assertEqual(retired.submit('plan',{'text':'继续'})['code'],'inactive_actor')

    def test_quotes_are_readonly_exact_and_reject_foreign_sites(self):
        w=self.world();p=w.runtime.player(w,Q);before=w.runtime.save_mod_state(w)
        farm=next(k for k in p.query('building_catalog') if k['id']=='native:farm')
        costs=farm['costs_by_level'][0]
        self.assertEqual(costs,{'native:gold':20,'native:wood':15})
        self.assertEqual(w.runtime.save_mod_state(w),before)
        result=p.submit('build',{'site':'scenario:qc','kind':'native:farm'})
        self.assertEqual(dict(result['data']['costs']),costs)
        self.assertEqual(p.query('econ')['inventory']['total'],p.query('econ')['inventory']['known'])
        self.assertFalse(p.submit('build',{'site':'scenario:cc','kind':'native:farm'})['ok'])

    def test_policy_quotes_schema_and_scope_are_identical(self):
        w=self.world();w.runtime.activate_actor(w,Q,'native:huns',site='scenario:qc')
        p=w.runtime.player(w,Q)
        self.assertNotIn('bloc_found',{s['function']['name'] for s in p.actions()})
        self.assertEqual(p.query('unit_catalog')['native:cavalry']['recruit'],{'native:food':8,'native:equipment':8})
        w.resources.credit(Q,'native:wood',15)
        self.assertEqual(dict(p.submit('build',{'site':'scenario:qc','kind':'native:farm'})['data']['costs'])['native:gold'],26)
        self.assertFalse(p.submit('relations', {'action': 'bloc_found', 'bloc':'scenario:b','name':'盟','tos':[C]})['ok'])
        self.assertFalse(any(s['title']=='匈奴政体' for s in w.runtime.player(w,C).rules()))

    def test_pending_receipts_and_event_cursor_do_not_confuse_execution(self):
        w=self.world();p=w.runtime.player(w,Q)
        result=p.submit('relations', {'action': 'guarantee', 'target':C})
        self.assertEqual(result['status'],'submitted');self.assertEqual(p.call_api('native:diplomacy','snapshot')['pacts'],[])
        w.resolve_turn();w.phase='行动'
        self.assertEqual(len(p.call_api('native:diplomacy','snapshot')['pacts']),1)
        w.intelligence.emit('private','仅楚',parties=(C,))
        visible=p.events();self.assertNotIn('仅楚',[e['text'] for e in visible['items']])
        self.assertEqual(p.events(visible['cursor'])['items'],[])
        with self.assertRaises(ValueError):p.events(-1)

    def test_visible_path_and_forecast_queries_cannot_peek_outside_fog(self):
        w=self.world();a=w.military.seed(Q,'native:infantry',1,1)[0];p=w.runtime.player(w,Q)
        saved=w.runtime.save_mod_state(w)
        with self.assertRaisesRegex(ValueError,'迷雾'):p.query('tile',{'x':6,'y':6})
        self.assertNotIn([6,6],[point for point,_ in p.query('reachable',{'id':a.id,'attack':True})])
        self.assertEqual(w.runtime.save_mod_state(w),saved)

    def test_internal_runtime_faults_void_the_action_without_faking_a_rejection(self):
        """模块内部异常只作废这一次动作：回执必须说清楚是 internal_fault，
        不能伪装成「参数错」，也不能把整场看海带走（停机权只属于结算本身）。"""
        w=self.world();seen=[];p=w.runtime.player(w,Q,on_fault=lambda action,exc,detail:seen.append((action,exc,detail)))
        with patch.object(w.runtime,'call',side_effect=RuntimeError('broken invariant')):
            receipt=p.submit('plan',{'text':'发展'})
        self.assertFalse(receipt['ok']);self.assertEqual(receipt['code'],'internal_fault')
        self.assertEqual(receipt['status'],'failed');self.assertIsNone(receipt['data'])
        self.assertEqual(seen[0][0],'plan');self.assertIn('broken invariant',seen[0][2])
        self.assertIn('RuntimeError',seen[0][2])


if __name__=='__main__':unittest.main()
