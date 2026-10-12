"""Isolated resource-domain contracts, independent of the unfinished world."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
from types import SimpleNamespace
import unittest

from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import GameMod, ModError, Runtime, Unit as RuntimeUnit
from engine.state import unit_context
from mods.native.resources.mod import ResourcesMod
from mods.native.resources.objects import ResourceAccounts
from mods.native.resources.types import RESOURCE_TYPES, Gold, Food
from mods.native.localization import resource_name

ROOT = Path(__file__).resolve().parents[1]


def resource_runtime():
    path = ROOT/'mods/native/resources/mod.toml'
    mod = ResourcesMod()
    mod.configure(tomllib.loads(path.read_text()), path)
    return Runtime([RuntimeUnit('native:resources', mod)])


class TestResources(unittest.TestCase):
    def test_template_is_extended_and_currency_overrides_behavior(self):
        self.assertTrue(all(issubclass(cls, Resource) for cls in RESOURCE_TYPES))
        gold, food = Gold('Qin', 50), Food('Qin', 10)
        self.assertEqual(gold.value_of(50, 10), 50)
        self.assertEqual(food.value_of(10, 1.5), 15)
        self.assertFalse(gold.TRADEABLE)
        gold.set_amount(-5)
        self.assertFalse(gold.can_consume(1))
        with self.assertRaises(ValueError):food.set_amount(-5)
        self.assertEqual(food.amount, 10)

    def test_invalid_mutations_do_not_change_stock(self):
        stock = Food('Qin', 10)
        for value in (-1, True, float('nan'), float('inf'), '3'):
            for action in (stock.credit, stock.consume, stock.set_amount):
                with self.subTest(value=value, action=action.__name__),self.assertRaises(ValueError):
                    action(value)
                self.assertEqual(stock.amount, 10)
        with self.assertRaises(ValueError):stock.consume(11)
        stock.consume(10)
        self.assertEqual(stock.amount, 0)
        stock.credit(2)
        self.assertEqual(stock.amount, 2)
        with self.assertRaises(AttributeError):stock.amount = 100
        with self.assertRaises(AttributeError):stock.owner = 'Chu'

    def test_identity_is_namespaced_and_language_independent(self):
        for key in ('gold', '黄金', 'native:黄金', 'Native:gold', 'native:', 'native:gold extra'):
            with self.assertRaises(ValueError):content_id(key)
        account = ResourceAccounts(RESOURCE_TYPES)
        with self.assertRaises(ValueError):account.open('Qin', {'黄金': 50})
        self.assertEqual(account.to_save(), {'accounts': []})
        account.open('Qin', {'native:gold': 50})
        self.assertEqual(resource_name('native:gold'), '黄金')
        self.assertEqual(resource_name('native:gold', 'en-US'), 'Gold')
        self.assertEqual(account.balance('Qin', 'native:gold'), 50)
        with self.assertRaises(KeyError):account.balance('Qin', '黄金')
        with self.assertRaises(KeyError):account.balance('missing', 'native:gold')

    def test_account_creation_and_restore_are_atomic(self):
        account = ResourceAccounts(RESOURCE_TYPES)
        with self.assertRaises(ValueError):account.open('Qin', {'native:gold': 50, 'native:food': -1})
        self.assertEqual(account.to_save(), {'accounts': []})
        account.open('Qin', {'native:gold': 50})
        before = account.to_save()
        bad = deepcopy(before);bad['accounts'][0]['stocks'][1]['amount'] = -1
        with self.assertRaises(ValueError):account.restore(bad)
        self.assertEqual(account.to_save(), before)
        with self.assertRaises(ValueError):account.open('Qin')

    def test_snapshots_are_read_only_and_do_not_follow_future_changes(self):
        account = ResourceAccounts(RESOURCE_TYPES)
        account.open('Qin', {'native:food': 10})
        snapshot = account.interface('native:food').snapshot('Qin')
        with self.assertRaises(FrozenInstanceError):snapshot.amount = 3
        account.consume('Qin', 'native:food', 3)
        self.assertEqual(snapshot.amount, 10)
        self.assertEqual(account.balance('Qin', 'native:food'), 7)

    def test_another_namespace_and_subclass_need_no_native_special_case(self):
        class Mana(Resource):
            TYPE_ID = 'example:mana'
            NAME_KEY = 'resource.example:mana.name'

            @classmethod
            def validate_amount(cls, amount):
                super().validate_amount(amount)
                if type(amount) is not int:raise ValueError('Mana uses integer units')

            @classmethod
            def value_of(cls, amount, unit_price):
                return super().value_of(amount, unit_price) * 2

        account = ResourceAccounts((*RESOURCE_TYPES, Mana))
        account.open('Qin', {'example:mana': 3})
        account.consume('Qin', 'example:mana', 1)
        self.assertEqual(account.interface('example:mana').value(2, 5), 20)
        with self.assertRaises(ValueError):account.credit('Qin', 'example:mana', 0.5)
        self.assertEqual(account.balance('Qin', 'example:mana'), 2)
        with self.assertRaises(ValueError):ResourceAccounts((Mana, Mana))

    def test_explicit_records_roundtrip_and_corruption_is_rejected(self):
        account = ResourceAccounts(RESOURCE_TYPES)
        account.open('Chu', {'native:gold': -10})
        account.open('Qin', {'native:food': 20})
        data = account.to_save()
        with TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic-resources.json'
            path.write_text(json.dumps(data, ensure_ascii=False))
            restored = ResourceAccounts.from_save(json.loads(path.read_text()), resource_types=RESOURCE_TYPES)
            self.assertEqual(json.dumps(data), json.dumps(restored.to_save()))
        for damage in ('unknown_type', 'duplicate_stock', 'missing_stock', 'duplicate_owner', 'nan', 'extra_field'):
            bad = deepcopy(data)
            stocks = bad['accounts'][0]['stocks']
            if damage == 'unknown_type':stocks[0]['id'] = 'example:missing'
            if damage == 'duplicate_stock':stocks.append(deepcopy(stocks[0]))
            if damage == 'missing_stock':stocks.pop()
            if damage == 'duplicate_owner':bad['accounts'].append(deepcopy(bad['accounts'][0]))
            if damage == 'nan':stocks[0]['amount'] = float('nan')
            if damage == 'extra_field':stocks[0]['cheat'] = True
            with self.subTest(damage=damage),self.assertRaises(ValueError):
                ResourceAccounts.from_save(bad, resource_types=RESOURCE_TYPES)

    def test_mod_owns_objects_persistence_and_capabilities(self):
        runtime = resource_runtime()
        world = runtime.world_class()()
        world.resources.open('Qin', {'native:food': 10})
        self.assertEqual(runtime.state_owners['resources'], 'native:resources')
        self.assertEqual(runtime.capabilities['native:food'], 'native:resources')
        saved = runtime.save_mod_state(world)
        world.resources.consume('Qin', 'native:food', 1)
        runtime.load_mod_state(world, saved)
        self.assertEqual(world.resources.balance('Qin', 'native:food'), 10)
        with unit_context('unmodified-client'),self.assertRaises(ModError):
            world.resources.consume('Qin', 'native:food', 1)
        detached = deepcopy(world.resources)
        detached.consume('Qin', 'native:food', 2)
        self.assertEqual(world.resources.balance('Qin', 'native:food'), 10)

    def test_declaration_must_match_concrete_types(self):
        path = ROOT/'mods/native/resources/mod.toml'
        declaration = tomllib.loads(path.read_text())
        declaration['ResourceTypes'] = ['native:gold']
        with self.assertRaises(ModError):ResourcesMod().configure(declaration, path)

    def test_resource_module_loads_in_isolation_from_its_declaration(self):
        with TemporaryDirectory() as directory:
            path = Path(directory)/'resources.toml'
            path.write_text((ROOT/'mods/native/resources/mod.toml').read_text())
            runtime = Runtime.load(directory)
            world = runtime.world_class()()
            world.resources.open('Qin', {'native:gold': 100})
            self.assertEqual(runtime.object_call(world, 'native:gold', 'balance', 'Qin'), 100)
            manifest = runtime.dependency_manifest()
            runtime.check_save_dependencies(deepcopy(manifest))
            manifest['mods'][0]['version'] = 'older'
            with self.assertRaises(ModError):runtime.check_save_dependencies(manifest)

    def test_another_domain_registers_resource_without_editing_native_catalog(self):
        class Mana(Resource):
            TYPE_ID='example:mana'
            NAME_KEY='resource.example:mana.name'

        class Extension(GameMod):
            def mixins(self):return ()
            def content_definitions(self):return {'example:mana':Mana}

        path=ROOT/'mods/native/resources/mod.toml'
        native=ResourcesMod();native.configure(tomllib.loads(path.read_text()),path)
        runtime=Runtime([RuntimeUnit('native:resources',native),RuntimeUnit('example:extras',Extension())])
        self.assertIs(runtime.content['native']['gold'],Gold)
        self.assertIs(runtime.content['example']['mana'],Mana)
        world=runtime.world_class()()
        world.resources.open('Qin',{'example:mana':3})
        self.assertEqual(runtime.object_call(world,'example:mana','balance','Qin'),3)
        runtime.object_call(world,'example:mana','consume','Qin',1)
        self.assertEqual(world.resources.balance('Qin','example:mana'),2)
        data=runtime.save_mod_state(world)
        runtime.load_mod_state(world,data)
        self.assertEqual(runtime.save_mod_state(world),data)
        before=native.content_definitions()
        self.assertNotIn('example:mana',before)
