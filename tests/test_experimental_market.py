"""Offline acceptance of real experimental manifests and finite deliveries."""
from copy import deepcopy
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from engine.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]


class TestInventoryMarket(unittest.TestCase):
    def world(self):
        with TemporaryDirectory() as directory:
            members = [str(ROOT / path) for path in (
                'mods/native/resources/mod.toml', 'mods/experimental/market_pricing.toml',
                'mods/experimental/market.toml')]
            (Path(directory)/'mod.toml').write_text(
                '[Distribution]\nId="buffer-test"\nVersion="1"\nMembers='+json.dumps(members)+
                '\nRequiredState=["resources","market"]\n[[Hooks]]\nId="market.settlement"\n')
            runtime = Runtime.load(directory)
        world = runtime.world_class()()
        world.resources.open('Qin', {'native:gold':100000, 'native:food':10000})
        world.resources.open('Chu', {'native:gold':100000, 'native:food':10000})
        return world

    def stock(self, world):return dict(world.market.snapshot().inventory)['native:food']
    def price(self, world):return dict(world.market.snapshot().prices)['native:food']

    def test_quotes_are_pure_deliveries_conserve_goods_and_orders_are_all_or_nothing(self):
        world = self.world()
        saved = world.runtime.save_mod_state(world)
        world.market.quote('native:food',192,'buy','Qin')
        self.assertEqual(world.runtime.save_mod_state(world), saved)
        world.market.buy('Qin','native:food',192)
        self.assertEqual(self.stock(world),0)
        self.assertEqual(world.resources.balance('Qin','native:food')+self.stock(world),10192)
        for action,args in ((world.market.buy,('Chu','native:food',1)),
                            (world.market.quote,('native:food',1,'buy','Chu')),
                            (world.market.sell,('Chu','native:food',10001)),
                            (world.market.buy,('Qin','native:food',True))):
            saved = world.runtime.save_mod_state(world)
            with self.assertRaises(ValueError):action(*args)
            self.assertEqual(world.runtime.save_mod_state(world),saved)
        world.market.sell('Qin','native:food',20)
        world.market.buy('Chu','native:food',20)
        self.assertEqual(self.stock(world),0)
        self.assertEqual(sum(world.resources.balance(o,'native:food') for o in ('Qin','Chu')),20192)

    def test_insufficient_cash_never_changes_inventory_price_or_carry(self):
        world = self.world()
        world.resources.consume('Qin','native:gold',100000)
        saved = world.runtime.save_mod_state(world)
        with self.assertRaises(ValueError):world.market.buy('Qin','native:food',1)
        self.assertEqual(world.runtime.save_mod_state(world),saved)

    def test_feedback_matches_formula_no_base_reversion_or_phantom_deliveries(self):
        world = self.world()
        world.market.buy('Qin','native:food',96)
        self.assertEqual(self.price(world),1.5)
        for period in range(1,4):
            closed = world.market.settle({'native:food':999999},{'native:food':1},participants=8)
            self.assertAlmostEqual(self.price(world),1.5*math.exp(.05*period))
            self.assertEqual(self.stock(world),96)
            self.assertEqual(len(closed),1 if period==1 else 0)
        # Replenishment to target freezes the current price, above opening base.
        world.market.sell('Qin','native:food',96)
        prior = self.price(world)
        world.market.settle({}, {})
        self.assertAlmostEqual(self.price(world),prior)
        world.market.sell('Qin','native:food',192)
        world.market.settle({}, {})
        self.assertAlmostEqual(self.price(world),prior*math.exp(-.1))

    def test_real_stage_keeps_inventory_and_returns_closed_trades(self):
        world = self.world()
        trade = world.market.buy('Qin','native:food',192)
        result = world.resolve_turn()
        self.assertEqual(result['market_trades'],(trade,))
        self.assertEqual(self.stock(world),0)
        self.assertAlmostEqual(self.price(world),1.5*math.exp(.1))

    def test_roundtrip_cannot_mint_integer_gold_and_split_orders_match(self):
        for quantity in (1,10,100):
            world = self.world()
            for _ in range(50):
                world.market.buy('Qin','native:food',quantity)
                world.market.sell('Qin','native:food',quantity)
            self.assertLessEqual(world.resources.balance('Qin','native:gold'),100000)
            self.assertEqual(self.stock(world),192)
        batch, split = self.world(),self.world()
        for side in ('buy','sell'):
            getattr(batch.market,side)('Qin','native:food',100)
            for _ in range(100):getattr(split.market,side)('Qin','native:food',1)
        self.assertEqual(batch.resources.to_save(),split.resources.to_save())
        self.assertAlmostEqual(dict(batch.market.snapshot().carry)['Qin'],dict(split.market.snapshot().carry)['Qin'])

    def test_save_reload_continues_exactly_and_bad_saves_fail_atomically(self):
        world = self.world()
        world.market.buy('Qin','native:food',1)
        world.market.settle({}, {})
        saved = json.loads(json.dumps(world.runtime.save_mod_state(world)))
        other = self.world()
        other.runtime.load_mod_state(other,saved)
        for side,qty in (('buy',10),('sell',50)):
            self.assertEqual(getattr(world.market,side)('Qin','native:food',qty),
                             getattr(other.market,side)('Qin','native:food',qty))
        world.market.settle({}, {});other.market.settle({}, {})
        self.assertEqual(world.runtime.save_mod_state(world),other.runtime.save_mod_state(other))
        for field,value in (('inventory',{'native:food':-1}),('targets',{}),('buffer_policy',{}),
                            ('prices',None),('trades',[{}])):
            bad = deepcopy(saved['native:market']);bad[field]=value
            before = world.market.to_save()
            with self.assertRaises(ValueError):world.market.restore(bad)
            self.assertEqual(world.market.to_save(),before)

    def test_late_bad_flow_never_partially_updates_prices(self):
        world = self.world()
        world.market.buy('Qin','native:food',100)
        before = world.runtime.save_mod_state(world)
        with self.assertRaises(ValueError):world.market.settle({'native:wood':float('nan')},{})
        self.assertEqual(world.runtime.save_mod_state(world),before)

    def test_extended_extreme_inventory_stays_positive_and_finite(self):
        for side,quantity,bound in (('buy',192,1e9),('sell',10000,1e-6)):
            world = self.world()
            getattr(world.market,side)('Qin','native:food',quantity)
            for _ in range(1000):world.market.settle({}, {})
            self.assertEqual(self.price(world),bound)
            self.assertTrue(math.isfinite(self.price(world)))
            getattr(world.market,'sell' if side == 'buy' else 'buy')('Qin','native:food',1)
            self.assertGreaterEqual(self.stock(world),1)

    def test_full_distribution_exposes_buffer_to_queries_and_chairman_rules(self):
        runtime = Runtime.load(ROOT/'mods.experimental.toml')
        world = runtime.world_class()()
        row = runtime.object_call(world,'economy:queries','market')
        self.assertEqual(dict(row.inventory)['native:food'],192)
        self.assertEqual(dict(row.targets)['native:food'],192)
        rules = runtime.rules(world,'市场')
        self.assertIn('不回归基准价',rules)
        self.assertIn('库存不足整单拒绝',rules)
        native = Runtime.load(ROOT/'mods/native').world_class()()
        self.assertFalse(hasattr(native.market.snapshot(),'inventory'))


if __name__ == '__main__':unittest.main()
