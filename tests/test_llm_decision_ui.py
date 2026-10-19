"""Player query boundary and model state usability, without network calls."""
from copy import deepcopy
from clients.model_view import model_view_for
import json
import unittest

from host import GameHost
from clients.llm import LLMClient,assert_paired
from tests.test_player_clients import FakeBackend,call
from tests import test_player_api as fixtures
from tests.test_world_domains import Q,C
from ruleai.v11plus import combat


class TestDecisionUI(unittest.TestCase):
    def host(self,experimental=False):
        cfg={'map_size':12,'seed':73,'nations':[{'id':Q,'name':'秦','client':'llm'}]}
        if experimental:cfg['mods']='mods.experimental.toml'
        return GameHost.new(cfg)

    def test_only_single_tile_query_accepts_coordinates_in_both_distributions(self):
        for experimental in (False,True):
            h=self.host(experimental)
            tools=h.runtime.tools
            spatial={name for name,tool in tools.items() if tool.read_only and not tool.hidden and
                     {'x','y','site','tile','position','at'} & set(tool.schema()['function']['parameters']['properties'])}
            self.assertEqual(spatial,{'query'})
            p=h.player(Q)
            panels=next(s['function']['parameters']['properties']['panel']['enum'] for s in p.actions() if s['function']['name']=='query')
            self.assertIn('tile',panels);self.assertIn('land',panels);self.assertNotIn('building_sites',panels)
            self.assertTrue({'build_quote','recruit_quote','battle_estimate'}.isdisjoint(tools))
            before=h.to_save();p.query('building_catalog');p.query('building_economics');p.query('terrain_catalog')
            self.assertEqual(h.to_save(),before)

    def test_default_state_has_catalogs_prices_shortfalls_and_plan_purpose(self):
        for experimental in (False,True):
            h=self.host(experimental);p=h.player(Q);obs=p.observe();before=deepcopy(obs)
            ui=model_view_for(h.ui, h.cfg);ui.actions(p.actions());tail,_=ui.state(obs,[])
            self.assertEqual(obs,before)
            # 目录与规则不进常驻载荷，按需查询。
            self.assertNotIn('costs_by_level',ui.system())
            self.assertIn('未来',next(s['function']['description'] for s in p.actions() if s['function']['name']=='plan'))
            self.assertIn('一两句',ui.system())
            self.assertIn('【国策规划】',tail);self.assertIn('尚无国策',tail)
            self.assertIn('【军队】',tail);self.assertIn('【世界市场】',tail)
            self.assertTrue(p.submit('economy', {'action': 'buy', 'resource':'wood','quantity':2})['ok'])
            tail,_=ui.state(p.observe(),[])
            stocks={s['id']:s['amount'] for s in p.observe()['facts']['econ']['stocks']}
            self.assertIn(f"{stocks['native:wood']}木头",tail)
            # 批次之间不再追加上下文：模型靠自己的对话历史与回执。
            self.assertFalse(hasattr(ui,'refresh'))

    def test_llm_receives_fresh_inventory_from_the_receipt_without_extra_queries(self):
        h=self.host();p=h.player(Q)
        backend=FakeBackend([
            {'tool_calls':[call('b','economy',action='buy',resource='wood',quantity=2)]},
            {'tool_calls':[call('p','plan',text='经济：保障粮木和能源；军事：集中守边；情报：依据公开事实；外交：维持和平，库存不足时放缓扩张。'),call('e','end_turn',summary='购入木材，完成国策。')]}])
        client=LLMClient(h.cfg,backend=backend,ui=h.ui);client.play(p)
        latest=backend.requests[1][-1]
        # 回执自己带「余N」：模型看完这一单就知道还剩多少；批次之间不再追加刷新消息。
        self.assertEqual(latest['role'],'tool')
        self.assertIn('购入 木头×2',latest['content'])
        self.assertIn('余202',latest['content'])
        self.assertNotIn('costs_by_level',latest['content'])
        self.assertIn('query(panel=',backend.requests[0][0]['content'])
        assert_paired(client.state.replay[0]['messages'])

    def test_player_state_is_hidden_and_stale_calls_are_rejected_without_querying(self):
        for experimental in (False,True):
            h=self.host(experimental);p=h.player(Q)
            self.assertIn('player_state',{s['function']['name'] for s in p.actions()})
            backend=FakeBackend([{'tool_calls':[call('s','player_state')]},
                {'tool_calls':[call('p','plan',text='经营经济并保持独立的国策'),
                               call('e','end_turn',summary='制定有效国策并结束行动')]}])
            client=LLMClient(h.cfg,backend=backend,ui=h.ui);receipts=client.play(p)
            self.assertFalse(receipts[0]['ok']);self.assertEqual(receipts[0]['code'],'tool_not_exposed')
            self.assertIsNone(receipts[0]['data'])
            names={s['function']['name'] for s in client.ui.actions(p.actions())}
            self.assertNotIn('player_state',names);self.assertNotIn('player_events',names)
            self.assertNotIn('government',names);self.assertIn('query',names)
            self.assertNotIn('player_state',client.ui.system())
            self.assertIn('player_events',{s['function']['name'] for s in p.actions()})
            self.assertFalse(any(row['action']=='player_state' for row in h.audit))
            # Automatic facts and subsequent patches still reach the model.
            first=backend.requests[0][-1]['content']
            self.assertIn('【国力】',first);self.assertIn('【国策规划】',first)
            self.assertTrue(p.query('player_state')['facts'])
            assert_paired(client.state.replay[0]['messages'])

    def test_government_model_receipt_omits_history_without_mutating_api_or_save(self):
        for experimental in (False,True):
            h=self.host(experimental);p=h.player(Q)
            self.assertTrue(p.submit('plan',{'text':'持续经营并维持和平的有效国策'})['ok'])
            self.assertTrue(p.submit('end_turn',{'summary':'UNIQUE-HISTORY-小结应由客户端记忆保管'})['ok'])
            receipt=p.submit('government');before=deepcopy(receipt);saved=h.runtime.save_mod_state(h.world)
            self.assertTrue(receipt['data']['summaries'])
            ui=model_view_for(h.ui, h.cfg);presented=ui.receipt(receipt,{})
            self.assertNotIn('UNIQUE-HISTORY',presented)
            self.assertIn('持续经营并维持和平的有效国策',presented)
            self.assertEqual(receipt,before);self.assertEqual(h.runtime.save_mod_state(h.world),saved)

    def test_tile_fog_and_enemy_privacy_and_client_forecast_match_domain(self):
        w=fixtures.TestPlayerAPI().world();p=w.runtime.player(w,Q)
        w.territory.create('scenario:near',C,2,1,terrain='native:plain')
        w.buildings.seed('scenario:near','native:castle');w.buildings.seed('scenario:near','native:farm')
        w.military.seed(C,'native:infantry',2,1)
        w.military.seed(Q,'native:infantry',1,1,count=3)
        before=w.runtime.save_mod_state(w)
        foreign=p.query('tile',{'x':2,'y':1})
        self.assertNotIn('resources',foreign);self.assertNotIn('capacity',foreign)
        self.assertEqual({b['kind'] for b in foreign['buildings']},{'native:castle'})
        with self.assertRaisesRegex(ValueError,'迷雾'):p.query('tile',{'x':6,'y':6})
        p.submit('relations', {'action': 'declare_war', 'target':C});w.resolve_turn();w.phase='行动'
        obs=p.observe();own=[u for u in obs['facts']['army'] if u['owner']==Q]
        before=w.runtime.save_mod_state(w);forecast=combat.forecaster(p,obs)
        for n in (1,2,3):
            expected=w.military.battle_estimate(Q,[u['id'] for u in own[:n]],2,1)
            self.assertEqual(forecast(p,(2,1),own[:n]),expected)
        self.assertFalse(forecast(p,(6,6),own)['known'])
        self.assertEqual(w.runtime.save_mod_state(w),before)


if __name__=='__main__':unittest.main()
