"""Independent accounting acceptance over real building/market facts."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import unittest

from engine.runtime import GameMod, ModError, Runtime, Stage, Unit
from engine.state import unit_context
from mods.native.economy_reports.contracts import EconomicFact, Money
from mods.native.economy_reports.mod import EconomyMod
from mods.native.economy_valuation.mod import ValuationMod
from tests.test_building_objects import TeaContent

ROOT = Path(__file__).resolve().parents[1]


class TestEconomy(unittest.TestCase):
    def runtime(self, *, every=2, tea=False, tea_price=None, hooks=None, extra=()):
        loaded = Runtime.load(ROOT/'mods/economy_acceptance')
        units = list(loaded.units.values())
        for unit in units:
            if unit.name=='native:economy':
                declaration = deepcopy(unit.mod.declaration);declaration['ReportEvery']=every
                module=EconomyMod();module.configure(declaration,unit.mod.declaration_path);unit.mod=module
            if unit.name=='native:economy-valuation' and tea_price is not None:
                declaration = deepcopy(unit.mod.declaration);declaration['Prices']={'example:tea':tea_price}
                module=ValuationMod();module.configure(declaration,unit.mod.declaration_path);unit.mod=module
        distribution = deepcopy(loaded.distribution)
        if hooks is not None:distribution['Hooks']=[{'Id':key} for key in hooks]
        if tea:units.append(Unit('example:tea-content',TeaContent()))
        return Runtime([*units,*extra],distribution)

    def world(self, **kwargs):
        runtime = self.runtime(**kwargs);world=runtime.world_class()()
        world.resources.open('Qin',{'native:gold':10000,'native:wood':100,'native:food':10,'native:ore':10,'native:oil':10})
        world.resources.open('Chu',{})
        world.buildings.register_site('example:qin','Qin',(0,0),resources={'native:farmland':20,'native:wood':20,'native:ore':20,'native:gold':20})
        return world

    def state(self, world):return world.runtime.save_mod_state(world)
    def qin(self, rows):return next(row for row in rows if row.owner=='Qin')

    def test_production_value_added_gdp_excludes_investment_and_trade(self):
        world = self.world()
        for kind in ('native:farm','native:mine','native:wood-power-plant','native:supply-factory'):
            world.buildings.seed('example:qin',kind)
        world.buildings.build('Qin','example:qin','native:watchtower')
        trade=world.market.buy('Qin','native:wood',3)
        result=world.resolve_turn();row=self.qin(result['economy_turns'])
        prices=dict(world.market.snapshot().prices)
        expected_output=prices['native:food']+prices['native:ore']+2*prices['native:supply']
        expected_inputs=2*prices['native:food']+prices['native:ore']+prices['native:wood']
        self.assertAlmostEqual(row.production.total,expected_output)
        self.assertAlmostEqual(row.inputs.total,expected_inputs)
        self.assertAlmostEqual(row.gdp.total,expected_output-expected_inputs)
        self.assertAlmostEqual(row.investment.total,60+30*prices['native:wood'])
        self.assertEqual(row.imports,trade.gold)
        self.assertEqual(row.exports,0)
        self.assertEqual(row.spending,row.investment)
        self.assertEqual(row.cumulative_spending,row.investment)
        self.assertEqual(result['economy_publications'],())

    def test_report_windows_use_latest_gdp_and_accumulated_investment(self):
        world=self.world()
        world.buildings.build('Qin','example:qin','native:farm')
        first=self.qin(world.resolve_turn()['economy_turns'])
        world.buildings.build('Qin','example:qin','native:farm')
        result=world.resolve_turn();second=self.qin(result['economy_turns']);report=self.qin(result['economy_publications'])
        self.assertEqual((report.start,report.end,report.span),(0,1,2))
        self.assertEqual(report.gdp,second.gdp)
        self.assertEqual(first.gdp.total,0)
        self.assertAlmostEqual(report.investment.total,first.investment.total+second.investment.total)
        self.assertIsNone(report.gdp_growth)
        self.assertEqual(report.military_ratio,0)
        self.assertAlmostEqual(second.cumulative_spending.total,report.investment.total)
        world.resolve_turn();result=world.resolve_turn()
        next_report=self.qin(result['economy_publications'])
        self.assertEqual((next_report.start,next_report.end,next_report.span),(2,3,2))
        self.assertEqual(next_report.investment.total,0)
        self.assertEqual(next_report.investment_growth,-1)
        self.assertAlmostEqual(next_report.gdp_growth,next_report.gdp.total/report.gdp.total-1)
        self.assertAlmostEqual(self.qin(result['economy_turns']).cumulative_spending.total,report.investment.total)

    def test_inventory_and_replacement_assets_include_castle_wood_and_current_owner(self):
        world=self.world(every=1)
        world.buildings.seed('example:qin','native:castle',2)
        world.buildings.seed('example:qin','native:farm')
        world.buildings.transfer_site('example:qin','Chu')
        result=world.resolve_turn();qin=self.qin(result['economy_turns']);chu=next(row for row in result['economy_turns'] if row.owner=='Chu')
        prices=dict(world.market.snapshot().prices)
        self.assertEqual(qin.building_assets.total,0)
        self.assertAlmostEqual(chu.building_assets.total,50+100+20+55*prices['native:wood'])
        self.assertAlmostEqual(chu.inventory_assets.total,prices['native:food'])
        self.assertAlmostEqual(chu.total_assets.total,chu.inventory_assets.total+chu.building_assets.total)
        expected=10000+100*prices['native:wood']+10*prices['native:food']+10*prices['native:ore']+10*prices['native:oil']
        self.assertAlmostEqual(qin.inventory_assets.total,expected)

    def test_cash_outputs_and_negative_cash_balances_are_face_value(self):
        world=self.world()
        world.buildings.seed('example:qin','native:gold-mine')
        row=self.qin(world.resolve_turn()['economy_turns'])
        self.assertEqual(row.gdp.total,10)
        self.assertEqual(row.cash_production,10)
        world.resources._stock('Qin','native:gold').set_amount(-5)
        quotes=world.runtime.object_call(world,'economy:valuation','quote',{'native:gold':-5})
        self.assertEqual((quotes[0].value,quotes[0].source),(-5,'cash'))
        self.assertEqual(world.resources.balance('Qin','native:gold'),-5)

    def test_tea_without_declared_price_is_explicitly_unpriced(self):
        world=self.world(tea=True,every=1)
        world.buildings.seed('example:qin','example:tea-garden')
        result=world.resolve_turn();row=self.qin(result['economy_turns'])
        self.assertEqual(world.resources.balance('Qin','example:tea'),2)
        self.assertEqual(row.gdp.known,0)
        self.assertEqual(row.gdp.unpriced,('example:tea',))
        self.assertIsNone(row.gdp.total)
        self.assertIsNone(row.inventory_assets.total)
        self.assertIsNone(row.total_assets.total)
        self.assertIsNone(self.qin(result['economy_publications']).trade_ratio)
        self.assertNotIn('example:tea',dict(world.market.snapshot().prices))
        chu=next(row for row in result['economy_turns'] if row.owner=='Chu')
        self.assertEqual(chu.inventory_assets.total,0) # zero tea does not make all accounts unpriced

    def test_explicit_tea_valuation_completes_accounts_without_listing_it(self):
        world=self.world(tea=True,tea_price=3,every=1)
        world.buildings.seed('example:qin','example:tea-garden')
        result=world.resolve_turn();row=self.qin(result['economy_turns'])
        self.assertEqual(row.gdp.total,6)
        self.assertTrue(row.inventory_assets.complete)
        self.assertNotIn('example:tea',world.market.ids)
        quote=world.runtime.object_call(world,'economy:valuation','quote',{'example:tea':2})[0]
        self.assertEqual((quote.price,quote.value,quote.source),(3,6,'declared'))
        self.assertEqual(self.qin(result['economy_publications']).trade_ratio,0)

    def test_duplicate_facts_and_closed_period_replay_do_not_double_count(self):
        world=self.world()
        facts=(EconomicFact('example:cost/1','Qin',0,'construction',(('native:gold',5),)),)
        self.assertEqual(world.economy.ingest(facts),1)
        before=world.economy.to_save()
        self.assertEqual(world.economy.ingest(facts),0)
        self.assertEqual(world.economy.to_save(),before)
        row=self.qin(world.economy.close(0))
        self.assertEqual(row.investment.total,5)
        before=world.economy.to_save()
        self.assertEqual(world.economy.settle(0,facts),(row,next(r for r in world.economy.turns() if r.owner=='Chu')))
        self.assertEqual(world.economy.to_save(),before)
        with self.assertRaises(ValueError):world.economy.ingest((replace(facts[0],inputs=(('native:gold',6),)),))
        self.assertEqual(world.economy.to_save(),before)
        with self.assertRaises(ValueError):world.economy.settle(0,(EconomicFact('example:future','Qin',1,'construction'),))
        self.assertEqual(world.economy.to_save(),before)

    def test_batch_ingest_and_asset_failure_are_atomic(self):
        world=self.world()
        good=EconomicFact('example:good','Qin',0,'production',outputs=(('native:food',1),))
        bad=EconomicFact('example:bad','Qin',0,'production',outputs=(('example:missing',1),))
        before=world.economy.to_save()
        with self.assertRaises(KeyError):world.economy.ingest((good,bad))
        self.assertEqual(world.economy.to_save(),before)
        original=world.economy._replacement
        world.economy._replacement=lambda owner:(('example:missing',1),)
        with self.assertRaises(KeyError):world.economy.settle(0,(good,))
        self.assertEqual(world.economy.to_save(),before)
        world.economy._replacement=original
        self.assertEqual(self.qin(world.economy.settle(0,(good,))).gdp.total,1.5)

    def test_actual_trade_payments_and_ratio_use_integer_currency_not_repricing(self):
        world=self.world(every=1)
        first=world.market.buy('Qin','native:food',3)
        second=world.market.sell('Qin','native:food',2)
        result=world.resolve_turn();row=self.qin(result['economy_turns']);report=self.qin(result['economy_publications'])
        self.assertEqual((row.imports,row.exports),(first.gold,second.gold))
        self.assertEqual(row.gdp.total,0)
        self.assertEqual(row.spending.total,0)
        self.assertEqual(report.trade_ratio,(first.gold+second.gold)/first.gold)
        self.assertEqual(len(world.economy.snapshot().events),2)

    def test_explicit_recruit_and_military_facts_do_not_change_gdp(self):
        class ArmyFacts(GameMod):
            def mixins(self):return ()
            def stages(self):
                def provide(world,context):
                    context['economic_facts']=(EconomicFact('example:recruit','Qin',0,'recruit',(('native:gold',50),('native:food',5))),
                                               EconomicFact('example:supply','Qin',0,'military',(('native:supply',1),)))
                return (Stage('example:army-facts',provide,hook='economy.inputs'),)
        world=self.world(every=1,extra=(Unit('example:army-fixture',ArmyFacts()),),hooks=(
            'buildings.production','market.settlement','buildings.completion','economy.inputs','economy.reports'))
        result=world.resolve_turn();row=self.qin(result['economy_turns'])
        self.assertEqual(row.gdp.total,0)
        self.assertEqual(row.recruitment.total,57.5)
        self.assertEqual(row.military.total,6)
        self.assertEqual(row.spending.total,63.5)
        self.assertEqual(row.cumulative_spending.total,63.5)
        self.assertIsNone(self.qin(result['economy_publications']).military_ratio)
        # This is an explicit receipt fixture, not a production adapter to army.
        self.assertNotIn('native:army',world.runtime.units)

    def test_save_and_resume_preserve_valuations_replay_ids_and_growth(self):
        world=self.world(tea=True,tea_price=3)
        world.buildings.build('Qin','example:qin','example:tea-garden')
        world.market.buy('Qin','native:food',3)
        world.resolve_turn()
        saved=json.loads(json.dumps(self.state(world)))
        other=self.world(tea=True,tea_price=3);other.runtime.load_mod_state(other,saved)
        self.assertEqual(self.state(other),saved)
        for _ in range(3):
            world.resolve_turn();other.resolve_turn()
            self.assertEqual(self.state(other),self.state(world))
        # Native (not JSON encoded) save protocol also roundtrips.
        native=self.state(other);other.runtime.load_mod_state(other,native)
        self.assertEqual(self.state(other),native)
        original=world.economy.snapshot().events[0]
        before=world.economy.to_save()
        self.assertEqual(world.economy.ingest((original.fact,)),0)
        self.assertEqual(world.economy.to_save(),before)

    def test_corrupt_economy_restore_is_rejected_without_mutation(self):
        world=self.world(every=1)
        world.buildings.seed('example:qin','native:farm')
        world.resolve_turn();saved=json.loads(json.dumps(world.economy.to_save()))
        for damage in ('policy','duplicate_fact','unknown_category','unknown_resource','nan','totals','period','missing_publication','unknown_owner','extra'):
            bad=deepcopy(saved)
            if damage=='policy':bad['report_every']=2
            if damage=='duplicate_fact':bad['events'].append(deepcopy(bad['events'][0]))
            if damage=='unknown_category':bad['events'][0]['fact']['category']='bank'
            if damage=='unknown_resource':bad['events'][0]['fact']['outputs'][0][0]='example:missing'
            if damage=='nan':bad['turns'][0]['gdp']['known']=float('nan')
            if damage=='totals':bad['turns'][0]['gdp']['known']=999
            if damage=='period':bad['next_period']=0
            if damage=='missing_publication':bad['reports']=[]
            if damage=='unknown_owner':bad['turns'][0]['owner']='missing'
            if damage=='extra':bad['secret']=1
            before=world.economy.to_save()
            with self.subTest(damage=damage),self.assertRaises(ValueError):world.economy.restore(bad)
            self.assertEqual(world.economy.to_save(),before)

    def test_permissions_and_snapshots_prevent_cross_domain_mutation(self):
        class Client(GameMod):
            declaration={'Interfaces':{'native:economy':['snapshot','reports']}}
            def mixins(self):return ()
            def needs(self):return ('native:economy',)
        world=self.world(extra=(Unit('example:client',Client()),))
        world.resolve_turn()
        with unit_context('example:client'):
            with self.assertRaises(ModError):world.economy.close(1)
            with self.assertRaises(ModError):world.runtime.object_call(world,'native:economy','ingest',())
            with self.assertRaises(ModError):world.runtime.object_call(world,'native:resource-inspection','snapshot','Qin')
            snapshot=world.runtime.object_call(world,'native:economy','snapshot')
        with self.assertRaises(FrozenInstanceError):snapshot.turns[0].gdp.known=5
        self.assertEqual(world.runtime.read_state(world,'economy'),snapshot)

    def test_report_order_is_distribution_declared_and_dependencies_checked(self):
        default=self.world()
        self.assertEqual(default.runtime.stage_order[-1],'native:economy.reports')
        # Reporting before construction completion is a legal package choice:
        # new buildings are still pending and excluded from building assets.
        alternate=self.world(hooks=('buildings.production','market.settlement','economy.reports','buildings.completion'))
        for world in (default,alternate):world.buildings.build('Qin','example:qin','native:farm')
        first=self.qin(default.resolve_turn()['economy_turns'])
        second=self.qin(alternate.resolve_turn()['economy_turns'])
        self.assertGreater(first.building_assets.total,0)
        self.assertEqual(second.building_assets.total,0)
        self.assertEqual(first.investment,second.investment)
        with self.assertRaises(ModError):self.runtime(hooks=('economy.reports','buildings.production','market.settlement','buildings.completion'))

    def test_resource_valuation_overrides_are_used_and_read_only(self):
        from engine.resource import Resource
        class DoubleResource(Resource):
            TYPE_ID='example:double';NAME_KEY='resource.example:double.name'
            @classmethod
            def value_of(cls,amount,price):return super().value_of(amount,price)*2
        class Extra(GameMod):
            def mixins(self):return ()
            def content_definitions(self):return {DoubleResource.TYPE_ID:DoubleResource}
        loaded=self.runtime(extra=(Unit('example:double-content',Extra()),))
        unit=loaded.units['native:economy-valuation']
        declaration=deepcopy(unit.mod.declaration);declaration['Prices']={'example:double':3}
        module=ValuationMod();module.configure(declaration,unit.mod.declaration_path);unit.mod=module
        runtime=Runtime(loaded.units.values(),loaded.distribution);world=runtime.world_class()()
        world.resources.open('Qin',{'example:double':2})
        before=self.state(world)
        quote=runtime.object_call(world,'economy:valuation','quote',{'example:double':2})[0]
        self.assertEqual(quote.value,12)
        self.assertEqual(self.state(world),before)
        row=self.qin(world.resolve_turn()['economy_turns'])
        self.assertEqual(row.inventory_assets.total,12)

    def test_market_price_takes_precedence_over_declared_price(self):
        loaded=self.runtime()
        unit=loaded.units['native:economy-valuation'];declaration=deepcopy(unit.mod.declaration)
        declaration['Prices']={'native:food':99}
        module=ValuationMod();module.configure(declaration,unit.mod.declaration_path);unit.mod=module
        runtime=Runtime(loaded.units.values(),loaded.distribution);world=runtime.world_class()()
        world.resources.open('Qin',{})
        quote=runtime.object_call(world,'economy:valuation','quote',{'native:food':2})[0]
        self.assertEqual((quote.price,quote.value,quote.source),(1.5,3,'market'))
        with self.assertRaises(ModError):self.world(tea_price=3) # no resource registered

    def test_same_hook_before_after_and_inserted_phase_are_declared(self):
        from engine.runtime import Unit
        class ReceiptStage(GameMod):
            def mixins(self):return ()
            def stages(self):
                def receipt(world,context):
                    context['economic_facts']=(EconomicFact('example:injected','Qin',0,'construction',(('native:gold',7),)),)
                return (Stage('z.receipt',receipt,hook='economy.reports'),)
        loaded=self.runtime(extra=(Unit('example:receipt-stage',ReceiptStage()),))
        unit=loaded.units['native:economy'];declaration=deepcopy(unit.mod.declaration)
        declaration['Stages'][0]['After']=['z.receipt']
        module=EconomyMod();module.configure(declaration,unit.mod.declaration_path);unit.mod=module
        runtime=Runtime(loaded.units.values(),loaded.distribution);world=runtime.world_class()()
        world.resources.open('Qin',{})
        self.assertLess(runtime.stage_order.index('z.receipt'),runtime.stage_order.index('native:economy.reports'))
        self.assertEqual(self.qin(world.resolve_turn()['economy_turns']).investment.total,7)

    def test_late_account_has_partial_first_report_window(self):
        world=self.world(every=3)
        world.resolve_turn();world.resolve_turn()
        world.resources.open('Han',{'native:gold':5})
        result=world.resolve_turn();report=next(row for row in result['economy_publications'] if row.owner=='Han')
        self.assertEqual((report.start,report.end,report.span),(2,2,1))
        self.assertEqual(report.inventory_assets.total,5)
        saved=self.state(world);world.runtime.load_mod_state(world,saved)
        self.assertEqual(self.state(world),saved)

    def test_input_only_production_can_have_negative_gdp(self):
        world=self.world(every=1)
        fact=EconomicFact('example:fuel-only','Qin',0,'production',(('native:wood',2),))
        row=self.qin(world.economy.settle(0,(fact,)))
        self.assertEqual(row.gdp.total,-4)
        self.assertEqual(row.spending.total,0)
        self.assertIsNone(world.economy.reports('Qin')[0].gdp_growth)
