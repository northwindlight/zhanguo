"""Army/resource collaboration, with explicit fixtures for unconverted domains."""
from copy import deepcopy
from pathlib import Path
import tomllib
import unittest

from engine.runtime import GameMod, ModError, Runtime, Unit as RuntimeUnit
from engine.state import unit_context
from mods.native.army.logic import ArmyLogic
from mods.native.army.objects import Units
from mods.native.army.parameters import UNIT_TYPES
from mods.native.army.rules import army_name, unit_label
from mods.native.army.content import native_unit_definitions, unit_definitions
from mods.native.resources.mod import ResourcesMod

ROOT = Path(__file__).resolve().parents[1]
ARMY_DECLARATION = tomllib.loads((ROOT/'mods/native/army/mod.toml').read_text())
RESOURCE_IDS = ('native:gold', 'native:food', 'native:equipment', 'native:supply')


class ArmyResourceClient(GameMod):
    # This test client exercises the real ArmyLogic resource paths. It uses
    # army's actual grants and supplies no production adapters to old domains.
    declaration = ARMY_DECLARATION

    def mixins(self):return ()
    def needs(self):return RESOURCE_IDS
    def state_keys(self):return ('units',)
    def content_definitions(self):return native_unit_definitions()


class ArmyFixture(ArmyLogic):
    def __init__(self, extra_modules=()):
        path = ROOT/'mods/native/resources/mod.toml'
        resources = ResourcesMod();resources.configure(tomllib.loads(path.read_text()), path)
        self.runtime = Runtime([RuntimeUnit('native:resources',resources),
                                RuntimeUnit('native:army',ArmyResourceClient()), *extra_modules])
        self.size = 16;self.turn = 1
        resources.initialize_world(self)
        self.resources.bind_state(self,'resources')
        self.resources.open('Qin',{'native:gold':100,'native:food':40,
                                  'native:equipment':20,'native:supply':10})
        self.units = Units(self.size,definitions=unit_definitions(self.runtime.content));self.units.bind_state(self,'units')
        self.nations = {'Qin':object()}
        self.tiles = {(3,4): {'owner':'Qin','name':'test tile','buildings':{'兵营':1,'军屯':1},
                              'recruited_this_turn':0}}
        self.grid_short = {}
        self.flow_out = {key:0 for key in RESOURCE_IDS if key!='native:gold'}
        self.ledger = {'Qin':{'supply_eaten':0,'heal_value':0}}
        self.spend = {'Qin':{'recruit':0,'supply':0}}
        self.events = []

    def res(self,*args):raise AssertionError('Legacy account read')
    def add_res(self,*args):raise AssertionError('Legacy account write')
    def _mval(self,*args):raise AssertionError('Legacy resource valuation')
    def market_price(self,key):
        return {'native:gold':10,'native:food':1.5,'native:equipment':8,'native:supply':6}[key]
    def polity_rule(self,*args):return {}
    def nation_building_count(self,*args):return 1
    def alive(self):return ['Qin']
    def battle_sides(self,*args):return {}
    def _ledger(self,owner):return self.ledger[owner]
    def _spend(self,owner):return self.spend[owner]
    def log(self,text,**kwargs):self.events.append(text)


class TestArmyResources(unittest.TestCase):
    def test_army_declares_exact_resource_capabilities_and_limited_grants(self):
        needs = ARMY_DECLARATION['Capabilities']['Needs']
        self.assertTrue(set(RESOURCE_IDS) <= set(needs))
        self.assertFalse({'method.res','method.add_res','method._mval','native:resources'} & set(needs))
        self.assertEqual(ARMY_DECLARATION['Mod']['Id'],'native:army')
        self.assertEqual(ARMY_DECLARATION['Mod']['Requires'],['native:resources'])
        for key in RESOURCE_IDS:
            self.assertEqual(ARMY_DECLARATION['Interfaces'][key],['balance','consume','value'])
        with self.assertRaisesRegex(ModError,'所需能力无提供者'):
            Runtime([RuntimeUnit('native:army',ArmyResourceClient())])

    def test_recruit_uses_concrete_resources_and_currency_polymorphism(self):
        w=ArmyFixture()
        with unit_context('native:army'):
            ok,message=w.recruit('Qin',3,4,1,'native:militia')
        self.assertTrue(ok,message)
        self.assertEqual(w.resources.balance('Qin','native:gold'),50)
        self.assertEqual(w.resources.balance('Qin','native:food'),35)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),20)
        self.assertEqual(w.spend['Qin']['recruit'],57.5)
        self.assertEqual(w.units[0].kind,'native:militia')
        self.assertIn('民兵',message)

    def test_infantry_recruit_consumes_food_and_equipment(self):
        w=ArmyFixture()
        with unit_context('native:army'):
            self.assertTrue(w.recruit('Qin',3,4,1,'native:infantry')[0])
        self.assertEqual(w.resources.balance('Qin','native:food'),30)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),15)
        self.assertEqual(w.spend['Qin']['recruit'],55)

    def test_supply_and_healing_use_resource_owned_inventory(self):
        w=ArmyFixture();unit=w.units.spawn('Qin','native:infantry',3,4)
        unit.take_damage(60)
        with unit_context('native:army'):w._stage_supply({})
        self.assertEqual(w.resources.balance('Qin','native:supply'),9)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),19)
        self.assertEqual(unit.hp,70)
        self.assertEqual(w.spend['Qin']['supply'],6)
        self.assertEqual(w.ledger['Qin']['heal_value'],8)

    def test_starvation_and_militia_exemption_preserve_rules(self):
        w=ArmyFixture();unit=w.units.spawn('Qin','native:infantry',3,4)
        w.resources.consume('Qin','native:supply',10)
        with unit_context('native:army'):w._stage_supply({})
        self.assertEqual(unit.hp,65)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),20)
        w=ArmyFixture();unit=w.units.spawn('Qin','native:militia',3,4)
        unit.take_damage(30)
        with unit_context('native:army'):w._stage_supply({})
        self.assertEqual(unit.hp,80)
        self.assertEqual(w.resources.balance('Qin','native:supply'),10)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),20)

    def test_permissions_prevent_raw_writes_and_ungranted_operations(self):
        w=ArmyFixture();before=w.resources.to_save()
        with unit_context('native:army'):
            with self.assertRaises(ModError):w.resources.consume('Qin','native:food',1)
            with self.assertRaises(ModError):w.resources._stock('Qin','native:food').consume(1)
            with self.assertRaises(ModError):w.runtime.object_call(w,'native:gold','credit','Qin',100)
            with self.assertRaises(ModError):w.runtime.object_call(w,'native:wood','consume','Qin',1)
            w._consume_resource('Qin','native:food',1)
        self.assertEqual(w.resources.balance('Qin','native:food'),39)
        self.assertEqual(before['accounts'][0]['stocks'][0]['amount'],100)

    def test_stock_and_unit_ids_survive_explicit_roundtrip(self):
        w=ArmyFixture()
        with unit_context('native:army'):w.recruit('Qin',3,4,1,'native:infantry')
        units=Units.from_save(w.units.to_save(),size=w.size)
        self.assertEqual(units.to_save(),w.units.to_save())
        self.assertEqual(units[0].kind,'native:infantry')
        before=deepcopy(w.units.to_save())
        for kind in ('步','infantry','other:infantry'):
            with self.assertRaises(ValueError):w.units.spawn('Qin',kind,3,4)
            self.assertEqual(w.units.to_save(),before)

    def test_display_locale_does_not_change_kind_or_cost(self):
        cost=deepcopy(UNIT_TYPES['native:infantry']['recruit'])
        self.assertEqual(unit_label('native:infantry'),'步兵')
        self.assertEqual(unit_label('native:infantry','en-US'),'Infantry')
        self.assertNotEqual(army_name('Qin',1,'native:infantry'),army_name('Qin',1,'native:infantry','en-US'))
        self.assertEqual(cost,UNIT_TYPES['native:infantry']['recruit'])

    def test_registered_fourth_unit_can_recruit_heal_and_roundtrip(self):
        class Archers(GameMod):
            def mixins(self):return ()
            def content_definitions(self):
                return {'example:archer': {'category':'unit','name_key':'unit.example:archer.name',
                                          'hp':120,'speed':1,'supply':1,'atk':60,
                                          'recruit':{'native:food':8,'native:equipment':3},
                                          'move_cost':{'平原':1}}}
        w=ArmyFixture((RuntimeUnit('example:archers',Archers()),))
        self.assertIn('archer',w.runtime.content['example'])
        with unit_context('native:army'):
            ok,message=w.recruit('Qin',3,4,1,'example:archer')
        self.assertTrue(ok,message)
        unit=w.units[0]
        self.assertEqual((unit.kind,unit.hp,unit.atk()),('example:archer',120,60))
        self.assertEqual(w.resources.balance('Qin','native:food'),32)
        self.assertEqual(w.resources.balance('Qin','native:equipment'),17)
        unit.take_damage(20)
        with unit_context('native:army'):w._stage_supply({})
        self.assertEqual(unit.hp,120)
        saved=w.units.to_save()
        restored=Units.from_save(saved,size=w.size,definitions=unit_definitions(w.runtime.content))
        self.assertEqual(restored.to_save(),saved)
        self.assertEqual(restored[0].atk(),60)
        with self.assertRaises(ValueError):Units.from_save(saved,size=w.size)
        with self.assertRaises(TypeError):unit.definition['hp']=999
        from mods.native.army.mod import ArmyMod
        schema={'function':{'name':'recruit','parameters':{'properties':{'kind':{}}}}}
        adapted=ArmyMod().schema_for_actor(w,'Qin',schema)
        self.assertIn('example:archer',adapted['function']['parameters']['properties']['kind']['enum'])
