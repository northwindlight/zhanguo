from dataclasses import replace
from engine.player import wire
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
    def numbers(self):return {'units':wire(unit_definitions(self.runtime.content)),'army.starvation':ARMY_STARVE_DAMAGE,'army.heal':ARMY_HEAL_PER_TURN,'army.retreat.attack-penalty':RETREAT_ATK_PENALTY,'army.retreat.defender-cover':RETREAT_DEF_COVER}
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
        parameters['oneOf']=[{'required':['site'],'not':{'anyOf':[{'required':['x']},{'required':['y']}]}},{'required':['x','y'],'not':{'required':['site']}}]
        def forms(tool):
            # 坐标类工具：place（地名或坐标串）与 x/y 二选一。
            params=tool.schema()['function']['parameters']
            params['oneOf']=[{'required':['place'],'not':{'anyOf':[{'required':['x']},{'required':['y']}]}},
                             {'required':['x','y'],'not':{'required':['place']}}]
            return replace(tool,parameters=params)
        return (Tool('recruit',self._recruit,parameters=parameters,content_parameters=('kind',)),forms(Tool('move',self._move,description='移动本国单支军队 aid 至目标格：place 给地名或 "x y" 坐标串，或两个整数 x、y。纯移动不占地。新兵、已移动或交战部队不能普通移动；与异地进攻和撤退共用额度。敌国领土须 attack，路线按地形与兵种速度校验。')),forms(Tool('attack',self._attack,description='本国军队整数 ids 数组向目标格进攻（place 给地名或 "x y"，或两个整数 x、y）；均须能抵达且有额度。无敌军可直接占领，有守军进入交战并等待结算；不能把提交当战果。他国须已生效战争。新兵不能进攻，原地进攻可保留移动额度。')),
                forms(Tool('retreat',self._retreat,description='交战部队 aid 退往相邻合法格（place 给地名或 "x y"，或两个整数 x、y），与移动共用额度。提交后仍在原地参加本期战斗，结算后才撤离；撤退降低输出，防守撤退有承伤减免。')),Tool('disband',self._disband,parameters={'type':'object','properties':{'aid':{'type':'integer','minimum':1},'ids':{'type':'array','items':{'type':'integer','minimum':1},'minItems':1,'uniqueItems':True}},'oneOf':[{'required':['aid'],'not':{'required':['ids']}},{'required':['ids'],'not':{'required':['aid']}}],'additionalProperties':False},description='免费遣散本国军队，aid 单支或 ids 数组批量（二选一）；交战中不可遣散。立即停止维持并释放编制，不返还粮食、装备或金币。'),
                Tool('army',lambda w,a:w.military.visible_units(a),read_only=True,description='一次列出本国及视野内军队：国家、整数编号、兵种、位置、数量和行动/交战/撤退状态；无需坐标。军队编号各国独立。'),Tool('reachable',self._reachable,read_only=True,description='按本国军队 aid 列出可见合法移动目的地和路径代价；attack=true 列攻击可达地。只接军队编号，无需目标坐标，不透露迷雾路径。'),
                Tool('frontier',self._frontier,read_only=True,description='全国可见无主地总数、本回合可行动军队可及目的地与可拓荒地；无需坐标。'),
                Tool('logistics',lambda w,a:w.military.supply_preview(a),read_only=True,description='按本国当前军队、军屯与库存只读预演补给：需求、可用、缺口、免供支数和断粮损伤；不扣资源，不含后续生产与行动，无需参数。'),
                Tool('unit_catalog',self._catalog,read_only=True,description='全部兵种目录：征召材料、数量上限、攻击、速度、地形移动代价与补给；应用本国政策，无需坐标。'),
                Tool('threats',self._threats,read_only=True,description='全视野内其他国家的军队和当前关系，排除本国与野人；无需坐标。'),
                Tool('battle',self._battle,read_only=True,description='当前全部可见或本国参与交战：各方兵种/逐支数量、输出基数、地形/城堡减伤与撤退。盲战仅列本国，不泄露敌军；不是按目标预测战果。'),
                forms(Tool('tile',self._tile,read_only=True,description='唯一按单格查询的工具：place 给地名或 "x y"，或两个整数 x、y；返回可见单格详情（有主地块显示地名，无主只显示坐标）。本国及物化无主遗址显示资源、容量、施工与建筑；他国仅显示地形、归属、公开城堡和市政厅及可见军队。迷雾不可查。')))
    def schema_for_actor(self, world, actor, schema):
        result=deepcopy(schema)
        if result['function']['name']=='recruit':
            result['function']['parameters']['properties']['kind']['enum']=list(world.units.type_ids)
            result['function']['description']='在本国地块征召（site 传地名或 "x y" 坐标串，或传两个整数 x、y），直接返回实际扣款和军队编号。新兵本回合不能行动；兵营与能源门槛查规则，各兵种的征召材料、上限和速度查 unit_catalog。'
        return result
    @staticmethod
    def _recruit(world, actor, site: str | None=None, kind: str='native:infantry', count: int=1, x: int | None=None, y: int | None=None):
        site=world.runtime.object_call(world,'native:buildings','resolve_site',actor,site,x,y)
        return world.military.recruit(actor,site,kind,count,energy_short=world.military.grid_short(actor))
    @staticmethod
    def _where(world, place, x, y):
        # 地块引用统一入口：地名或 "x y" 坐标串，或者直接给两个整数 x、y。
        # 两条路等价（玩家面向坐标从 0 开始）；同时给两种就报错，免得猜错意图。
        if place is None:
            if x is None or y is None:raise ValueError('给 place（地名或 "x y"），或者两个整数 x、y')
            return int(x),int(y)
        if x is not None or y is not None:raise ValueError('place 与 x、y 只能选一种')
        return world.runtime.object_call(world,'native:territory','resolve',place)

    @staticmethod
    def _move(world, actor, aid: int, x: int | None=None, y: int | None=None, place: str | None=None):
        x,y=ArmyMod._where(world,place,x,y)
        return world.military.move(actor,aid,x,y)
    @staticmethod
    def _attack(world, actor, ids: list[int], x: int | None=None, y: int | None=None, place: str | None=None):
        x,y=ArmyMod._where(world,place,x,y)
        return world.military.attack(actor,ids,x,y)
    @staticmethod
    def _retreat(world, actor, aid: int, x: int | None=None, y: int | None=None, place: str | None=None):
        x,y=ArmyMod._where(world,place,x,y)
        return world.military.retreat(actor,aid,x,y)
    @staticmethod
    def _disband(world, actor, aid: int | None=None, ids: list[int] | None=None):
        if (aid is None)==(ids is None):raise ValueError('aid 与 ids 二选一')
        if ids is None:return world.military.disband(actor,aid)
        if not ids or len(ids)!=len(set(ids)):raise ValueError('ids 须为非空、不重复数组')
        own={u.id:u for u in world.military.snapshot(actor)}
        if any(i not in own or not world.military.can_disband(actor,i) or not own[i].alive for i in ids):raise ValueError('批量遣散包含不存在或交战中的军队')
        return tuple(world.military.disband(actor,i) for i in ids)
    @staticmethod
    def _reachable(world, actor, aid: int, attack: bool=False):return world.military.reachable(actor,aid,attack,visible_only=True)
    @staticmethod
    def _frontier(world,actor):
        tiles=world.runtime.object_call(world,'native:vision','tiles',actor)
        visible={t.position for t in tiles};unowned={t.position for t in tiles if t.owner is None}
        period=world.runtime.object_call(world,'native:government','period');paths={};reachable=set()
        for u in world.military.snapshot(actor):
            if not u.can_act(period):continue
            key=(u.x,u.y,u.kind)
            if key not in paths:paths[key]=world.military.reachable(actor,u.id,True,visible_only=True)
            reachable.update(p for p,cost in paths[key] if p!=(u.x,u.y))
        return {'visible_unowned':len(unowned),'reachable':sorted(reachable&visible),'frontier':sorted(unowned&reachable)}

    @staticmethod
    def _catalog(world, actor):
        overrides=world.runtime.parameter(world,actor,'recruit',{})
        return {kind:{**dict(world.units.definition(kind)),
                      'recruit':dict(overrides.get(kind,world.units.definition(kind)['recruit'])),
                      'speed':world.runtime.parameter(world,actor,'unit.speed.'+kind,world.units.definition(kind)['speed']),
                      'supply':world.runtime.parameter(world,actor,'unit.supply.'+kind,world.units.definition(kind)['supply']),
                      'retreat_attack_penalty':RETREAT_ATK_PENALTY}
                for kind in world.units.type_ids}
    @staticmethod
    def _threats(world,actor):
        call=lambda cap,method,*args:world.runtime.object_call(world,cap,method,*args)
        return [{'unit':u,'relation':'war' if call('native:diplomacy','war_between',actor,u.owner) else 'ally' if call('native:diplomacy','allied',actor,u.owner) else 'neutral'}
                for u in world.military.visible_units(actor) if u.owner not in (actor,'野人') and u.alive]

    @staticmethod
    def _battle(world,actor):
        call=lambda cap,method,*args:world.runtime.object_call(world,cap,method,*args)
        units=world.military.visible_units(actor)
        positions=sorted({(u.x,u.y) for u in units if u.engaged and u.alive})
        result=[]
        for x,y in positions:
            visible=call('native:vision','visible',actor,x,y)
            troops=[u for u in units if (u.x,u.y)==(x,y) and u.alive and (visible or u.owner==actor)]
            fact=call('native:territory','cell',x,y) if visible else None
            terrain=call('native:territory','terrain',x,y) if visible else None
            castle=(call('native:buildings','effects',fact.tile.id,'native:castle').get('defense_per_level',0)*call('native:buildings','count',fact.tile.id,'native:castle')) if fact and fact.tile.id else 0
            active={u.owner for u in troops if u.engaged and u.owner!='野人'};sides=[]
            for owner in sorted({u.owner for u in troops}):
                group=[u for u in troops if u.owner==owner]
                defending=bool(fact and (owner==fact.owner or owner=='野人' or owner not in active))
                reduction=world.military.defense_pct(terrain.defense,castle if owner==fact.owner else 0) if defending else 0 if visible else None
                foes=[other for other in {u.owner for u in troops} if other!=owner and ((owner=='野人' and other in active) or (other=='野人' and owner in active) or owner!='野人' and other!='野人' and call('native:diplomacy','war_between',owner,other))] if visible else None
                sides.append({'owner':owner,'role':'bystander' if visible and not foes else 'defending' if defending else 'attacking' if visible else 'unknown',
                              'units':group,'count':len(group),'output_base':world.military.faction_power(group) if foes or not visible else 0,'targets':foes,
                              'terrain_reduction':terrain.defense if defending else 0 if visible else None,
                              'castle_reduction':castle if fact and owner==fact.owner else 0 if visible else None,'combined_reduction':reduction})
            result.append({'position':(x,y),'blind':not visible,'terrain':terrain.id if terrain else None,'sides':sides})
        return result

    @staticmethod
    def _tile(world, actor, x: int | None=None, y: int | None=None, place: str | None=None):
        call=lambda cap,method,*args:world.runtime.object_call(world,cap,method,*args)
        x,y=ArmyMod._where(world,place,x,y)
        call('native:territory','check',x,y)
        if not call('native:vision','visible',actor,x,y):raise ValueError('该格在迷雾中，无法查询')
        fact=call('native:territory','cell',x,y)
        snap=call('native:buildings','snapshot')
        full=fact.owner==actor or fact.owner is None and any(b.site==fact.tile.id for b in snap.instances)
        buildings=[b for b in snap.instances if b.site==fact.tile.id and (full or b.remaining==0 and b.kind in ('native:castle','native:town-hall'))]
        result={'position':(x,y),'name':fact.tile.name if fact.owner is not None else '', 'terrain':fact.tile.terrain,'owner':fact.owner,'buildings':[{'kind':b.kind,'level':b.level,'remaining':b.remaining} for b in buildings],
                'army':[u for u in world.military.visible_units(actor) if (u.x,u.y)==(x,y)]}
        if full:
            site=call('native:buildings','site',fact.tile.id)
            result.update(resources=dict(fact.resources),capacity=site.capacity,
                          construction=[r for r in snap.construction if r.site==site.id])
        return result

    def panels(self):return (Panel('army',lambda w,a:w.military.visible_units(a)),)
    def stages(self):
        return (Stage('native:army.supply',self._supply,hook='military.supply',needs=('relations.applied','buildings.produced'),provides=('military.supplied',)),
                Stage('native:army.battle',lambda w,c:c.update(battles=w.military.battle()),hook='military.battle',needs=('military.supplied',),provides=('battles.resolved',)),
                Stage('native:army.withdraw',lambda w,c:c.update(withdrawals=w.military.withdrawals()),hook='military.withdraw',needs=('battles.resolved',),provides=('withdrawals.resolved',)),
                Stage('native:army.claims',self._claims,hook='military.claims',needs=('withdrawals.resolved',),provides=('claims.resolved',)),
                Stage('native:army.repatriate',self._repatriate,hook='military.repatriate',needs=('actors.eliminated',),provides=('military.repatriated',)))
    @staticmethod
    def _supply(world, context):
        world.military.record_grid(context['building_production'].energy)
        report,facts=world.military.supply(context.get('diplomacy_effects',()))
        context['military_supply']=report;context.setdefault('economic_records',[]).extend(facts)
    @staticmethod
    def _claims(world, context):
        context['claims']=world.military.claims();context.setdefault('domain_events',[]).extend(world.military.drain_events())
    @staticmethod
    def _repatriate(world, context):
        context['repatriations']=world.military.repatriate()
        # 脱离清扫收在军事序列的末尾：claims 要读 engaged，而 0.2 的 repatriate 没有
        # 0.1 那条「与交战国之间的滞留是合法的」豁免，清早一步会把合法驻扎在敌国地上的
        # 军队一并送回国。放这里只差一个回合，不会误伤。
        context['disengaged']=world.military.clear_disengaged()
        assert not world.military.engaged_without_foes(), 'Engaged units without foes after settlement'
        context.setdefault('domain_events',[]).extend(world.military.drain_events())
    def rule_sections(self, world=None):
        return (RuleSection('军队','调动只改变位置。移动、异地进攻、撤退共用行动额度，新兵当期不能行动；原地进攻保留额度。外交先于补给与战斗，撤离提交后仍参加本期战斗。军队从资源域扣除征召、补给与回血材料，并发布经济事实。logistics 可只读预演当前补给和断粮损伤，不扣资源。所有坐标统一 0-based。',keywords=('army','battle','move','attack','retreat','disband','军队','战斗','撤退','遣散','裁军','移动','攻击','速度','征兵')),)
