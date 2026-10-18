"""Public guides, national panels and client memory over synthetic games."""
from copy import deepcopy
import json
from tempfile import TemporaryDirectory
from pathlib import Path
import unittest

from clients import memory
from clients.llm import LLMClient,LLMState
from host import GameHost
from tests import test_player_api as fixtures
from tests.test_world_domains import Q,C
from tests.test_player_clients import FakeBackend,call


class ToolcallParity(unittest.TestCase):
    def host(self,experimental=False):
        cfg={'map_size':12,'seed':73,'nations':[{'id':Q,'name':'秦','client':'llm'}]}
        if experimental:cfg['mods']='mods.experimental.toml'
        return GameHost.new(cfg)

    def test_shared_guides_aliases_and_default_prompt_lifecycle(self):
        for experimental in (False,True):
            h=self.host(experimental);p=h.player(Q);before=h.to_save()
            for topic in ('基础指南','经济学手册','战争手册','战略手册','兵营','战斗','撤退','宣战'):
                sections=p.rules(topic)
                self.assertTrue(sections,topic);self.assertNotEqual(sections[0]['title'],'规则目录',topic)
            basics=p.rules('基础指南')[0]['body']
            self.assertGreater(len(basics),1400);self.assertIn('move',basics)
            self.assertEqual(p.rules('不存在')[0]['title'],'规则目录')
            self.assertEqual(h.to_save(),before)
            if experimental:
                service=h.services[Q]['council']
                self.assertEqual(service._manual(p,'基础指南')['body'],basics)
            ui=h.ui.llm(h.cfg);ui.rules(p.rules('all'));obs=p.observe();ui.state(obs,[])
            self.assertIn('query(panel=',ui.system())
            # 旧版口径：开局指南常驻前 20 回合的 system 前缀，过期后只能按需查。
            self.assertIn(basics,ui.system())
            obs['period']=20;ui.state(obs,[])
            self.assertNotIn(basics,ui.system())
            self.assertTrue(p.rules('经济手册'))

    def test_tool_descriptions_and_input_constraints(self):
        for experimental in (False,True):
            p=self.host(experimental).player(Q)
            schemas={s['function']['name']:s['function'] for s in p.actions()}
            self.assertFalse([n for n,s in schemas.items() if s.get('description')==n])
            # 动作工具按命令收口：模型只看到 umbrella，action 参数选具体动作。
            self.assertIn('oneOf',schemas['build']['parameters'])
            military=schemas['military']['parameters']
            self.assertEqual(military['required'],['action'])
            self.assertEqual(military['properties']['action']['enum'],
                             ['recruit','move','attack','retreat','disband'])
            self.assertEqual({'aid','ids','site','kind','count'}<=set(military['properties']),True)
            relations=schemas['relations']['parameters']
            self.assertIn('vote',relations['properties']['action']['enum'])
            self.assertEqual(relations['properties']['kind']['enum'],['white','pay','demand'])
            self.assertEqual(relations['required'],['action'])
            self.assertEqual({'aid','ids','attack'}<=set(schemas['query']['parameters']['properties']),True)
            # 市场上不再有单独的查价工具：报价就在行情里，且与数量无关。
            self.assertNotIn('market_quote',schemas)
            self.assertEqual(schemas['query']['parameters']['required'],['panel'])
            economy=schemas['economy']['parameters']
            self.assertEqual(economy['properties']['action']['enum'],['buy','sell','borrow','buy_report'])
            self.assertEqual({'resource','quantity','target','turn'}<=set(economy['properties']),True)
            # 外交与情报的散装动作不再暴露给模型；按名调用由其余用例覆盖。
            for name in ('recruit','declare_war','send_letter','buy'):
                self.assertNotIn(name,schemas)

    def test_report_latest_selected_and_all_are_readonly(self):
        w=fixtures.TestPlayerAPI().world();p=w.runtime.player(w,Q)
        for _ in range(20):w.resolve_turn();w.phase='行动'
        before=w.runtime.save_mod_state(w);all_rows=p.query('report',{'all':True})
        self.assertEqual(len(all_rows),2)
        self.assertEqual(p.query('report'),all_rows[-1:])
        self.assertEqual(p.query('report',{'period':9}),all_rows[:1])
        for args in ({'period':3},{'period':9,'all':True},{'period':-1}):
            self.assertFalse(p.submit('report',args)['ok'])
        self.assertEqual(w.runtime.save_mod_state(w),before)
        from mods.ui.console import ConsoleUI
        ui=ConsoleUI({},tool_layer=w.runtime.tool_layer);text=ui.action(20,Q,'report',{},p.submit('report'),{Q:'秦'})
        self.assertIn('GDP增长',text);self.assertIn('第10—19期',text)

    def test_country_threats_battle_privacy_and_batch_atomicity(self):
        w=fixtures.TestPlayerAPI().world();p=w.runtime.player(w,Q)
        w.territory.create('scenario:near',C,2,1,terrain='native:hill')
        w.buildings.seed('scenario:near','native:castle')
        w.military.seed(C,'native:infantry',2,1)
        w.military.seed(C,'native:cavalry',6,6)
        w.military.seed(Q,'native:infantry',1,1,count=3)
        self.assertTrue(next(n for n in p.call_api('native:intelligence','countries') if n['id']==C)['border'])
        threats=p.query('threats');self.assertEqual(len(threats),1)
        self.assertEqual((threats[0]['unit']['owner'],threats[0]['unit']['x']),(C,2))
        own=p.query('army');ids=[u['id'] for u in own if u['owner']==Q]
        before=w.runtime.save_mod_state(w)
        self.assertFalse(p.submit('military', {'action': 'disband', 'ids':[ids[0],999999]})['ok'])
        self.assertEqual(w.runtime.save_mod_state(w),before)
        self.assertTrue(p.submit('relations', {'action': 'declare_war', 'target':C})['ok']);w.resolve_turn();w.phase='行动'
        country=next(n for n in p.call_api('native:intelligence','countries') if n['id']==C)
        self.assertEqual((country['relation'],country['war_representative']),('war',C))
        self.assertTrue(p.submit('military', {'action': 'attack', 'ids':[ids[0]],'x':2,'y':1})['ok'])
        before=w.runtime.save_mod_state(w);battle=p.query('battle')[0]
        defending=next(s for s in battle['sides'] if s['owner']==C)
        self.assertEqual((defending['terrain_reduction'],defending['castle_reduction'],defending['combined_reduction']),(25,10,33))
        self.assertFalse(battle['blind']);self.assertEqual(w.runtime.save_mod_state(w),before)
        self.assertTrue(p.submit('military', {'action': 'disband', 'ids':ids[1:]})['ok'])
        # An owned engaged force beyond building vision reveals only itself.
        w.military.seed(Q,'native:infantry',7,7)
        remote=next(u for u in w.units if u.owner==Q and (u.x,u.y)==(7,7));remote.engaged=True
        w.military.seed(C,'native:cavalry',7,7)
        blind=next(row for row in p.query('battle') if row['position']==[7,7])
        self.assertTrue(blind['blind']);self.assertIsNone(blind['terrain'])
        self.assertEqual([s['owner'] for s in blind['sides']],[Q])
        self.assertIsNone(blind['sides'][0]['combined_reduction'])

    def test_filters_and_peace_note(self):
        w=fixtures.TestPlayerAPI().world();p=w.runtime.player(w,Q)
        rows=p.query('land');self.assertEqual(p.query('land',{'offset':0,'limit':1}),rows[:1])
        self.assertTrue(p.query('land',{'terrain':'plain'}))
        self.assertEqual(p.query('land',{'terrain':'mountain'}),[])
        self.assertEqual(p.query('intel',{'kind':'letter'}),[])
        self.assertFalse(p.submit('intel',{'kind':'hidden'})['ok'])
        p.submit('relations', {'action': 'declare_war', 'target':C});w.resolve_turn();w.phase='行动'
        self.assertTrue(p.submit('relations', {'action': 'offer_peace', 'target':C,'kind':'white','note':'停战建设，互不侵犯。'})['ok'])
        w.resolve_turn();w.phase='行动'
        offer=p.call_api('native:diplomacy','snapshot')['offers'][0]
        self.assertIn(['note','停战建设，互不侵犯。'],offer['arguments'])
        from mods.ui.console import ConsoleUI
        receipt={'ok':True,'status':'effective','code':None,'message':'','action':'diplomacy',
                 'data':p.call_api('native:diplomacy','snapshot')}
        text=ConsoleUI({},tool_layer=w.runtime.tool_layer).action(p.observe()['period'],Q,'diplomacy',{},receipt,{Q:'秦',C:'楚'})
        self.assertIn('提议和平',text);self.assertIn('停战建设，互不侵犯。',text);self.assertIn('战争#',text)
        saved=w.runtime.save_mod_state(w);w.runtime.load_mod_state(w,saved)
        self.assertEqual(p.call_api('native:diplomacy','snapshot')['offers'][0],offer)

    def test_memory_tool_client_scope_validation_and_searchable_history(self):
        h=self.host();p=h.player(Q)
        class Backend(FakeBackend):
            def chat_turn(self,messages,tools,cfg,on_retry=None):
                self.tools=deepcopy(tools)
                return super().chat_turn(messages,tools,cfg,on_retry)
        backend=Backend([{'content':'种田发展农场','reasoning_content':'SECRET_PRIVATE_THOUGHT',
            'tool_calls':[call('p','plan',text='保障粮食生产，建设农场。')]},
            {'tool_calls':[call('m','memory_search',query='农场'),call('e','end_turn',summary='制定粮食国策。')]}])
        client=LLMClient(h.cfg,backend=backend,ui=h.ui)
        h.clients[Q]=client
        receipts=client.play(p)
        self.assertIn('memory_search',{s['function']['name'] for s in backend.tools})
        self.assertIn('memory_search',{s['function']['name'] for s in p.actions()})
        self.assertIn('农场',receipts[1]['data'][0]['context'])
        self.assertNotIn('SECRET_PRIVATE_THOUGHT',json.dumps(client.state.history))
        self.assertNotIn('memory_search',json.dumps(client.state.history))
        for args in ({},{'query':' '},{'query':'农场','limit':True},{'query':'农场','limit':9},{'query':'农场','actor':C}):
            self.assertEqual(client._submit(p,'memory_search',args)['code'],'invalid_arguments')
        fresh=LLMClient(h.cfg,ui=h.ui)
        self.assertEqual(fresh.search_memory('农场'),[])
        self.assertTrue(p.query('memory_search',{'query':'农场'}))
        loaded=LLMState.from_save(client.state.to_save());loaded.replay.clear()
        self.assertTrue(memory.search(loaded.history,'农场'))
        h.clients[Q]=client
        h.cycle()
        with TemporaryDirectory() as temp:
            path=Path(temp)/'synthetic.json';h.save(path);restored=GameHost.load(path,h.cfg)
        self.assertEqual(restored.clients[Q].state.history,client.state.history)

    def test_memory_ranks_all_words_then_recent_and_returns_matching_excerpt(self):
        rows=[{'period':0,'text':'farm '+('x'*1000)+' grain'},
              {'period':5,'text':'only farm'},{'period':3,'text':'farm grain'}]
        matches=memory.search(rows,'farm grain',2)
        self.assertEqual([r['period'] for r in matches],[3,0])
        self.assertIn('grain',matches[1]['context'])

    def test_chinese_console_panels_show_prices_army_counts_and_full_reports(self):
        h=self.host();p=h.player(Q);ui=h.ui.console
        names={Q:'秦'}
        for action in ('market','army','report','battle','threats'):
            args={};receipt=p.submit(action,args)
            text=ui.action(0,Q,action,args,receipt,names)
            self.assertNotIn('→ 已生效',text)
            if action=='market':self.assertIn('／卖',text)
            if action=='army':self.assertNotIn('血量',text)


if __name__=='__main__':unittest.main()
