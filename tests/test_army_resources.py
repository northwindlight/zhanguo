"""Army/resource contracts against the current declared distribution."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from engine.runtime import ModError, Runtime
from engine.state import unit_context
from mods.native.army.objects import Units
from mods.native.army.rules import army_name, unit_label
from mods.native.army.parameters import UNIT_TYPES
from tests import test_world_domains as fixtures

ROOT = Path(__file__).resolve().parents[1]
RESOURCE_IDS = ('native:gold', 'native:food', 'native:equipment', 'native:supply')
Q = fixtures.Q


class TestArmyResources(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TestWorldDomains()
        self.world = self.fixture.world()
        self.site = self.fixture.site(self.world, 'scenario:capital', Q, 1, 1)
        self.world.buildings.seed(self.site, 'native:barracks')
        self.world.buildings.seed(self.site, 'native:militia-camp')

    def recruit(self, kind):
        return self.world.call_tool(Q, 'recruit', {'site': self.site, 'kind': kind})[0]

    def test_army_declares_concrete_reads_and_atomic_exchange(self):
        declaration = self.world.runtime.units['native:army'].mod.declaration
        self.assertTrue(set(RESOURCE_IDS) <= set(declaration['Capabilities']['Needs']))
        self.assertFalse({'method.res', 'method.add_res', 'native:resources'} & set(declaration['Capabilities']['Needs']))
        for resource in RESOURCE_IDS:
            self.assertEqual(declaration['Interfaces'][resource], ['balance'])
        self.assertEqual(declaration['Interfaces']['native:resource-exchange'], ['exchange', 'exchange_batch'])

    def test_recruitment_consumes_exact_declared_resources(self):
        w = self.world
        before = {key: w.resources.balance(Q, key) for key in RESOURCE_IDS}
        unit = self.recruit('native:militia')
        for key in RESOURCE_IDS:
            self.assertEqual(before[key] - w.resources.balance(Q, key), unit.definition['recruit'].get(key, 0))
        self.assertEqual(unit.kind, 'native:militia')

    def test_infantry_recruitment_is_atomic_on_insufficient_resources(self):
        w = self.world
        w.resources.consume(Q, 'native:equipment', w.resources.balance(Q, 'native:equipment'))
        before = self.fixture.save(w)
        with self.assertRaises(ValueError):
            self.recruit('native:infantry')
        self.assertEqual(self.fixture.save(w), before)

    def test_supply_and_healing_use_resource_inventory(self):
        w = self.world
        unit = w.military.seed(Q, 'native:infantry', 1, 1)[0]
        w.units.by_id(unit.id, Q).take_damage(60)
        supply = w.resources.balance(Q, 'native:supply')
        equipment = w.resources.balance(Q, 'native:equipment')
        self.fixture.cycle(w)
        self.assertEqual(w.units.by_id(unit.id, Q).hp, 70)
        self.assertEqual(w.resources.balance(Q, 'native:supply'), supply - 1)
        self.assertEqual(w.resources.balance(Q, 'native:equipment'), equipment - 1)
        self.assertGreater(w.economy.turns(Q)[0].military.total, 0)

    def test_starvation_and_garrison_militia_exemption(self):
        w = self.world
        infantry = w.military.seed(Q, 'native:infantry', 1, 1)[0]
        militia = w.military.seed(Q, 'native:militia', 1, 1)[0]
        w.units.by_id(militia.id, Q).take_damage(30)
        w.resources.consume(Q, 'native:supply', w.resources.balance(Q, 'native:supply'))
        self.fixture.cycle(w)
        self.assertEqual(w.units.by_id(infantry.id, Q).hp, 65)
        self.assertEqual(w.units.by_id(militia.id, Q).hp, 50)
        # Exempt militia avoid starvation; national shortage still stops healing.
        w.call_tool(Q, 'disband', {'aid': infantry.id})
        equipment = w.resources.balance(Q, 'native:equipment')
        self.fixture.cycle(w)
        self.assertEqual(w.units.by_id(militia.id, Q).hp, 80)
        self.assertEqual(w.resources.balance(Q, 'native:equipment'), equipment)

    def test_permissions_reject_ungranted_raw_resource_writes(self):
        w = self.world
        before = w.resources.to_save()
        with unit_context('native:army'):
            with self.assertRaises(ModError):
                w.resources.consume(Q, 'native:food', 1)
            with self.assertRaises(ModError):
                w.runtime.object_call(w, 'native:gold', 'credit', Q, 100)
            with self.assertRaises(ModError):
                w.runtime.object_call(w, 'native:wood', 'balance', Q)
        self.assertEqual(w.resources.to_save(), before)

    def test_unit_ids_and_resource_state_survive_roundtrip(self):
        w = self.world
        self.recruit('native:infantry')
        restored = self.fixture.restore(w)
        self.assertEqual(restored.units.to_save(), w.units.to_save())
        before = w.units.to_save()
        for kind in ('步', 'infantry', 'other:infantry'):
            with self.assertRaises(ValueError):
                w.units.spawn(Q, kind, 1, 1)
            self.assertEqual(w.units.to_save(), before)

    def test_display_locale_does_not_change_unit_identity_or_cost(self):
        cost = deepcopy(UNIT_TYPES['native:infantry']['recruit'])
        self.assertEqual(unit_label('native:infantry'), '步兵')
        self.assertEqual(unit_label('native:infantry', 'en-US'), 'Infantry')
        self.assertNotEqual(army_name(Q, 1, 'native:infantry'), army_name(Q, 1, 'native:infantry', 'en-US'))
        self.assertEqual(cost, UNIT_TYPES['native:infantry']['recruit'])

    def test_registered_unit_recruits_heals_and_roundtrips(self):
        with TemporaryDirectory() as folder:
            edition = Path(folder) / 'mod.toml'
            edition.write_text(self.fixture.edition().replace(
                'Members = [', 'Members = [' + json.dumps((ROOT / 'mods/examples/elephants/mod.toml').as_posix()) + ', '), encoding='utf-8')
            w = self.fixture.world(Runtime.load(edition.parent))
            site = self.fixture.site(w, 'scenario:elephant-capital', Q, 1, 1)
            w.buildings.seed(site, 'native:barracks')
            unit = w.call_tool(Q, 'recruit', {'site': site, 'kind': 'example:war-elephant'})[0]
            self.assertEqual((unit.kind, unit.hp, unit.atk()), ('example:war-elephant', 200, 90))
            w.units.by_id(unit.id, Q).take_damage(20)
            self.fixture.cycle(w)
            self.assertEqual(w.units.by_id(unit.id, Q).hp, 200)
            saved = w.units.to_save()
            restored = Units.from_save(saved, size=8, definitions=w.units.definitions)
            self.assertEqual(restored.to_save(), saved)
            with self.assertRaises(ValueError):
                Units.from_save(saved, size=8)
            with self.assertRaises(TypeError):
                unit.definition['hp'] = 999


if __name__ == '__main__':
    unittest.main()
