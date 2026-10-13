from engine.runtime import GameMod, Stage
from engine.state import active_unit
from engine.runtime import ModError


class Succession:
    def __init__(self, call):self._call=call;self._enrolled=set();self._world=None;self._domain=None
    def bind_state(self, world, key):self._world,self._domain=world,world.runtime.state_owners[key];return self
    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use succession interface')
    def enroll(self, owner):
        self._write()
        if owner not in self._call('native:nation-inspection','ids'):raise ValueError('Nation is not alive')
        self._enrolled.add(owner)
    def settle(self):
        self._write();dead=[]
        for owner in self._call('native:nation-inspection','ids'):
            if owner not in self._enrolled:continue
            sites=self._call('native:nation-inspection','overview',owner).sites
            if any(self._call('native:buildings','count',site.id,'native:town-hall') for site in sites):continue
            for site in sites:self._call('native:buildings','transfer_site',site.id,None)
            survivors=self._call('native:diplomacy','eliminate',owner)
            self._call('native:army','remove_nation',owner);self._call('native:army','disengage',survivors)
            self._call('native:nations','extinguish',owner,'No completed town hall remains')
            self._world.runtime.deactivate_actor(self._world,owner)
            dead.append(owner)
        return tuple(dead)
    def to_save(self):return {'enrolled':sorted(self._enrolled)}
    def restore(self, data):
        self._write()
        if not isinstance(data,dict) or set(data)!={'enrolled'} or not isinstance(data['enrolled'],list) or len(set(data['enrolled']))!=len(data['enrolled']):raise ValueError('Invalid succession save')
        for owner in data['enrolled']:self._call('native:nation-inspection','get',owner)
        self._enrolled=set(data['enrolled'])


class SuccessionMod(GameMod):
    def mixins(self):return ()
    def state_keys(self):return ('succession',)
    def needs(self):return ('native:nation-inspection','native:nations','native:buildings','native:diplomacy','native:army')
    def provides(self):return ('native:succession',)
    def initialize_world(self, world):world.succession=Succession(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))
    def objects(self, world):return {'native:succession':world.succession}
    def save_state(self, world):return world.succession.to_save()
    def load_state(self, world, state):world.succession.restore(state)
    def stages(self):return (Stage('native:succession.settle',lambda w,c:c.update(extinct_nations=w.succession.settle()),hook='succession',needs=('claims.resolved',),provides=('actors.eliminated',)),)
