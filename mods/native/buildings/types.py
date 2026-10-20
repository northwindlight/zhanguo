"""Native building content: namespaced identities and concrete behavior."""
from types import MappingProxyType
from engine.building import Building, BuildingCycle, integer, recipe


class NativeBuilding(Building):
    PHASE = 'passive'
    GOLD_COSTS = (0,)
    WOOD_COST = 0
    SITE_RESOURCE = None
    MIN_SLOTS = 0
    MAX_PER_SITE = None
    SELF_BUILD_LIMIT = None
    ENERGY_USE = 0
    BUILD_DISCOUNT = 0
    EFFECTS = MappingProxyType({})
    INPUTS = MappingProxyType({})
    OUTPUTS = MappingProxyType({})
    ENERGY_OUTPUT = 0

    @classmethod
    def construction_cost(cls, level, *, penalty=0, discount=0, policy_pct=100):
        integer(level,1);integer(penalty);integer(discount);integer(policy_pct)
        if discount > 100:raise ValueError('Invalid construction discount')
        if len(cls.GOLD_COSTS)>1 and level>len(cls.GOLD_COSTS):raise ValueError('Maximum building level')
        cost = cls.GOLD_COSTS[level-1 if len(cls.GOLD_COSTS)>1 else 0]
        cost = cost*(100+penalty)//100
        cost = cost*(100-discount)//100
        cost = cost*policy_pct//100
        return {key:value for key,value in (('native:gold',cost),('native:wood',cls.WOOD_COST)) if value}

    def cycle(self, *, other_slots=0):
        return BuildingCycle(tuple(self.INPUTS.items()),tuple(self.OUTPUTS.items()),self.ENERGY_OUTPUT)


class Castle(NativeBuilding):
    TYPE_ID='native:castle';NAME_KEY='building.native:castle.name';ROLES=('castle',)
    GOLD_COSTS=(50,100,200,400,800);WOOD_COST=20;MAX_PER_SITE=5
    EFFECTS=MappingProxyType({'defense_per_level':10})


class LumberCamp(NativeBuilding):
    TYPE_ID='native:lumber-camp';NAME_KEY='building.native:lumber-camp.name'
    PHASE='gather';GOLD_COSTS=(20,);WOOD_COST=15;SITE_RESOURCE='native:wood'
    OUTPUTS=recipe({'native:wood':1})


class Farm(NativeBuilding):
    TYPE_ID='native:farm';NAME_KEY='building.native:farm.name'
    PHASE='gather';GOLD_COSTS=(20,);WOOD_COST=15;SITE_RESOURCE='native:farmland'
    OUTPUTS=recipe({'native:food':1})


class Mine(NativeBuilding):
    TYPE_ID='native:mine';NAME_KEY='building.native:mine.name'
    PHASE='gather';GOLD_COSTS=(30,);WOOD_COST=15;SITE_RESOURCE='native:ore'
    OUTPUTS=recipe({'native:ore':1})


class OilWell(NativeBuilding):
    TYPE_ID='native:oil-well';NAME_KEY='building.native:oil-well.name'
    PHASE='gather';GOLD_COSTS=(60,);WOOD_COST=20;SITE_RESOURCE='native:oil'
    OUTPUTS=recipe({'native:oil':1})


class GoldMine(NativeBuilding):
    TYPE_ID='native:gold-mine';NAME_KEY='building.native:gold-mine.name'
    PHASE='gather';GOLD_COSTS=(85,);WOOD_COST=25;SITE_RESOURCE='native:gold'
    OUTPUTS=recipe({'native:gold':10})


class WoodPowerPlant(NativeBuilding):
    TYPE_ID='native:wood-power-plant';NAME_KEY='building.native:wood-power-plant.name';ROLES=('power',)
    PHASE='power';GOLD_COSTS=(60,);WOOD_COST=30
    INPUTS=recipe({'native:wood':1});ENERGY_OUTPUT=2


class OilPowerPlant(NativeBuilding):
    TYPE_ID='native:oil-power-plant';NAME_KEY='building.native:oil-power-plant.name';ROLES=('power',)
    PHASE='power';GOLD_COSTS=(120,);WOOD_COST=30
    INPUTS=recipe({'native:oil':1});ENERGY_OUTPUT=8


class SupplyFactory(NativeBuilding):
    TYPE_ID='native:supply-factory';NAME_KEY='building.native:supply-factory.name'
    PHASE='industry';GOLD_COSTS=(90,);WOOD_COST=25;ENERGY_USE=1
    INPUTS=recipe({'native:food':2,'native:ore':1});OUTPUTS=recipe({'native:supply':2})


class EquipmentFactory(NativeBuilding):
    TYPE_ID='native:equipment-factory';NAME_KEY='building.native:equipment-factory.name'
    PHASE='industry';GOLD_COSTS=(105,);WOOD_COST=25;ENERGY_USE=1
    INPUTS=recipe({'native:ore':1,'native:oil':1});OUTPUTS=recipe({'native:equipment':2})


class Barracks(NativeBuilding):
    TYPE_ID='native:barracks';NAME_KEY='building.native:barracks.name';ROLES=('recruiter',)
    GOLD_COSTS=(175,);WOOD_COST=40;MIN_SLOTS=3;ENERGY_USE=1
    EFFECTS=MappingProxyType({'recruit_cap':1,'recruit_food':10,'recruit_equipment':5})


class TownHall(NativeBuilding):
    TYPE_ID='native:town-hall';NAME_KEY='building.native:town-hall.name';ROLES=('capital',)
    SLOT_BONUS=3  # 经济估算的密度假设：厅按另增 3 座建筑算
    PHASE='income';GOLD_COSTS=(250,);WOOD_COST=80;MIN_SLOTS=6;MAX_PER_SITE=1;ENERGY_USE=1
    EFFECTS=MappingProxyType({'gold_base':5,'gold_per_slot':1})

    def cycle(self, *, other_slots=0):
        integer(other_slots)
        return BuildingCycle(outputs=(('native:gold',self.EFFECTS['gold_base']+other_slots*self.EFFECTS['gold_per_slot']),))


class Watchtower(NativeBuilding):
    TYPE_ID='native:watchtower';NAME_KEY='building.native:watchtower.name';ROLES=('vision',)
    GOLD_COSTS=(60,);WOOD_COST=30;EFFECTS=MappingProxyType({'vision_radius':4})


class DiplomacyCenter(NativeBuilding):
    TYPE_ID='native:diplomacy-center';NAME_KEY='building.native:diplomacy-center.name'
    GOLD_COSTS=(200,);WOOD_COST=60;MIN_SLOTS=5;MAX_PER_SITE=1;SELF_BUILD_LIMIT=1


class Academy(NativeBuilding):
    TYPE_ID='native:academy';NAME_KEY='building.native:academy.name';ROLES=('academy',)
    AMORT_TURNS=30  # 一次省下的建造金折成每回合收益时的摊销期
    GOLD_COSTS=(150,);WOOD_COST=60;MIN_SLOTS=4;MAX_PER_SITE=1;BUILD_DISCOUNT=25
    EFFECTS=MappingProxyType({'build_discount':25})


class MilitiaCamp(NativeBuilding):
    TYPE_ID='native:militia-camp';NAME_KEY='building.native:militia-camp.name';ROLES=('militia',)
    GOLD_COSTS=(110,);WOOD_COST=30;SITE_RESOURCE='native:farmland';MAX_PER_SITE=1
    EFFECTS=MappingProxyType({'militia_cap':2,'militia_per_turn':1})


BUILDING_TYPES=(Castle,LumberCamp,Farm,Mine,OilWell,GoldMine,WoodPowerPlant,OilPowerPlant,
                SupplyFactory,EquipmentFactory,Barracks,TownHall,Watchtower,DiplomacyCenter,Academy,MilitiaCamp)
