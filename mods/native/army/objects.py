"""部队对象和集合门面。字段、编号、索引与序列化归军队域所有。"""
from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field
from collections.abc import Mapping, MutableMapping
import math
from engine.state import active_unit, own, freeze
from engine.runtime import ModError
from .parameters import ARMY_GID_BASE
from .content import native_unit_definitions

@dataclass(eq=False)
class Unit(MutableMapping):
    """一支部队；字典访问仅用于逐步迁移本域的既有结算代码。"""
    kind: str
    hp: int | float
    x: int
    y: int
    owner: str
    id: int
    gid: int
    name: str
    moved_turn: int = -1
    engaged: bool = False
    born_turn: int | None = None
    engage_seq: int | None = None
    retreat_to: tuple[int, int] | None = None
    retreat_role: str | None = None
    retreat_cover: int | None = None

    def to_public(self):
        # The actor-local integer ID is the action key. Internal global IDs,
        # generated names and combat tie-break sequences are not player input.
        result={k:getattr(self,k) for k in ('id','kind','owner','x','y','hp','moved_turn','engaged','born_turn','retreat_to')}
        if self.retreat_to is not None:
            result.update(retreat_role=self.retreat_role,retreat_cover=self.retreat_cover)
        return result
    _collection: object = field(default=None, repr=False)
    _definitions: object = field(default=None, repr=False)
    _present: tuple = field(default=(), init=False, repr=False)
    _ready: bool = field(default=False, init=False, repr=False)

    FIELDS = ('id','gid','name','type','hp','x','y','owner','moved_turn','born_turn',
              'engaged','engage_seq','retreat_to','retreat_role','retreat_cover')
    ATTRS = {'type': 'kind'}

    def __post_init__(self):
        if self._definitions is None:
            object.__setattr__(self, '_definitions', freeze(native_unit_definitions()))
        self.validate()
        data = {'id':self.id,'gid':self.gid,'name':self.name,'type':self.kind,'hp':self.hp,
                'x':self.x,'y':self.y,'owner':self.owner,'moved_turn':self.moved_turn,'engaged':self.engaged}
        for key in ('born_turn','engage_seq','retreat_to','retreat_role','retreat_cover'):
            value = getattr(self,key)
            if value is not None: data[key] = list(value) if key=='retreat_to' else value
        object.__setattr__(self,"_present",tuple(data))
        object.__setattr__(self,'_ready',True)

    def validate(self, size=None):
        if not isinstance(self.name,str) or not self.name:raise ValueError('部队必须有名称')
        if self.retreat_role not in (None,'攻','守'):raise ValueError('部队撤退角色非法')
        if self.retreat_cover is not None and (type(self.retreat_cover) is not int or not 0<=self.retreat_cover<=100):raise ValueError('部队撤退承伤比例非法')
        if not isinstance(self.kind,str) or self.kind not in (self._definitions if self._definitions is not None else native_unit_definitions()): raise ValueError(f'非法兵种：{self.kind}')
        if not isinstance(self.owner,str) or not self.owner: raise ValueError('部队必须有归属')
        if type(self.id) is not int or self.id < 1 or type(self.gid) is not int or self.gid < 0: raise ValueError('部队编号非法')
        if isinstance(self.hp,bool) or not isinstance(self.hp,(int,float)) or not math.isfinite(self.hp): raise ValueError('部队生命值非法')
        if type(self.x) is not int or type(self.y) is not int or self.x < 0 or self.y < 0: raise ValueError('部队位置非法')
        if size is not None and (self.x >= size or self.y >= size): raise ValueError('部队位置越界')
        if type(self.moved_turn) is not int or type(self.engaged) is not bool: raise ValueError('部队行动状态非法')
        for key in ('born_turn','engage_seq'):
            value=getattr(self,key)
            if value is not None and (type(value) is not int or value < (-1 if key=='born_turn' else 0)): raise ValueError(f'部队 {key} 非法')
        if self.retreat_to is not None:
            if len(self.retreat_to)!=2 or any(type(v) is not int or v < 0 or size is not None and v>=size for v in self.retreat_to): raise ValueError('部队撤退目标非法')

    @property
    def alive(self): return self.hp > 0
    @property
    def definition(self): return (self._definitions if self._definitions is not None else native_unit_definitions())[self.kind]
    def can_act(self, turn): return self.alive and self.born_turn != turn and self.moved_turn != turn and not self.engaged
    def can_attack(self, turn, x, y):
        return self.alive and self.born_turn != turn and ((self.x,self.y)==(x,y) or self.can_act(turn))
    def atk(self, stats=None): return (self.definition if stats is None else stats[self.kind])['atk'] if self.alive else 0
    def move_to(self,x,y,turn):
        self._write()
        size=self._collection.size if self._collection is not None else None
        if type(x) is not int or type(y) is not int or x<0 or y<0 or size is not None and (x>=size or y>=size): raise ValueError('部队位置越界')
        if type(turn) is not int: raise ValueError('部队行动回合非法')
        self.update(x=x, y=y, moved_turn=turn)
    def take_damage(self, amount):
        if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<0: raise ValueError('伤害非法')
        self.hp=max(0,self.hp-amount)
        return self.alive
    def heal(self, amount):
        if isinstance(amount,bool) or not isinstance(amount,(int,float)) or not math.isfinite(amount) or amount<0: raise ValueError('治疗非法')
        if self.alive:self.hp=min(self.definition['hp'], self.hp+amount)
    def disengage(self): self.engaged=False

    def _write(self):
        if self._collection is not None:self._collection._write()
    def __setattr__(self,key,value):
        if not self.__dict__.get('_ready',False) or key.startswith('_'):return object.__setattr__(self,key,value)
        if key=='kind': key='type'
        if key not in self.FIELDS: raise AttributeError(f'部队没有字段：{key}')
        self[key]=value
    def __setitem__(self,key,value):
        self._write()
        if key not in self.FIELDS:raise ValueError(f'部队没有字段：{key}')
        attr=self.ATTRS.get(key,key)
        old=getattr(self,attr)
        if self._collection is not None and key in ("id","gid","owner","type") and value!=old:
            raise ValueError("已登记部队的编号、归属与兵种不能直接改写")
        if key=='retreat_to' and value is not None:value=tuple(value)
        object.__setattr__(self,attr,value)
        try:self.validate(self._collection.size if self._collection is not None else None)
        except Exception:
            object.__setattr__(self,attr,old)
            raise
        if key not in self._present:object.__setattr__(self,'_present',(*self._present,key))
        if self._collection is not None and key in ("x","y") and self.owner=="野人":self._collection.invalidate()
    def __delitem__(self,key):
        self._write()
        if key not in ('born_turn','engage_seq','retreat_to','retreat_role','retreat_cover'):raise ValueError('不能删除部队基本字段')
        if key not in self._present:raise KeyError(key)
        object.__setattr__(self,'_present',tuple(k for k in self._present if k!=key))
        object.__setattr__(self,key,None)
    def pop(self,key,*default):
        if key not in self:
            if default:return default[0]
            raise KeyError(key)
        value=self[key];del self[key];return value
    def update(self,*args,**kwargs):
        self._write()
        changes=dict(*args,**kwargs)
        if set(changes)-set(self.FIELDS): raise ValueError('部队记录含未知字段')
        if self._collection is not None and any(
                key in changes and changes[key] != getattr(self,self.ATTRS.get(key,key))
                for key in ('id','gid','owner','type')):
            raise ValueError('已登记部队的编号、归属与兵种不能直接改写')
        candidate=self.from_save({**self.to_save(),**changes},
                                 size=self._collection.size if self._collection is not None else None,
                                 definitions=self._definitions)
        for key in changes:
            attr=self.ATTRS.get(key,key)
            object.__setattr__(self,attr,getattr(candidate,attr))
        object.__setattr__(self,'_present',candidate._present)
        if self._collection is not None and self.owner=='野人' and set(changes)&{'x','y'}:
            self._collection.invalidate()
    def clear(self):raise ValueError('不能清空部队字段')
    def setdefault(self,key,value=None):
        if key not in self:self[key]=value
        return self[key]
    def popitem(self):raise ValueError('不能任意删除部队字段')
    def __ior__(self,value):self.update(value);return self
    def __getitem__(self,key):
        if key not in self._present:raise KeyError(key)
        value=getattr(self,self.ATTRS.get(key,key))
        return list(value) if key=='retreat_to' and value is not None else value
    def __iter__(self):return iter(self._present)
    def __len__(self):return len(self._present)
    def copy(self):return self.to_save()
    def to_save(self):
        # 仅序列化显式协议字段；保留输入的物化顺序，不遍历对象属性。
        return {key:deepcopy(self[key]) for key in self._present}
    @classmethod
    def from_save(cls,data,*,size=None,definitions=None):
        if not isinstance(data,Mapping) or set(data)-set(cls.FIELDS):raise ValueError('部队记录含未知字段')
        u=cls(kind=data['type'],hp=data['hp'],x=data['x'],y=data['y'],owner=data['owner'],
              id=data['id'],gid=data.get('gid',data['id']),name=data.get('name',str(data['id'])),
              moved_turn=data.get('moved_turn',-1),engaged=data.get('engaged',False),born_turn=data.get('born_turn'),
              engage_seq=data.get('engage_seq'),retreat_to=tuple(data['retreat_to']) if data.get('retreat_to') is not None else None,
              retreat_role=data.get('retreat_role'),retreat_cover=data.get('retreat_cover'),_definitions=definitions)
        u.validate(size)
        object.__setattr__(u,'_present',tuple(data))
        return u
    def __deepcopy__(self,memo):
        result=self.from_save(self.to_save(),definitions=self._definitions);memo[id(self)]=result;return result

class Units(list):
    """唯一可写的部队集合；跨域调用声明过的行为，读取返回部队对象。"""
    def __init__(self,size,world=None,*,definitions=None):
        self.size=size;self._world=world;self._owner='native:army'
        self._definitions = freeze(dict(definitions if definitions is not None else native_unit_definitions()))
        self._troops=[];self._key=None
        self.nation_code={};self.next_army_seq={};self.guard_once=set()
        self.next_code=1;self.engage_seq=0;self._revision=0;self._ids=set();self._local_ids=set()
    def _write(self):
        caller=active_unit.get()
        if self._world is not None and caller is not None and caller != self._owner:raise ModError(f'{caller} 不能直接写部队；请调用 army.units 的接口')
    def __setattr__(self,key,value):
        if key in ('size','nation_code','next_army_seq','guard_once','next_code','engage_seq') and self.__dict__.get('_world') is not None:
            self._write()
        object.__setattr__(self,key,value)
    def bind_state(self,world,key):
        if self._world is world:return self
        self._world=world;self._owner=world.runtime.state_owners[key]
        for attr in ('nation_code','next_army_seq','guard_once'):
            object.__setattr__(self,attr,own(getattr(self,attr),world,key))
        for unit in self:object.__setattr__(unit,'_collection',self)
        return self
    def invalidate(self):self._revision+=1;self._key=None
    @property
    def definitions(self):
        from types import MappingProxyType
        return MappingProxyType(self._definitions)
    @property
    def type_ids(self):return tuple(self._definitions)
    def definition(self,kind):return self._definitions[kind]
    def _adopt(self,unit):
        if not isinstance(unit,Unit):unit=Unit.from_save(unit,size=self.size,definitions=self._definitions)
        elif unit._collection is not None and unit._collection is not self:unit=deepcopy(unit)
        object.__setattr__(unit,'_definitions',self._definitions)
        unit.validate(self.size);object.__setattr__(unit,'_collection',self);return unit
    def append(self,unit):
        self._write();unit=self._adopt(unit)
        if unit.gid in self._ids or (unit.owner,unit.id) in self._local_ids:raise ValueError('部队编号重复')
        list.append(self,unit);self._ids.add(unit.gid);self._local_ids.add((unit.owner,unit.id))
        self.next_army_seq[unit.owner]=max(unit.id,self.next_army_seq.get(unit.owner,0))
        self.invalidate()
    def extend(self,units):
        for unit in units:self.append(unit)
    def insert(self,index,unit):
        self._write();unit=self._adopt(unit)
        if unit.gid in self._ids or (unit.owner,unit.id) in self._local_ids:raise ValueError('部队编号重复')
        list.insert(self,index,unit);self._ids.add(unit.gid);self._local_ids.add((unit.owner,unit.id))
        self.next_army_seq[unit.owner]=max(unit.id,self.next_army_seq.get(unit.owner,0))
        self.invalidate()
    def remove(self,unit):self._write();list.remove(self,unit);self._ids.discard(unit.gid);self._local_ids.discard((unit.owner,unit.id));self.invalidate()
    def pop(self,index=-1):self._write();result=list.pop(self,index);self._ids.discard(result.gid);self._local_ids.discard((result.owner,result.id));self.invalidate();return result
    def clear(self):self._write();list.clear(self);self._ids.clear();self._local_ids.clear();self.invalidate()
    def replace(self,values):
        self._write();adopted=[self._adopt(u) for u in values]
        if len({u.gid for u in adopted})!=len(adopted) or len({(u.owner,u.id) for u in adopted})!=len(adopted):raise ValueError('部队编号重复')
        list.clear(self);list.extend(self,adopted);self._ids={u.gid for u in adopted};self._local_ids={(u.owner,u.id) for u in adopted}
        for u in adopted:self.next_army_seq[u.owner]=max(u.id,self.next_army_seq.get(u.owner,0))
        self.invalidate()
    def __setitem__(self,key,value):
        values=list(self);values[key]=value;self.replace(values)
    def __delitem__(self,key):self._write();list.__delitem__(self,key);self._ids={u.gid for u in self};self._local_ids={(u.owner,u.id) for u in self};self.invalidate()
    def __iadd__(self,value):self.extend(value);return self
    def __imul__(self,value):
        if value==0:self.clear()
        elif value!=1:raise ValueError('部队集合不能复制编号')
        return self
    def reverse(self):self._write();list.reverse(self);self.invalidate()
    def sort(self,*args,**kwargs):self._write();list.sort(self,*args,**kwargs);self.invalidate()
    def _index(self):
        if self._key==self._revision:return
        self._troops=[u for u in self if u.owner!='野人']
        groups={}
        for unit in self:groups.setdefault(unit.owner,[]).append(unit)
        self._by_nation={nation:tuple(units) for nation,units in groups.items()}
        self._by_id={(unit.owner,unit.id):unit for unit in self}
        self._by_gid={unit.gid:unit for unit in self}
        self._key=self._revision
    @property
    def troops(self):self._index();return list(self._troops)
    def of(self,nation):
        self._index();return self._by_nation.get(nation,())
    def at(self,x,y):return tuple(u for u in self if (u.x,u.y)==(x,y))
    def by_id(self,aid,nation=None):
        """指定国家时查询本国番号，否则查询全局 gid。"""
        self._index()
        return self._by_gid.get(aid) if nation is None else self._by_id.get((nation,aid))
    def assign_code(self,name):
        self._write()
        if name=='野人':self.nation_code.setdefault(name,0)
        elif name not in self.nation_code:self.nation_code[name]=self.next_code;self.next_code+=1
    def allocate(self,owner):
        self._write()
        if not isinstance(owner,str) or not owner: raise ValueError('部队必须有归属')
        seq=self.next_army_seq.get(owner,0)+1
        if seq >= ARMY_GID_BASE: raise ValueError('部队序号已耗尽')
        self.assign_code(owner);self.next_army_seq[owner]=seq
        return self.nation_code[owner]*ARMY_GID_BASE+seq,seq
    def next_engagement(self):self._write();self.engage_seq+=1;return self.engage_seq
    def spawn(self,owner,kind,x,y,*,turn=-1,newborn=False):
        self._write()
        if kind not in self._definitions or type(x) is not int or type(y) is not int or not (0<=x<self.size and 0<=y<self.size):raise ValueError("部队兵种或位置非法")
        if not isinstance(owner,str) or not owner: raise ValueError('部队必须有归属')
        if type(turn) is not int or newborn and turn < -1: raise ValueError('部队行动回合非法')
        if type(newborn) is not bool: raise ValueError('部队新兵状态非法')
        gid,seq=self.allocate(owner)
        from .rules import army_name
        unit=Unit(kind,self.definition(kind)['hp'],x,y,owner,seq,gid,f"野人{seq}" if owner=="野人" else army_name(owner,seq,kind),moved_turn=turn,born_turn=turn if newborn else None,_definitions=self._definitions)
        self.append(unit);return unit
    def to_save(self):
        return {'armies':[unit.to_save() for unit in self],
                'next_army_seq':dict(self.next_army_seq),'nation_code':dict(self.nation_code),
                'guard_once':[list(pos) for pos in sorted(self.guard_once)]}
    def restore(self,data):
        self._write()
        restored=Units.from_save(data,size=self.size,definitions=self._definitions)
        self.replace(restored)
        self.next_army_seq=own(restored.next_army_seq,self._world,'units') if self._world else restored.next_army_seq
        self.nation_code=own(restored.nation_code,self._world,'units') if self._world else restored.nation_code
        self.guard_once=own(restored.guard_once,self._world,'units') if self._world else restored.guard_once
        self.next_code=restored.next_code;self.engage_seq=restored.engage_seq
    @classmethod
    def from_save(cls,data,*,size,definitions=None):
        result=cls(size,definitions=definitions)
        result.extend(Unit.from_save(u,size=size,definitions=result._definitions) for u in data['armies'])
        for key in ('nation_code','next_army_seq'):
            values=data[key]
            if not isinstance(values,dict) or any(not isinstance(k,str) or type(v) is not int or v<0 for k,v in values.items()):raise ValueError('部队编号表非法')
        result.nation_code=dict(data['nation_code'])
        result.next_army_seq=dict(data['next_army_seq'])
        if any(not name or (code != 0 if name == '野人' else code < 1)
               for name,code in result.nation_code.items()): raise ValueError('部队国家编码非法')
        if len(set(result.nation_code.values())) != len(result.nation_code): raise ValueError('部队国家编码重复')
        if any(name not in result.nation_code or seq >= ARMY_GID_BASE
               for name,seq in result.next_army_seq.items()): raise ValueError('部队序号表与国家编码不一致')
        for u in result:
            if u.owner not in result.nation_code or not 1 <= u.id < ARMY_GID_BASE:
                raise ValueError('部队归属或番号非法')
            if u.gid != result.nation_code[u.owner]*ARMY_GID_BASE+u.id:
                raise ValueError('部队全局编号与国家编码、番号不一致')
            if 'nations' in data and u.owner != '野人' and u.owner not in data['nations']:
                raise ValueError('部队归属国家不存在')
        if any(result.next_army_seq.get(u.owner,0)<u.id for u in result):raise ValueError('部队序号计数器落后于现存部队')
        result.guard_once=set()
        for pos in data['guard_once']:
            if not isinstance(pos,(list,tuple)) or len(pos)!=2 or any(type(v) is not int or not 0<=v<size for v in pos):raise ValueError('守卫出生记录非法')
            result.guard_once.add(tuple(pos))
        result.next_code=max(result.nation_code.values(),default=0)+1
        result.engage_seq=max((u.engage_seq or 0 for u in result),default=0)
        return result
    def __deepcopy__(self,memo):
        result=self.from_save(self.to_save(),size=self.size,definitions=self._definitions);memo[id(self)]=result;return result
