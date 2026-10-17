"""Restore legacy design constraints through public facts, without real saves/models."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost
from mods.ui.console import ConsoleUI
from tests import test_world_domains as fixtures
Q,C,Z=fixtures.Q,fixtures.C,fixtures.Z


class TestUIRegressions(unittest.TestCase):
    def host(self):
        return GameHost.new({'map_size':12,'seed':17,'nations':[{'id':Q,'name':'秦'},{'id':C,'name':'楚'}]})

    def present(self,h,action,args=None):
        player=h.player(Q);h.ui.console.remember(player.observe())
        return h.ui.console.action(h.clock(),Q,action,args or {},player.submit(action,args or {}),{Q:'秦',C:'楚'})

    def test_market_receipt_gives_equilibrium_tone_and_large_order_trial(self):
        """旧版行情行：现价/均衡/贵贱 + 大单试算；只给现价，模型只能猜贵贱。"""
        h=self.host();text=self.present(h,'market')
        self.assertIn('均衡',text)
        self.assertIn('卖50≈',text)
        self.assertTrue(any(word in text for word in ('贵','贱','平')))

    def test_bank_receipt_states_the_credit_formula_rates_and_term(self):
        """借贷欲望低不是模型笨：状态里只有一个额度数字，没有 GDP×5、利率和期限。"""
        h=self.host();text=self.present(h,'bank')
        self.assertIn('信用额度',text)
        self.assertIn('期限5回合',text)
        self.assertIn('贷款',text)

    def test_army_line_reports_per_unit_reach(self):
        h=self.host();x,y=h.player(Q).query('land')[0]['tile']['position']
        h.world.units.spawn(Q,'native:infantry',x,y)
        self.assertIn('本回合可及',self.present(h,'army'))

    def test_state_facts_carry_flows_reach_and_bank_terms(self):
        h=self.host();x,y=h.player(Q).query('land')[0]['tile']['position']
        h.world.units.spawn(Q,'native:infantry',x,y)
        for _ in range(2):h.cycle()
        facts=h.player(Q).observe()['facts']
        self.assertTrue(facts['frontier']['units'])
        self.assertIn('outputs',facts['econ']['latest_flows'])
        self.assertIn('gdp',facts['bank'])
        self.assertIn('loan_rate',facts['bank'])

    def test_same_plan_text_does_not_renew_itself(self):
        """同一份国策再交一遍不该重置「制定于」与到期日（模型常为保险重复提交）。"""
        h=self.host();text='经济：先铺采集与能源；军事：守土；情报：按需；外交：中立。'
        first=h.world.government.plan(Q,text)
        h.world.government.advance()
        second=h.world.government.plan(Q,text)
        self.assertEqual(first.period,second.period)
        self.assertEqual(first.valid_until,second.valid_until)

    def test_news_does_not_replay_this_turns_own_action_receipts(self):
        """模型手里已有本回合回执；news 再抄一遍国策全文就是白付一次长文。"""
        ui=ConsoleUI({});ui._period=5
        rows=[{'id':1,'period':5,'kind':'action','position':None,'text':'提交国策：全文','details':{'source':'action_receipt'}},
              {'id':2,'period':4,'kind':'action','position':None,'text':'售出 木头×1','details':{'source':'action_receipt'}}]
        text=ui.receipt_body('news',{}, {'ok':True,'data':rows,'message':'','action':'news'}, None)
        self.assertNotIn('提交国策',text);self.assertIn('售出 木头×1',text)

    def test_news_receipt_keeps_latest_and_counts_the_rest(self):
        """十期近讯一次七十多条——旧版只给最近十条，多的收成一行计数。"""
        ui=ConsoleUI({})
        rows=[{'id':i,'period':i,'kind':'action','position':None,'text':f'事件{i}'} for i in range(30)]
        text=ui.receipt_body('news',{}, {'ok':True,'data':rows,'message':'','action':'news'}, None)
        self.assertIn('事件29',text)
        self.assertNotIn('事件0',text)
        self.assertIn('另有 18 条',text)

    def test_public_queries_use_prose_without_changing_public_data(self):
        h=self.host();before=h.runtime.save_mod_state(h.world)
        for action in ('view','nation','nations','government','terrain_catalog','frontier','dispatches'):
            text=self.present(h,action)
            for field in ('visible_unowned：','build_penalty：','extinction_reason：','summaries：','nation：'):
                self.assertNotIn(field,text)
            self.assertNotIn('· ·',text)
        self.assertEqual(before,h.runtime.save_mod_state(h.world))

    def test_army_puts_own_units_before_foreign_and_summarizes_guards(self):
        h=self.host();tile=h.player(Q).query('land')[0]['tile'];x,y=tile['position']
        for _ in range(15):h.world.units.spawn(C,'native:infantry',x,y)
        h.world.units.spawn(Q,'native:infantry',x,y,turn=h.clock(),newborn=True)
        full=self.present(h,'army');preview=h.ui.console.preview(full)
        self.assertIn('秦 步兵#1',preview);self.assertIn('新兵，本回合不可行动',preview)
        self.assertLess(full.index('秦 步兵#1'),full.index('楚 步兵#1'))
        self.assertNotIn('野人 步兵#',full)
        self.assertIn('位置见文字地图',full)

    def test_army_ready_and_moved_are_distinct(self):
        h=self.host();x,y=h.player(Q).query('land')[0]['tile']['position']
        unit=h.world.units.spawn(Q,'native:infantry',x,y)
        self.assertIn('可行动',self.present(h,'army'))
        unit.update(moved_turn=h.clock())
        self.assertIn('本回合已行动',self.present(h,'army'))

    def test_map_contains_coordinates_owner_hall_and_army_and_no_fog_leak(self):
        h=self.host();obs=h.player(Q).observe();x,y=obs['facts']['land'][0]['tile']['position']
        h.world.units.spawn(Q,'native:infantry',x,y);obs=h.player(Q).observe()
        obs['facts']['army'].append({'owner':'secret:owner','id':888,'kind':'secret:unit','x':999,'y':999,'hp':100})
        before=deepcopy(obs);text=h.ui.map_text(obs)
        for value in ('y\\x','A=秦','厅×1','步1',f'({x},{y})'):self.assertIn(value,text)
        self.assertNotIn('secret:',text);self.assertNotIn('888',text)
        self.assertEqual(before,obs)
        ui=h.ui.llm({'vision':True});_,image=ui.map_context(obs)
        self.assertIn('步1',image['fallback'])
        self.assertIn('y\\x',image['fallback'])

    def test_real_castle_levels_visible_in_tile_and_land(self):
        h=self.host();tile=h.player(Q).query('land')[0]['tile'];x,y=tile['position']
        h.world.buildings.seed(tile['id'],'native:castle',count=3)
        for action,args in (('tile',{'x':x,'y':y}),('land',{}),('building_sites',{})):
            text=self.present(h,action,args)
            self.assertIn('城堡 L3',text)
            self.assertNotIn('城堡×3',text)

    def test_land_order_core_garrison_and_pagination(self):
        h=self.host();p=h.player(Q);tile=next(t['tile'] for t in p.query('land') if dict(t['resources']).get('native:wood',0)>0)
        x,y=tile['position'];h.world.units.spawn(Q,'native:infantry',x,y)
        self.assertTrue(p.submit('build',{'x':x,'y':y,'kind':'native:lumber-camp'})['ok'])
        text=self.present(h,'land')
        for value in ('核心属秦','本回合已下单','驻军步兵#1','在建 林场×1'):self.assertIn(value,text)
        text=self.present(h,'land',{'offset':1,'limit':1})
        self.assertIn('本次列出1块',text);self.assertIn('全国共5块',text)
        self.assertNotIn('国土 1 块',text)

    def test_unit_catalog_explains_cavalry_terrain_costs(self):
        text=self.present(self.host(),'unit_catalog')
        cavalry=next(line for line in text.splitlines() if line.startswith('骑兵'))
        for value in ('移动2','森林 2','山地 2','每步取起终两格较大值'):self.assertIn(value,cavalry)

    def test_government_preserves_current_plan_without_replaying_summaries(self):
        h=self.host();p=h.player(Q);p.submit('plan',{'text':'持续保障粮食与补给'})
        p.submit('end_turn',{'summary':'历史流水不该出现在当前国策查询'})
        text=self.present(h,'government')
        self.assertIn('持续保障粮食与补给',text);self.assertIn('有效至',text)
        self.assertNotIn('历史流水不该',text)

    def test_pending_dispatch_is_readable_before_and_after_dispatch(self):
        h=self.host();p=h.player(Q)
        self.assertTrue(p.submit('send_letter',{'target':C,'text':'会盟请回复'})['ok'])
        text=self.present(h,'dispatches')
        for value in ('信件 → 楚','会盟请回复','送达','费用'):self.assertIn(value,text)
        for value in ('submitted：','arrive：','fee：'):self.assertNotIn(value,text)

    def test_treaty_direction_is_public_and_rendered(self):
        h=self.host();w=h.world
        w.diplomacy.seed_pact('guarantee',Q,C)
        text=self.present(h,'countries');self.assertIn('本实体保障它',text)
        row=h.player(C).query('countries')[0]
        self.assertIn({'kind':'guarantee','direction':'incoming'},row['treaties'])
        w.diplomacy.seed_pact('defense',Q,C)
        self.assertIn('共同防御',self.present(h,'countries'))

    def test_latest_arrival_precedes_old_long_report_even_with_lower_dispatch_id(self):
        ui=ConsoleUI({});names={Q:'秦',C:'楚'}
        old={'id':10,'source':C,'target':Q,'kind':'letter','sampled':0,'payload':{'text':'旧信\n'*40}}
        new={**old,'id':2,'sampled':1,'payload':{'text':'迟到但刚收到的重要新信'}}
        rows=[old,new];before=deepcopy(rows)
        full=ui.action(1,Q,'intel',{}, {'ok':True,'data':rows},names)
        preview=ui.preview(full)
        self.assertIn('迟到但刚收到的重要新信',preview)
        self.assertLess(full.index('重要新信'),full.index('旧信'))
        self.assertEqual(rows,before)

    def test_purchased_report_visible_in_bank_and_trend_is_latest_first(self):
        f=fixtures.TestWorldDomains();w=f.world();f.site(w,'scenario:q-site',Q,1,1);f.site(w,'scenario:c-site',C,6,6)
        # Produce enough real periods to publish reports, without any model backend.
        for _ in range(21):f.cycle(w)
        p=w.runtime.player(w,Q);purchase=p.submit('buy_report',{'target':C})
        self.assertTrue(purchase['ok'],purchase)
        ui=ConsoleUI({});ui.remember(p.observe());names={Q:'秦',C:'楚'}
        bank=ui.action(21,Q,'bank',{},p.submit('bank'),names)
        self.assertIn('已购报表·楚',bank);self.assertIn('GDP',bank)
        bought=ui.action(21,Q,'buy_report',{'target':C},purchase,names)
        self.assertIn('已购报表·楚',bought);self.assertIn('GDP',bought)
        report=p.submit('report',{'all':True});text=ui.action(21,Q,'report',{'all':True},report,names)
        rows=report['data'];self.assertGreaterEqual(len(rows),2)
        self.assertEqual(len(text.splitlines()),len(rows)+1)
        self.assertIn(str(max(r['end'] for r in rows)),text.splitlines()[1])

    def threat_world(self,enemy_x=2,blocked=False):
        f=fixtures.TestWorldDomains();w=f.world();f.site(w,'scenario:q-site',Q,1,1);f.site(w,'scenario:c-site',C,enemy_x,1)
        if blocked:
            for y in range(8):f.site(w,f'scenario:wall-{y}',Z,2,y,hall=y==0)
        f.declare(w)
        return f,w

    def test_hall_alert_requires_actual_ready_visible_enemy_and_unguarded_hall(self):
        f,w=self.threat_world();period=w.government.period()
        enemy=w.units.spawn(C,'native:infantry',2,1)
        p=w.runtime.player(w,Q);before=f.save(w)
        warnings=p.query('frontier')['capital_warning']
        self.assertEqual(warnings[0]['position'],[1,1]);self.assertEqual(warnings[0]['enemies'][0]['id'],enemy.id)
        self.assertEqual(f.save(w),before)
        enemy.update(born_turn=period);self.assertEqual(p.query('frontier')['capital_warning'],[])
        enemy.update(born_turn=-1,moved_turn=period);self.assertEqual(p.query('frontier')['capital_warning'],[])
        enemy.update(moved_turn=-1)
        w.units.spawn(Q,'native:infantry',1,1)
        self.assertEqual(p.query('frontier')['capital_warning'],[])

    def test_hall_alert_respects_real_blocking_and_never_uses_straight_line_range(self):
        f,w=self.threat_world(enemy_x=3,blocked=True)
        # A full neutral wall blocks every route; the city remains within cavalry's geometric range.
        w.units.spawn(C,'native:cavalry',3,1)
        p=w.runtime.player(w,Q)
        self.assertEqual(p.query('frontier')['capital_warning'],[])

    def test_truce_rows_render_from_the_engine_snapshot_shape(self):
        """引擎快照的 truces 是 {'a','b','until'}；按元组取值会抛错并掉进字段倾倒。"""
        ui=ConsoleUI({});ui._names={Q:'秦',C:'楚'}
        snap={'alliances':[],'pacts':[],'wars':[],'truces':[{'a':'nation/'+Q,'b':'nation/'+C,'until':101}],
              'offers':[],'votes':[],'pending':[],'results':[]}
        text=ui.diplomacy(snap)
        self.assertIn('秦 与 楚｜休战至第101期',text)
        self.assertNotIn('甲方：',text)

    def test_dashboard_uses_exact_public_alerts_without_geometry_fallback(self):
        h=self.host();obs=h.player(Q).observe()
        supplied=[{'position':[1,1],'name':'守城验收','enemies':[]}]
        obs['facts']['frontier']['capital_warning']=supplied
        ui=h.ui.llm({});self.assertEqual(ui.dashboard(obs)['capital_warning'],supplied)
        del obs['facts']['frontier']['capital_warning']
        self.assertEqual(ui.dashboard(obs)['capital_warning'],[])

    def test_hall_alert_is_visible_before_long_destination_tables(self):
        ui=self.host().ui.console;destinations=[[x,y] for x in range(20) for y in range(20)]
        data={'visible_unowned':400,'reachable':destinations,'frontier':destinations,
              'capital_warning':[{'position':[1,1],'name':'险城',
                  'enemies':[{'owner':C,'id':2,'kind':'native:cavalry','position':[2,1]}]}]}
        output=ui.action(0,Q,'frontier',{}, {'ok':True,'data':data},{Q:'秦',C:'楚'})
        preview=ui.preview(output)
        self.assertIn('国祚警报',preview);self.assertIn('险城',preview);self.assertIn('楚 骑兵#2',preview)

    def test_occupation_contains_major_structured_facts_and_survives_routine_news(self):
        f,w=self.threat_world();w.units.spawn(Q,'native:infantry',2,1)
        p=w.runtime.player(w,Q);receipt=p.submit('attack',{'ids':[1],'x':2,'y':1});self.assertTrue(receipt['ok'],receipt)
        f.cycle(w)
        occupation=next(e for e in p.events()['items'] if e['kind']=='occupation')
        details=occupation['details']
        self.assertEqual(details['previous_owner'],C);self.assertEqual(details['owner'],Q)
        self.assertEqual(details['town_halls'],1);self.assertEqual(details['core'],C)
        self.assertEqual(details['importance'],'major')
        ui=ConsoleUI({});text=ui.event(occupation,{Q:'秦',C:'楚'})
        for value in ('楚失地','秦得地','核心属楚','市政厅×1'):self.assertIn(value,text)
        h=self.host();obs=h.player(Q).observe();noise=[{'id':occupation['id']+i+1,'period':2,'kind':'action','text':'普通动作'} for i in range(30)]
        text=h.ui.llm({}).state(obs,[occupation,*noise])[0]
        self.assertIn('秦得地',text)

    def test_same_version_save_resumes_identically_after_new_public_queries(self):
        h=self.host();before=h.runtime.save_mod_state(h.world)
        for name in ('frontier','countries','land','view'):self.present(h,name)
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'session.json';h.save(path)
            restored=GameHost.load(path,h.cfg)
            self.assertEqual(h.to_save(),restored.to_save())
            self.assertEqual(h.player(Q).query('frontier'),restored.player(Q).query('frontier'))


if __name__=='__main__':unittest.main()
