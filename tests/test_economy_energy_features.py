"""Bound-API power observations: actual supply, pending capacity and choices."""
from pathlib import Path
from tempfile import TemporaryDirectory
import hashlib
import json
import unittest

from tests.test_economy_bc import cfg, torch
from host import GameHost
from ruleai.v11plus.ledger import Ledger

if torch is not None:
    import numpy as np
    from rl.economy.adapter import (ACTIONS, BUILDINGS, UNITS, RESOURCES, PROFILES_ORDER,
        WIDTH, ENERGY_COLUMNS, PROJECT_FINGERPRINT, observation, scale)
    from rl.economy.projects import InvestmentPlanner


@unittest.skipIf(torch is None, 'Optional economic training dependencies')
class EnergyFeatureChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        torch.set_num_threads(1)

    def planner(self):
        host = GameHost.new(cfg())
        player = host.player(host.actors()[0])
        ledger = Ledger(player)
        ledger.do('plan', text='检验真实供电与投资后的额定电力')
        return host, InvestmentPlanner(ledger, host.clients[player.actor].state)

    def candidate(self, encoded, kind):
        index = next(i for i, (action, args) in enumerate(encoded['actions'])
                     if action == 'build' and args['kind'] == 'native:' + kind)
        return encoded['candidates'][index]

    def assert_features(self, features, **expected):
        for name, value in expected.items():
            with self.subTest(feature=name):
                self.assertAlmostEqual(float(features[ENERGY_COLUMNS[name]]), value, places=6)

    def test_short_grid_exposes_each_buildings_load_and_repair_capacity(self):
        host, planner = self.planner()
        before = host.runtime.save_mod_state(host.world)
        encoded = planner.encode()
        self.assertEqual(host.runtime.save_mod_state(host.world), before)
        self.assertEqual(encoded['candidates'].shape[1], WIDTH)
        for features in [encoded['context'][0], *encoded['candidates']]:
            self.assert_features(features, current_short=1, current_sufficient=0,
                current_shortfall=scale(1), committed_capacity_gap=scale(1))
        self.assert_features(self.candidate(encoded, 'barracks'), consumes_power=1,
            provides_power=0, post_build_capacity_gap=scale(2), post_build_short=1,
            post_build_sufficient=0)
        for kind, gap in [('wood-power-plant', -1), ('oil-power-plant', -7)]:
            self.assert_features(self.candidate(encoded, kind), consumes_power=0,
                provides_power=1, post_build_capacity_gap=scale(gap), post_build_short=0,
                post_build_sufficient=1)
        self.assert_features(self.candidate(encoded, 'farm'), consumes_power=0,
            provides_power=0, post_build_capacity_gap=scale(1))
        # Every public catalog definition and every owned-site build candidate
        # carries the same explicit consumer/source markers, even if unavailable.
        catalog = {b['id']: b for b in planner.player.observe()['facts']['building_catalog']}
        for kind, definition in catalog.items():
            row = next(r for r in encoded['context'] if r[2] and r[17+BUILDINGS.index(kind)]
                       and not r[36:38].any())
            for features in (row, self.candidate(encoded, kind.removeprefix('native:'))):
                self.assert_features(features, consumes_power=int(definition['energy_use'] > 0),
                    provides_power=int(definition['energy_output'] > 0))
        np.testing.assert_array_equal(encoded['candidates'][:, 85], encoded['legal'])

    def test_current_supply_and_pending_load_are_separate_at_exact_balance(self):
        host, planner = self.planner()
        site = planner.sites['sites'][0]['id']
        host.world.buildings.seed(site, 'native:wood-power-plant')
        host.world.buildings.seed(site, 'native:barracks', completed=False)
        # A foreign power plant must not improve our nominal or actual supply.
        other = host.player(host.actors()[1]).observe()['facts']['building_sites']['sites'][0]['id']
        host.world.buildings.seed(other, 'native:oil-power-plant')
        planner.ledger._queries.clear()
        encoded = planner.encode()
        self.assert_features(encoded['context'][0], current_short=0, current_sufficient=1,
            current_shortfall=0, committed_capacity_gap=0,
            post_build_capacity_gap=0, post_build_short=0, post_build_sufficient=1)
        self.assert_features(self.candidate(encoded, 'barracks'), current_sufficient=1,
            post_build_capacity_gap=scale(1), post_build_short=1, post_build_sufficient=0)

    def test_selected_power_construction_refreshes_once_without_claiming_current_supply(self):
        host, planner = self.planner()
        site = planner.sites['sites'][0]['id']
        self.assertTrue(planner.ledger.do('build', site=site, kind='native:wood-power-plant'))
        encoded = planner.encode()
        self.assert_features(encoded['context'][0], current_short=1, current_sufficient=0,
            committed_capacity_gap=scale(-1), post_build_sufficient=1)
        self.assert_features(self.candidate(encoded, 'barracks'),
            post_build_capacity_gap=0, post_build_short=0, post_build_sufficient=1)
        self.assert_features(self.candidate(encoded, 'wood-power-plant'),
            post_build_capacity_gap=scale(-3))
        # Atomic and project observations share the same energy encoding.
        atomic = observation(planner.ledger, planner.state)
        np.testing.assert_array_equal(atomic['context'][0, 87:96], encoded['context'][0, 87:96])

    def test_no_fuel_still_means_current_short_even_with_enough_installed_capacity(self):
        host, planner = self.planner()
        site = planner.sites['sites'][0]['id']
        host.world.buildings.seed(site, 'native:oil-power-plant')
        host.world.resources._stock(planner.player.actor, 'native:oil').set_amount(0)
        planner.ledger._queries.clear()
        encoded = planner.encode()
        preview = planner.player.observe()['facts']['econ']['preview']['energy']
        self.assertGreater(preview['capacity'], preview['demand'])
        self.assertGreater(preview['fuel_short_plants'], 0)
        self.assert_features(encoded['context'][0], current_short=1, current_sufficient=0,
            current_shortfall=scale(1), committed_capacity_gap=scale(-7),
            post_build_short=0, post_build_sufficient=1)

    def test_v3_data_and_weights_cannot_silently_acquire_new_feature_semantics(self):
        from rl.economy.model import Policy, load_policy
        from rl.economy.data import Writer, Dataset
        old = hashlib.sha256(json.dumps(['economy-bc-3', ACTIONS, RESOURCES, BUILDINGS,
            UNITS, PROFILES_ORDER, WIDTH], ensure_ascii=True).encode()).hexdigest()
        old = hashlib.sha256((old + ':funded-projects-1').encode()).hexdigest()
        self.assertNotEqual(PROJECT_FINGERPRINT, old)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            model = Policy(width=16, depth=1, heads=2, action_mode='projects')
            torch.save(dict(config=model.config, model=model.state_dict(), fingerprint=old),
                       root/'v3.pt')
            with self.assertRaisesRegex(ValueError, 'Incompatible'):
                load_policy(root/'v3.pt')
            host, planner = self.planner()
            encoded = planner.encode()
            encoded.update(target=0, quantity=0.)
            writer = Writer(old)
            writer.add(encoded)
            writer.save(root/'v3.npz', dict(seed=73))
            with self.assertRaisesRegex(ValueError, 'Incompatible data'):
                Dataset([root/'v3.npz'], fingerprint=PROJECT_FINGERPRINT)


if __name__ == '__main__':
    unittest.main()
