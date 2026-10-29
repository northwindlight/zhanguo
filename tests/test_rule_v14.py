"""Joint optimization and conservation checks against real actor-bound APIs."""
from collections import Counter
from itertools import product
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from host import GameHost, SaveFormatError
import ruleai
from ruleai.v11plus.ledger import Ledger
from ruleai.v14 import RuleState
from ruleai.v14.economy import execute, run
from ruleai.v14.inputs import collect
from ruleai.v14.joint import Joint, Plan
from ruleai.v14.linear import Model, Solution

GOLD, WOOD, ORE, OIL = ('native:'+s for s in ('gold','wood','ore','oil'))
PLANT='native:wood-power-plant'


class TestIntegerModel(unittest.TestCase):
    def test_complementary_projects_match_complete_enumeration(self):
        # Shared cash/wood, productive factory requires its power investment.
        m=Model()
        plant=m.var('plant',upper=1,integer=True)
        factory=m.var('factory',upper=1,integer=True)
        mine=m.var('mine',upper=1,integer=True)
        m.limit(3*plant+5*factory+5*mine,upper=8)
        m.limit(2*plant+3*factory+4*mine,upper=5)
        m.limit(factory-plant,upper=0)
        objective=12*factory+7*mine-plant
        candidates=[(12*f+7*r-p,(p,f,r)) for p,f,r in product((0,1),repeat=3)
                    if 3*p+5*f+5*r<=8 and 2*p+3*f+4*r<=5 and f<=p]
        expected=max(candidates)
        result=m.solve(objective)
        self.assertEqual(result.status,'optimal')
        self.assertAlmostEqual(result.objective,expected[0])
        self.assertEqual(tuple(round(v.value(result.values)) for v in (plant,factory,mine)),expected[1])
        self.assertAlmostEqual(result.bound,result.objective)

    def test_invalid_incumbents_are_rejected_independently(self):
        m=Model();x=m.var('integer',upper=1,integer=True);m.limit(x,upper=0)
        for values in ([1],[.5],[float('nan')]):
            raw=SimpleNamespace(status=1,x=values,message='time limit',mip_gap=1)
            with patch('scipy.optimize.milp',return_value=raw):
                self.assertIsNone(m.solve(x).values)

    def test_node_limited_valid_incumbent_never_claims_optimality(self):
        m=Model();x=m.var('integer',upper=1,integer=True)
        raw=SimpleNamespace(status=4,x=[1],message='HiGHS Status 16: Solution limit reached',
                            mip_gap=.2,mip_dual_bound=-1.2,mip_node_count=20)
        with patch('scipy.optimize.milp',return_value=raw):
            result=m.solve(x)
        self.assertEqual(result.status,'limited')
        self.assertAlmostEqual(result.bound,1.2)
        self.assertEqual(result.objective,1)


class TestRuleV14(unittest.TestCase):
    def cfg(self, version='v14'):
        return {'map_size':12,'seed':73,'nations':[{'id':'scenario:qin','name':'秦',
            'client':'rule','rule_ai':version,'rule_strategy':'merchant','rule_seed':73}]}

    def fixture(self):
        h=GameHost.new(self.cfg());p=h.player('scenario:qin');l=Ledger(p)
        self.assertTrue(l.do('plan',text='联合规划资金、材料与持续生产'))
        data=collect(p,manage_military=False)
        hall=next(b for b in data['sites']['instances'] if b['kind']=='native:town-hall')
        site=next(s for s in data['sites']['sites'] if s['id']==hall['site'])
        return h,p,l,site

    def fixed_model(self,p,horizon=1,starts=None):
        data=collect(p,manage_military=False)
        joint=Joint(data,p,RuleState(profile='merchant'),horizon=horizon).build()
        starts=starts or {}
        for key,value in joint.x.items():joint.model.equal(value,starts.get(key,0))
        for value in (*joint.buys.values(),*joint.sells.values()):joint.model.equal(value,0)
        return data,joint,joint.solve()

    def settle(self,h,l):
        self.assertTrue(l.do('end_turn',summary='方案已提交，检查正式结算'))
        h.cycle()

    def assert_stocks(self,p,forecast,cash_exact=True):
        actual={r['id']:r['amount'] for r in p.call_api('economy:queries','balance')['stocks']}
        for resource,value in forecast.items():
            if resource==GOLD and not cash_exact:
                self.assertGreaterEqual(actual[resource]+1e-5,value)
            else:self.assertAlmostEqual(actual[resource],value,places=5,msg=resource)

    def test_initial_outage_and_new_plant_only_produces_next_period(self):
        h,p,l,site=self.fixture()
        data,joint,plan=self.fixed_model(p,2,{(site['id'],PLANT,0):1})
        self.assertTrue(plan.forecast,plan.certificate)
        self.assertEqual([r['grid'] for r in plan.forecast],[0,1])
        self.assertAlmostEqual(plan.certificate['service_shortfall'],1)
        self.assertTrue(execute(l,data,plan)['executed'])
        self.settle(h,l);self.assert_stocks(p,plan.forecast[0]['stocks'])
        l=Ledger(p);self.settle(h,l);self.assert_stocks(p,plan.forecast[1]['stocks'])

    def test_shared_ore_and_automatic_factory_order_matches_engine(self):
        h,p,l,site=self.fixture()
        for kind in ('native:oil-power-plant','native:supply-factory','native:equipment-factory'):
            h.world.buildings.seed(site['id'],kind)
        for resource,amount in ((ORE,1),(OIL,5),('native:food',2)):
            h.world.resources._stock(p.actor,resource).set_amount(amount)
        _,_,plan=self.fixed_model(p)
        count={r['kind']:r['count'] for r in plan.forecast[0]['production'] if r['site']==site['id']}
        self.assertEqual(count['native:supply-factory'],1)
        self.assertEqual(count['native:equipment-factory'],0)
        self.settle(h,l);self.assert_stocks(p,plan.forecast[0]['stocks'])

    def test_lack_of_fuel_stops_entire_grid_not_selected_factories(self):
        h,p,l,site=self.fixture()
        for kind in ('native:oil-power-plant','native:supply-factory','native:equipment-factory'):
            h.world.buildings.seed(site['id'],kind)
        h.world.resources._stock(p.actor,OIL).set_amount(0)
        _,_,plan=self.fixed_model(p)
        self.assertEqual(plan.forecast[0]['grid'],0)
        rows=plan.forecast[0]['production']
        self.assertTrue(all(r['count']==0 for r in rows if r['kind'] in ('native:supply-factory','native:equipment-factory','native:town-hall')))
        self.settle(h,l);self.assert_stocks(p,plan.forecast[0]['stocks'])

    def test_upkeep_and_repair_are_automatic_and_starvation_disables_healing(self):
        for supply in (1,0):
            with self.subTest(supply=supply):
                h,p,l,site=self.fixture()
                unit=h.world.military.seed(p.actor,'native:infantry',*site['position'])[0]
                h.world.units.by_id(unit.id,p.actor).take_damage(60)
                h.world.resources._stock(p.actor,'native:supply').set_amount(supply)
                h.world.resources._stock(p.actor,'native:equipment').set_amount(1)
                _,_,plan=self.fixed_model(p)
                self.assertAlmostEqual(plan.forecast[0]['stocks']['native:supply'],0)
                self.assertAlmostEqual(plan.forecast[0]['stocks']['native:equipment'],0 if supply else 1)
                self.settle(h,l);self.assert_stocks(p,plan.forecast[0]['stocks'])
                if supply:self.assertEqual(h.world.units.by_id(unit.id,p.actor).hp,70)

    def test_academy_discount_is_joint_cross_period_cost_not_building_bonus(self):
        h,p,l,site=self.fixture()
        h.world.buildings.seed(site['id'],PLANT,count=3)
        before=h.runtime.save_mod_state(h.world)
        data=collect(p,manage_military=False)
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        quoted=next(r for r in data['plans'][site['id']]['buildings'] if r['kind']=='native:equipment-factory')
        self.assertEqual(quoted['price']['costs'][GOLD],105)
        self.assertEqual(quoted['price_after_academy']['costs'][GOLD],78)
        data,joint,plan=self.fixed_model(p,2,{
            (site['id'],'native:academy',0):1,(site['id'],'native:equipment-factory',1):1})
        self.assertTrue(execute(l,data,plan)['executed'])
        self.settle(h,l);self.assert_stocks(p,plan.forecast[0]['stocks'])
        l=Ledger(p)
        self.assertTrue(l.do('build',site=site['id'],kind='native:equipment-factory'))
        self.assertEqual(dict(l.acts[-1]['data']['costs'])[GOLD],78)
        self.settle(h,l);self.assert_stocks(p,plan.forecast[1]['stocks'])

    def test_cancel_keeps_official_quota_and_queue_keeps_reserved_space(self):
        h,p,l,site=self.fixture()
        self.assertTrue(l.do('build',site=site['id'],kind=PLANT))
        self.assertTrue(p.call_api('native:buildings','enqueue',{'site':site['id'],'kind':PLANT})['ok'])
        self.assertTrue(p.call_api('native:buildings','demolish',{'id':l.acts[-1]['data']['id']})['ok'])
        before=h.runtime.save_mod_state(h.world)
        data=collect(p,manage_military=False);j=Joint(data,p,RuleState(),horizon=2)
        self.assertEqual(data['plans'][site['id']]['space']['order_remaining'],0)
        for (sid,kind,t),v in j.x.items():
            if sid==site['id']:self.assertEqual(j.model.upper[next(iter(v.terms))],0)
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        self.assertTrue(data['orders'])

    def test_action_limited_whole_package_cannot_make_partial_trades(self):
        h,p,l,_=self.fixture();data=collect(p,manage_military=False)
        before=h.runtime.save_mod_state(h.world)
        l.max_actions=len(l.acts)+1
        plan=Plan('optimal',{WOOD:5},{ORE:2},[],[],{},[{}])
        self.assertFalse(execute(l,data,plan)['executed'])
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        l.max_actions=None
        plan=Plan('optimal',{}, {ORE:1000000},[],[],{},[{}])
        self.assertFalse(execute(l,data,plan)['executed'])
        self.assertEqual(h.runtime.save_mod_state(h.world),before)

    def test_previous_feasible_stage_is_reported_when_economic_search_has_no_incumbent(self):
        _,p,_,_=self.fixture()
        _,joint,_=self.fixed_model(p)
        service=joint.model.solve(-joint.service_objective)
        absent=Solution(None,'limited',None,None,None,0,'Time limit reached')
        with patch.object(joint.model,'solve',side_effect=[service,absent]):plan=joint.solve()
        self.assertEqual(plan.status,'feasible_fallback')
        self.assertEqual(plan.certificate['economic_status'],'limited')
        self.assertEqual(plan.certificate['solution_stage'],'previous_feasible')
        self.assertFalse(plan.certificate['optimality_proven'])
        self.assertTrue(plan.forecast)

    def test_unsolved_long_plan_restores_current_operations_with_joint_model(self):
        h,p,l,site=self.fixture()
        h.world.buildings.seed(site['id'],PLANT)
        h.world.resources._stock(p.actor,WOOD).set_amount(0)
        solve=Joint.solve
        def limited(joint):
            if joint.horizon>1:
                return Plan('limited',{}, {},[],[],{'status':'limited'},[])
            return solve(joint)
        with patch.object(Joint,'solve',limited):result=run(l,RuleState(),horizon=4)
        self.assertTrue(result['result']['executed'],result)
        self.assertEqual(result['certificate']['mode'],'operations_only')
        self.assertFalse(result['certificate']['optimality_proven'])
        self.assertEqual(result['plan'].construction,[])
        self.assertEqual(result['plan'].recruitment,[])
        self.assertEqual(result['plan'].forecast[0]['grid'],1)
        self.assertGreater(result['plan'].purchases.get(WOOD,0),0)
        self.settle(h,l);self.assert_stocks(p,result['plan'].forecast[0]['stocks'],cash_exact=False)

    def test_long_investment_plan_cannot_worsen_attainable_current_service(self):
        h,p,l,site=self.fixture();h.world.buildings.seed(site['id'],PLANT)
        h.world.resources._stock(p.actor,WOOD).set_amount(0)
        solve=Joint.solve
        def worse_service(joint):
            if joint.horizon>1:joint.model.limit(joint.period_unserved[0],lower=1)
            return solve(joint)
        with patch.object(Joint,'solve',worse_service):result=run(l,RuleState(),horizon=2)
        self.assertTrue(result['result']['executed'])
        self.assertEqual(result['certificate']['current_service_reference']['current_shortfall'],0)
        self.assertEqual(result['certificate']['mode'],'operations_only')
        self.assertEqual(result['plan'].forecast[0]['grid'],1)

    def test_joint_plan_respects_budget_and_actual_conservation(self):
        h,p,l,_=self.fixture()
        l.max_actions=len(l.acts)+5
        result=run(l,RuleState(profile='merchant'),manage_military=False,horizon=3)
        self.assertTrue(result['result']['executed'],result)
        self.assertTrue(all(r['ok'] for r in l.acts))
        self.assertLessEqual(len(l.acts),l.max_actions)
        plan=result['plan']
        self.assertTrue(plan.construction)
        self.assertFalse(set(plan.sales)&set(plan.purchases))
        self.assertFalse(plan.recruitment)
        # End is a protocol action, outside the optimization action budget.
        l.max_actions=None;self.settle(h,l)
        self.assert_stocks(p,plan.forecast[0]['stocks'],cash_exact=False)

    def test_terminal_wealth_uses_real_bulk_liquidation_not_marginal_price(self):
        _,p,_,_=self.fixture();data=collect(p,manage_military=False)
        joint=Joint(data,p,RuleState(),horizon=1)
        price=dict(data['market']['sell_prices'])[WOOD]
        result=joint.model.solve(joint.liquidation(WOOD,50,price))
        quote=p.call_api('economy:queries','trade_quote',{'resource':WOOD,'quantity':50,'side':'sell'})
        self.assertAlmostEqual(result.objective,quote['exact_total'])
        self.assertLess(result.objective,50*price)

    def test_speculative_price_appreciation_cannot_finance_certain_profit(self):
        from ruleai.v13.market import MarketModel
        _,p,_,_=self.fixture();data=collect(p,manage_military=False)
        buy=dict(data['market']['buy_prices']);sell=dict(data['market']['sell_prices'])
        def rising(model,own,extra,horizon):
            for t in range(horizon):yield ({r:v*10 for r,v in buy.items()},{r:v*10 for r,v in sell.items()})
        with patch.object(MarketModel,'path',rising):joint=Joint(data,p,RuleState(),horizon=2).build()
        for x in joint.x.values():joint.model.equal(x,0)
        plan=joint.solve()
        self.assertTrue(plan.forecast,plan.certificate)
        self.assertFalse(plan.purchases)

    def test_cash_rich_material_poor_joint_plan_still_invests(self):
        h,p,l,site=self.fixture()
        h.world.buildings.seed(site['id'],'native:oil-power-plant')
        for resource,amount in ((GOLD,6000),(ORE,2),(OIL,10)):
            h.world.resources._stock(p.actor,resource).set_amount(amount)
        result=run(l,RuleState(profile='merchant'),manage_military=False,horizon=3)
        self.assertTrue(result['result']['executed'],result['certificate'])
        self.assertTrue(result['plan'].construction)
        self.assertFalse(set(result['plan'].sales)&set(result['plan'].purchases))
        self.assertTrue(all(r['ok'] for r in l.acts))

    def test_new_troops_fit_real_post_growth_income_not_cash_or_nominal_capacity(self):
        for factories in (0,3):
            with self.subTest(factories=factories):
                h,p,l,site=self.fixture()
                h.world.buildings.seed(site['id'],'native:oil-power-plant')
                h.world.buildings.seed(site['id'],'native:barracks')
                if factories:h.world.buildings.seed(site['id'],'native:equipment-factory',count=factories)
                h.world.resources._stock(p.actor,OIL).set_amount(10)
                result=run(l,RuleState(),horizon=1)
                self.assertTrue(result['result']['executed'],result)
                self.assertEqual(sum(r['count'] for r in result['plan'].recruitment),int(bool(factories)))

    def test_bank_repayment_forecast_is_pure_and_matches_real_rounding(self):
        from mods.native.bank.objects import Loan
        h,p,_,_=self.fixture();bank=h.world.bank
        bank._loans[p.actor]=Loan(p.actor,100,103,3,0,bank.loan_rate)
        before=h.runtime.save_mod_state(h.world)
        schedule=p.call_api('economy:bank','position')['repayment_schedule']
        self.assertEqual(h.runtime.save_mod_state(h.world),before)
        entries=[]
        for period in range(3):entries.extend(bank.settle(period))
        repaid=next(e for e in entries if e.kind=='repayment')
        self.assertEqual(schedule[0]['period'],repaid.period)
        self.assertEqual(schedule[0]['gold'],-repaid.cash)

    def test_known_loan_maturity_extends_the_joint_planning_window(self):
        from mods.native.bank.objects import Loan
        h,_,l,_=self.fixture()
        h.world.bank._loans[l.player.actor]=Loan(l.player.actor,100,100,2,0,h.world.bank.loan_rate)
        result=run(l,RuleState(),manage_military=False,horizon=1)
        self.assertTrue(result['result']['executed'],result)
        self.assertEqual(result['certificate']['horizon'],2)
        self.assertGreaterEqual(result['plan'].forecast[1]['stocks'][GOLD],0)

    def test_tribute_carry_and_stock_match_real_subject_production(self):
        from mods.native.diplomacy.vassalage import Vassal
        from decimal import Decimal
        h,p,l,site=self.fixture()
        # Independent public subject fixture; no hidden foreign production used.
        cfg=self.cfg();cfg['nations'].append({'id':'scenario:lord','name':'宗主','position':[2,2]})
        h=GameHost.new(cfg);p=h.player('scenario:qin');l=Ledger(p)
        self.assertTrue(l.do('plan',text='按正式贡赋比例核对生产'))
        h.world.diplomacy._vassals[p.actor]=Vassal(p.actor,'scenario:lord','ordinary',15)
        site=next(s for s in collect(p,manage_military=False)['sites']['sites'] if dict(s['resources']).get(WOOD,0)>0)
        h.world.buildings.seed(site['id'],'native:lumber-camp',count=3)
        h.world.buildings._tribute_remainders[p.actor,'scenario:lord',WOOD]=Decimal('.9')
        _,_,plan=self.fixed_model(p)
        for client in h.clients.values():client.play=lambda *a,**kw:[]
        self.settle(h,l);self.assert_stocks(p,plan.forecast[0]['stocks'])

    def test_versioned_save_restores_same_joint_decisions_and_rejects_relabel(self):
        h=GameHost.new(self.cfg());h.cycle()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.json';h.save(path)
            loaded=GameHost.load(path,self.cfg())
            self.assertEqual(h.to_save(),loaded.to_save())
            h.cycle();loaded.cycle()
            self.assertEqual(h.to_save(),loaded.to_save())
            with self.assertRaises(SaveFormatError):GameHost.load(path,self.cfg('v13'))

    def test_old_v13_save_without_config_version_remains_v13(self):
        cfg=self.cfg('v13');h=GameHost.new(cfg);h.cycle()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.json';h.save(path)
            cfg['nations'][0].pop('rule_ai');loaded=GameHost.load(path,cfg)
            self.assertEqual(loaded.clients['scenario:qin'].version,'v13')
            self.assertEqual(loaded.to_save(),h.to_save())

    def test_absolute_recruitment_deadline_roundtrips_and_invalid_values_fail(self):
        state=RuleState(profile='merchant',recruit_due=7)
        self.assertEqual(RuleState.from_save(state.to_save()),state)
        for invalid in (-1,True,1.5,'7'):
            raw=state.to_save();raw['recruit_due']=invalid
            with self.assertRaises(ValueError):RuleState.from_save(raw)

    def test_experimental_factory_keeps_forecast_and_joint_version(self):
        from tests.test_parliament import CouncilBackend
        cfg=self.cfg();cfg.update(mods='mods.experimental.toml',parliament={
            'reviewer':{'model':'offline-fixture','api_key':'offline-fixture'}})
        h=GameHost.new(cfg)
        for services in h.services.values():services['council']._review_backend=CouncilBackend([True]*4)
        client=h.clients['scenario:qin']
        self.assertEqual(client.version,'v14')
        self.assertTrue(client.research);self.assertTrue(callable(client.forecast))
        h.cycle()
        self.assertTrue(client.last_economy['result']['executed'])


if __name__=='__main__':unittest.main()
