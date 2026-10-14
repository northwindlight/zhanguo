"""Observer and compact action contracts, using synthetic games and fake models."""
from copy import deepcopy
import contextlib
import io
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch
from host import GameHost
from engine.content import ContentRegistry
from clients.llm import LLMClient
from tests.test_player_clients import FakeBackend,call
from mp_run import Observer,command


class TestObserver(unittest.TestCase):
    def cfg(self,experimental=False):
        cfg={'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦'},{'id':'s:c','name':'楚'}]}
        if experimental:cfg['mods']='mods.experimental.toml'
        return cfg

    def test_world_observer_is_detached_and_never_exposed_to_players(self):
        for experimental in (False,True):
            h=GameHost.new(self.cfg(experimental));before=h.to_save();s=h.observer_snapshot()
            self.assertEqual(len(s['map']['facts']['view']),144)
            self.assertLess(len(h.player('s:q').observe()['facts']['view']),144)
            self.assertEqual(before,h.to_save())
            self.assertFalse(h.player('s:q').submit('observer_snapshot',{})['ok'])
            text=h.ui.console.overview(s);self.assertIn('世界市场',text);self.assertIn('补给',text)
            self.assertIn('A=秦',h.ui.observer_map(s));s['map']['facts']['view'].clear()
            self.assertEqual(len(h.observer_snapshot()['map']['facts']['view']),144)

    def test_add_subsidy_rate_and_resume_without_changing_source_config(self):
        for experimental in (False,True):
            cfg=self.cfg(experimental);h=GameHost.new(cfg);original=deepcopy(cfg)
            actor=h.add('齐');h.subsidize(actor,{'native:gold':77},2);h.set_rate(.05)
            self.assertEqual(len(h.world.military.snapshot(actor)),2)
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/'save.json';h.save(p);restored=GameHost.load(p,cfg)
                self.assertEqual(restored.to_save(),h.to_save())
                self.assertEqual(restored.world.bank.rate,.05)
                restored.cycle()
            self.assertEqual(cfg,original)
            before=h.to_save()
            with self.assertRaises(ValueError):h.subsidize(actor,{'native:food':-1},0)
            self.assertEqual(before,h.to_save())

    def test_console_counts_each_army_kind_without_total_hp(self):
        h=GameHost.new(self.cfg());s=h.observer_snapshot()
        s['nations'][0]['facts']['army']=[
            {'owner':'s:q','kind':'native:infantry','hp':999},
            {'owner':'s:q','kind':'native:infantry','hp':777},
            {'owner':'s:q','kind':'native:cavalry','hp':123},
            {'owner':'s:c','kind':'native:cavalry','hp':888}]
        text=h.ui.console.overview(s)
        self.assertIn('步兵 2',text);self.assertIn('骑兵 1',text)
        self.assertNotIn('999',text);self.assertNotIn('1899',text)

    def test_receipts_show_tile_names_coordinates_and_army_kinds(self):
        h=GameHost.new(self.cfg());ui=h.ui.console;observation=h.player('s:q').observe();ui.remember(observation)
        facts=observation['facts'];tile=facts['land'][0]['tile'];x,y=tile['position'];name=tile['name']
        receipt={'ok':True,'status':'effective','message':'','data':{'site':facts['building_sites']['sites'][0]['id'],
            'kind':'native:lumber-camp','costs':{'native:gold':20,'native:wood':15}}}
        text=ui.action(0,'s:q','build',{'kind':'lumber-camp','site':'所选地块'},receipt,{'s:q':'秦'})
        self.assertIn(f'建造 林场 所选地块 {name}({x}, {y})',text)
        self.assertIn(f'动工 林场（{name}({x}, {y})',text)
        position=(x+1,y)
        ui.remember({'actor':'s:q','facts':{'view':[{'position':position,'name':'试验地'}],
            'army':[{'owner':'s:q','id':1,'kind':'native:infantry'},{'owner':'s:q','id':2,'kind':'native:militia'}]}})
        attack={'ok':True,'status':'effective','message':'','data':{'units':[1,2],'engaged':True}}
        text=ui.action(0,'s:q','attack',{'ids':[1,2],'place':'试验地'},attack,{'s:q':'秦'})
        self.assertIn('步兵#1、民兵#2 → 试验地('+str(position[0])+', '+str(position[1])+')',text)

    def test_overview_shows_only_production_and_military_flow_deltas(self):
        h=GameHost.new(self.cfg());snapshot=h.observer_snapshot();actor='s:q'
        snapshot['resource_flows']={'period':0,'owners':{actor:{'native:gold':10,'native:ore':-3}}}
        text=h.ui.console.overview(snapshot)
        self.assertIn('\033[32m+10',text);self.assertIn('\033[31m-3',text)
        self.assertNotIn('(+10)',text);self.assertNotIn('(-3)',text)
        self.assertIn('不含市场交易和赠礼',text)

    def test_overview_omits_policy_but_submission_receipt_keeps_it(self):
        h=GameHost.new(self.cfg());p=h.player('s:q');policy='保障粮木与能源，集中军队守卫边境。'
        receipt=p.submit('plan',{'text':policy})
        self.assertTrue(receipt['ok'])
        for _ in range(2):
            text=h.ui.console.overview(h.observer_snapshot())
            self.assertNotIn(policy,text);self.assertNotIn('国策：',text)
            self.assertIn('世界市场',text);self.assertIn('秦',text)
        action=h.ui.console.action(0,'s:q','plan',{'text':policy},receipt,{'s:q':'秦'})
        self.assertIn(policy,action)

    def test_commands_process_after_calls_and_quit_waits_for_complete_save(self):
        cfg=self.cfg();cfg['nations']=cfg['nations'][:1];cfg['nations'][0]['client']='llm'
        h=GameHost.new(cfg)
        h.clients['s:q']._backend=FakeBackend([
            {'content':'中文发言','reasoning_content':'完整思考正文'*80,'tool_calls':[call('q','player_rules')]},
            {'content':'','tool_calls':[call('p','plan',text='经营'),call('e','end_turn',summary='本期已完成全部经营行动')]}])
        class Console:
            panel_on=False
            def __init__(self):self.queue=queue.Queue();self.lines=[]
            def write(self,text):self.lines.append(text)
            def clear_panel(self):pass
        console=Console();console.queue.put('say 中途公告');console.queue.put('board');console.queue.put('quit')
        with tempfile.TemporaryDirectory() as d,patch('mp_run.CONSOLE',console):
            obs=Observer(h,Path(d)/'journal.md');obs.show_thinking=True
            h.cycle(on_result=obs.result,emit=obs.model_output,on_call=obs.on_call)
            self.assertTrue(obs.stop_requested);self.assertEqual(h.clock(),1)
            self.assertIn('完整思考正文'*80,'\n'.join(console.lines))
            self.assertIn('查询规则','\n'.join(console.lines))
            self.assertTrue(any(e['text']=='中途公告' for e in h.records()['events']))
            self.assertNotIn('scenario:','\n'.join(console.lines))
            h.save(Path(d)/'save.json')

    def test_full_map_and_private_map_export(self):
        h=GameHost.new(self.cfg())
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()):
            obs=Observer(h,Path(d)/'journal.md')
            for suffix in ('txt','svg','png'):
                p=Path(d)/('world.'+suffix);self.assertTrue(command(h,f'map {p}',obs));self.assertGreater(p.stat().st_size,20)
            p=Path(d)/'qin.txt';command(h,f'map 秦 {p}',obs);self.assertTrue(p.exists())
            self.assertTrue(command(h,'利率 5',obs));self.assertEqual(h.world.bank.rate,.05)
            self.assertTrue(command(h,'补助 秦 金10 骑1',obs));self.assertEqual(len(h.world.military.snapshot('s:q')),1)


    def test_receipts_are_readable_truncated_only_on_screen_and_thoughts_silent(self):
        h=GameHost.new(self.cfg());ui=h.ui.console
        receipt={'ok':True,'status':'effective','message':'','data':{'kind':'native:farm','costs':{'native:gold':20,'native:wood':15}}}
        text=ui.action(0,'s:q','build','{"kind":"farm","x":4,"y":11}',receipt,{'s:q':'秦'})
        self.assertIn('建造 农场 (4, 11) → 动工 农场（(4, 11)',text)
        self.assertIn('−20金 −15木',text)
        self.assertNotIn('native:',text);self.assertNotIn('类型=',text)
        self.assertNotIn('已生效',text)
        receipt['data']={'text':'回执全文'*500}
        full=ui.action(0,'s:q','player_rules',{},receipt,{'s:q':'秦'})
        short=ui.preview(full);self.assertLess(len(short),len(full));self.assertIn('完整内容见日志',short)
        # 国策与回合小结整段照打；规则这类仍然是屏幕上早截断。
        for action in ('plan','end_turn','explain_plan'):
            self.assertEqual(ui.preview(full,action=action),full)
        self.assertIn('完整内容见日志',ui.preview(full,action='player_rules'))
        with tempfile.TemporaryDirectory() as d,contextlib.redirect_stdout(io.StringIO()) as out:
            obs=Observer(h,Path(d)/'journal.md');obs.model_output('💭 s:q 静默保留思考')
            self.assertEqual(out.getvalue(),'')
            obs.pending={'s:q':[('player_rules',{},receipt)]};obs.flush('s:q')
            log=obs.path.read_text();self.assertIn(full,log);self.assertIn('静默保留思考',log)
            self.assertLess(out.getvalue().count('回执全文'),500)

    def test_complete_building_returns_have_no_site_parameter_or_construction_eligibility(self):
        for experimental in (False,True):
            h=GameHost.new(self.cfg(experimental));p=h.player('s:q')
            before=h.to_save();rows=p.query('building_economics',{})
            self.assertEqual(len(rows),16);self.assertEqual(h.to_save(),before)
            schema=next(s['function']['parameters'] for s in p.actions() if s['function']['name']=='building_economics')
            for key in ('site','x','y'):self.assertNotIn(key,schema['properties'])
            self.assertFalse(schema.get('required'))
            site=p.query('building_sites')['sites'][0]
            args={'kind':'barracks'}
            self.assertTrue(p.submit('building_economics',args)['ok'])
            self.assertTrue(p.submit('build',{'kind':'watchtower','site':site['id']})['ok'])
            self.assertTrue(p.submit('building_economics',args)['ok'])
            self.assertEqual(rows,p.query('building_economics'))
            for row in rows:
                self.assertTrue(row['costs_by_level'])
                for key in ('recipe_inputs','recipe_outputs','effects','min_slots','site_resource','energy_used','payback_periods'):
                    self.assertIn(key,row)
            self.assertFalse(p.submit('building_economics',{'site':site['id']})['ok'])
            text=h.ui.console.action(0,'s:q','building_economics',{},p.submit('building_economics',{}),{'s:q':'秦'})
            for name in ('农场','林场','工程院','补给厂'):self.assertIn(name,text)
            self.assertIn('每回合净收益',text);self.assertIn('需已有3座建筑',text)
            self.assertNotIn('native:',text)

    def test_experimental_returns_preserve_old_basis_without_mutating_market(self):
        h=GameHost.new(self.cfg(True));p=h.player('s:q');before=h.to_save()
        farm=p.query('building_economics',{'kind':'farm'})
        self.assertAlmostEqual(farm['net_per_period']['total'],1.5*.95)
        factory=p.query('building_economics',{'kind':'supply-factory'})
        self.assertAlmostEqual(factory['net_per_period']['total'],12*1.05-5.5*1.05-2*1.05/2)
        power=p.query('building_economics',{'kind':'oil-power-plant'})
        self.assertAlmostEqual(power['net_per_period']['total'],-6*1.05)
        self.assertIsNone(power['payback_periods'])
        # An empty market still has valuation prices; it cannot fulfill purchases.
        h.world.market._inventory['native:food']=0
        empty=h.to_save();self.assertIsNotNone(p.query('building_economics',{'kind':'farm'}))
        self.assertEqual(h.to_save(),empty)
        h.world.market._inventory['native:food']=h.world.market.reference['native:food']
        self.assertEqual(h.to_save(),before)

    def test_rules_topics_and_unknown_topic_never_succeed_with_empty_results(self):
        for experimental in (False,True):
            p=GameHost.new(self.cfg(experimental)).player('s:q')
            self.assertEqual(p.query('player_rules',{'topic':'general'}),p.rules())
            for topic in ('build','energy'):
                rows=p.query('player_rules',{'topic':topic})
                self.assertTrue(rows);self.assertIn('每地块每回合只能动工一次',rows[0]['body'])
            rejected=p.submit('player_rules',{'topic':'nonexistent'})
            self.assertTrue(rejected['ok']);self.assertEqual(rejected['data'],p.rules());self.assertEqual(p.rules()[0]['title'],'规则目录')
            self.assertGreater(len(p.rules('all')),1)

    def test_bulk_construction_quotes_and_build_receipt_match_actual_costs(self):
        for experimental in (False,True):
            h=GameHost.new(self.cfg(experimental));p=h.player('s:q');ui=h.ui.console
            ui.remember(p.observe());site=p.query('building_sites')['sites'][0];x,y=site['position']
            args={'x':x,'y':y};before=h.to_save()
            rows=p.query('building_catalog')
            self.assertEqual(len(rows),16);self.assertEqual(h.to_save(),before)
            farm=next(r for r in rows if r['id']=='native:farm')
            denied=p.submit('build',{**args,'kind':'barracks'})
            self.assertFalse(denied['ok']);self.assertIn('slots',denied['message'])
            farm['costs']={'native:gold':20*(100+site['penalty'])//100,'native:wood':15}
            args['kind']='farm';gold=h.world.resources.balance('s:q','native:gold')
            wood=h.world.resources.balance('s:q','native:wood');receipt=p.submit('build',args)
            self.assertTrue(receipt['ok']);self.assertEqual(dict(receipt['data']['costs']),farm['costs'])
            self.assertEqual(gold-h.world.resources.balance('s:q','native:gold'),farm['costs']['native:gold'])
            self.assertEqual(wood-h.world.resources.balance('s:q','native:wood'),farm['costs']['native:wood'])
            text=ui.action(0,'s:q','build',args,receipt,{'s:q':'秦'})
            self.assertIn('动工 农场',text);self.assertIn('结算后落成',text)
            self.assertNotIn('native:',text);self.assertNotIn('已生效',text)
            again=p.submit('build',args);self.assertFalse(again['ok'])
            self.assertIn('❌',ui.action(0,'s:q','build',args,again,{'s:q':'秦'}))
            self.assertEqual(len(p.query('building_economics')),16)

    def test_trade_receipt_uses_actual_cash_and_no_invented_balance(self):
        h=GameHost.new(self.cfg());p=h.player('s:q');ui=h.ui.console
        before=h.world.resources.balance('s:q','native:gold')
        args={'resource':'wood','quantity':2};r=p.submit('buy',args)
        self.assertTrue(r['ok']);data=r['data']
        self.assertEqual(before-h.world.resources.balance('s:q','native:gold'),data['gold'])
        text=ui.action(0,'s:q','buy',args,r,{'s:q':'秦'})
        self.assertIn('买入 木材×2',text);self.assertIn(f'实付{data["gold"]}金',text)
        self.assertNotIn('carry',text);self.assertNotIn('余额',text)

    def test_military_and_pending_receipts_preserve_authoritative_status(self):
        ui=GameHost.new(self.cfg()).ui.console
        r={'ok':True,'status':'effective','message':'','data':{'units':[7],'occupied':True,'engaged':False}}
        text=ui.action(3,'s:q','attack',{'ids':[7],'x':2,'y':3},r,{'s:q':'秦'})
        self.assertIn('#7',text);self.assertIn('已占领',text);self.assertNotIn('待结算',text)
        r['data'].update(occupied=False,engaged=True)
        self.assertIn('伤亡在结算时计算',ui.action(3,'s:q','attack',{'ids':[7],'x':2,'y':3},r,{}))
        r={'ok':True,'status':'submitted','message':'','data':{'fee':5}}
        text=ui.action(3,'s:q','declare_war',{'target':'s:c'},r,{'s:q':'秦','s:c':'楚'})
        self.assertIn('等待外交结算',text);self.assertNotIn('已生效',text)

    def test_failed_script_preserves_prior_output_and_successful_action_count(self):
        h=GameHost.new(self.cfg(True));p=h.player('s:q');ui=h.ui.console
        source='print("脚本已开始")\napi.submit("buy", {"resource": "wood", "quantity": 1})\n1 / 0'
        self.assertTrue(p.submit('script_write',{'source':source})['ok'])
        r=p.submit('script_run',{})
        self.assertFalse(r['ok']);self.assertEqual(r['data']['actions'],1)
        text=ui.action(0,'s:q','script_run',{},r,{'s:q':'秦'})
        self.assertIn('已成功动作1',text);self.assertIn('脚本已开始',text);self.assertIn('第3行',text)
        self.assertTrue(p.observe()['can_act']);self.assertFalse(p.query('script_status')['available'])


class TestShortKeys(unittest.TestCase):
    def test_unique_and_conflicting_keys(self):
        r=ContentRegistry();r.register('a',{'a:farm':{}})
        self.assertEqual(r.resolve('farm'),'a:farm');self.assertEqual(r.key('a:farm'),'farm')
        r.register('b',{'b:farm':{}})
        with self.assertRaisesRegex(ValueError,'键值冲突'):r.resolve('farm')
        self.assertEqual(r.key('a:farm'),'a:farm');self.assertEqual(r.resolve('b:farm'),'b:farm')
        with self.assertRaises(ValueError):r.resolve(['farm'])

    def test_coordinates_short_keys_and_army_integer_actions(self):
        h=GameHost.new({'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦'},{'id':'s:c','name':'楚'}]})
        p=h.player('s:q');site=p.observe()['facts']['building_sites']['sites'][0];x,y=site['position']
        catalog=p.query('building_catalog')
        self.assertIn('native:farm',{row['id'] for row in catalog})
        self.assertEqual(p.query('building_economics',{'kind':'farm'}),p.query('building_economics',{'kind':'native:farm'}))
        self.assertEqual(p.query('building_economics',{'kind':'farm'}),p.query('building_economics',{'kind':'native:farm'}))
        self.assertTrue(p.submit('build',{'kind':'farm','x':x,'y':y})['ok'])
        other=h.player('s:c').observe()['facts']['building_sites']['sites'][0]['position']
        self.assertFalse(p.submit('build',{'kind':'farm','x':other[0],'y':other[1]})['ok'])
        self.assertFalse(p.submit('build',{'kind':'farm','x':x})['ok'])
        self.assertFalse(p.submit('build',{'kind':'farm','site':site['id'],'x':x,'y':y})['ok'])
        h.subsidize('s:q',{},1);units=p.query('army');own=next(u for u in units if u['owner']=='s:q')
        self.assertIsInstance(own['id'],int);self.assertNotIn('gid',own);self.assertNotIn('name',own)
        self.assertTrue(p.submit('disband',{'aid':own['id']})['ok'])
        schema=next(s for s in p.actions() if s['function']['name']=='build')
        self.assertIn('farm',schema['function']['parameters']['properties']['kind']['enum'])

    def test_script_prompt_is_capability_driven_and_failure_only_spends_script_quota(self):
        for experimental in (False,True):
            cfg={'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦'}]}
            if experimental:cfg['mods']='mods.experimental.toml'
            h=GameHost.new(cfg);p=h.player('s:q');ui=h.ui.llm(h.cfg)
            ui.actions(p.actions())
            self.assertEqual('【内置 Python】' in ui.system(),experimental)
            if experimental:
                schema=next(t for t in p.actions() if t['function']['name']=='script_run')
                self.assertIn({'type':'object'},schema['function']['parameters']['properties']['inputs']['anyOf'])
                self.assertTrue(p.submit('script_write',{'source':'1 / 0'})['ok'])
                self.assertFalse(p.submit('script_run',{})['ok'])
                self.assertFalse(p.query('script_status')['available'])
                self.assertTrue(p.observe()['can_act'])
                self.assertTrue(p.submit('plan',{'text':'继续经营'})['ok'])
                self.assertTrue(p.submit('end_turn',{'summary':'脚本失败后仍完成了本期经营行动'})['ok'])

    def test_model_keys_follow_the_language(self):
        """键值随语言走：zh 是金/农场，切 en 才变 gold/farm；入口两种都收。"""
        from clients.llm import LLMClient
        for language,building,resource in (('zh-CN','农场','粮食'),('en-US','farm','food')):
            with self.subTest(language=language):
                cfg={'map_size':12,'seed':17,'language':language,'nations':[{'id':'s:q','name':'秦','client':'llm'}]}
                h=GameHost.new(cfg);p=h.player('s:q');ui=h.ui.llm(h.cfg)
                schemas={s['function']['name']:s['function'] for s in ui.actions(p.actions())}
                self.assertIn(building,schemas['build']['parameters']['properties']['kind']['enum'])
                stocks=json.loads(ui.state(p.observe(),[])[0])['state']['facts']['econ']['stocks']
                self.assertIn(resource,stocks)
                client=LLMClient(h.cfg,ui=h.ui)
                self.assertTrue(client._submit(p,'sell',{'resource':resource,'quantity':1})['ok'])
                # 正文里的同一个词不能被当成键值改掉。
                self.assertEqual(client._submit(p,'plan',{'text':f'{resource}要攒'})['data']['text'],f'{resource}要攒')

    def test_model_compaction_preserves_prose_and_raw_api_facts(self):
        h=GameHost.new({'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦'}]})
        obs=h.player('s:q').observe();before=deepcopy(obs);ui=h.ui.llm(h.cfg)
        raw=json.dumps({'state':obs,'events':[]},ensure_ascii=False)
        text,_=ui.state(obs,[]);data=json.loads(text)['state']['facts']
        self.assertLess(len(text),len(raw));self.assertEqual(obs,before)
        # 键值随语言走：zh 下是"金币"，不是 gold。
        self.assertIsInstance(data['econ']['stocks'],dict);self.assertIn('金币',data['econ']['stocks'])
        self.assertNotIn('/land-',text);self.assertNotIn('native:gold',text);self.assertNotIn('native:infantry',text)
        receipt={'data':{'kind':'native:farm','text':'native:farm','message':'native:gold'}}
        compact=json.loads(ui.receipt(receipt))
        self.assertEqual(compact['data'],{'kind':'农场','text':'native:farm','message':'native:gold'})
