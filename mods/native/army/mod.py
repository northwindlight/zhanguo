from engine.runtime import GameMod, Panel, RuleSection, Stage, Tool
from copy import deepcopy
from .content import native_unit_definitions, unit_definitions
from .objects import Units
from .domain_objects import Military
from .parameters import ARMY_STARVE_DAMAGE, ARMY_HEAL_PER_TURN, RETREAT_ATK_PENALTY, RETREAT_DEF_COVER


TERRAIN_IDS={'平原':'native:plain','森林':'native:forest','丘陵':'native:hill','山地':'native:mountain','沙漠':'native:desert'}


class ArmyMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('units','military')
    def needs(self):return ('native:nation-inspection','native:government','native:territory','native:vision','native:diplomacy','native:buildings','native:resource-exchange','native:gold','native:food','native:equipment','native:supply')
    def provides(self):return ('native:army',)
    def numbers(self):return {'army.starvation':ARMY_STARVE_DAMAGE,'army.heal':ARMY_HEAL_PER_TURN,'army.retreat.attack-penalty':RETREAT_ATK_PENALTY,'army.retreat.defender-cover':RETREAT_DEF_COVER}
    def content_definitions(self):
        return {key:{**value,'move_cost':{TERRAIN_IDS[k]:v for k,v in value['move_cost'].items()}} for key,value in native_unit_definitions().items()}
    def initialize_world(self, world):
        size,_=self.runtime.object_call(world,'native:territory','dimensions')
        world.units=Units(size,definitions=unit_definitions(self.runtime.content))
        world.military=Military(world.units,
            lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            lambda actor,key,default:self.runtime.parameter(world,actor,key,default))
    def objects(self, world):return {'native:army':world.military}
    def save_state(self, world):return {'units':{**world.units.to_save(),'engage_sequence':world.units.engage_seq},'military':world.military.to_save()}
    def load_state(self, world, state):
        if not isinstance(state,dict) or set(state)!={'units','military'}:raise ValueError('Invalid army save')
        size,_=self.runtime.object_call(world,'native:territory','dimensions')
        candidate=Units.from_save(state['units'],size=size,definitions=world.units.definitions)
        sequence=state['units'].get('engage_sequence')
        if type(sequence) is not int or sequence<candidate.engage_seq:raise ValueError('Invalid engagement sequence')
        owners=self.runtime.object_call(world,'native:nation-inspection','ids')
        if any(u.owner!='野人' and u.owner not in owners for u in candidate):raise ValueError('Unit owner is not a living nation')
        world.military.restore(state['military']);world.units.size=size;world.units.restore(state['units']);world.units.engage_seq=sequence
    def tools(self):
        recruit=Tool('recruit',self._recruit)
        parameters=recruit.schema()['function']['parameters']
        parameters['properties']['kind']['enum']=list(unit_definitions(self.runtime.content))
        return (Tool('recruit',self._recruit,parameters=parameters),Tool('move',self._move),Tool('attack',self._attack),
                Tool('retreat',self._retreat),Tool('disband',self._disband),
                Tool('army',lambda w,a:w.military.visible_units(a),read_only=True),Tool('reachable',self._reachable,read_only=True),
                Tool('unit_catalog',self._catalog,read_only=True),
                Tool('recruit_quote',self._recruit_quote,read_only=True),
                Tool('battle_estimate',self._battle_estimate,read_only=True))
    def schema_for_actor(self, world, actor, schema):
        result=deepcopy(schema)
        if result['function']['name']=='recruit':
            result['function']['parameters']['properties']['kind']['enum']=list(world.units.type_ids)
            overrides=world.runtime.parameter(world,actor,'recruit',{})
            costs={kind:dict(overrides.get(kind,world.units.definition(kind)['recruit'])) for kind in world.units.type_ids}
            result['function']['description']='Recruit on an owned, powered site. Resource costs per registered kind: '+str(costs)
        return result
    @staticmethod
    def _recruit(world, actor, site: str, kind: str='native:infantry', count: int=1):
        return world.military.recruit(actor,site,kind,count,energy_short=world.military.grid_short(actor))
    @staticmethod
    def _move(world, actor, aid: int, x: int, y: int):return world.military.move(actor,aid,x,y)
    @staticmethod
    def _attack(world, actor, ids: list[int], x: int, y: int):return world.military.attack(actor,ids,x,y)
    @staticmethod
    def _retreat(world, actor, aid: int, x: int, y: int):return world.military.retreat(actor,aid,x,y)
    @staticmethod
    def _disband(world, actor, aid: int):return world.military.disband(actor,aid)
    @staticmethod
    def _reachable(world, actor, aid: int, attack: bool=False):return world.military.reachable(actor,aid,attack,visible_only=True)
    @staticmethod
    def _catalog(world, actor):
        overrides=world.runtime.parameter(world,actor,'recruit',{})
        return {kind:{**dict(world.units.definition(kind)),
                      'recruit':dict(overrides.get(kind,world.units.definition(kind)['recruit'])),
                      'speed':world.runtime.parameter(world,actor,'unit.speed.'+kind,world.units.definition(kind)['speed']),
                      'supply':world.runtime.parameter(world,actor,'unit.supply.'+kind,world.units.definition(kind)['supply'])}
                for kind in world.units.type_ids}
    @staticmethod
    def _recruit_quote(world, actor, site: str, kind: str='native:infantry', count: int=1):
        return world.military.recruit_quote(actor,site,kind,count,energy_short=world.military.grid_short(actor))
    @staticmethod
    def _battle_estimate(world, actor, ids: list[int], x: int, y: int):
        return world.military.battle_estimate(actor,ids,x,y)

    def panels(self):return (Panel('army',lambda w,a:w.military.visible_units(a)),)
    def stages(self):
        return (Stage('native:army.supply',self._supply,hook='military.supply',needs=('relations.applied','buildings.produced'),provides=('military.supplied',)),
                Stage('native:army.battle',lambda w,c:c.update(battles=w.military.battle()),hook='military.battle',needs=('military.supplied',),provides=('battles.resolved',)),
                Stage('native:army.withdraw',lambda w,c:c.update(withdrawals=w.military.withdrawals()),hook='military.withdraw',needs=('battles.resolved',),provides=('withdrawals.resolved',)),
                Stage('native:army.claims',self._claims,hook='military.claims',needs=('withdrawals.resolved',),provides=('claims.resolved',)),
                Stage('native:army.repatriate',lambda w,c:c.update(repatriations=w.military.repatriate()),hook='military.repatriate',needs=('actors.eliminated',),provides=('military.repatriated',)))
    @staticmethod
    def _supply(world, context):
        world.military.record_grid(context['building_production'].energy)
        report,facts=world.military.supply(context.get('diplomacy_effects',()))
        context['military_supply']=report;context.setdefault('economic_records',[]).extend(facts)
    @staticmethod
    def _claims(world, context):
        context['claims']=world.military.claims();context.setdefault('domain_events',[]).extend(world.military.drain_events())
    def rule_sections(self, world=None):
        return (RuleSection('军队','调动只改变位置。移动、异地进攻、撤退共用行动额度，新兵当期不能行动；原地进攻保留额度。外交先于补给与战斗，撤离提交后仍参加本期战斗。军队从资源域扣除征召、补给与回血材料，并发布经济事实。坐标接口使用 0-based。'),)
