"""军事行为：路径、征召、战斗、补给、撤离；Unit/Units 继续拥有部队。"""
from copy import deepcopy
from dataclasses import dataclass
import heapq
import random
from math import ceil

from engine.building import integer
from engine.identifiers import content_id
from engine.runtime import DomainRejection, ModError
from engine.state import active_unit
from .objects import Unit
from .parameters import ARMY_GID_BASE, ARMY_STARVE_DAMAGE, ARMY_HEAL_PER_TURN, HEAL_EQUIP_COST, COMBAT_DIE_MOD, RETREAT_ATK_PENALTY, RETREAT_DEF_COVER


@dataclass(frozen=True)
class MilitaryReceipt:
    status: str
    units: tuple
    position_changed: bool
    consumed_action: bool
    occupied: bool = False
    engaged: bool = False
    message: str = ''
    items: tuple = ()


class Military:
    def __init__(self, units, call, parameter):
        self._units,self._call,self._parameter=units,call,parameter
        self._recruited={};self._facts=[];self._rng=None;self._seed=None;self._grid_short={};self._events=[]
        self._world,self._domain=None,None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Military already bound')
        self._world,self._domain=world,world.runtime.state_owners[key];return self
    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use army interface')
    def _name(self,owner):return owner if owner=='野人' else self._call('native:nation-inspection','get',owner).name
    def _event(self,kind,text,position=None,parties=()):
        self._events.append({'kind':kind,'text':text,'position':position,'parties':tuple(parties),'snapshot_only':True})
    def _period(self):return self._call('native:government','period')
    def _living(self, owner):
        if owner not in self._call('native:nation-inspection','ids'):raise ValueError('Nation is not alive')
    def _sync(self, initialize_rng=False):
        size,seed=self._call('native:territory','dimensions')
        for unit in self._units:unit.validate(size)
        self._units.size=size
        if initialize_rng and self._rng is None:self._rng=random.Random(seed);self._seed=seed
        elif self._rng is not None and self._seed!=seed:raise ValueError('Cannot replace map seed after military initialization')
    def _action(self, owner):
        self._write();self._living(owner)
        if self._world is not None and self._world.phase!='行动':raise ValueError('Act only during action phase')
        self._sync()
    def _stock(self, owner, key):return self._call(key,'balance',owner)
    def _effect(self, site, kind):return self._call('native:buildings','effects',site,kind)
    def grid_short(self, owner):return self._grid_short.get(owner,False)
    def record_grid(self, reports):
        self._write();self._grid_short={row.owner:row.short for row in reports}
    def _own_unit(self, owner, aid):
        integer(aid,1);unit=self._units.by_id(aid,owner)
        if unit is None or not unit.alive:raise ValueError(f'没有这支军队：#{aid} 不存在或已阵亡。编号用 army 查询里本国军队的 id（不是固定兵种号）')
        return unit

    @staticmethod
    def _reject_batch(items):
        """整批预验失败不执行任何一支，但保留逐支原因，不能把通过者写成已行动。"""
        if not any(row.get('reason') for row in items):return
        rows=[{**row,'status':'rejected' if row.get('reason') else 'not_executed',
               'message':row.get('reason') or '校验通过，但本批未执行',
               'position_changed':False,'consumed_action':False} for row in items]
        message='整批未执行：\n'+'\n'.join(f"#{row['aid']}：{row['message']}" for row in rows)
        raise DomainRejection(message,{'reason':'batch_rejected','atomic':True,'items':rows})

    def _selection(self, owner, ids):
        if not isinstance(ids,(list,tuple)) or not ids or any(type(i) is not int or i<1 for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('ids 须为非空且不重复的正整数军队编号数组；单支军队用 aid')
        units=[];items=[]
        for aid in ids:
            try:units.append(self._own_unit(owner,aid));items.append({'aid':aid})
            except ValueError as exc:units.append(None);items.append({'aid':aid,'reason':str(exc)})
        return units,items

    def action_reason(self, owner, unit, attack=False, target=None):
        period=self._period()
        if unit.born_turn==period:return '新兵本回合不能行动'
        # 原地进攻允许已用过移动额度的部队；沿用 Unit.can_attack 的规则。
        if attack and target==(unit.x,unit.y):
            return ''
        if unit.moved_turn==period:return '本回合已移动或已消耗行动额度'
        if unit.engaged:return '正在交战，须先撤退'
        if not attack and any(v.alive and v.engaged and v.owner!='野人' and self._hostile(owner,v.owner) for v in self._units.at(unit.x,unit.y)):
            return '本格正受进攻，须用 retreat 撤出'
        return ''
    def snapshot(self, owner=None):
        return tuple(deepcopy(u) for u in self._units if owner is None or u.owner==owner)
    def visible_units(self, owner):
        self._living(owner)
        tiles=self._call('native:vision','tiles',owner);visible={t.position for t in tiles}
        result=[deepcopy(u) for u in self._units if u.owner==owner or (u.x,u.y) in visible]
        size,_=self._call('native:territory','dimensions')
        for cell in tiles:
            x,y=cell.position
            if (x,y) in self._units.guard_once or self._call('native:territory','cell',x,y).tile.id:continue
            aid=x*size+y+1
            result.append(Unit('native:infantry',self._units.definition('native:infantry')['hp'],x,y,'野人',aid,aid,f'野人{aid}',_definitions=self._units.definitions))
        return tuple(result)
    def seed(self, owner, kind, x, y, count=1):
        self._write();self._living(owner);integer(count,1);self._sync()
        self._call('native:territory','check',x,y)
        if kind not in self._units.type_ids:raise ValueError(f'未知兵种：{kind}。可用兵种与短键见 unit_catalog')
        if self._units.next_army_seq.get(owner,0)+count>=ARMY_GID_BASE:raise ValueError('本国军队编号已用尽，无法再新增军队')
        return tuple(deepcopy(self._units.spawn(owner,kind,x,y)) for _ in range(count))
    def recruit_quote(self, owner, site: str, kind: str, count=1, *, energy_short=False):
        self._living(owner);integer(count,1)
        place=self._call('native:buildings','site',site)
        if place.owner!=owner:raise ValueError('只能在本国地块征召：该地块不属于你。用 land 查本国国土与坐标')
        if kind not in self._units.type_ids:raise ValueError(f'未知兵种：{kind}。可用兵种与短键见 unit_catalog')
        period=self._period();key=(period,site,kind=='native:militia')
        building=self._call('native:buildings','kind_with_role','militia' if kind=='native:militia' else 'recruiter')
        effect=self._effect(site,building)
        if kind=='native:militia':
            # 说明书第十八条：民兵只能在自有已落成军屯征召；第十九条再核全国编制。
            if not effect.get('militia_cap'):raise ValueError(
                '该地没有军屯：民兵只能在自有已落成的军屯征召（每座军屯可驻 2 支，驻本格免补给）；'
                '用 query(panel=quote, action=recruit) 查可征站点')
            per=effect.get('militia_per_turn',0)
            cap=sum(self._effect(s.id,building).get('militia_cap',0) for s in self._call('native:buildings','snapshot').sites if s.owner==owner)
            have=sum(u.kind==kind and u.alive for u in self._units.of(owner))
            if have+count>cap:raise ValueError(f'全国民兵编制已满：军屯每座可驻 2 支，上限 {cap} 支，现有 {have} 支，本次还要再加 {count} 支。先增建军屯，或裁撤旧民兵')
        else:
            if not effect.get('recruit_cap'):raise ValueError(
                '该地没有兵营：步兵与骑兵只能在自有已落成的兵营征召，每兵营每回合 1 支；'
                '用 query(panel=quote, action=recruit) 查可征站点')
            per=effect.get('recruit_cap',0)
            if energy_short:raise ValueError('上期电网不足，征兵停摆：电网不足时补给厂/装备厂/兵营/市政厅全部停摆。先补燃料与电厂，结算后再征')
        used=self._recruited.get(key,0)
        if used+count>per:raise ValueError(f'该地本回合征兵额度已用尽：每地每回合 {per} 支，本回合已用 {used} 支，本次要 {count} 支。下回合再征，或换另一座兵营')
        x,y=place.position;self._call('native:territory','check',x,y)
        if self._units.next_army_seq.get(owner,0)+count>=ARMY_GID_BASE:raise ValueError('本国军队编号已用尽，无法再新增军队')
        overrides=self._parameter(owner,'recruit',{})
        recipe=overrides.get(kind,self._units.definition(kind)['recruit'])
        costs={key:integer(value,1)*count for key,value in recipe.items()}
        return {'site':site,'kind':kind,'count':count,'costs':costs,'position':(x,y),
                'quota':per-self._recruited.get(key,0),'period':period}

    def recruit(self, owner, site: str, kind: str, count=1, *, energy_short=False):
        self._action(owner)
        integer(count,1)
        first=self.recruit_quote(owner,site,kind,1,energy_short=energy_short)
        affordable=min((max(0,self._stock(owner,key))//value for key,value in first['costs'].items()),default=count)
        actual=min(count,first['quota'],int(affordable))
        if kind=='native:militia':
            cap=sum(self._effect(t.tile.id,'native:militia-camp').get('militia_cap',0) for t in self._call('native:territory','owned',owner))
            actual=min(actual,cap-sum(u.kind==kind and u.alive for u in self._units.of(owner)))
        if actual<1:raise ValueError('征召材料或本回合额度不足；请补充粮食/装备或等待下回合')
        count=actual
        quote=self.recruit_quote(owner,site,kind,count,energy_short=energy_short)
        costs=quote['costs'];period=quote['period'];x,y=quote['position'];key=(period,site,kind=='native:militia')
        short={key:value-self._stock(owner,key) for key,value in costs.items() if self._stock(owner,key)<value}
        if short:raise ValueError('征兵材料不足：'+'、'.join(f'{key.split(":",1)[-1]} 缺 {value}' for key,value in short.items())+'。先补料、减少数量，或等下回合产出')
        self._call('native:resource-exchange','exchange',owner,costs,{})
        created=tuple(self._units.spawn(owner,kind,x,y,turn=period,newborn=True) for _ in range(count))
        self._recruited[key]=self._recruited.get(key,0)+count
        self._facts.append({'id':f'native:recruit/{owner.split(":",1)[1]}/{created[0].gid}', 'owner':owner,'period':period,'category':'recruit','inputs':tuple(costs.items())})
        return tuple(deepcopy(u) for u in created)

    def _hostile(self, a, b):
        return a!=b and (a=='野人' or b=='野人' or self._call('native:diplomacy','war_between',a,b))
    def _pass(self, owner, x, y):
        tile_owner=self._call('native:territory','owner',x,y)
        if tile_owner==owner or tile_owner is not None and self._call('native:diplomacy','allied',owner,tile_owner):return True
        if tile_owner is not None and tile_owner!=owner and not self._call('native:diplomacy','allied',owner,tile_owner):return False
        return not any(u.owner!='野人' and u.alive and self._hostile(owner,u.owner) for u in self._units.at(x,y))
    def reachable(self, owner, aid, attack=False, *, visible_only=False):
        unit=self._own_unit(owner,aid);size,_=self._call('native:territory','dimensions')
        speed=self._parameter(owner,'unit.speed.'+unit.kind,unit.definition['speed'])
        start=(unit.x,unit.y);best={start:0};queue=[(0,start)]
        visible={t.position for t in self._call('native:vision','tiles',owner)} if visible_only else None
        terrain_cost={}
        def cost(x,y):
            if (x,y) not in terrain_cost:
                terrain=self._call('native:territory','terrain',x,y)
                terrain_cost[x,y]=unit.definition['move_cost'][terrain.id]
            return terrain_cost[x,y]
        while queue:
            distance,point=heapq.heappop(queue)
            if distance!=best[point]:continue
            for target in self._call('native:territory','neighbors',*point):
                if visible is not None and target not in visible:continue
                next_distance=distance+max(cost(*point),cost(*target))
                if next_distance>speed or next_distance>=best.get(target,speed+1):continue
                movable=self._pass(owner,*target)
                territory_owner=self._call('native:territory','owner',*target)
                target_ok=territory_owner is None or territory_owner==owner or self._call('native:diplomacy','allied',owner,territory_owner) or self._call('native:diplomacy','war_between',owner,territory_owner)
                if not movable and not (attack and target_ok):continue
                best[target]=next_distance
                if movable:heapq.heappush(queue,(next_distance,target))
        return tuple(sorted(best.items()))

    def reachable_many(self, owner, ids, attack=False):
        units,items=self._selection(owner,ids);self._reject_batch(items)
        rows=[]
        for u in units:
            reason=self.action_reason(owner,u,attack)
            if attack and reason and u.can_attack(self._period(),u.x,u.y):reason+='；异地进攻不可用，原地进攻仍可提交'
            rows.append({'aid':u.id,'action_available':not reason,'action_reason':reason,
                         'can_attack_current_tile':u.can_attack(self._period(),u.x,u.y),
                         'destinations':self.reachable(owner,u.id,attack,visible_only=True)})
        return tuple(rows)
    def _batch(self, owner, ids, x, y, attack):
        self._action(owner);self._call('native:territory','check',x,y)
        units,items=self._selection(owner,ids);period=self._period()
        for u,row in zip(units,items):
            if u is not None:row['reason']=self.action_reason(owner,u,attack,(x,y))
        paths={}
        for u in units:
            if u is None:continue
            key=(u.x,u.y,u.kind,self._parameter(owner,'unit.speed.'+u.kind,u.definition['speed']))
            if key not in paths:paths[key]=dict(self.reachable(owner,u.id,attack))
        # 被挡的原因各不相同，回执要说出是哪一种，否则模型只能靠猜（打了自家地却被告知「须先宣战」）。
        reason=''
        target_blocked=not self._pass(owner,x,y) if not attack else False
        if target_blocked:reason='移动目标不合法：只能是野地、自家或盟国格；敌国领土要用 attack'
        tile_owner=self._call('native:territory','owner',x,y)
        if attack and tile_owner is not None and (tile_owner==owner or self._call('native:diplomacy','allied',owner,tile_owner)):
            target_blocked=True;reason='那是你自己（或盟国）的地块：不能进攻自家领土，占地请选无主地或敌国领土'
        if attack and tile_owner not in (None,owner) and not self._call('native:diplomacy','allied',owner,tile_owner) and not self._call('native:diplomacy','war_between',owner,tile_owner):
            target_blocked=True;reason=f'你和{tile_owner.split(":",1)[-1]}之间没有生效的战争：先 declare_war，或改打无主地/已在交战的目标'
        busy=[u for u in self._units.at(x,y) if u.alive and u.engaged and u.owner not in ('野人',owner)]
        if attack and tile_owner is None and busy and not any(self._hostile(owner,u.owner) or self._call('native:diplomacy','allied',owner,u.owner) for u in busy):
            target_blocked=True;reason='该无主地上有与你非敌非盟的一方正在打野：不能抢别人的战斗（可先旁观），换一格或等它打完'
        # 先排除不能行动的部队，再处理盲推。盲推不得向玩家泄露真实目标阻挡原因。
        if target_blocked and not self._call('native:vision','visible',owner,x,y):
            self._reject_batch(items)
            for unit in units:unit.update(moved_turn=period)
            message='未探明路线或目标位置受阻（盲推）：未移动到目标，行动额度已消耗'
            details=tuple({'aid':u.id,'status':'failed','message':message,'position_changed':False,'consumed_action':True} for u in units)
            return MilitaryReceipt('failed',tuple(ids),False,True,message=message,items=details)
        for u,row in zip(units,items):
            if u is None or row.get('reason'):continue
            if target_blocked:row['reason']=reason
            elif (x,y) not in paths[(u.x,u.y,u.kind,self._parameter(owner,'unit.speed.'+u.kind,u.definition['speed']))]:
                row['reason']='超出移动范围或沿途有阻挡；先查 reachable'
        self._reject_batch(items)
        return units
    def move(self, owner, aid, x, y):
        return self.move_many(owner,[aid],x,y)
    def move_many(self, owner, ids, x, y):
        result=self._batch(owner,ids,x,y,False)
        if isinstance(result,MilitaryReceipt):return result
        changed=any((unit.x,unit.y)!=(x,y) for unit in result)
        items=tuple({'aid':u.id,'status':'effective','position_changed':(u.x,u.y)!=(x,y),'consumed_action':True,
                     'message':'已移动到目标' if (u.x,u.y)!=(x,y) else '原地调动；已消耗行动额度'} for u in result)
        for unit in result:unit.move_to(x,y,self._period())
        return MilitaryReceipt('effective',tuple(ids),changed,True,items=items)
    def _guard(self, x, y):
        fact=self._call('native:territory','cell',x,y)
        if fact.tile.id or (x,y) in self._units.guard_once:return
        aid=x*self._units.size+y+1
        unit=Unit('native:infantry',self._units.definition('native:infantry')['hp'],x,y,'野人',aid,aid,f'野人{aid}',_definitions=self._units.definitions)
        self._units.assign_code('野人');self._units.append(unit);self._units.guard_once.add((x,y))
    def _claim(self, owner, x, y):
        fact=self._call('native:territory','cell',x,y)
        halls=self._call('native:buildings','count_role',fact.tile.id,'capital') if fact.tile.id else 0
        seen=tuple(n for n in self._call('native:nation-inspection','ids') if self._call('native:vision','visible',n,x,y) or n in (owner,fact.owner))
        by=owner
        if fact.tile.core and fact.tile.core!=owner and fact.tile.core in self._call('native:nation-inspection','ids') and self._call('native:diplomacy','allied',owner,fact.tile.core) and self._call('native:diplomacy','at_war',owner):by=fact.tile.core
        if fact.owner==by:return
        if fact.tile.id:
            if fact.owner is None:self._call('native:territory','claim',fact.tile.id,by)
            else:self._call('native:territory','transfer',fact.tile.id,by)
        else:self._call('native:territory','create_claim',f'native:tile/{x}-{y}',by,x,y)
        claimed=self._call('native:territory','cell',x,y)
        self._events.append({'kind':'occupation','text':f'{self._name(by)} 占领 ({x},{y})，原属 {self._name(fact.owner) if fact.owner else "无主失地" if fact.tile.core is not None else "野地"}；守军已清除，归属已变更。',
            'position':(x,y),'parties':seen,'snapshot_only':True,
            'details':{'importance':'major','previous_owner':fact.owner,'previous_core':fact.tile.core,'owner':by,'core':claimed.tile.core,
                       'name':claimed.tile.name,'town_halls':halls}})
    def attack(self, owner, ids, x, y):
        result=self._batch(owner,ids,x,y,True)
        if isinstance(result,MilitaryReceipt):return result
        units=result;self._guard(x,y)
        changed=any((u.x,u.y)!=(x,y) for u in units);sequence=self._units.next_engagement()
        items=tuple({'aid':u.id,'status':'effective','position_changed':(u.x,u.y)!=(x,y),'consumed_action':(u.x,u.y)!=(x,y),
                     'message':'异地进攻；已消耗行动额度' if (u.x,u.y)!=(x,y) else '原地进攻；未额外消耗移动额度'} for u in units)
        for unit in units:
            values={'x':x,'y':y,'engaged':True,'engage_seq':sequence}
            if (unit.x,unit.y)!=(x,y):values['moved_turn']=self._period()
            unit.update(values)
        defenders=any(u.alive and self._hostile(owner,u.owner) for u in self._units.at(x,y))
        if not defenders:
            self._claim(owner,x,y)
            for unit in units:unit.disengage()
        return MilitaryReceipt('effective',tuple(ids),changed,changed,not defenders,defenders,
                               '已进入交战，占地等结算——别把提交当战果' if defenders else '已占领该地',items)
    def retreat(self, owner, aid, x, y):
        return self.retreat_many(owner,[aid],x,y)
    def retreat_many(self, owner, ids, x, y):
        self._action(owner);self._call('native:territory','check',x,y);period=self._period()
        units,items=self._selection(owner,ids);plans=[]
        for unit,row in zip(units,items):
            if unit is None:continue
            attacked=any(v.alive and v.engaged and v.owner!='野人' and self._hostile(owner,v.owner) for v in self._units.at(unit.x,unit.y))
            if unit.born_turn==period:row['reason']='新兵本回合不能撤退'
            elif unit.moved_turn==period:row['reason']='本回合已移动或已提交撤退'
            elif not (unit.engaged or attacked):row['reason']='未交战、未受进攻，无需撤退；请用 move 调动'
            elif (x,y) not in self._call('native:territory','neighbors',unit.x,unit.y) or not self._pass(owner,x,y):row['reason']='撤退目标非法：只能退到相邻的野地、自家或盟国格'
            defender=not any(v.alive and v.engaged and v.owner==owner for v in self._units.at(unit.x,unit.y)) or self._call('native:territory','owner',unit.x,unit.y)==owner
            plans.append((unit,defender))
        self._reject_batch(items)
        for unit,defender in plans:
            unit.update(retreat_to=(x,y),retreat_role='守' if defender else '攻',retreat_cover=RETREAT_DEF_COVER if defender else 100,moved_turn=period)
        details=tuple({'aid':u.id,'status':'submitted','position_changed':False,'consumed_action':True,
                       'retreat_role':'守' if defender else '攻','retreat_cover':RETREAT_DEF_COVER if defender else 100,
                       'message':('守方' if defender else '攻方')+'撤退已提交；仍在原格参战，结算后才退出'} for u,defender in plans)
        return MilitaryReceipt('submitted',tuple(ids),False,True,engaged=True,message='撤退已提交，本回合仍参加战斗，结算后才退出',items=details)

    def disband_many(self, owner, ids):
        self._action(owner);units,items=self._selection(owner,ids)
        for u,row in zip(units,items):
            if u is not None and not self.can_disband(owner,u.id):row['reason']='正在交战或受到进攻，须先撤退并等待结算'
        self._reject_batch(items)
        return tuple(self.disband(owner,u.id) for u in units)
    def can_disband(self, owner, aid):
        unit=self._own_unit(owner,aid)
        return not unit.engaged and not any(v.alive and v.engaged and v.owner!='野人' and self._hostile(owner,v.owner) for v in self._units.at(unit.x,unit.y))

    def disband(self, owner, aid):
        self._action(owner);unit=self._own_unit(owner,aid)
        if not self.can_disband(owner,aid):raise ValueError('正在交战或受到进攻的军队不能遣散；请提交撤退并等待结算')
        self._units.remove(unit);return MilitaryReceipt('effective',(aid,),False,False,message='已遣散，不退还任何粮/装备/黄金')

    def disengage(self, owners):
        """亡国当场的强制停战（用户 2026-10-07：「正在发生的战争必须强制停下」）。

        ★ 只清**站在国有地上**的军队：打野人的仗不是「战争」（野人只守无主格），
          全天下休战不该顺手把清野地一并掐掉。0.1 的 `_eliminate_if_dead` 就是这条
          过滤器，0.2 改写时丢了。它必须先于「余土变无主」跑——那一步一执行，
          站在上面的军队就没有归属可查，洞会从另一个方向复发。
        """
        self._write()
        for unit in self._units:
            if unit.owner not in owners:continue
            if self._call('native:territory','owner',unit.x,unit.y) is None:continue
            unit.disengage()
            for key in ('retreat_to','retreat_role','retreat_cover'):
                if key in unit:unit.pop(key)

    def clear_disengaged(self):
        """脱离清扫：格上已无活敌军的 engaged 军队就地解除交战（0.1 结算第 4.9 步）。

        补的是两个死角——敌军**撤退落地离开原格**（留守者否则要白挂一整回合：下回合
        move 被拒、当回合不回血、次回合还多一条 0 伤害假战报）、以及任何路径留下的
        空交战标记。必须在 claims 之后跑：占领判定要读 engaged。
        """
        self._write();cleared=[]
        for unit in self._units:
            if not unit.engaged or self._engaged_foes(unit):continue
            unit.disengage();cleared.append((unit.owner,unit.id))
        return tuple(cleared)

    def _engaged_foes(self,unit):
        """与 0.1 `_clear_disengaged` 同口径：野人只与「交战中的进攻方」为敌，
        和平停驻在无主格上的军队不算它的敌人。"""
        near=[u for u in self._units.at(unit.x,unit.y) if u is not unit and u.alive]
        if unit.owner=='野人':return [u for u in near if u.owner!='野人' and u.engaged]
        return [u for u in near if u.owner=='野人' or (u.owner!=unit.owner and self._hostile(unit.owner,u.owner))]

    def engaged_without_foes(self):
        """不变量自检：交战中却找不到活敌军的军队（正常结算后必须为空）。"""
        return tuple((u.owner,u.id,u.x,u.y) for u in self._units if u.engaged and not self._engaged_foes(u))
    def remove_nation(self, owner):
        self._write()
        for unit in tuple(self._units.of(owner)):self._units.remove(unit)
    def _feeding(self,owner):
        units=[u for u in self._units.of(owner) if u.alive];exempt=set()
        for tile in self._call('native:territory','owned',owner):
            cap=self._call('native:buildings','effect_role',tile.tile.id,'militia','militia_cap')
            eligible=[u for u in units if u.kind=='native:militia' and (u.x,u.y)==tile.tile.position]
            exempt.update(u.gid for u in eligible[:cap])
        eaters=[u for u in units if u.gid not in exempt]
        need=sum(self._parameter(owner,'unit.supply.'+u.kind,u.definition['supply']) for u in eaters)
        used=min(max(0,self._stock(owner,'native:supply')),need)
        return units,eaters,need,used

    def supply_preview(self,owner):
        self._living(owner)
        units,eaters,need,used=self._feeding(owner);short=need-used
        loss=max(1,ARMY_STARVE_DAMAGE*short//need) if short else 0
        plan=self._healing_plan(owner,units,bool(short))
        eaters_ids={u.gid for u in eaters}
        rows=[{**row,'position':(u.x,u.y),'kind':u.kind,'hp':u.hp,'max_hp':u.definition['hp'],
               'supply_need':self._parameter(owner,'unit.supply.'+u.kind,u.definition['supply']) if u.gid in eaters_ids else 0,
               'supply_exempt':u.gid not in eaters_ids,'starvation_damage':loss if u.gid in eaters_ids else 0}
              for u,row in zip(units,plan)]
        equipment_need=ceil(sum(row['equipment_need'] for row in rows))
        equipment=sum(row['equipment_used'] for row in rows)
        charge=ceil(equipment-1e-12)
        return {'period':self._period(),'need':need,'available':self._stock(owner,'native:supply'),
                'used':used,'shortfall':short,'starvation_damage_per_unit':loss,
                'starving_units':[u.id for u in eaters] if short else [],
                'exempt_count':len(units)-len(eaters),
                'healing_units':[row['aid'] for row in rows if row['healing_hp']], 'healing_equipment':charge,
                'units':rows,'equipment_need':equipment_need,'equipment_available':self._stock(owner,'native:equipment'),
                'equipment_shortfall':max(0,equipment_need-self._stock(owner,'native:equipment')),
                'healing_hp':sum(row['healing_hp'] for row in rows),
                'equipment_rounding':max(0.0,round(charge-equipment,12)),
                'assumptions':'按当前军队与库存只读预演；未计本期后续生产、行动或外交变化，不扣资源。补给统一从全国库存扣除，缺口导致所有非免供军队受损，不按军队先后分粮。每件装备提供30HP总恢复量，合格正规军平均分配、满血余量再分，单支每期最多恢复30HP；全军按总恢复量向上取整扣装备，分摊份额可带小数。民兵独立免费恢复。'}

    @staticmethod
    def healing_policy(kind):
        return {'hp_per_turn':ARMY_HEAL_PER_TURN,'equipment_per_30hp':0 if kind=='native:militia' else HEAL_EQUIP_COST}

    def _healing_plan(self, owner, units, supply_short=False):
        """逐军队列恢复条件与等分装备容量；查询和结算共用，不改变库存。"""
        equipment=max(0,self._stock(owner,'native:equipment'));rows=[];regular=[]
        for u in units:
            fee=self.healing_policy(u.kind)['equipment_per_30hp'];need=0;used=0;hp=0
            if not u.alive:status='dead'
            elif u.hp>=u.definition['hp']:status='full'
            elif any(o.engaged for o in self._units.at(u.x,u.y)):status='engaged'
            else:
                hp=min(ARMY_HEAL_PER_TURN,u.definition['hp']-u.hp)
                need=hp/ARMY_HEAL_PER_TURN if fee else 0
                if supply_short:status='supply_shortage';hp=0
                elif fee:status='ready';regular.append((len(rows),hp))
                else:status='ready'
            rows.append({'aid':u.id,'equipment_per_heal':fee,'equipment_need':need,
                         'equipment_used':used,'healing_hp':hp,'healing_status':status})
        if regular:
            shares=self.equal_allocation(equipment*ARMY_HEAL_PER_TURN,[need for _,need in regular])
            for (index,demand),amount in zip(regular,shares):
                rows[index]['healing_hp']=amount
                rows[index]['equipment_used']=amount/ARMY_HEAL_PER_TURN
                if amount+1e-12<demand:rows[index]['healing_status']='equipment_shortage'
        return rows

    def _healing(self, owner, units):
        """预演与结算共用：同格交战、满血、装备不足均不进入恢复名单。"""
        plan=self._healing_plan(owner,units)
        share=sum(row['equipment_used'] for row in plan)
        return [(u,row['healing_hp']) for u,row in zip(units,plan) if row['healing_hp']],ceil(share-1e-12)

    def supply(self, effects=()):
        self._write();self._sync();period=self._period();facts=self._facts;self._facts=[]
        for effect in effects:
            if effect[0]=='peace':
                self.disengage(effect[1]);self._call('native:territory','snapshot_cores',effect[1])
        reports=[]
        for owner in self._call('native:nation-inspection','ids'):
            units,eaters,need,used=self._feeding(owner);short=need-used
            costs={};operations=[]
            if used:costs['native:supply']=used
            if short:
                loss=max(1,ARMY_STARVE_DAMAGE*short//need)
                for u in eaters:u.take_damage(loss)
                self._event('supply',f'{self._name(owner)} 断粮：补给需{need}、实耗{used}、缺{short}；每支掉{loss}HP，饿毙{sum(not u.alive for u in eaters)}支。',parties=(owner,))
            healed,equipment=self._healing(owner,units) if not short else ([],0)
            if equipment:costs['native:equipment']=equipment
            costs={key:value for key,value in costs.items() if value}
            if costs:self._call('native:resource-exchange','exchange',owner,costs,{})
            for u,amount in healed:u.heal(amount)
            if healed:self._event('healing',f'{self._name(owner)} {len(healed)}支军队各恢复至多{ARMY_HEAL_PER_TURN}HP，消耗装备{costs.get("native:equipment",0)}。',parties=(owner,))
            if costs:facts.append({'id':f'native:military/{owner.split(":",1)[1]}/{period}', 'owner':owner,'period':period,'category':'military','inputs':tuple(costs.items())})
            reports.append((owner,need,used,short,len(healed)))
        for u in tuple(self._units):
            if not u.alive:self._units.remove(u)
        self._recruited={key:value for key,value in self._recruited.items() if key[0]>=period}
        return tuple(reports),tuple(facts)

    def battle(self):
        self._write();self._sync(initialize_rng=True);reports=[]
        positions=sorted({(u.x,u.y) for u in self._units if u.engaged and u.owner!='野人'})
        for x,y in positions:
            seen=tuple(n for n in self._call('native:nation-inspection','ids') if self._call('native:vision','visible',n,x,y))
            troops=[u for u in self._units.at(x,y) if u.alive];groups={}
            for u in troops:groups.setdefault(u.owner,[]).append(u)
            active={u.owner for u in troops if u.engaged and u.owner!='野人'}
            enemies={owner:[other for other in groups if other!=owner and ((owner=='野人' and other in active) or (other=='野人' and owner in active) or owner!='野人' and other!='野人' and self._hostile(owner,other))] for owner in groups}
            damage={owner:0 for owner in groups};tile=self._call('native:territory','cell',x,y)
            terrain=self._call('native:territory','terrain',x,y)
            castle=self._call('native:buildings','effect_role',tile.tile.id,'castle','defense_per_level') if tile.tile.id else 0
            dice={}
            for owner in sorted(groups):
                if not enemies[owner]:continue
                power=self.faction_power(groups[owner])
                dice[owner]=self._rng.randint(min(COMBAT_DIE_MOD),max(COMBAT_DIE_MOD))
                power=max(1,power*(100+COMBAT_DIE_MOD[dice[owner]])//100)
                for enemy in enemies[owner]:
                    defense=self.defense_pct(terrain.defense,castle if enemy==tile.owner else 0)
                    cover=defense if enemy==tile.owner or enemy=='野人' or enemy not in active else 0
                    damage[enemy]+=max(1,round(power/len(enemies[owner])*(100-cover)/100))
            for owner,units in groups.items():
                self.spread_damage(damage[owner],units)
            for u in troops:
                if not u.alive:self._units.remove(u)
            surviving={owner:[u for u in units if u.alive] for owner,units in groups.items()}
            candidates=[owner for owner in active if any(not u.retreat_to for u in surviving[owner]) and not any(surviving[e] for e in enemies[owner])]
            if candidates:
                winner=min(candidates,key=lambda owner:(min(u.engage_seq or 0 for u in surviving[owner] if not u.retreat_to),owner))
                if winner!=tile.owner and (tile.owner is None or self._call('native:diplomacy','war_between',winner,tile.owner)):self._claim(winner,x,y)
            for owner,units in surviving.items():
                if not any(surviving[e] for e in enemies[owner]):
                    for u in units:u.disengage()
            reports.append((x,y,tuple(sorted(damage.items()))))
            detail='；'.join(f'{self._name(owner)}：骰{dice.get(owner,"未参战")}，承伤{damage[owner]}，剩余{sum(u.alive for u in units)}支/{sum(u.hp for u in units)}HP' for owner,units in groups.items())
            self._event('battle',f'({x},{y})战报，地形减伤{terrain.defense}%，城堡减伤{castle}%；{detail}。'+('战斗结束。' if not any(u.engaged for u in troops if u.alive) else '交战继续，占领未定。'),(x,y),seen)
        return tuple(reports)
    def battle_estimate(self, owner, ids, x, y):
        self._living(owner);self._call('native:territory','check',x,y)
        if not isinstance(ids,(list,tuple)) or not ids or len(set(ids))!=len(ids):raise ValueError('Unique own unit IDs required')
        attackers=[deepcopy(self._own_unit(owner,aid)) for aid in ids]
        if not self._call('native:vision','visible',owner,x,y):
            return {'known':False,'winnable':None,'rounds':None,'losses':None}
        defenders=[deepcopy(u) for u in self.visible_units(owner) if (u.x,u.y)==(x,y) and u.alive and self._hostile(owner,u.owner)]
        fact=self._call('native:territory','cell',x,y);terrain=self._call('native:territory','terrain',x,y)
        initial=sum(u.hp for u in attackers)
        for turn in range(101):
            if not any(u.alive for u in defenders):
                return {'known':True,'winnable':True,'rounds':turn,'losses':initial-sum(u.hp for u in attackers)}
            if not any(u.alive for u in attackers):break
            # Expected die modifier is zero. Each faction fires simultaneously;
            # the forecast excludes other participants, supply and reinforcements.
            groups={}
            for u in defenders:
                if u.alive:groups.setdefault(u.owner,[]).append(u)
            outgoing=self.faction_power([u for u in attackers if u.alive])
            incoming=sum(self.faction_power(group) for group in groups.values())
            for defender_owner,group in groups.items():
                castle=0
                if fact.tile.id and defender_owner==fact.owner:
                    castle=self._call('native:buildings','effect_role',fact.tile.id,'castle','defense_per_level')
                defense=self.defense_pct(terrain.defense,castle)
                self.spread_damage(max(1,round(outgoing/len(groups)*(100-defense)/100)),group)
            self.spread_damage(incoming,[u for u in attackers if u.alive])
        return {'known':True,'winnable':False,'rounds':None,'losses':initial}

    @staticmethod
    def faction_power(units):
        return sum(max(1,u.atk()*(100-RETREAT_ATK_PENALTY)//100) if u.retreat_to else u.atk() for u in units)

    @staticmethod
    def defense_pct(terrain, castle=0):
        return 100-((100-terrain)*(100-castle))//100

    @staticmethod
    def spread_damage(damage, units):
        if not units:return
        ratios=[(u.retreat_cover if u.retreat_cover is not None else 100)/100 for u in units]
        # Divide incoming damage equally. If a unit dies, its capped share is
        # redistributed over survivors; protection remains individual.
        caps=[float('inf') if ratio==0 else u.hp/ratio for u,ratio in zip(units,ratios)]
        shares=Military.equal_allocation(damage,caps)
        for unit,share,ratio in zip(units,shares,ratios):unit.take_damage(share*ratio)

    @staticmethod
    def equal_allocation(amount, limits):
        """Water-fill equally, redistributing a capped member's unused share."""
        allocated=[0.0]*len(limits);active={i for i,limit in enumerate(limits) if limit>0}
        remaining=max(0.0,float(amount))
        while active and remaining>1e-12:
            share=remaining/len(active)
            capped=[i for i in active if limits[i]-allocated[i]<=share+1e-12]
            if not capped:
                for i in active:allocated[i]+=share
                break
            spent=0.0
            for i in capped:
                amount=min(share,max(0.0,limits[i]-allocated[i]));allocated[i]+=amount
                spent+=amount;active.remove(i)
            remaining=max(0.0,remaining-spent)
        return allocated

    def drain_events(self):
        self._write();result=tuple(deepcopy(self._events));self._events=[];return result
    def withdrawals(self):
        self._write();reports=[]
        for unit in tuple(self._units):
            if unit.retreat_to is None:continue
            start=(unit.x,unit.y);target=unit.retreat_to;requested=target
            if not self._pass(unit.owner,*target):
                target=next((p for p in self._call('native:territory','neighbors',unit.x,unit.y) if self._pass(unit.owner,*p)),None)
            if target is not None:
                unit.update(x=target[0],y=target[1],engaged=False)
                reports.append((unit.owner,unit.id,target))
            self._event('withdrawal',f'{self._name(unit.owner)} #{unit.id} 从{start}'+(f'撤至{target}'+('（原落点受阻，已改撤）' if target!=requested else '') if target is not None else '撤退受阻，原地留守')+'。',parties=(unit.owner,))
            for key in ('retreat_to','retreat_role','retreat_cover'):
                if key in unit:unit.pop(key)
        return tuple(reports)
    def claims(self):
        self._write();reports=[];by_position={}
        for unit in self._units:
            if unit.alive:by_position.setdefault((unit.x,unit.y),[]).append(unit)
        for fact in self._call('native:territory','snapshot'):
            x,y=fact.tile.position;troops=by_position.get((x,y),())
            claimants=[u for u in troops if u.owner!='野人' and u.engaged and u.engage_seq is not None and u.owner!=fact.owner and (fact.owner is None or self._call('native:diplomacy','war_between',u.owner,fact.owner))]
            eligible=[u for u in claimants if not any(v.alive and self._hostile(u.owner,v.owner) for v in troops)]
            if eligible:
                winner=min(eligible,key=lambda u:(u.engage_seq,u.owner));self._claim(winner.owner,x,y);reports.append((fact.tile.id,winner.owner))
        return tuple(reports)
    def repatriate(self):
        self._write();reports=[];homes={}
        for unit in self._units.troops:
            owner=self._call('native:territory','owner',unit.x,unit.y)
            if unit.engaged or owner is None or owner==unit.owner or self._call('native:diplomacy','passage',unit.owner,owner):continue
            if unit.owner not in homes:
                homes[unit.owner]=[t.tile.position for n in self._call('native:nation-inspection','ids') if n==unit.owner or self._call('native:diplomacy','allied',unit.owner,n) for t in self._call('native:territory','owned',n)]
            home=homes[unit.owner]
            if not home:continue
            start=(unit.x,unit.y)
            goal=min(home,key=lambda p:(max(abs(p[0]-unit.x),abs(p[1]-unit.y)),p))
            point=start
            for _ in range(self._parameter(unit.owner,'unit.speed.'+unit.kind,unit.definition['speed'])):
                if point==goal:break
                point=(point[0]+(goal[0]>point[0])-(goal[0]<point[0]),point[1]+(goal[1]>point[1])-(goal[1]<point[1]))
            if point!=start:
                unit.update(x=point[0],y=point[1]);reports.append((unit.owner,unit.id,point))
                self._event('repatriation',f'{self._name(unit.owner)} #{unit.id} 从{start}自动遣返至{point}。',parties=(unit.owner,))
        return tuple(reports)

    def to_save(self):
        rng=self._rng.getstate() if self._rng else None
        return {'recruited':[{'period':p,'site':s,'militia':m,'count':n} for (p,s,m),n in self._recruited.items()],
                'facts':deepcopy(self._facts),'seed':self._seed,'grid_short':dict(self._grid_short),'events':deepcopy(self._events),
                'rng':None if rng is None else [rng[0],list(rng[1]),rng[2]]}
    def restore(self, data):
        self._write()
        if not isinstance(data,dict) or set(data)!={'recruited','facts','seed','rng','grid_short','events'} or not isinstance(data['recruited'],list) or not isinstance(data['facts'],list) or not isinstance(data['events'],list):raise ValueError('Invalid military save')
        if not isinstance(data['grid_short'],dict) or any(type(value) is not bool for value in data['grid_short'].values()):raise ValueError('Invalid military grid facts')
        for owner in data['grid_short']:self._call('native:nation-inspection','get',owner)
        recruited={}
        for row in data['recruited']:
            if not isinstance(row,dict) or set(row)!={'period','site','militia','count'}:raise ValueError('Invalid recruitment record')
            integer(row['period']);integer(row['count'],1);self._call('native:buildings','site',row['site'])
            if type(row['militia']) is not bool:raise ValueError('Invalid militia flag')
            key=(row['period'],row['site'],row['militia'])
            if key in recruited:raise ValueError('Duplicate recruitment counter')
            recruited[key]=row['count']
        rng=None
        if data['rng'] is not None:
            integer(data['seed'])
            if data['seed']!=self._call('native:territory','dimensions')[1]:raise ValueError('Military/map seeds disagree')
            values=data['rng']
            if not isinstance(values,list) or len(values)!=3 or not isinstance(values[1],list):raise ValueError('Invalid random state')
            rng=random.Random();rng.setstate((values[0],tuple(values[1]),values[2]))
        elif data['seed'] is not None:raise ValueError('Uninitialized random state has seed')
        for row in data['facts']:
            if not isinstance(row,dict) or set(row)!={'id','owner','period','category','inputs'} or row['category']!='recruit':raise ValueError('Invalid military economic fact')
            self._call('native:nation-inspection','get',row['owner']);integer(row['period'])
            if not isinstance(row['id'],str) or not row['id'] or not isinstance(row['inputs'],(list,tuple)):raise ValueError('Invalid military fact inputs')
            seen=set()
            for item in row['inputs']:
                if not isinstance(item,(list,tuple)) or len(item)!=2:raise ValueError('Invalid military fact quantity')
                ident,amount=item;content_id(ident);integer(amount,1)
                if ident in seen:raise ValueError('Duplicate military fact resource')
                seen.add(ident)
        for event in data['events']:
            # ★★ 2026-10-23 修：`occupation` 事件**带一个 `details` 段**
            #   （占领的前后归属/核心/名称/厅数 —— 主线的 UI 与战报都在读它，
            #   见 `tests/test_ui_regressions.py`）。
            #   原来的校验是 `set(event) != {...}`（**不允许任何多余键**）
            #   ⇒ **引擎自己 `to_save()` 出来的事件自己 `restore()` 不回去**
            #   ⇒ 任何"打过一仗、占过地"的世界都**克隆不了**（`Env.clone()` 抛
            #   `Invalid military event`）—— 潜伏的引擎 bug，被 RL 线"野地也能进攻"
            #   那一改**引爆**（占领事件从此天天出现）。
            #   ⇒ 这里改成"**必需键一个都不能少，多余键只允许 `details`**"。
            if not isinstance(event,dict) or not {'kind','text','position','parties','snapshot_only'}<=set(event) or set(event)-{'kind','text','position','parties','snapshot_only','details'}:raise ValueError('Invalid military event')
            if event['kind'] not in ('battle','occupation','supply','healing','withdrawal','repatriation') or not isinstance(event['text'],str) or event['snapshot_only'] is not True:raise ValueError('Invalid military event fields')
            if 'details' in event and not isinstance(event['details'],dict):raise ValueError('Invalid military event details')
            if event['position'] is not None:
                if not isinstance(event['position'],(list,tuple)) or len(event['position'])!=2:raise ValueError('Invalid military event position')
                self._call('native:territory','check',*event['position'])
            if not isinstance(event['parties'],(list,tuple)) or len(set(event['parties']))!=len(event['parties']):raise ValueError('Invalid military event recipients')
            for owner in event['parties']:self._call('native:nation-inspection','get',owner)
        self._recruited,self._facts,self._seed,self._rng=recruited,deepcopy(data['facts']),data['seed'],rng
        self._grid_short=dict(data['grid_short'])
        self._events=deepcopy(data['events'])
    def __deepcopy__(self,memo):return self.to_save()
