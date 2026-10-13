from dataclasses import dataclass
from engine.runtime import GameMod, Panel, RuleSection, Tool


@dataclass(frozen=True)
class VisibleTile:
    position: tuple
    terrain: str
    owner: str | None
    buildings: tuple
    resources: tuple


class Vision:
    def __init__(self, call):self._call=call
    def visible(self, actor, x, y):
        self._call('native:territory','check',x,y)
        entity=self._call('native:diplomacy','entity',actor)
        allies=self._call('native:diplomacy','members',entity)
        tiles=[t for owner in allies for t in self._call('native:territory','owned',owner)]
        if any(max(abs(t.tile.position[0]-x),abs(t.tile.position[1]-y))<=1 for t in tiles):return True
        for t in tiles:
            effect=self._call('native:buildings','effects',t.tile.id,'native:watchtower')
            radius=effect.get('vision_radius',0)
            tx,ty=t.tile.position
            if radius and (tx-x)**2+(ty-y)**2<=radius**2:return True
        return False
    def tiles(self, actor):
        size,_=self._call('native:territory','dimensions');snapshot=self._call('native:buildings','snapshot')
        result=[]
        for x in range(size):
            for y in range(size):
                if not self.visible(actor,x,y):continue
                fact=self._call('native:territory','cell',x,y)
                kinds={}
                for b in snapshot.instances:
                    if b.site!=fact.tile.id or b.remaining != 0:continue
                    if fact.owner not in (None,actor) and b.kind not in ('native:castle','native:town-hall'):continue
                    kinds[b.kind]=kinds.get(b.kind,0)+1
                result.append(VisibleTile((x,y),fact.tile.terrain,fact.owner,tuple(kinds.items()),fact.resources if fact.owner in (None,actor) else ()))
        return tuple(result)


class VisionMod(GameMod):
    def mixins(self):return ()
    def needs(self):return ('native:territory','native:diplomacy','native:buildings')
    def provides(self):return ('native:vision',)
    def objects(self, world):return {'native:vision':Vision(lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))}
    def tools(self):return (Tool('view',lambda w,a:self.objects(w)['native:vision'].tiles(a),read_only=True),)
    def panels(self):return (Panel('view',lambda w,a:self.objects(w)['native:vision'].tiles(a)),)
    def rule_sections(self, world=None):return (RuleSection('视野','本国和同盟土地周围一格可见，哨塔扩展视野。他国可见地块只显示已落成的市政厅和城堡，不公开库存、资源容量或其他建筑。'),)
