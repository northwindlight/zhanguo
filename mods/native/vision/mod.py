from dataclasses import dataclass
from engine.runtime import GameMod, RuleSection
from .api import VisionAPI


@dataclass(frozen=True)
class VisibleTile:
    position: tuple
    name: str
    terrain: str
    owner: str | None
    buildings: tuple
    resources: tuple


class Vision:
    def __init__(self, call):self._call=call
    def positions(self, actor):
        size,_=self._call('native:territory','dimensions')
        entity=self._call('native:diplomacy','entity',actor)
        allies=self._call('native:diplomacy','members',entity)
        result=set()
        for owner in allies:
            for fact in self._call('native:territory','owned',owner):
                tx,ty=fact.tile.position
                radius=self._call('native:buildings','effects',fact.tile.id,'native:watchtower').get('vision_radius',0)
                reach=max(1,radius)
                for x in range(max(0,tx-reach),min(size,tx+reach+1)):
                    for y in range(max(0,ty-reach),min(size,ty+reach+1)):
                        if max(abs(tx-x),abs(ty-y))<=1 or radius and (tx-x)**2+(ty-y)**2<=radius**2:result.add((x,y))
        return frozenset(result)
    def visible(self, actor, x, y):
        self._call('native:territory','check',x,y)
        return (x,y) in self._positions(actor)
    def _positions(self, actor):
        return self._derive(('vision.positions',actor),lambda:self.positions(actor)) if hasattr(self,'_derive') else self.positions(actor)
    def tiles(self, actor):
        def calculate():
            snapshot=self._call('native:buildings','snapshot');by_site={}
            for b in snapshot.instances:by_site.setdefault(b.site,[]).append(b)
            result=[]
            for x,y in sorted(self._positions(actor)):
                fact=self._call('native:territory','cell',x,y);kinds={}
                full=fact.owner==actor or fact.owner is None and bool(by_site.get(fact.tile.id))
                for b in by_site.get(fact.tile.id,()):
                    if b.remaining != 0:continue
                    if fact.owner not in (None,actor) and b.kind not in ('native:castle','native:town-hall'):continue
                    kinds[b.kind]=kinds.get(b.kind,0)+1
                result.append(VisibleTile((x,y),fact.tile.name if fact.owner is not None else '',fact.tile.terrain,fact.owner,tuple(kinds.items()),fact.resources if full else ()))
            return tuple(result)
        return self._derive(('vision.tiles',actor),calculate) if hasattr(self,'_derive') else calculate()


class VisionMod(GameMod):
    def mixins(self):return ()
    def needs(self):return ('native:territory','native:diplomacy','native:buildings')
    def provides(self):return ('native:vision',)
    def objects(self, world):
        view=Vision(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))
        view._derive=lambda key,calculate:self.runtime.derived(world,key,calculate)
        return {'native:vision':view}
    def player_api(self, world):return VisionAPI(world)
    def rule_sections(self, world=None):return (RuleSection('视野','本国和同盟土地周围一格可见，哨塔扩展视野。他国可见地块只显示已落成的市政厅和城堡，不公开库存、资源容量或其他建筑。'),)
