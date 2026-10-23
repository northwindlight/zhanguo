"""Economic regressions use synthetic inventories and real bound player APIs."""
from copy import deepcopy
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from host import GameHost, SaveFormatError
from ruleai.v11plus.ledger import Ledger
from ruleai.v13 import RuleClient, RuleState
from ruleai.v13.economy import Planner, fund, operating_targets
from ruleai.v13.market import MarketModel
from ruleai.v13.profiles import PROFILES

GOLD='native:gold'
WOOD='native:wood'
OIL='native:oil'


class TestRuleMarket(unittest.TestCase):
    def cfg(self, version='v13'):
        return {'map_size':12,'seed':73,'nations':[{'id':'scenario:qin','name':'秦',
            'client':'rule','rule_ai':version,'rule_seed':73}]}

    def host(self):
        h=GameHost.new(self.cfg())
        return h,h.player('scenario:qin')

    def test_random_profiles_differ_and_are_not_resampled_on_restore(self):
        profiles={RuleClient(seed=i).state.profile for i in range(80)}
        self.assertEqual(profiles,set(PROFILES))
        for name in PROFILES:
            original=RuleClient(profile=name)
            restored=RuleClient(RuleState.from_save(original.to_save()['state']),seed=9999)
            self.assertEqual(restored.state.profile,name)
            self.assertEqual(restored.reset_state(2).profile,name)
            self.assertEqual(restored.reset_state(2).market,{})
        self.assertEqual(RuleClient(seed=73).to_save(),RuleClient(seed=73).to_save())

    def test_trade_quote_is_pure_and_matches_real_depth_volume_and_coin_carry(self):
        h,p=self.host()
        p.call_api('native:government','plan',{'text':'按实际库存交易'})
        for side,quantity in [('sell',3),('buy',7),('buy',4),('sell',2)]:
            before=h.runtime.save_mod_state(h.world)
            args={'resource':WOOD,'quantity':quantity,'side':side}
            q=p.call_api('economy:queries','trade_quote',args)
            self.assertEqual(h.runtime.save_mod_state(h.world),before)
            r=p.call_api('economy:queries',side,{'resource':WOOD,'quantity':quantity})
            self.assertTrue(r['ok'],r)
            for key in ('gold','exact_total','carry_after'):
                self.assertEqual(q[key],r['data'][key])
        with self.assertRaises(ValueError):
            p.call_api('economy:queries','trade_quote',{'resource':WOOD,'quantity':100000,'side':'buy'})
        tool=p.query('query',{'panel':'trade_quote','resource':'wood','quantity':2,'side':'buy'})
        self.assertEqual(tool['quantity'],2)

    def test_unaffordable_or_unavailable_recipe_does_not_buy_a_partial_package(self):
        h,p=self.host()
        p.call_api('native:government','plan',{'text':'先核对整单成本'})
        ledger=Ledger(p)
        before=h.runtime.save_mod_state(h.world)
        self.assertFalse(fund(ledger,{WOOD:205,'native:ore':100000}))
        self.assertFalse(fund(ledger,{WOOD:205,GOLD:100000}))
        self.assertEqual(ledger.acts,[])
        self.assertEqual(before,h.runtime.save_mod_state(h.world))

    def test_fuel_for_both_types_of_power_plant_is_reserved_including_pending(self):
        catalog={
            'wood-plant':{'inputs':{WOOD:1},'outputs':{},'energy_output':2},
            'oil-plant':{'inputs':{OIL:1},'outputs':{},'energy_output':8},
            'factory':{'inputs':{'native:ore':1},'outputs':{'native:equipment':2},'energy_output':0}}
        buildings=[{'kind':'wood-plant','remaining':0},{'kind':'oil-plant','remaining':1},
                   {'kind':'factory','remaining':0}]
        prices={'buy':{WOOD:2,OIL:6,'native:ore':2.5},'sell':{'native:equipment':8}}
        targets=operating_targets(catalog,buildings,{},prices,3)
        self.assertEqual(targets[WOOD],3)
        self.assertEqual(targets[OIL],3)
        self.assertEqual(targets['native:ore'],3)
        prices['sell']['native:equipment']=.1
        self.assertNotIn('native:ore',operating_targets(catalog,buildings,{},prices,3))
        self.assertEqual(operating_targets(catalog,buildings,{},prices,3)[OIL],3)

    def test_oversupply_and_copied_investments_reduce_expected_export_returns(self):
        _,p=self.host()
        market=p.call_api('economy:queries','market')
        model=MarketModel(market,{},8)
        food='native:food'
        extra={food:10}
        normal=model.value(extra,{},extra,48)
        crowded=model.value(extra,{},extra,48,crowd=8)
        self.assertLess(crowded,normal)
        self.assertLess(normal,10*dict(market['sell_prices'])[food])
        old=model.remember({food:2})
        next_market=deepcopy(market)
        next_market['period']+=1
        next_market['inventory']=[[k,v+(10 if k==food else 0)] for k,v in market['inventory']]
        observed=MarketModel(next_market,old,8)
        self.assertEqual(observed.external[food],8) # exclude our two units
        self.assertLess(observed.value(extra,{},extra,48),normal)

    def test_profiles_stagger_actual_fuel_orders_without_starving_the_grid(self):
        schedules=[]
        for name in PROFILES:
            h,p=self.host()
            site=p.call_api('native:buildings','sites')['sites'][0]['id']
            h.world.buildings.seed(site,'native:wood-power-plant')
            h.world.resources._stock(p.actor,WOOD).set_amount(0)
            state=RuleState(profile=name)
            orders=[]
            for turn in range(6):
                ledger=Ledger(p)
                self.assertTrue(ledger.do('plan',text='维持燃料储备'))
                planner=Planner(ledger,state)
                planner.operations();planner.finish()
                orders.extend((turn,r['data']['quantity']) for r in ledger.acts
                              if r['ok'] and r['action']=='buy' and r['data']['resource']==WOOD)
                self.assertGreaterEqual(h.world.resources.balance(p.actor,WOOD),1)
                self.assertTrue(ledger.do('end_turn',summary='燃料到库'))
                h.cycle()
            schedules.append(tuple(orders))
        self.assertGreaterEqual(len(set(schedules)),4)

    def test_state_roundtrip_and_invalid_memory_are_rejected(self):
        h,p=self.host()
        h.cycle()
        state=h.clients[p.actor].state
        self.assertEqual(RuleState.from_save(state.to_save()),state)
        for field,value in [('flow',math.nan),('period',-1),('inventory',{WOOD:-1}),
                            ('external',{WOOD:math.inf})]:
            bad=state.to_save();bad['market'][field]=value
            with self.assertRaises(ValueError):RuleState.from_save(bad)

    def test_real_continuation_matches_uninterrupted_random_strategy_and_forecast(self):
        h,p=self.host()
        for _ in range(5):h.cycle()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'synthetic.json'
            h.save(path)
            loaded=GameHost.load(path,self.cfg())
            self.assertEqual(loaded.to_save(),h.to_save())
            h.cycle();loaded.cycle()
            self.assertEqual(loaded.to_save(),h.to_save())
            cfg=self.cfg('v11plus')
            with self.assertRaises(SaveFormatError):GameHost.load(path,cfg)

    def test_legacy_can_still_be_selected_without_reinterpreting_its_state(self):
        cfg=self.cfg('v11plus')
        h=GameHost.new(cfg);h.cycle()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'legacy.json';h.save(path)
            cfg['nations'][0].pop('rule_ai')
            restored=GameHost.load(path,cfg)
            self.assertEqual(restored.clients['scenario:qin'].version,'v11plus')
            self.assertEqual(restored.to_save(),h.to_save())


if __name__=='__main__':unittest.main()
