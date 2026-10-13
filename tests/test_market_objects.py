"""Synthetic market acceptance: real manifests, interchangeable modules, tea."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.resource import Resource
from engine.runtime import GameMod, ModError, Runtime, Stage, Unit
from engine.state import unit_context
from mods.native.resources.objects import ResourceAccounts
from mods.native.resources.types import RESOURCE_TYPES

ROOT = Path(__file__).resolve().parents[1]

# The acceptance declarations are the shipped ones, so the curve's own numbers
# are the reference here instead of a second copy of the formula.
FOOD_DEPTH = 1920
FOOD_BASE = 1.5
ELASTICITY = 1.0
FLOW = 0.25
REFILL = 0.0
SPREAD = 0.10


def sweep(price, depth, quantity, side, volume=0, elasticity=ELASTICITY, spread=SPREAD):
    """Independent restatement of the published schedule: one price per unit."""
    sign = 1 if side == 'buy' else -1
    units = sum(price*math.exp(sign*elasticity*(volume+step)/depth) for step in range(quantity))
    return units*(1+sign*spread/2)


def level(base, reference, inventory, elasticity=ELASTICITY):
    return base*math.exp(elasticity*(reference-inventory)/reference)


def drift(depth, net_flow, coefficient=FLOW):
    """Net flow moves the reference stock; nothing else does."""
    return max(1.0, depth+coefficient*net_flow)


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
        self.assertEqual(dict(world.market.snapshot().prices)['native:food'], FOOD_BASE)
        quote = world.market.quote('native:food', 10, 'buy', 'Qin')
        self.assertAlmostEqual(quote.exact_total, sweep(FOOD_BASE, FOOD_DEPTH, 10, 'buy'))
        self.assertEqual(quote.gold, 16)
        # The period's quote is published, not consumed: nothing moves it.
        self.assertEqual((quote.price_before, quote.price_after), (FOOD_BASE, FOOD_BASE))
        self.assert_unchanged(world, before)
        trade = world.market.buy('Qin', 'native:food', 10)
        self.assertEqual(trade.gold, quote.gold)
        self.assertEqual(world.resources.balance('Qin', 'native:gold'), 100000-quote.gold)
        self.assertEqual(world.resources.balance('Qin', 'native:food'), 10010)
        self.assertEqual(dict(world.market.snapshot().inventory)['native:food'], FOOD_DEPTH-10)
        self.assertEqual(dict(world.market.snapshot().prices)['native:food'], FOOD_BASE)
        self.assertEqual(trade.period, 0)
        with self.assertRaises(FrozenInstanceError):trade.gold = 100
        with self.assertRaises(FrozenInstanceError):world.market.snapshot().period = 3

    def test_arrival_order_cannot_move_anybodys_quote(self):
        def play(order):
            world = self.world();rows = {}
            for owner, side, quantity in order:
                rows.setdefault(owner, []).append(getattr(world.market, side)(owner, 'native:food', quantity))
            return world, rows

        first_world, first = play([('Qin','buy',40), ('Chu','buy',40)])
        second_world, second = play([('Chu','buy',40), ('Qin','buy',40)])
        for owner in ('Qin','Chu'):
            self.assertEqual([row.gold for row in first[owner]], [row.gold for row in second[owner]], owner)
            self.assertEqual([row.exact_total for row in first[owner]], [row.exact_total for row in second[owner]], owner)
        self.assertEqual(first_world.market.snapshot().inventory, second_world.market.snapshot().inventory)
        self.assertEqual(first_world.market.snapshot().prices, second_world.market.snapshot().prices)

    def test_large_orders_pay_a_premium_and_large_lots_sell_at_a_discount(self):
        world = self.world()
        for side, order in (('buy', 1), ('sell', -1)):
            units = [world.market.quote('native:food', quantity, side).exact_total/quantity
                     for quantity in (1, 10, 100, 900)]
            self.assertEqual(units, sorted(units, reverse=order == -1), side)
            self.assertGreater(units[-1]*order, units[0]*order, side)
        # The premium is a function of the buffer it sweeps: halving the depth
        # for the same quantity doubles the sweep.
        shallow = self.world(market_text=self.declaration(food_depth=FOOD_DEPTH//2))
        deep = self.world()
        self.assertAlmostEqual(shallow.market.quote('native:food', 100, 'buy').exact_total/100,
                               sweep(FOOD_BASE, FOOD_DEPTH//2, 100, 'buy')/100)
        self.assertGreater(shallow.market.quote('native:food', 100, 'buy').exact_total,
                           deep.market.quote('native:food', 100, 'buy').exact_total)

    def declaration(self, food_depth=FOOD_DEPTH):
        text = (ROOT/'mods/native/market/mod.toml').read_text()
        return text.replace('Resource = "native:food"\nBasePrice = 1.5\nDepth = 1920\nInitialInventory = 1920',
                            f'Resource = "native:food"\nBasePrice = 1.5\nDepth = {food_depth}\nInitialInventory = {food_depth}')

    def test_split_orders_cannot_dodge_the_premium(self):
        # The sweep starts where this owner's own trading left off, so the only
        # difference between one order and many is integer currency rounding.
        for side in ('buy','sell'):
            for quantity in (10, 100, 700):
                with self.subTest(side=side,quantity=quantity):
                    batch, split = self.world(), self.world()
                    if side == 'sell':batch.resources.credit('Qin','native:food',quantity);split.resources.credit('Qin','native:food',quantity)
                    one = getattr(batch.market,side)('Qin','native:food',quantity)
                    rows = [getattr(split.market,side)('Qin','native:food',1) for _ in range(quantity)]
                    self.assertLessEqual(abs(one.gold-sum(row.gold for row in rows)), 1)
                    for key in ('native:food','native:gold'):
                        self.assertLessEqual(abs(batch.resources.balance('Qin',key)-split.resources.balance('Qin',key)), 1)
                    self.assertEqual(batch.market.snapshot().inventory,split.market.snapshot().inventory)
                    self.assertAlmostEqual(dict(batch.market.snapshot().carry)['Qin'],dict(split.market.snapshot().carry)['Qin'])

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

    def test_inventory_is_a_hard_limit_and_carry_is_per_owner(self):
        world = self.world()
        world.market.buy('Qin','native:food',1)
        self.assertEqual(world.market.quote('native:food',1,'buy','Chu').carry_before,0)
        self.assertGreater(world.market.quote('native:food',1,'buy','Qin').carry_before,0)
        world.market.buy('Qin','native:food',FOOD_DEPTH-6)
        before = world.runtime.save_mod_state(world)
        with self.assertRaisesRegex(ValueError,'insufficient'):
            world.market.buy('Chu','native:food',6)
        self.assert_unchanged(world,before)
        with self.assertRaisesRegex(ValueError,'insufficient'):
            world.market.quote('native:food',6,'buy','Chu')
        self.assertEqual(world.market.buy('Chu','native:food',5).quantity,5)
        self.assertEqual(dict(world.market.snapshot().inventory)['native:food'],0)

    def test_period_close_drifts_the_reference_and_reprices(self):
        world = self.world()
        world.market.buy('Qin','native:food',1400)
        self.assertEqual(dict(world.market.snapshot().prices)['native:food'],FOOD_BASE)
        closed = world.market.settle()
        self.assertEqual(len(closed),1)
        # This period's buying raised the reference; nothing restocks the
        # buffer, so the survivors are priced against the tighter scale.
        reference = drift(FOOD_DEPTH,1400)
        stock = FOOD_DEPTH-1400
        state = world.market.snapshot()
        self.assertEqual(dict(state.reference)['native:food'],reference)
        self.assertEqual(dict(state.inventory)['native:food'],stock)
        self.assertAlmostEqual(dict(state.prices)['native:food'],level(FOOD_BASE,reference,stock))
        self.assertEqual(state.period,1)
        self.assertEqual(world.market.churn('Qin','native:food').quantity,0)
        # With no trades nothing moves at all: no restocking, no drift.
        for _ in range(5):world.market.settle()
        state = world.market.snapshot()
        self.assertEqual((dict(state.reference)['native:food'],dict(state.inventory)['native:food']),
                         (reference,stock))
        # Selling the same lot back walks both numbers home to the base price.
        world.resources.credit('Qin','native:food',1400)
        world.market.sell('Qin','native:food',1400)
        world.market.settle()
        state = world.market.snapshot()
        self.assertEqual(dict(state.inventory)['native:food'],FOOD_DEPTH)
        self.assertEqual(dict(state.reference)['native:food'],FOOD_DEPTH)
        self.assertAlmostEqual(dict(state.prices)['native:food'],FOOD_BASE,places=9)
        before = world.runtime.save_mod_state(world)
        with self.assertRaises(ValueError):world.market.restore({'currency':'native:gold'})
        self.assert_unchanged(world,before)

    def test_heavy_flow_moves_the_reference_stock_both_ways(self):
        # 大买：基准被推高，外部随后照着更高的基准补货，市场是长大了而不是被抽干。
        buying = self.world()
        buying.market.buy('Qin','native:food',FOOD_DEPTH)
        self.assertEqual(dict(buying.market.snapshot().inventory)['native:food'],0)
        buying.market.settle()
        grown = buying.market.snapshot()
        self.assertEqual(dict(grown.reference)['native:food'],drift(FOOD_DEPTH,FOOD_DEPTH))
        self.assertGreater(dict(grown.reference)['native:food'],FOOD_DEPTH)
        # Nothing restocks it: the market stays empty and dear until somebody
        # sells, and only selling can walk the reference back down.
        for _ in range(40):buying.market.settle()
        drained = buying.market.snapshot()
        self.assertEqual(dict(drained.inventory)['native:food'],0)
        self.assertEqual(dict(drained.reference)['native:food'],drift(FOOD_DEPTH,FOOD_DEPTH))
        self.assertGreater(dict(drained.prices)['native:food'],FOOD_BASE)
        self.assertTrue(math.isfinite(dict(drained.prices)['native:food']))
        # 大卖：基准被压低，市场认为自己只需要更少的货，超出的部分长期是积压价。
        selling = self.world()
        selling.resources.credit('Qin','native:food',3000)
        selling.market.sell('Qin','native:food',3000)
        selling.market.settle()
        shrunk = selling.market.snapshot()
        self.assertEqual(dict(shrunk.reference)['native:food'],drift(FOOD_DEPTH,-3000))
        self.assertLess(dict(shrunk.reference)['native:food'],FOOD_DEPTH)
        self.assertLess(dict(shrunk.prices)['native:food'],FOOD_BASE)
        self.assertEqual(dict(shrunk.inventory)['native:food'],FOOD_DEPTH+3000)

    def test_period_churn_reports_overlapping_trades(self):
        world = self.world()
        world.market.buy('Qin','native:food',10)
        world.market.sell('Qin','native:food',5)
        churn = world.market.churn('Qin','native:food')
        self.assertEqual(churn.quantity,5)
        self.assertGreater(churn.loss,0)
        world.market.settle()
        self.assertEqual(world.market.churn('Qin','native:food').quantity,0)

    def test_replace_pricing_by_changing_only_distribution_member(self):
        native, fixed = self.world(), self.world(fixed=True)
        self.assertEqual(native.runtime.units['native:market'].mod.declaration,
                         fixed.runtime.units['native:market'].mod.declaration)
        self.assertEqual(native.market.ids,fixed.market.ids)
        q1 = native.market.quote('native:food',10,'buy','Qin')
        q2 = fixed.market.quote('native:food',10,'buy','Qin')
        self.assertAlmostEqual(q1.exact_total,sweep(FOOD_BASE,FOOD_DEPTH,10,'buy'))
        self.assertEqual((q2.exact_total,q2.gold,q2.price_after),(15,15,1.5))
        fixed.market.buy('Qin','native:food',10)
        sold = fixed.market.sell('Qin','native:food',10)
        self.assertEqual(sold.gold,15)
        self.assertEqual(fixed.resources.balance('Qin','native:gold'),100000)
        # A flat provider ignores the buffer entirely, in both directions.
        fixed.market.settle()
        self.assertEqual(dict(fixed.market.snapshot().prices)['native:food'],FOOD_BASE)
        native.market.settle()
        self.assertEqual(dict(native.market.snapshot().prices)['native:food'],FOOD_BASE)
        native.market.buy('Qin','native:food',FOOD_DEPTH)
        native.market.settle()
        self.assertGreater(dict(native.market.snapshot().prices)['native:food'],FOOD_BASE)
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
                world.market.settle()
                self.assertEqual(world.resources.to_save(),before)
                self.assertNotIn('example:tea',dict(world.market.snapshot().prices))
                self.assertEqual(dict(world.market.snapshot().prices)['native:food'],FOOD_BASE)
                saved = world.runtime.save_mod_state(world)
                world.runtime.load_mod_state(world,saved)
                self.assertEqual(world.resources.balance('Qin','example:tea'),20)
                self.assertEqual(world.runtime.save_mod_state(world),saved)

    def test_listing_new_tea_requires_only_market_declaration(self):
        text = (ROOT/'mods/native/market/mod.toml').read_text()
        text = text.replace('[Mod]', '[[Listings]]\nResource="example:tea"\nBasePrice=3\nDepth=10\nInitialInventory=10\n\n[Mod]')
        world = self.world(tea=True,market_text=text,fixed=True)
        bought = world.market.buy('Qin','example:tea',2)
        self.assertEqual(bought.gold,6)
        self.assertEqual(world.resources.balance('Qin','example:tea'),2)
        self.assertEqual(world.market.sell('Qin','example:tea',1).gold,3)
        self.assertEqual(world.resources.balance('Qin','example:tea'),1)
        world.market.settle()
        self.assertIn('example:tea',dict(world.market.snapshot().prices))
        with self.assertRaises(ModError):self.world(market_text=text)
        bad = text.replace('Resource="example:tea"','Resource="native:gold"')
        with self.assertRaises(ModError):self.world(market_text=bad)

    def test_roundtrip_cannot_mint_gold_and_extremes_stay_bounded(self):
        world = self.world()
        bought = world.market.buy('Qin','native:food',40)
        sold = world.market.sell('Qin','native:food',40)
        self.assertLess(sold.gold,bought.gold)
        self.assertEqual(world.resources.balance('Qin','native:food'),10000)
        # However extreme the buffer gets, the quote stays finite and inside the
        # declared limits, and a sale is always deliverable.
        for side, quantity in (('buy',FOOD_DEPTH),('sell',10000)):
            with self.subTest(side=side):
                extreme = self.world()
                extreme.resources.credit('Qin','native:food',quantity)
                getattr(extreme.market,side)('Qin','native:food',quantity)
                extreme.market.settle()
                shock = dict(extreme.market.snapshot().prices)['native:food']
                # A glut is cheap, scarcity is dear.
                self.assertLess(shock,FOOD_BASE) if side == 'sell' else self.assertGreater(shock,FOOD_BASE)
                for _ in range(400):extreme.market.settle()
                price = dict(extreme.market.snapshot().prices)['native:food']
                # Extreme inventories stay finite, positive and inside the
                # declared limits, and nothing drifts while nobody trades.
                self.assertTrue(math.isfinite(price))
                self.assertGreater(price,0)
                self.assertLessEqual(price,1000000000.0)
                self.assertGreaterEqual(dict(extreme.market.snapshot().inventory)['native:food'],0)
                self.assertAlmostEqual(price,shock,places=9)

    def test_distribution_exposes_the_buffer_and_publishes_the_rules(self):
        runtime = Runtime.load(ROOT/'mods.toml')
        world = runtime.world_class()()
        row = runtime.object_call(world,'economy:queries','market')
        self.assertEqual(dict(row.inventory)['native:food'],FOOD_DEPTH)
        self.assertEqual(dict(row.reference)['native:food'],FOOD_DEPTH)
        rules = runtime.rules(world,'市场')
        for phrase in ('同一期内所有国家拿到同一报价','报价与先后手无关','单笔订单越大均价越高',
                       '基准库存（每期按本期净流量偏移','库存不足整单拒绝','外部不回补'):
            self.assertIn(phrase,rules)

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
        world.market.settle()
        other.market.settle()
        self.assertEqual(world.runtime.save_mod_state(world),other.runtime.save_mod_state(other))
        for field,value in (('currency','example:cash'),('period',True),('inventory',{}),
                            ('reference',{'native:food':FOOD_DEPTH}),('prices',{}),('carry',{'Qin':1}),('trades',[{}])):
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
            with self.assertRaises(ModError):world.market.settle()
            with self.assertRaises(ModError):runtime.object_call(world,'native:market','sell','Qin','native:food',1)
            with self.assertRaises(ModError):runtime.object_call(world,'native:resource-exchange','exchange','Qin',{'native:gold':1},{'native:food':1})
            trade = runtime.object_call(world,'native:market','buy','Qin','native:food',1)
        self.assertEqual(world.resources.balance('Qin','native:food'),1)
        self.assertEqual(world.resources.balance('Qin','native:gold'),100-trade.gold)

    def test_national_production_is_not_a_delivery_to_the_market(self):
        class Producer(GameMod):
            def mixins(self):return ()
            def stages(self):
                def supply(world, context):
                    # A nation's own output belongs to that nation only.
                    context['building_production'] = 'no market input here'
                return (Stage('example.flow',supply,hook='market.inputs'),)
        loaded = self.runtime()
        distribution = {**loaded.distribution,
                        'Hooks':[{'Id':'market.inputs'},{'Id':'market.settlement'}]}
        runtime = Runtime([*loaded.units.values(),Unit('example:flows',Producer())],distribution)
        world = runtime.world_class()()
        world.resources.open('Qin',{'native:gold':100})
        trade = world.market.buy('Qin','native:food',1)
        result = world.resolve_turn()
        self.assertEqual(result['market_trades'],(trade,))
        # Only the trade moved the buffer: production delivers nothing, and
        # nothing restocks the market either.
        reference, stock = drift(FOOD_DEPTH,1), FOOD_DEPTH-1
        self.assertEqual(dict(world.market.snapshot().inventory)['native:food'],stock)
        self.assertEqual(dict(world.market.snapshot().reference)['native:food'],reference)
        self.assertAlmostEqual(dict(world.market.snapshot().prices)['native:food'],
                               level(FOOD_BASE,reference,stock))

    def broken(self, engine):
        class BrokenPricing(GameMod):
            def mixins(self):return ()
            def provides(self):return ('market:pricing',)
            def objects(self,world):return {'market:pricing':engine}
        loaded = self.runtime()
        units = [unit for key,unit in loaded.units.items() if key != 'native:market-pricing']
        return Runtime([*units,Unit('example:broken-pricing',BrokenPricing())],loaded.distribution)

    def test_rejected_pricing_results_never_commit_inventory_or_partial_prices(self):
        from mods.native.market.contracts import PriceSettlement
        class BrokenWalk:
            def walk(self, *args):return {'next_price':2,'exact_total':1}
            def level(self, listing, *args):return PriceSettlement(2)
        class BrokenLevel(BrokenWalk):
            def level(self, listing, *args):
                return None if listing.resource == 'native:wood' else PriceSettlement(2)
        runtime = self.broken(BrokenWalk())
        world = runtime.world_class()()
        world.resources.open('Qin',{'native:gold':100})
        before = runtime.save_mod_state(world)
        with self.assertRaises(ValueError):world.market.buy('Qin','native:food',1)
        self.assert_unchanged(world,before)
        # The opening quote is priced from the buffer, so a provider that cannot
        # price a listing is rejected before the world exists.
        with self.assertRaises(ValueError):self.broken(BrokenLevel()).world_class()()
