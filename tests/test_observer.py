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
from mp_run import Observer,command,run

try:
    import PIL  # noqa: F401
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


class TestObserver(unittest.TestCase):
    def cfg(self,experimental=False):
        cfg={'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦'},{'id':'s:c','name':'楚'}]}
        if experimental:cfg['mods']='mods.experimental.toml'
        return cfg

    def test_overview_contains_current_state_without_replaying_messages_or_reports(self):
        h=GameHost.new(self.cfg());state=h.observer_snapshot();state['period']=5
        for sampled in (3,4,5):
            state['records']['messages'].append({'id':sampled,'source':'s:q','target':'s:c',
                'kind':'letter','sampled':sampled,'payload':{'text':f'通信正文只应增量播报{sampled}'}})
        state['nations'][0]['facts']['econ']['latest_turn']={'gdp':{'total':987654},'investment':{'total':0},
            'military':{'total':0},'total_assets':{'total':0},'liabilities':0}
        before=deepcopy(state)
        text=h.ui.console.overview(state)
        self.assertIn('各国现状',text)
        self.assertNotIn('通信正文只应增量播报',text)
        self.assertNotIn('987654',text)
        self.assertNotIn('上期 GDP',text)
        self.assertEqual(state,before)

    def test_delayed_lower_id_message_is_announced_once_and_resume_skips_history(self):
        h=GameHost.new(self.cfg())
        def message(ident,text):
            return {'id':ident,'source':'s:q','target':'s:c','kind':'letter','sampled':1,'payload':{'text':text}}
        records={'events':[],'messages':[]}
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()),patch.object(h,'records',return_value=records):
            observer=Observer(h,Path(directory)/'journal.md')
            records['messages'].append(message(5,'先到的新编号信件'))
            observer.events()
            records['messages'].append(message(2,'迟到的旧编号消息'))
            observer.events();observer.events()
            resumed=Observer(h,observer.path,resume=True)
            resumed.events()
            records['messages'].append(message(3,'续局后新到的旧编号消息'))
            resumed.events();resumed.events()
            text=observer.path.read_text(encoding='utf-8')
        for body in ('先到的新编号信件','迟到的旧编号消息','续局后新到的旧编号消息'):
            self.assertEqual(text.count(body),1)

    def test_intelligence_is_readable_and_screen_is_bounded_while_log_keeps_details(self):
        h=GameHost.new(self.cfg());ui=h.ui.console
        money={'known':100,'unpriced':[],'total':100,'complete':True}
        payload={'inventory':{'native:gold':221,'native:food':30},'unit_counts':{'native:infantry':1},
            'economy':{'period':3,'gdp':money,'total_assets':money,'liabilities':0},
            'buildings_total':12,'tiles':[{'position':[x,1],'terrain':'native:plain','owner':'s:c',
                'buildings':{'native:farm':1},'resources':{'native:farmland':3},'construction':{'native:mine':1}} for x in range(25)]}
        message={'id':2,'source':'s:c','target':'s:q','kind':'spy','sampled':4,'payload':payload}
        before=deepcopy(message);full=ui.message(message,{'s:q':'秦','s:c':'楚'})
        self.assertIn('秦 获得对 楚 的间谍报告',full)
        self.assertIn('库存：',full);self.assertIn('步兵×1',full)
        self.assertIn('(24, 1)',full);self.assertIn('在建 矿场×1',full)
        self.assertNotIn('native:',full);self.assertNotIn('"inventory"',full)
        self.assertNotIn('市场库存',full);self.assertNotIn('建造投入',full)
        self.assertIn('第3回合经济报告',full);self.assertNotIn('已落成',full)
        self.assertEqual(before,message)
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as screen,patch.object(h,'records',return_value={'events':[],'messages':[message]}):
            observer=Observer(h,Path(directory)/'journal.md');observer.events();observer.events()
            journal=observer.path.read_text(encoding='utf-8')
        self.assertEqual(journal.count('间谍报告 #2'),1)
        self.assertEqual(screen.getvalue().count('间谍报告 #2'),1)
        self.assertIn('(24, 1)',journal);self.assertNotIn('(24, 1)',screen.getvalue())
        self.assertIn('完整内容见日志',screen.getvalue())

    def test_map_gift_and_letter_have_distinct_presentations_and_preserve_prose(self):
        ui=GameHost.new(self.cfg()).ui.console;names={'s:q':'秦','s:c':'楚'}
        base={'id':1,'source':'s:q','target':'s:c','sampled':2}
        letter={**base,'kind':'letter','payload':{'text':'正文原样保留：{"inventory":"native:gold"}'}}
        self.assertIn(letter['payload']['text'],ui.message(letter,names))
        gift={**base,'kind':'gift','payload':{'resource':'native:gold','quantity':10}}
        self.assertIn('收到赠送物资',ui.message(gift,names));self.assertIn('10黄金',ui.message(gift,names))
        shared={**base,'kind':'map','payload':{'tiles':[{'position':[1,2],'terrain':'native:plain','owner':None,
                'buildings':{},'construction':{},'resources':{'native:farmland':3}}]}}
        full=ui.message(shared,names)
        self.assertIn('共享地图',full);self.assertIn('(1, 2)',full);self.assertIn('无主',full)
        self.assertNotIn('native:',full)

    def test_cli_prints_each_period_overview_once(self):
        cfg=self.cfg();cfg['max_turns']=2
        for nation in cfg['nations']:nation['client']='rule'
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()) as screen,patch('sys.stdin',io.StringIO('')):
            root=Path(directory);cfg.update(save=str(root/'save.json'),journal=str(root/'journal.md'))
            path=root/'config.json';path.write_text(json.dumps(cfg,ensure_ascii=False), encoding='utf-8')
            with patch('select.select',return_value=([],[],[])):
                self.assertEqual(run(['--config',str(path)]),0)
            journal=(root/'journal.md').read_text(encoding='utf-8')
            self.assertEqual(json.loads((root/'save.json').read_text(encoding='utf-8'))['modules']['native:government']['period'],2)
        for output in (journal,screen.getvalue()):
            for period in range(2):self.assertEqual(output.count(f'世界全景 · 第 {period} 回合'),1)
            self.assertNotIn('世界全景 · 第 2 回合',output)

    def test_major_events_wait_until_the_settlement_boundary(self):
        h=GameHost.new(self.cfg())
        class Console:
            panel_on=False
            def __init__(self):self.queue=queue.Queue()
            def write(self,text):pass
        console=Console();console.queue.put('say 重要事件只在回合间播报')
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()),patch('mp_run.CONSOLE',console):
            observer=Observer(h,Path(directory)/'journal.md',resume=True)
            observer.on_call('s:q',0,[])
            self.assertNotIn('重要事件只在回合间播报',observer.path.read_text(encoding='utf-8'))
            observer.events(period=0);observer.events(period=0)
            text=observer.path.read_text(encoding='utf-8')
        self.assertEqual(text.count('重要事件只在回合间播报'),1)
        self.assertEqual(text.count('第 0 回合结算动态'),1)
        self.assertLess(text.index('◈ 秦 第 1 次调用'),text.index('第 0 回合结算动态'))

    def test_major_announcement_survives_the_models_recent_action_window(self):
        h=GameHost.new(self.cfg());observation=h.player('s:q').observe()
        events=[{'id':1,'period':0,'kind':'diplomacy','text':'联盟创建失败，已向全体公告',
                 'details':{'importance':'major'}}]
        events.extend({'id':i,'period':0,'kind':'action','text':f'普通行动{i}'} for i in range(2,32))
        before=deepcopy(events)
        text,_=h.ui.llm({'model_state_tokens':12000}).state(observation,events)
        # 重大公告必须进简报；普通行动按最近十条取，不能把大事挤出窗口。
        self.assertIn('联盟创建失败',text)
        self.assertLessEqual(text.count('普通行动'),10)
        self.assertEqual(events,before)

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
        receipt={'ok':True,'status':'effective','message':'','data':{'site':tile['id'],
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

    def test_receipts_resolve_coordinate_strings_the_engine_accepts(self):
        """place/site 填坐标串（引擎认的那种）时，回执与日志必须照样打出那块地。"""
        h=GameHost.new(self.cfg());ui=h.ui.console;observation=h.player('s:q').observe();ui.remember(observation)
        tile=observation['facts']['land'][0]['tile'];x,y=tile['position'];name=tile['name']
        receipt={'ok':True,'status':'effective','message':'','data':{'units':[1]}}
        for place in (f'{x} {y}',f'{x},{y}',f'({x},{y})'):
            text=ui.action(0,'s:q','move',{'aid':1,'place':place},receipt,{'s:q':'秦'})
            self.assertIn(f'→ {name}({x}, {y})',text)
            self.assertIn(f'已移动至{name}({x}, {y})',text)
            self.assertNotIn('所选地块',text)
        # 认不出来的引用照原样给回去，不拿「所选地块」冒充地块。
        text=ui.action(0,'s:q','move',{'aid':1,'place':'没这个地方'},receipt,{'s:q':'秦'})
        self.assertIn('→ 没这个地方',text)

    def test_building_sites_use_known_names_without_duplicate_labels(self):
        h=GameHost.new(self.cfg());actor='s:q';ui=h.ui.console
        obs=h.player(actor).observe();ui.remember(obs)
        receipt=h.player(actor).submit('building_sites')
        text=ui.action(0,actor,'building_sites',{},receipt,{actor:'秦'})
        for land in obs['facts']['land']:
            tile=land['tile'];x,y=tile['position'];name=tile['name']
            self.assertIn(f'{name}({x}, {y})',text)
            self.assertEqual(text.count(name),1)
        self.assertEqual(receipt,h.player(actor).submit('building_sites'))

    def test_llm_finish_reports_full_elapsed_time_once_after_final_batch(self):
        h=GameHost.new({'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦','client':'llm'}]})
        clock=[100.0]
        class TimedBackend(FakeBackend):
            def chat_turn(self,*args,**kwargs):
                clock[0]+=12.0
                return super().chat_turn(*args,**kwargs)
        backend=TimedBackend([
            {'content':'','tool_calls':[call('p','plan',text='经营实际生产并保持国家独立')]},
            {'content':'','tool_calls':[call('e','end_turn',summary='完成本期行动')]}])
        h.clients['s:q']=LLMClient({'ctx_compact':'off'},backend=backend)
        with tempfile.TemporaryDirectory() as folder,contextlib.redirect_stdout(io.StringIO()) as screen,patch('mp_run.time.monotonic',side_effect=lambda:clock[0]):
            observer=Observer(h,Path(folder)/'journal.md')
            h.cycle(on_result=observer.result,head=observer.context_head,on_call=observer.on_call)
            journal=observer.path.read_text(encoding='utf-8')
        for output in (journal,screen.getvalue()):
            self.assertEqual(output.count('第 0 回合 · 秦 行动'),1)
            self.assertEqual(output.count('秦 行动结束'),1)
            self.assertIn('用时 24.0 秒',output)
            self.assertNotIn('用时 0.0 秒',output)
            self.assertLess(output.index('第 2 次调用'),output.index('◇ 结束回合'))
            self.assertLess(output.index('◇ 结束回合'),output.index('秦 行动结束'))

    def test_observer_writes_each_receipt_once_and_keeps_action_history(self):
        h=GameHost.new({'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦','client':'llm'}]})
        fake=FakeBackend([{'content':'','tool_calls':[
            call('p','plan',text='完整国策正文：建设生产与维持独立'),
            call('b1','economy',action='buy',resource='wood',quantity=1),call('b2','economy',action='buy',resource='wood',quantity=1),
            call('e','end_turn',summary='执行生产准备并维持独立')]}])
        h.clients['s:q']=LLMClient({'ctx_compact':'off'},backend=fake)
        with tempfile.TemporaryDirectory() as folder,contextlib.redirect_stdout(io.StringIO()) as screen:
            observer=Observer(h,Path(folder)/'journal.md')
            h.cycle(on_result=observer.result,on_call=observer.on_call)
            # Unrelated events must survive even when they have custom details.
            h.world.intelligence.emit('victory','测试胜利公告',public=True,details={'winners':['s:q']})
            observer.events();observer.events()
            text=observer.path.read_text(encoding='utf-8')
        for output in (text,screen.getvalue()):
            self.assertEqual(output.count('完整国策正文：建设生产与维持独立'),1)
            self.assertEqual(output.count('◇ 购入 木头×1'),2)
            self.assertEqual(output.count('测试胜利公告'),1)
            self.assertNotIn('[0·action]',output)
        events=h.records()['events']
        self.assertEqual(len([e for e in events if e['kind']=='action']),4)
        visible=h.player('s:q').events()['items']
        self.assertTrue(any(e['kind']=='action' for e in visible))

    def test_overview_shows_only_production_and_military_flow_deltas(self):
        h=GameHost.new(self.cfg());snapshot=h.observer_snapshot();actor='s:q'
        for observation,amount in zip(snapshot['nations'],(1,100)):
            for row in observation['facts']['econ']['stocks']:
                if row['id']=='native:gold':row['amount']=amount
        snapshot['resource_flows']={'period':0,'owners':{actor:{'native:gold':10,'native:ore':-3},
            's:c':{'native:gold':-2}}}
        text=h.ui.console.overview(snapshot)
        self.assertIn('\033[32m+10',text);self.assertIn('\033[31m-3',text)
        self.assertNotIn('(+10)',text);self.assertNotIn('(-3)',text)
        self.assertIn('不含市场交易和赠礼',text)
        import re
        plain=re.sub(r'\033\[[0-9;]*m','',text)
        qin=next(line for line in plain.splitlines() if line.startswith('秦 '))
        chu=next(line for line in plain.splitlines() if line.startswith('楚 '))
        self.assertEqual(qin.index('+10'),chu.index('-2'))

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
            obs=Observer(h,Path(d)/'journal.md');obs.show_thinking='full'
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
            suffixes=('txt','svg')+(('png',) if PIL_AVAILABLE else ())
            for suffix in suffixes:
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
            # 默认摘要模式：终端只留思考第一行，全文仍然进日志。
            obs=Observer(h,Path(d)/'journal.md');obs.model_output('💭 s:q 静默保留思考\n第二行细节')
            self.assertIn('静默保留思考',out.getvalue())
            self.assertNotIn('第二行细节',out.getvalue())
            obs.pending={'s:q':[('player_rules',{},receipt)]};obs.flush('s:q')
            log=obs.path.read_text(encoding='utf-8');self.assertIn(full,log);self.assertIn('静默保留思考',log)
            self.assertLess(out.getvalue().count('回执全文'),500)

    def test_complete_building_returns_have_no_site_parameter_or_construction_eligibility(self):
        for experimental in (False,True):
            h=GameHost.new(self.cfg(experimental));p=h.player('s:q')
            before=h.to_save();rows=p.query('building_economics',{})
            self.assertEqual(len(rows),16);self.assertEqual(h.to_save(),before)
            # 建筑收益并入 query(panel=building_economics)：它自己不接地块参数，
            # 真正的门槛由下面那次带 site 的提交被拒来钉。
            schema=next(s['function']['parameters'] for s in p.actions() if s['function']['name']=='query')
            self.assertIn('kind',schema['properties'])
            self.assertEqual(schema.get('required'),['panel'])
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
        args={'action':'buy','resource':'wood','quantity':2};r=p.submit('economy',args)
        self.assertTrue(r['ok']);data=r['data']
        self.assertEqual(before-h.world.resources.balance('s:q','native:gold'),data['gold'])
        text=ui.action(0,'s:q','buy',args,r,{'s:q':'秦'})
        self.assertIn('购入 木头×2',text);self.assertIn(f'实付 {data["gold"]}',text)
        self.assertNotIn('carry',text);self.assertNotIn('余额',text)

    def test_military_and_pending_receipts_preserve_authoritative_status(self):
        ui=GameHost.new(self.cfg()).ui.console
        r={'ok':True,'status':'effective','message':'','data':{'units':[7],'occupied':True,'engaged':False}}
        text=ui.action(3,'s:q','attack',{'ids':[7],'x':2,'y':3},r,{'s:q':'秦'})
        self.assertIn('#7',text);self.assertIn('已占领',text);self.assertNotIn('待结算',text)
        r['data'].update(occupied=False,engaged=True)
        self.assertIn('每回合结算一轮',ui.action(3,'s:q','attack',{'ids':[7],'x':2,'y':3},r,{}))
        r={'ok':True,'status':'submitted','message':'','data':{'fee':5}}
        text=ui.action(3,'s:q','declare_war',{'target':'s:c'},r,{'s:q':'秦','s:c':'楚'})
        self.assertIn('已提交',text);self.assertIn('宣战',text);self.assertNotIn('已生效',text)

    def test_failed_script_preserves_prior_output_and_successful_action_count(self):
        h=GameHost.new(self.cfg(True));p=h.player('s:q');ui=h.ui.console
        source='print("脚本已开始")\napi.call("economy:queries","buy", resource="native:wood", quantity=1)\n1 / 0'
        self.assertTrue(p.submit('script', {'action': 'write', 'source':source})['ok'])
        r=p.submit('script', {'action': 'run'})
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
        self.assertTrue(p.submit('military', {'action': 'disband', 'aid':own['id']})['ok'])
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
                schema=next(t for t in p.actions() if t['function']['name']=='script')
                self.assertIn({'type':'object'},schema['function']['parameters']['properties']['inputs']['anyOf'])
                self.assertTrue(p.submit('script', {'action': 'write', 'source':'1 / 0'})['ok'])
                self.assertFalse(p.submit('script', {'action': 'run'})['ok'])
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
                self.assertIn(resource,ui.state(p.observe(),[])[0])
                client=LLMClient(h.cfg,ui=h.ui)
                self.assertTrue(client._submit(p,'economy',{'action':'sell','resource':resource,'quantity':1})['ok'])
                # 正文里的同一个词不能被当成键值改掉。
                self.assertEqual(client._submit(p,'plan',{'text':f'{resource}要攒'})['data']['text'],f'{resource}要攒')

    def test_model_compaction_preserves_prose_and_raw_api_facts(self):
        """简报比原始事实短，键值随语言走，内部 ID 不外泄，正文不被改词。"""
        h=GameHost.new({'map_size':12,'seed':17,'nations':[{'id':'s:q','name':'秦'}]})
        obs=h.player('s:q').observe();before=deepcopy(obs);ui=h.ui.llm(h.cfg)
        raw=json.dumps({'state':obs,'events':[]},ensure_ascii=False)
        text,_=ui.state(obs,[])
        self.assertLess(len(text),len(raw));self.assertEqual(obs,before)
        # 键值随语言走：zh 下是"黄金"，不是 native:gold。
        self.assertIn('黄金',text)
        self.assertNotIn('/land-',text);self.assertNotIn('native:gold',text);self.assertNotIn('native:infantry',text)
        # 正文里的同一个词不能被当成键值改掉。
        receipt={'ok':True,'action':'plan','message':'','data':{'text':'粮食要攒','valid_until':9}}
        self.assertIn('粮食要攒',ui.receipt(receipt,{}))
