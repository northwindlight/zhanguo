"""Real isolated building/resource/market modules; synthetic sites and saves."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from engine.building import Building, BuildingCycle, recipe
from engine.resource import Resource
from engine.runtime import GameMod, ModError, Runtime, Unit
from engine.state import unit_context
from mods.native.buildings.types import Farm, NativeBuilding, BUILDING_TYPES
from mods.native.localization import building_name

ROOT = Path(__file__).resolve().parents[1]


class Tea(Resource):
    TYPE_ID='example:tea';NAME_KEY='resource.example:tea.name'


class TeaGarden(NativeBuilding):
    TYPE_ID='example:tea-garden';NAME_KEY='building.example:tea-garden.name'
    PHASE='gather';GOLD_COSTS=(30,);WOOD_COST=10;SITE_RESOURCE='native:farmland'
    OUTPUTS=recipe({'example:tea':2})


class TeaContent(GameMod):
    def mixins(self):return ()
    def content_definitions(self):return {Tea.TYPE_ID:Tea,TeaGarden.TYPE_ID:TeaGarden}


class TestBuildings(unittest.TestCase):
    def runtime(self, extra=()):
        loaded = Runtime.load(ROOT/'mods/buildings_acceptance')
        return Runtime([*loaded.units.values(),*extra],loaded.distribution)

    def world(self, extra=(), *, accounts=None):
        runtime = self.runtime(extra)
        world = runtime.world_class()()
        for owner,values in (accounts or {'Qin':{'native:gold':10000,'native:wood':1000,'native:food':100,'native:ore':100,'native:oil':100},
                                          'Chu':{'native:gold':10000,'native:wood':1000}}).items():
            world.resources.open(owner,values)
        world.buildings.register_site('example:qin','Qin',(0,0),resources={'native:farmland':20,'native:wood':20,'native:ore':20,'native:oil':20,'native:gold':20})
        world.buildings.register_site('example:chu','Chu',(1,0),resources={'native:farmland':20})
        return world

    def state(self,world):return world.runtime.save_mod_state(world)

    def test_template_content_identity_localization_and_owned_state(self):
        world = self.world()
        self.assertTrue(all(issubclass(cls,Building) for cls in BUILDING_TYPES))
        self.assertIs(world.runtime.content['native']['farm'],Farm)
        self.assertEqual(world.runtime.state_owners['buildings'],'native:buildings')
        self.assertEqual(building_name('native:farm'),'农场')
        self.assertEqual(building_name('native:farm','en-US'),'Farm')
        self.assertIn('native:farm',world.buildings.ids)
        before = self.state(world)
        for kind in ('农场','farm','example:unknown'):
            with self.assertRaises(ValueError):world.buildings.build('Qin','example:qin',kind)
            self.assertEqual(self.state(world),before)
        with self.assertRaises(FrozenInstanceError):world.buildings.site('example:qin').owner='Chu'

    def test_order_debits_immediately_and_produces_only_after_completion(self):
        world = self.world()
        receipt = world.buildings.build('Qin','example:qin','native:farm')
        self.assertEqual(dict(receipt.costs),{'native:gold':20,'native:wood':15})
        self.assertEqual(world.resources.balance('Qin','native:gold'),9980)
        self.assertEqual(world.resources.balance('Qin','native:wood'),985)
        self.assertEqual(world.buildings.count('example:qin','native:farm'),0)
        report = world.resolve_turn()
        self.assertEqual(dict(report['building_production'].outputs),{})
        self.assertEqual(dict(report['building_production'].inputs)['native:wood'],15)
        self.assertEqual(world.resources.balance('Qin','native:food'),100)
        self.assertEqual(report['building_completions'][0].id,receipt.id)
        self.assertEqual(world.buildings.count('example:qin','native:farm'),1)
        report = world.resolve_turn()
        self.assertEqual(dict(report['building_production'].outputs),{'native:food':1})
        self.assertEqual(world.resources.balance('Qin','native:food'),101)
        # National output is not a delivery: the buffer only moved by trades.
        self.assertEqual(dict(world.market.snapshot().inventory)['native:food'],
                         dict(world.market.snapshot().reference)['native:food'])

    def test_failed_order_is_atomic_and_does_not_take_quota_or_sequence(self):
        world = self.world(accounts={'Qin':{'native:gold':100,'native:wood':14},'Chu':{}})
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.build('Qin','example:qin','native:farm')
        self.assertEqual(self.state(world),before)
        world.resources.credit('Qin','native:wood',1)
        self.assertEqual(world.buildings.build('Qin','example:qin','native:farm').id,1)
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.build('Qin','example:qin','native:farm')
        with self.assertRaises(ValueError):world.buildings.build('Qin','example:chu','native:farm')
        self.assertEqual(self.state(world),before)

    def test_site_capacity_local_resource_limit_and_prerequisites(self):
        world = self.world()
        world.buildings.register_site('example:small','Qin',(2,0),capacity=1,resources={'native:farmland':1})
        world.buildings.build('Qin','example:small','native:farm')
        world.resolve_turn()
        before = self.state(world)
        for site,kind in (('example:small','native:farm'),('example:chu','native:mine'),('example:qin','native:barracks'),('example:qin','native:town-hall')):
            with self.subTest(site=site,kind=kind),self.assertRaises(ValueError):world.buildings.build('Qin',site,kind)
            self.assertEqual(self.state(world),before)
        world.buildings.seed('example:qin','native:farm',3)
        world.buildings.build('Qin','example:qin','native:barracks')
        self.assertEqual(world.buildings.count('example:qin','native:barracks',completed_only=False),1)

    def test_terrain_completed_academy_and_policy_discounts_keep_rounding_order(self):
        world = self.world()
        world.buildings.register_site('example:hill','Qin',(2,0),penalty=30,resources={'native:farmland':20})
        world.buildings.seed('example:hill','native:farm',4)
        world.buildings.seed('example:hill','native:academy')
        receipt = world.buildings.build('Qin','example:hill','native:supply-factory',policy_pct=80)
        self.assertEqual(dict(receipt.costs),{'native:gold':69,'native:wood':25})
        other = self.world()
        other.buildings.seed('example:qin','native:farm',4)
        other.buildings.seed('example:qin','native:academy',completed=False)
        receipt = other.buildings.build('Qin','example:qin','native:supply-factory')
        self.assertEqual(dict(receipt.costs)['native:gold'],90)

    def test_castle_levels_progressive_costs_and_limit(self):
        world = self.world()
        for level,cost in enumerate((50,100,200,400,800),1):
            receipt = world.buildings.build('Qin','example:qin','native:castle')
            self.assertEqual(dict(receipt.costs)['native:gold'],cost)
            self.assertEqual(world.buildings.instances('example:qin')[-1].level,level)
            world.resolve_turn()
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.build('Qin','example:qin','native:castle')
        self.assertEqual(self.state(world),before)
        self.assertEqual(world.buildings.effects('example:qin','native:castle')['defense_per_level'],10)

    def test_diplomacy_self_build_limit_survives_transfer(self):
        world = self.world()
        world.buildings.seed('example:qin','native:farm',5)
        world.buildings.build('Qin','example:qin','native:diplomacy-center')
        world.resolve_turn()
        world.buildings.transfer_site('example:qin','Chu')
        world.buildings.register_site('example:qin2','Qin',(2,0),resources={'native:farmland':20})
        world.buildings.seed('example:qin2','native:farm',5)
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.build('Qin','example:qin2','native:diplomacy-center')
        self.assertEqual(self.state(world),before)
        world.buildings.transfer_site('example:qin2','Chu')
        world.buildings.build('Chu','example:qin2','native:diplomacy-center')
        self.assertEqual(world.buildings.count('example:qin','native:diplomacy-center'),1)

    def test_gather_precedes_power_and_factory_with_exact_resource_flows(self):
        world = self.world(accounts={'Qin':{'native:food':1,'native:ore':0,'native:wood':0},'Chu':{}})
        for kind in ('native:farm','native:mine','native:lumber-camp','native:wood-power-plant','native:supply-factory'):
            world.buildings.seed('example:qin',kind)
        report = world.resolve_turn()['building_production']
        self.assertEqual(dict(report.inputs),{'native:wood':1,'native:food':2,'native:ore':1})
        self.assertEqual(dict(report.outputs),{'native:food':1,'native:ore':1,'native:wood':1,'native:supply':2})
        self.assertEqual(world.resources.balance('Qin','native:food'),0)
        self.assertEqual(world.resources.balance('Qin','native:ore'),0)
        self.assertEqual(world.resources.balance('Qin','native:wood'),0)
        self.assertEqual(world.resources.balance('Qin','native:supply'),2)
        energy = next(row for row in report.energy if row.owner=='Qin')
        self.assertEqual((energy.generated,energy.demand,energy.short),(2,1,False))

    def test_missing_factory_input_consumes_nothing_and_partial_plants_run(self):
        world = self.world(accounts={'Qin':{'native:wood':1,'native:food':5,'native:ore':0},'Chu':{}})
        world.buildings.seed('example:qin','native:wood-power-plant',2)
        world.buildings.seed('example:qin','native:supply-factory')
        report = world.resolve_turn()['building_production']
        status = [row.status for row in report.records]
        self.assertEqual(status,['running','missing-input','missing-input'])
        self.assertEqual(world.resources.balance('Qin','native:food'),5)
        self.assertEqual(world.resources.balance('Qin','native:supply'),0)
        self.assertEqual(dict(report.inputs),{'native:wood':1})
        self.assertEqual(world.buildings.industry_upkeep('Qin'),{'native:wood':2,'native:food':2,'native:ore':1})

    def test_national_energy_shortage_stops_all_industry_and_halls(self):
        world = self.world()
        world.buildings.seed('example:qin','native:wood-power-plant')
        world.buildings.seed('example:qin','native:supply-factory',2)
        world.buildings.seed('example:qin','native:town-hall')
        before = world.resources.to_save()
        report = world.resolve_turn()['building_production']
        energy = next(row for row in report.energy if row.owner=='Qin')
        self.assertEqual((energy.generated,energy.demand,energy.short),(2,3,True))
        self.assertEqual([row.status for row in report.records],['running','energy-shortage','energy-shortage','energy-shortage'])
        self.assertEqual(world.resources.balance('Qin','native:supply'),0)
        self.assertEqual(world.resources.balance('Qin','native:food'),100)
        self.assertEqual(world.resources.balance('Qin','native:gold'),10000)
        self.assertEqual(world.resources.balance('Qin','native:wood'),999)

    def test_external_energy_hall_output_and_gold_mine_are_explicit(self):
        world = self.world()
        world.buildings.seed('example:qin','native:farm',2)
        world.buildings.seed('example:qin','native:town-hall')
        world.buildings.seed('example:qin','native:gold-mine')
        report = world.buildings.produce(external_energy={'Qin':1})
        # Hall: 5 + three other completed slots; mine: ten cash.
        self.assertEqual(dict(report.outputs)['native:gold'],18)
        self.assertEqual(world.resources.balance('Qin','native:gold'),10018)
        self.assertEqual(next(row for row in report.energy if row.owner=='Qin').external,1)
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.produce()
        self.assertEqual(self.state(world),before)
        world.buildings.complete_construction()
        with self.assertRaises(ValueError):world.buildings.complete_construction()

    def test_new_tea_building_registers_produces_and_market_ignores_unlisted_tea(self):
        world = self.world((Unit('example:tea-content',TeaContent()),))
        self.assertIn('example:tea-garden',world.buildings.ids)
        world.buildings.build('Qin','example:qin','example:tea-garden')
        world.resolve_turn()
        result = world.resolve_turn()
        self.assertEqual(world.resources.balance('Qin','example:tea'),2)
        self.assertEqual(dict(result['building_production'].outputs),{'example:tea':2})
        self.assertNotIn('example:tea',dict(world.market.snapshot().prices))
        self.assertNotIn('example:tea',dict(world.market.snapshot().inventory))
        self.assertEqual(dict(world.market.snapshot().prices)['native:food'],1.5)
        saved = json.loads(json.dumps(self.state(world)))
        other = self.world((Unit('example:tea-content',TeaContent()),))
        other.runtime.load_mod_state(other,saved)
        self.assertEqual(self.state(other),saved)
        world.resolve_turn();other.resolve_turn()
        self.assertEqual(self.state(other),self.state(world))
        self.assertEqual(other.resources.balance('Qin','example:tea'),4)

    def test_batch_rejection_rolls_back_all_owners_and_building_period(self):
        class FragileTea(Tea):
            @classmethod
            def validate_amount(cls,amount):
                super().validate_amount(amount)
                if amount>1:raise ValueError('Tea warehouse capacity')
        class BadContent(GameMod):
            def mixins(self):return ()
            def content_definitions(self):return {Tea.TYPE_ID:FragileTea,TeaGarden.TYPE_ID:TeaGarden}
        world = self.world((Unit('example:bad-tea',BadContent()),))
        world.buildings.seed('example:chu','native:farm')
        world.buildings.seed('example:qin','native:farm')
        world.buildings.seed('example:qin','example:tea-garden')
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.produce()
        self.assertEqual(self.state(world),before)
        world.resources.credit('Qin','example:tea',1)
        before = world.resources.to_save()
        with self.assertRaises(ValueError):world.resources.exchange_batch([
            {'owner':'Chu','debits':{},'credits':{'native:food':1}},
            {'owner':'Qin','debits':{'native:food':1},'credits':{'example:tea':1}}])
        self.assertEqual(world.resources.to_save(),before)

    def test_invalid_cycle_and_external_energy_are_rejected_before_any_write(self):
        class BadFarm(Farm):
            TYPE_ID='example:bad-farm'
            def cycle(self,**context):return BuildingCycle(outputs=(('example:unregistered',1),))
        class BadContent(GameMod):
            def mixins(self):return ()
            def content_definitions(self):return {BadFarm.TYPE_ID:BadFarm}
        world = self.world((Unit('example:bad-cycle',BadContent()),))
        world.buildings.seed('example:qin','native:farm')
        world.buildings.seed('example:qin','example:bad-farm')
        before = self.state(world)
        with self.assertRaises(ValueError):world.buildings.produce()
        self.assertEqual(self.state(world),before)
        with self.assertRaises(ValueError):world.buildings.produce(external_energy={'Qin':True})
        self.assertEqual(self.state(world),before)

    def test_pending_save_roundtrip_and_corrupt_restore_is_atomic(self):
        world = self.world()
        world.buildings.build('Qin','example:qin','native:farm')
        saved = json.loads(json.dumps(self.state(world)))
        other = self.world();other.runtime.load_mod_state(other,saved)
        self.assertEqual(self.state(other),saved)
        world.resolve_turn();other.resolve_turn()
        world.resolve_turn();other.resolve_turn()
        self.assertEqual(self.state(other),self.state(world))
        for damage in ('counter','kind','missing_site','duplicate','level','capacity','progress','order','self_count','receipt','extra'):
            bad = deepcopy(saved['native:buildings'])
            if damage=='counter':bad['next_id']=1
            if damage=='kind':bad['instances'][0]['kind']='example:unknown'
            if damage=='missing_site':bad['sites']=[]
            if damage=='duplicate':bad['instances'].append(deepcopy(bad['instances'][0]))
            if damage=='level':bad['instances'][0]['level']=2
            if damage=='capacity':bad['sites'][0]['resources']['native:farmland']=0
            if damage=='progress':bad['instances'][0]['remaining']=2
            if damage=='order':bad['sites'][0]['last_order']=-1
            if damage=='self_count':bad['self_built']=[]
            if damage=='receipt':bad['construction'][0]['period']=2
            if damage=='extra':bad['secret']=1
            before = world.buildings.to_save()
            with self.subTest(damage=damage),self.assertRaises(ValueError):world.buildings.restore(bad)
            self.assertEqual(world.buildings.to_save(),before)

    def test_interface_permissions_and_immutable_instance_views(self):
        class Client(GameMod):
            declaration={'Interfaces':{'native:buildings':['build','instances']}}
            def mixins(self):return ()
            def needs(self):return ('native:buildings',)
        world = self.world((Unit('example:client',Client()),))
        with unit_context('example:client'):
            with self.assertRaises(ModError):world.buildings.build('Qin','example:qin','native:farm')
            with self.assertRaises(ModError):world.runtime.object_call(world,'native:buildings','seed','example:qin','native:farm')
            with self.assertRaises(ModError):world.runtime.object_call(world,'native:resource-exchange','exchange','Qin',{}, {'native:gold':10})
            receipt=world.runtime.object_call(world,'native:buildings','build','Qin','example:qin','native:farm')
        with self.assertRaises(FrozenInstanceError):world.buildings.instances()[0].remaining=0
        with unit_context('example:client'),self.assertRaises(ModError):world.buildings._instances[receipt.id].complete()
        read=world.runtime.read_state(world,'buildings')
        self.assertEqual(read.instances[0].remaining,1)

    def test_buildings_module_runs_without_market_or_legacy_domains(self):
        loaded = self.runtime()
        selected = [unit for name,unit in loaded.units.items() if name in ('native:resources','native:buildings')]
        runtime = Runtime(selected,{'Hooks':[{'Id':'buildings.production'},{'Id':'buildings.completion'}]})
        world = runtime.world_class()()
        world.resources.open('Qin',{})
        world.buildings.register_site('example:only','Qin',(0,0),resources={'native:farmland':1})
        world.buildings.seed('example:only','native:farm')
        result = world.resolve_turn()
        self.assertEqual(dict(result['building_production'].outputs),{'native:food':1})
        self.assertEqual(world.resources.balance('Qin','native:food'),1)
        self.assertNotIn('native:market',runtime.units)

    def test_factory_competition_follows_site_and_catalog_order(self):
        world = self.world(accounts={'Qin':{'native:food':2,'native:ore':1,'native:oil':1,'native:wood':2},'Chu':{}})
        # Create equipment first; native catalog still gives supply priority,
        # matching the legacy per-site building table traversal.
        world.buildings.seed('example:qin','native:equipment-factory')
        world.buildings.seed('example:qin','native:supply-factory')
        world.buildings.seed('example:qin','native:wood-power-plant',2)
        report = world.resolve_turn()['building_production']
        self.assertEqual(world.resources.balance('Qin','native:supply'),2)
        self.assertEqual(world.resources.balance('Qin','native:equipment'),0)
        self.assertEqual(next(row for row in report.records if row.kind=='native:equipment-factory').status,'missing-input')
        self.assertEqual(world.resources.balance('Qin','native:oil'),1)

    def test_save_between_production_and_completion_cannot_double_produce(self):
        world = self.world()
        world.buildings.seed('example:qin','native:farm')
        world.buildings.build('Qin','example:qin','native:farm')
        world.buildings.produce()
        saved = json.loads(json.dumps(self.state(world)))
        other = self.world();other.runtime.load_mod_state(other,saved)
        before = self.state(other)
        with self.assertRaises(ValueError):other.buildings.produce()
        with self.assertRaises(ValueError):other.buildings.build('Qin','example:qin','native:farm')
        self.assertEqual(self.state(other),before)
        other.buildings.complete_construction();world.buildings.complete_construction()
        self.assertEqual(self.state(other),self.state(world))
        self.assertEqual(other.resources.balance('Qin','native:food'),101)
        self.assertEqual(other.buildings.count('example:qin','native:farm'),2)

    def test_native_definitions_preserve_legacy_building_numbers(self):
        # Legacy vocabulary exists only in this audit fixture. New definitions
        # do not translate IDs or import the legacy parameter table.
        from mods.native.buildings.parameters import BUILDINGS
        names=('城堡','林场','农场','矿场','石油厂','黄金矿场','木材能源厂','石油能源厂',
               '补给厂','装备厂','兵营','市政厅','瞭望塔','外交中心','工程院','军屯')
        resources={'黄金':'native:gold','木头':'native:wood','粮食':'native:food','矿石':'native:ore',
                   '石油':'native:oil','装备':'native:equipment','补给':'native:supply','耕地':'native:farmland'}
        for cls,name in zip(BUILDING_TYPES,names):
            with self.subTest(kind=cls.TYPE_ID):
                old=BUILDINGS[name]
                self.assertEqual(cls.GOLD_COSTS,tuple(old['cost']) if isinstance(old['cost'],list) else (old['cost'],))
                self.assertEqual(cls.WOOD_COST,old['wood'])
                self.assertEqual(cls.SITE_RESOURCE,resources.get(old['cap_resource']))
                self.assertEqual(cls.MIN_SLOTS,old.get('min_slots',0))
                self.assertEqual(cls.MAX_PER_SITE,old.get('max_level',old.get('limit')))
                self.assertEqual(cls.SELF_BUILD_LIMIT,old.get('limit_nation'))
                self.assertEqual(cls.ENERGY_USE,old.get('energy',0))
                self.assertEqual(dict(cls.INPUTS),{resources[key]:value for key,value in old.get('fuel',old.get('inputs',{})).items()})
                expected={resources[key]:value*(10 if old['kind']=='gold' else 1) for key,value in old.get('outputs',{}).items()}
                self.assertEqual(dict(cls.OUTPUTS),expected)
                self.assertEqual(cls.ENERGY_OUTPUT,old.get('energy_out',0))
