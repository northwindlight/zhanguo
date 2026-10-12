"""Synthetic market acceptance: real manifests, interchangeable modules, tea."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.resource import Resource
from engine.runtime import GameMod, ModError, Runtime, Stage, Unit
from engine.state import unit_context
from mods.native.resources.objects import ResourceAccounts
from mods.native.resources.types import RESOURCE_TYPES

ROOT = Path(__file__).resolve().parents[1]


class Tea(Resource):
    TYPE_ID = 'example:tea'
    NAME_KEY = 'resource.example:tea.name'


class TeaMod(GameMod):
    def mixins(self):return ()
    def content_definitions(self):return {Tea.TYPE_ID: Tea}


class IntegerTea(Tea):
    TYPE_ID = 'example:integer-tea'
    @classmethod
    def validate_amount(cls, amount):
        super().validate_amount(amount)
        if type(amount) is not int:raise ValueError('Integer tea only')


class TestMarket(unittest.TestCase):
    def runtime(self, *, fixed=False, tea=False, market_text=None, pricing=True, both=False):
        # Load the production declarations through Runtime.load, replacing
        # precisely one distribution member to select another pricing engine.
        with TemporaryDirectory() as directory:
            base = Path(directory)
            (base/'members').mkdir()
            members = [str(ROOT/'mods/native/resources/mod.toml')]
            if pricing:members.append(str(ROOT/('mods/examples/fixed_market_pricing/mod.toml' if fixed else 'mods/native/market_pricing/mod.toml')))
            if both:members.append(str(ROOT/'mods/examples/fixed_market_pricing/mod.toml'))
            if market_text is None:members.append(str(ROOT/'mods/native/market/mod.toml'))
            else:
                (base/'members/market.toml').write_text(market_text)
                members.append(str(base/'members/market.toml'))
            if tea:
                (base/'members/tea.toml').write_text('[Mod]\nId="example:tea-content"\nVersion="1"\nEntry="tests.test_market_objects:TeaMod"\nRequires=[]\n[State]\nOwns=[]\n[Capabilities]\nNeeds=[]\nProvides=[]\n')
                members.append(str(base/'members/tea.toml'))
            (base/'mod.toml').write_text('[Distribution]\nId="market-test"\nVersion="1"\nMembers='+json.dumps(members)+'\nRequiredState=["resources","market"]\n[[Hooks]]\nId="market.settlement"\n')
            return Runtime.load(directory)

    def world(self, **kwargs):
        runtime = self.runtime(**kwargs)
        world = runtime.world_class()()
        world.resources.open('Qin', {'native:gold':100000, 'native:food':10000})
        world.resources.open('Chu', {'native:gold':100000, 'native:food':10000})
        return world

    def assert_unchanged(self, world, before):
        self.assertEqual(world.runtime.save_mod_state(world), before)

    def test_default_distribution_loads_and_executes_declared_hook(self):
        runtime = Runtime.load(ROOT/'mods/market_acceptance')
        world = runtime.world_class()()
        self.assertEqual(runtime.state_owners['market'], 'native:market')
        self.assertEqual(runtime.stage_order, ['native:market.settle'])
        world.resolve_turn()
        self.assertEqual(world.market.period, 1)
        self.assertEqual(runtime.hooks, ('market.settlement',))

    def test_quote_is_pure_and_trade_matches_currency_inventory_and_price(self):
        world = self.world();before = world.runtime.save_mod_state(world)
        quote = world.market.quote('native:food', 10, 'buy', 'Qin')
        self.assertEqual(quote.gold, 16)
        self.assertAlmostEqual(quote.exact_total, 16.0125)
        self.assertEqual(quote.price_after, 1.55)
        self.assert_unchanged(world, before)
        trade = world.market.buy('Qin', 'native:food', 10)
        self.assertEqual(trade.gold, quote.gold)
        self.assertEqual(world.resources.balance('Qin', 'native:gold'), 100000-quote.gold)
        self.assertEqual(world.resources.balance('Qin', 'native:food'), 10010)
        self.assertEqual(dict(world.market.snapshot().prices)['native:food'], 1.55)
        self.assertEqual(trade.period, 0)
        with self.assertRaises(FrozenInstanceError):trade.gold = 100
        with self.assertRaises(FrozenInstanceError):world.market.snapshot().period = 3

    def test_failed_buy_sell_and_invalid_orders_leave_all_state_unchanged(self):
        world = self.world()
        world.resources.consume('Qin', 'native:gold', 100000)
        for action, args in ((world.market.buy, ('Qin','native:food',10)),
                             (world.market.sell, ('Qin','native:food',10001)),
                             (world.market.buy, ('missing','native:food',1)),
                             (world.market.buy, ('Qin','native:food',True)),
                             (world.market.buy, ('Qin','native:food',1.2)),
                             (world.market.buy, ('Qin','native:food',0)),
                             (world.market.quote, ('native:food',1,'invalid'))):
            before = world.runtime.save_mod_state(world)
            with self.subTest(args=args),self.assertRaises((ValueError,KeyError)):action(*args)
            self.assert_unchanged(world, before)

    def test_split_orders_match_batch_in_both_directions_and_at_price_limits(self):
        # At 1 participant food reaches its cap after 600 buys and floor
        # after 200 sales. Compare beyond both boundaries, not just small orders.
        for side in ('buy','sell'):
            for quantity in (10,100,700):
                with self.subTest(side=side,quantity=quantity):
                    batch, split = self.world(), self.world()
                    one = getattr(batch.market,side)('Qin','native:food',quantity)
                    rows = [getattr(split.market,side)('Qin','native:food',1) for _ in range(quantity)]
                    self.assertEqual(one.gold, sum(row.gold for row in rows))
                    for key in ('native:food','native:gold'):
                        self.assertEqual(batch.resources.balance('Qin',key),split.resources.balance('Qin',key))
                    self.assertAlmostEqual(dict(batch.market.snapshot().prices)['native:food'],dict(split.market.snapshot().prices)['native:food'])
                    self.assertAlmostEqual(dict(batch.market.snapshot().carry)['Qin'],dict(split.market.snapshot().carry)['Qin'])

    def test_participant_count_changes_depth_and_currency_carry_is_per_owner(self):
        world = self.world()
        one = world.market.quote('native:food',10,'buy','Qin')
        world.market.set_participants(4)
        four = world.market.quote('native:food',10,'buy','Qin')
        self.assertLess(four.exact_total, one.exact_total)
        world.market.buy('Qin','native:food',1)
        self.assertEqual(world.market.quote('native:food',1,'buy','Chu').carry_before,0)
        self.assertGreater(world.market.quote('native:food',1,'buy','Qin').carry_before,0)
        world.market.set_participants(0)
        self.assertGreater(world.market.quote('native:food',1,'buy').gold,0)

    def test_supply_demand_settlement_and_period_churn(self):
        world = self.world()
        world.market.buy('Qin','native:food',10)
        world.market.sell('Qin','native:food',5)
        churn = world.market.churn('Qin','native:food')
        self.assertEqual(churn.quantity,5)
        self.assertGreater(churn.loss,0)
        prior = dict(world.market.snapshot().prices)['native:food']
        closed = world.market.settle({'native:food':10},{'native:food':30},participants=4)
        self.assertEqual(len(closed),2)
        state = world.market.snapshot()
        self.assertEqual(dict(state.equilibrium)['native:food'],1.875)
        self.assertEqual(dict(state.prices)['native:food'],round(1.875+(prior-1.875)*0.9,3))
        self.assertEqual(world.market.churn('Qin','native:food').quantity,0)
        self.assertEqual((state.period,state.participants),(1,4))
        before = world.runtime.save_mod_state(world)
        # Fail after an earlier commodity has already been calculated.
        with self.assertRaises(ValueError):world.market.settle({'native:wood':float('nan')},{},participants=2)
        self.assert_unchanged(world,before)

    def test_replace_pricing_by_changing_only_distribution_member(self):
        native, fixed = self.world(), self.world(fixed=True)
        self.assertEqual(native.runtime.units['native:market'].mod.declaration,
                         fixed.runtime.units['native:market'].mod.declaration)
        self.assertEqual(native.market.ids,fixed.market.ids)
        q1 = native.market.quote('native:food',10,'buy','Qin')
        q2 = fixed.market.quote('native:food',10,'buy','Qin')
        self.assertAlmostEqual(q1.exact_total,16.0125)
        self.assertEqual((q2.exact_total,q2.gold,q2.price_after),(15,15,1.5))
        fixed.market.buy('Qin','native:food',10)
        sold = fixed.market.sell('Qin','native:food',10)
        self.assertEqual(sold.gold,15)
        self.assertEqual(fixed.resources.balance('Qin','native:gold'),100000)
        fixed.market.settle({}, {'native:food':100})
        self.assertEqual(dict(fixed.market.snapshot().equilibrium)['native:food'],1.5)
        native.market.settle({}, {'native:food':100})
        self.assertEqual(dict(native.market.snapshot().equilibrium)['native:food'],1.8)
        # Changing provider is an environment change, not implicit save migration.
        with self.assertRaises(ModError):fixed.runtime.check_save_dependencies(native.runtime.dependency_manifest())

    def test_missing_or_multiple_pricing_providers_are_rejected(self):
        with self.assertRaises(ModError):self.runtime(pricing=False)
        with self.assertRaises(ModError):self.runtime(both=True)

    def test_registered_tea_is_ignored_unless_market_explicitly_lists_it(self):
        for fixed in (False,True):
            with self.subTest(fixed=fixed):
                world = self.world(fixed=fixed,tea=True)
                world.resources.credit('Qin','example:tea',20)
                self.assertIn('tea',world.runtime.content['example'])
                self.assertIn('example:tea',world.resources.ids)
                self.assertNotIn('example:tea',world.market.ids)
                for action,args in ((world.market.quote,('example:tea',1,'buy','Qin')),
                                    (world.market.buy,('Qin','example:tea',1)),
                                    (world.market.sell,('Qin','example:tea',1))):
                    before = world.runtime.save_mod_state(world)
                    with self.assertRaisesRegex(ValueError,'not listed'):action(*args)
                    self.assert_unchanged(world,before)
                before = world.resources.to_save()
                world.market.settle({'example:tea':9999},{'example:tea':8888})
                self.assertEqual(world.resources.to_save(),before)
                self.assertNotIn('example:tea',dict(world.market.snapshot().prices))
                self.assertNotIn('example:tea',dict(world.market.snapshot().equilibrium))
                self.assertEqual(dict(world.market.snapshot().prices)['native:food'],1.5)
                saved = world.runtime.save_mod_state(world)
                world.runtime.load_mod_state(world,saved)
                self.assertEqual(world.resources.balance('Qin','example:tea'),20)
                self.assertEqual(world.runtime.save_mod_state(world),saved)

    def test_listing_new_tea_requires_only_market_declaration(self):
        text = (ROOT/'mods/native/market/mod.toml').read_text()
        text = text.replace('[Mod]', '[[Listings]]\nResource="example:tea"\nBasePrice=3\nDepth=10\n\n[Mod]')
        world = self.world(tea=True,market_text=text,fixed=True)
        bought = world.market.buy('Qin','example:tea',2)
        self.assertEqual(bought.gold,6)
        self.assertEqual(world.resources.balance('Qin','example:tea'),2)
        self.assertEqual(world.market.sell('Qin','example:tea',1).gold,3)
        self.assertEqual(world.resources.balance('Qin','example:tea'),1)
        world.market.settle({'example:tea':20},{'example:tea':1})
        self.assertIn('example:tea',dict(world.market.snapshot().prices))
        with self.assertRaises(ModError):self.world(market_text=text)
        bad = text.replace('Resource="example:tea"','Resource="native:gold"')
        with self.assertRaises(ModError):self.world(market_text=bad)

    def test_resource_exchange_validates_all_legs_and_concrete_subclass_rules(self):
        accounts = ResourceAccounts((*RESOURCE_TYPES,IntegerTea))
        accounts.open('Qin',{'native:gold':100,'native:food':10,'example:integer-tea':1})
        stock = accounts._stock('Qin','native:gold')
        for debits,credits in (({'native:gold':5},{'example:integer-tea':0.5}),
                               ({'native:food':11},{'native:gold':5}),
                               ({'native:gold':True},{'native:food':1}),
                               ({'native:gold':5},{'example:missing':1})):
            before = accounts.to_save()
            with self.assertRaises((ValueError,KeyError)):accounts.exchange('Qin',debits,credits)
            self.assertEqual(accounts.to_save(),before)
        accounts.exchange('Qin',{'native:gold':5},{'example:integer-tea':2})
        self.assertIs(stock,accounts._stock('Qin','native:gold'))
        self.assertEqual(stock.amount,95)
        self.assertEqual(accounts.balance('Qin','example:integer-tea'),3)

    def test_save_roundtrip_continues_with_same_prices_carry_and_resource_balances(self):
        world = self.world()
        world.market.buy('Qin','native:food',1)
        saved = json.loads(json.dumps(world.runtime.save_mod_state(world)))
        other = self.world()
        other.runtime.load_mod_state(other,saved)
        for side,quantity in (('buy',1),('sell',2),('buy',100)):
            self.assertEqual(getattr(world.market,side)('Qin','native:food',quantity),
                             getattr(other.market,side)('Qin','native:food',quantity))
        world.market.settle({'native:food':10},{'native:food':20})
        other.market.settle({'native:food':10},{'native:food':20})
        self.assertEqual(world.runtime.save_mod_state(world),other.runtime.save_mod_state(other))
        for field,value in (('currency','example:cash'),('period',True),('participants',-1),
                            ('prices',{}),('carry',{'Qin':1}),('trades',[{}])):
            bad = deepcopy(saved['native:market']);bad[field]=value
            before = world.market.to_save()
            with self.subTest(field=field),self.assertRaises(ValueError):world.market.restore(bad)
            self.assertEqual(world.market.to_save(),before)

    def test_interface_grants_prevent_direct_and_ungranted_mutations(self):
        class Client(GameMod):
            declaration = {'Interfaces':{'native:market':['quote','buy']}}
            def mixins(self):return ()
            def needs(self):return ('native:market',)
        loaded = self.runtime()
        runtime = Runtime([*loaded.units.values(),Unit('example:client',Client())],loaded.distribution)
        world = runtime.world_class()()
        world.resources.open('Qin',{'native:gold':100})
        with unit_context('example:client'):
            with self.assertRaises(ModError):world.market.buy('Qin','native:food',1)
            with self.assertRaises(ModError):world.market.set_participants(4)
            with self.assertRaises(ModError):runtime.object_call(world,'native:market','sell','Qin','native:food',1)
            with self.assertRaises(ModError):runtime.object_call(world,'native:resource-exchange','exchange','Qin',{'native:gold':1},{'native:food':1})
            trade = runtime.object_call(world,'native:market','buy','Qin','native:food',1)
        self.assertEqual(world.resources.balance('Qin','native:food'),1)
        self.assertEqual(world.resources.balance('Qin','native:gold'),100-trade.gold)

    def test_supply_inputs_and_trade_outputs_pass_through_stage_context(self):
        class FlowInput(GameMod):
            def mixins(self):return ()
            def stages(self):
                def supply(world, context):
                    context['market'] = {'production':{'native:food':10,'example:tea':100},
                                         'consumption':{'native:food':30}, 'participants':4}
                return (Stage('example:flow',supply,hook='market.inputs'),)
        loaded = self.runtime(tea=True)
        distribution = {**loaded.distribution,
                        'Hooks':[{'Id':'market.inputs'},{'Id':'market.settlement'}]}
        runtime = Runtime([*loaded.units.values(),Unit('example:flows',FlowInput())],distribution)
        world = runtime.world_class()()
        world.resources.open('Qin',{'native:gold':100})
        trade = world.market.buy('Qin','native:food',1)
        result = world.resolve_turn()
        self.assertEqual(result['market_trades'],(trade,))
        self.assertEqual(dict(world.market.snapshot().equilibrium)['native:food'],1.875)
        self.assertEqual(world.market.participants,4)
        self.assertNotIn('example:tea',world.market.ids)

    def test_rejected_pricing_results_never_commit_inventory_or_partial_prices(self):
        class BrokenEngine:
            def walk(self, *args):return {'next_price':2,'exact_total':1}
            def settle(self, listing, *args):
                from mods.native.market.contracts import PriceSettlement
                if listing.resource == 'native:wood':return None
                return PriceSettlement(2,2)
        class BrokenPricing(GameMod):
            def mixins(self):return ()
            def provides(self):return ('market:pricing',)
            def objects(self,world):return {'market:pricing':BrokenEngine()}
        loaded = self.runtime()
        units = [unit for key,unit in loaded.units.items() if key != 'native:market-pricing']
        runtime = Runtime([*units,Unit('example:broken-pricing',BrokenPricing())],loaded.distribution)
        world = runtime.world_class()()
        world.resources.open('Qin',{'native:gold':100})
        before = runtime.save_mod_state(world)
        with self.assertRaises(ValueError):world.market.buy('Qin','native:food',1)
        self.assert_unchanged(world,before)
        with self.assertRaises(ValueError):world.market.settle({}, {})
        self.assert_unchanged(world,before)

    def test_default_floor_cap_and_known_sell_price(self):
        world = self.world()
        sell = world.market.quote('native:food',10,'sell')
        self.assertAlmostEqual(sell.exact_total,14.0125)
        self.assertEqual(sell.price_after,1.45)
        floor = world.market.quote('native:food',700,'sell')
        cap = world.market.quote('native:food',700,'buy')
        self.assertEqual(floor.price_after,0.5)
        self.assertEqual(cap.price_after,4.5)
        # 200 units of descent (average 1), then 500 at the floor.
        self.assertAlmostEqual(floor.exact_total,(200*1+500*0.5)*0.95)
        # 600 units of ascent (average 3), then 100 at the cap.
        self.assertAlmostEqual(cap.exact_total,(600*3+100*4.5)*1.05)
