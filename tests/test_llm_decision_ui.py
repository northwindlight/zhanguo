"""Player query boundary and model state usability, without network calls."""
from copy import deepcopy
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
            spatial={name for name,tool in tools.items() if tool.read_only and
                     {'x','y','site','tile','position','at'} & set(tool.schema()['function']['parameters']['properties'])}
            self.assertEqual(spatial,{'tile'})
            self.assertTrue({'build_quote','recruit_quote','battle_estimate'}.isdisjoint(tools))
            p=h.player(Q);before=h.to_save();p.query('building_catalog');p.query('building_economics');p.query('terrain_catalog')
            self.assertEqual(h.to_save(),before)

    def test_default_state_has_catalogs_prices_shortfalls_and_plan_purpose(self):
        for experimental in (False,True):
            h=self.host(experimental);p=h.player(Q);obs=p.observe();before=deepcopy(obs)
            ui=h.ui.llm(h.cfg);ui.actions(p.actions());tail,_=ui.state(obs,[]);state=json.loads(tail)
            self.assertEqual(obs,before)
            self.assertNotIn('building_catalog',state['state']['facts'])
            self.assertNotIn('costs_by_level',ui.system());self.assertIn('未来',next(s['function']['description'] for s in p.actions() if s['function']['name']=='plan'))
            self.assertIn('一两句',ui.system())
            self.assertEqual(state['state']['decision']['plan_status'],'须提交国策')
            self.assertIn('stock_shortfalls_before_production',state['state']['decision'])
            self.assertIn('army_actions',state['state']['decision']);self.assertIn('diplomacy_todo',state['state']['decision'])
            market=state['state']['facts']['market'];self.assertTrue(market['buy_prices']);self.assertTrue(market['sell_prices'])
            self.assertIsNone(ui.refresh(obs))
            self.assertTrue(p.submit('buy',{'resource':'wood','quantity':2})['ok'])
            delta=json.loads(ui.refresh(p.observe()).split('：',1)[1]);self.assertTrue(any(row['path'][:2]==['facts','econ'] for row in delta))
            self.assertFalse(any(row['path'][:2]==['facts','view'] for row in delta))

    def test_llm_receives_fresh_inventory_after_actions_without_extra_queries(self):
        h=self.host();p=h.player(Q)
        backend=FakeBackend([
            {'tool_calls':[call('b','buy',resource='wood',quantity=2)]},
            {'tool_calls':[call('p','plan',text='经济：保障粮木和能源；军事：集中守边；情报：依据公开事实；外交：维持和平，库存不足时放缓扩张。'),call('e','end_turn',summary='购入木材，完成国策。')]}])
        client=LLMClient(h.cfg,backend=backend,ui=h.ui);client.play(p)
        latest=backend.requests[1][-1]
        self.assertEqual(latest['role'],'user');self.assertIn('状态差分',latest['content'])
        patches=json.loads(latest['content'].split('：',1)[1])
        stock=next(row['set'] for row in patches if row['path']==['facts','econ','stocks','木头'])
        self.assertEqual(stock,62)
        self.assertNotIn('costs_by_level',latest['content'])
        self.assertIn('规则按需',backend.requests[0][0]['content'])
        assert_paired(client.state.replay[0]['messages'])

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
        p.submit('declare_war',{'target':C});w.resolve_turn();w.phase='行动'
        obs=p.observe();own=[u for u in obs['facts']['army'] if u['owner']==Q]
        before=w.runtime.save_mod_state(w);forecast=combat.forecaster(p,obs)
        for n in (1,2,3):
            expected=w.military.battle_estimate(Q,[u['id'] for u in own[:n]],2,1)
            self.assertEqual(forecast(p,(2,1),own[:n]),expected)
        self.assertFalse(forecast(p,(6,6),own)['known'])
        self.assertEqual(w.runtime.save_mod_state(w),before)


if __name__=='__main__':unittest.main()
