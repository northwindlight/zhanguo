"""状态请求服务：每个键只有一个所有者；兼容对象保留引用身份。"""
from __future__ import annotations
from contextvars import ContextVar
from contextlib import contextmanager
from copy import deepcopy
from types import MappingProxyType
from weakref import WeakValueDictionary
from collections.abc import Mapping

active_unit = ContextVar('active_unit', default=None)

@contextmanager
def unit_context(name):
    token=active_unit.set(name)
    try: yield
    finally: active_unit.reset(token)

def freeze(value):
    if isinstance(value,Mapping): return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value,(list,tuple)): return tuple(freeze(v) for v in value)
    if isinstance(value,set): return frozenset(value)
    return deepcopy(value)

class Owned:
    def setup(self,raw,world,key):
        self._raw,self._state_world,self._state_key=raw,world,key
        self.refresh()
        return self
    def refresh(self):
        if isinstance(self,dict):
            dict.clear(self);dict.update(self,self._raw)
        elif isinstance(self,list):
            list.clear(self);list.extend(self,self._raw)
        elif isinstance(self,set):
            set.clear(self);set.update(self,self._raw)
        return self
    def dual(self,operation,mirror):
        def apply():
            result=operation()
            mirror()
            return result
        return self.commit(apply)
    def commit(self,operation):
        return self._state_world.runtime.write_state(self._state_world,self._state_key,operation)
    def value(self,value):return own(value,self._state_world,self._state_key)
    def __deepcopy__(self,memo):return deepcopy(self._raw,memo)
    def __repr__(self):return repr(self._raw)
    def __len__(self):return len(self._raw)
    def __bool__(self):return bool(self._raw)
    def __contains__(self,value):return value in self._raw
    def __eq__(self,other):return self._raw==(other._raw if isinstance(other,Owned) else other)
    def __ne__(self,other):return not self==other

class OwnedDict(Owned,dict):
    def __getitem__(self,key):return self.value(self._raw[key])
    def __setitem__(self,key,value):return self.dual(lambda:self._raw.__setitem__(key,value),lambda:dict.__setitem__(self,key,value))
    def __delitem__(self,key):return self.dual(lambda:self._raw.__delitem__(key),lambda:dict.__delitem__(self,key))
    def __iter__(self):return iter(self._raw)
    def get(self,key,default=None):return self[key] if key in self else default
    def keys(self):return self._raw.keys()
    def items(self):return {k:self[k] for k in self._raw}.items()
    def values(self):return {k:self[k] for k in self._raw}.values()
    def copy(self):return {k:self[k] for k in self._raw}
    def clear(self):return self.dual(self._raw.clear,self.refresh)
    def pop(self,key,*args):return self.dual(lambda:self.value(self._raw.pop(key,*args)),self.refresh)
    def popitem(self):return self.dual(self._raw.popitem,self.refresh)
    def setdefault(self,key,default=None):
        if key not in self:self[key]=default
        return self[key]
    def update(self,*args,**kwargs):return self.dual(lambda:self._raw.update(*args,**kwargs),self.refresh)
    def __ior__(self,other):self.update(other);return self

class OwnedList(Owned,list):
    def __getitem__(self,key):
        value=self._raw[key]
        return [self.value(v) for v in value] if isinstance(key,slice) else self.value(value)
    def __setitem__(self,key,value):return self.dual(lambda:self._raw.__setitem__(key,value),lambda:list.__setitem__(self,key,value))
    def __delitem__(self,key):return self.dual(lambda:self._raw.__delitem__(key),lambda:list.__delitem__(self,key))
    def __iter__(self):return (self.value(v) for v in self._raw)
    def __reversed__(self):return (self.value(v) for v in reversed(self._raw))
    def index(self,*args):return self._raw.index(*args)
    def count(self,value):return self._raw.count(value)
    def append(self,value):return self.dual(lambda:self._raw.append(value),lambda:list.append(self,value))
    def extend(self,values):return self.dual(lambda:self._raw.extend(values),self.refresh)
    def insert(self,index,value):return self.dual(lambda:self._raw.insert(index,value),self.refresh)
    def pop(self,*args):return self.dual(lambda:self.value(self._raw.pop(*args)),self.refresh)
    def remove(self,value):return self.dual(lambda:self._raw.remove(value),self.refresh)
    def clear(self):return self.dual(self._raw.clear,self.refresh)
    def reverse(self):return self.dual(self._raw.reverse,self.refresh)
    def sort(self,*args,**kwargs):return self.dual(lambda:self._raw.sort(*args,**kwargs),self.refresh)
    def __add__(self,other):return list(self)+list(other)
    def __radd__(self,other):return list(other)+list(self)
    def __iadd__(self,values):self.extend(values);return self
    def __imul__(self,value):self.dual(lambda:self._raw.__imul__(value),self.refresh);return self

class OwnedSet(Owned,set):
    def __iter__(self):return iter(self._raw)
    def add(self,value):return self.dual(lambda:self._raw.add(value),lambda:set.add(self,value))
    def discard(self,value):return self.dual(lambda:self._raw.discard(value),lambda:set.discard(self,value))
    def remove(self,value):return self.dual(lambda:self._raw.remove(value),self.refresh)
    def pop(self):return self.dual(self._raw.pop,self.refresh)
    def clear(self):return self.dual(self._raw.clear,self.refresh)
    def update(self,*values):return self.dual(lambda:self._raw.update(*values),self.refresh)
    def difference_update(self,*values):return self.dual(lambda:self._raw.difference_update(*values),self.refresh)
    def intersection_update(self,*values):return self.dual(lambda:self._raw.intersection_update(*values),self.refresh)
    def symmetric_difference_update(self,value):return self.dual(lambda:self._raw.symmetric_difference_update(value),self.refresh)

def own(value,world,key):
    bind = getattr(value, 'bind_state', None)
    if bind: return bind(world,key)
    if isinstance(value,Owned):
        if value._state_world is world:
            if len(value._raw) != (dict.__len__(value) if isinstance(value,dict) else list.__len__(value) if isinstance(value,list) else set.__len__(value)):value.refresh()
            return value
        value=deepcopy(value)
    kind=OwnedDict if isinstance(value,dict) else OwnedList if isinstance(value,list) else OwnedSet if isinstance(value,set) else None
    if kind:
        cache=world.__dict__.setdefault('_state_wrapper_cache',WeakValueDictionary())
        result=cache.get(id(value))
        if result is None:
            result=kind().setup(value,world,key)
            cache[id(value)]=result
        return result
    if hasattr(value,'res') and isinstance(value.res,dict):value.res=own(value.res,world,key)
    return value


def references(value):
    """兼容引用保留底层值；派生索引不成为对象的新所有者。"""
    if isinstance(value,Owned):return value._raw
    if isinstance(value,dict):
        for key,item in list(value.items()):
            if isinstance(item,Owned):value[key]=item._raw
    elif isinstance(value,list):
        for i,item in enumerate(value):
            if isinstance(item,Owned):value[i]=item._raw
    return value
