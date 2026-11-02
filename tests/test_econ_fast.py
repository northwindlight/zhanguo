"""C fast path for the v13 economic solver: bit parity, fallback, invariance.

The extension is optional. These checks require it to be built
(``python -m ruleai.v13.build``); the fallback checks run either way.
"""
from contextlib import contextmanager
import unittest
from unittest.mock import patch

from host import GameHost
from ruleai.v13 import econ_fast
from ruleai.v13.economy import _net_recipe, _operating_targets, _reserve_cash
from ruleai.v13.market import MarketModel
from ruleai.v13.profiles import PROFILES
from tests.test_economy_bc import cfg

RESOURCES = tuple('native:'+r for r in ('gold','food','wood','ore','oil','equipment','supply'))
GOLD = 'native:gold'

CATALOG = {
    'native:lumber-camp': {'inputs':{}, 'outputs':{'native:wood':2},
        'effects':{}, 'energy_output':0, 'energy_use':0, 'costs_by_level':[{'native:gold':10,'native:wood':5}]},
    'native:wood-power-plant': {'inputs':{'native:wood':1}, 'outputs':{},
        'effects':{}, 'energy_output':3, 'energy_use':0, 'costs_by_level':[{'native:gold':20,'native:wood':8}]},
    'native:town-hall': {'inputs':{}, 'outputs':{},
        'effects':{'gold_base':4,'gold_per_slot':1}, 'energy_output':0, 'energy_use':0,
        'costs_by_level':[{'native:gold':30,'native:wood':10}]},
    'native:farm': {'inputs':{'native:equipment':1}, 'outputs':{'native:food':3},
        'effects':{}, 'energy_output':0, 'energy_use':1, 'costs_by_level':[{'native:gold':12,'native:wood':6}]},
}


def buildings():
    return [{'site':'s0','kind':'native:lumber-camp','remaining':0},
            {'site':'s0','kind':'native:town-hall','remaining':0},
            {'site':'s1','kind':'native:wood-power-plant','remaining':1},
            {'site':'s1','kind':'native:farm','remaining':0}]


def prices():
    return {'buy':{k:(2.0 if k!='native:oil' else 4.0) for k in RESOURCES},
            'sell':{k:(1.5 if k!='native:oil' else 3.0) for k in RESOURCES}}


def market_snapshot():
    inventory = {k:40 for k in RESOURCES}
    inventory['native:oil'] = 0
    reference = {k:60 for k in RESOURCES}
    trials = [(k,110.0,95.0) for k in RESOURCES]
    return {'period':4, 'inventory':inventory, 'reference':reference,
            'buy_prices':prices()['buy'], 'sell_prices':prices()['sell'],
            'prices':{k:2.0 for k in RESOURCES}, 'equilibrium':{k:2.2 for k in RESOURCES},
            'trials':trials, 'trial_units':50}


def model(participants=8):
    return MarketModel(market_snapshot(), {}, participants=participants)


@contextmanager
def shadow(mismatches):
    """Run every accelerated call through both implementations."""
    original_call = econ_fast._call
    original_value = econ_fast.market_value

    def call(name, reference, *args):
        expected = reference(*args)
        actual = getattr(econ_fast.FAST, name)(*args)
        if isinstance(actual, dict):
            if list(actual) != list(expected) or set(actual) != set(expected):
                mismatches.append((name, '键或顺序', list(actual), list(expected)))
            else:
                for key in actual:
                    if float(actual[key]) != float(expected[key]) or type(actual[key]) is not type(expected[key]):
                        mismatches.append((name, key, actual[key], expected[key]))
        elif float(actual) != float(expected):
            mismatches.append((name, '标量', actual, expected))
        return expected

    def value(reference, model_, net, own_net, extra, horizon, crowd):
        expected = reference(net, own_net, extra, horizon, crowd)
        actual = econ_fast.FAST.market_value(econ_fast._pack(model_), list(net.items()),
                                             own_net, extra, horizon, float(crowd))
        if float(actual) != float(expected):
            mismatches.append(('market_value', horizon, crowd, actual, expected))
        return expected

    econ_fast._call = call
    econ_fast.market_value = value
    try:
        yield
    finally:
        econ_fast._call = original_call
        econ_fast.market_value = original_value


@contextmanager
def backend(enabled):
    previous = econ_fast.ENABLED
    econ_fast.ENABLED = bool(enabled) and econ_fast.FAST is not None
    try:
        yield
    finally:
        econ_fast.ENABLED = previous


class FallbackChecks(unittest.TestCase):
    def setUp(self):
        econ_fast.reset_fallbacks()

    def test_missing_extension_uses_python_reference(self):
        military = {'need':2,'equipment_need':1}
        with patch.object(econ_fast, 'FAST', None):
            with patch.object(econ_fast, 'ENABLED', False):
                got = econ_fast.net_recipe(_net_recipe, CATALOG, buildings(), military)
        self.assertEqual(got, _net_recipe(CATALOG, buildings(), military))
        self.assertEqual(econ_fast.FALLBACKS, [])

    def test_failing_extension_falls_back_and_is_recorded(self):
        class Broken:
            def net_recipe(self, *args):
                raise RuntimeError('合成故障')
        military = {'need':2,'equipment_need':1}
        with patch.object(econ_fast, 'FAST', Broken()), patch.object(econ_fast, 'ENABLED', True):
            got = econ_fast.net_recipe(_net_recipe, CATALOG, buildings(), military)
        self.assertEqual(got, _net_recipe(CATALOG, buildings(), military))
        self.assertTrue(any('合成故障' in row for row in econ_fast.FALLBACKS))

    def test_build_must_match_source_digest(self):
        # 改了 _econ_fast.c 却没重编译：加载时就该回退，不能跑旧二进制。
        if econ_fast.FAST is None:
            self.skipTest('Optional C extension not built')
        self.assertFalse(econ_fast._stale())
        with patch.object(econ_fast.FAST, 'DIGEST', '0' * 64):
            self.assertTrue(econ_fast._stale())

    def test_disabled_switch_skips_extension(self):
        military = {'need':2,'equipment_need':1}
        with patch.object(econ_fast, 'FAST', AssertionError), patch.object(econ_fast, 'ENABLED', False):
            got = econ_fast.operating_targets(_operating_targets, CATALOG, buildings(), military,
                                              prices(), 3)
        self.assertEqual(got, _operating_targets(CATALOG, buildings(), military, prices(), 3))


@unittest.skipIf(econ_fast.FAST is None, 'Optional C extension not built')
class FastPathChecks(unittest.TestCase):
    def setUp(self):
        econ_fast.reset_fallbacks()

    def test_net_recipe_matches_counter_order_and_types(self):
        for military in ({}, {'need':2,'equipment_need':1,'need_float':0.5}):
            expected = _net_recipe(CATALOG, buildings(), military)
            actual = econ_fast.FAST.net_recipe(CATALOG, buildings(), military)
            self.assertEqual(list(actual), list(expected))
            for key in expected:
                self.assertEqual(actual[key], expected[key], key)
                self.assertIs(type(actual[key]), type(expected[key]), key)

    def test_operating_targets_match_across_periods(self):
        military = {'need':2,'equipment_need':1}
        for periods in (1, 3, 6):
            expected = _operating_targets(CATALOG, buildings(), military, prices(), periods)
            actual = econ_fast.FAST.operating_targets(CATALOG, buildings(), military, prices(), periods)
            self.assertEqual(list(actual), list(expected))
            self.assertEqual(actual, expected)

    def test_reserve_cash_matches(self):
        military = {'need':2,'equipment_need':1}
        for name in PROFILES:
            expected = _reserve_cash(CATALOG, buildings(), military, prices()['buy'], PROFILES[name])
            actual = econ_fast.FAST.reserve_cash(CATALOG, buildings(), military,
                                                 prices()['buy'], PROFILES[name])
            self.assertEqual(actual, expected)
            self.assertIs(type(actual), int)

    def test_market_value_matches_for_every_profile_horizon_and_crowd(self):
        net = {GOLD:6, 'native:wood':2, 'native:food':-4, 'native:oil':-1,
               'native:equipment':0, 'native:supply':-3}
        own_net = {'native:wood':-5, 'native:food':3}
        extra = {'native:wood':-2, 'native:gold':1}
        for horizon in (1, 12, 24, 32, 48):
            for crowd in (1, 1.5, 3.0):
                model_ = model()
                expected = model_._value_reference(net, own_net, extra, horizon, crowd)
                actual = econ_fast.FAST.market_value(econ_fast._pack(model_),
                                                     list(net.items()), own_net, extra,
                                                     horizon, float(crowd))
                self.assertEqual(actual, expected, (horizon, crowd))

    def test_market_value_returns_negative_infinity_for_unpriced_resource(self):
        model_ = MarketModel(market_snapshot(), {}, participants=1)
        net = {'native:missing':1, GOLD:2}
        self.assertEqual(econ_fast.FAST.market_value(econ_fast._pack(model_), list(net.items()),
                                                     {}, {}, 4, 1.0), float('-inf'))
        self.assertEqual(model_._value_reference(net, {}, {}, 4), float('-inf'))

    def test_planner_turns_match_python_bit_for_bit(self):
        mismatches = []
        with shadow(mismatches), backend(True):
            host = GameHost.new(cfg())
            for _ in range(3):
                host.cycle()
        self.assertEqual(mismatches[:4], [])
        self.assertEqual(econ_fast.FALLBACKS, [])

    def test_world_and_decisions_are_identical_with_and_without_extension(self):
        def run(enabled):
            with backend(enabled):
                host = GameHost.new(cfg())
                for _ in range(3):
                    host.cycle()
                return host.runtime.save_mod_state(host.world)
        self.assertEqual(run(False), run(True))


if __name__ == '__main__':
    unittest.main()
