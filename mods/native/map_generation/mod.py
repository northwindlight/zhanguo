from dataclasses import dataclass
from engine.building import integer
from engine.runtime import GameMod, Tool
from .algorithm import MapGen
from .parameters import VALUES

TERRAIN_IDS = {'平原':'native:plain','森林':'native:forest','丘陵':'native:hill','山地':'native:mountain','沙漠':'native:desert'}
RESOURCE_IDS = {'矿石':'native:ore','黄金':'native:gold','耕地':'native:farmland','石油':'native:oil','木头':'native:wood'}


@dataclass(frozen=True)
class MapCell:
    position: tuple
    terrain: str
    resources: tuple
    name: str


class MapGenerator:
    def cell(self, seed, size, x, y, terrain=None):
        integer(seed);integer(size,1);integer(x);integer(y)
        if x>=size or y>=size:raise ValueError('Position outside map')
        key = (seed,size)
        if getattr(self,'_key',None)!=key:self._grid = MapGen(seed,size);self._key=key
        old = self._grid.terrain(x,y) if terrain is None else next((k for k,v in TERRAIN_IDS.items() if v==terrain),None)
        if old is None:raise ValueError('Unknown terrain')
        resources = self._grid.resources_as(x,y,old)
        return MapCell((x,y),TERRAIN_IDS[old],tuple((RESOURCE_IDS[k],v) for k,v in resources.items()),
                       self._grid.name(x,y))


    def where(self, seed, size, name):
        if not isinstance(name,str) or not name:raise ValueError('地名必须是非空字符串')
        integer(seed);integer(size,1)
        key = (seed,size)
        if getattr(self,'_key',None)!=key:self._grid = MapGen(seed,size);self._key=key
        return self._grid.where(name)


class MapGenerationMod(GameMod):
    def mixins(self):return ()
    def numbers(self):return {key:value for key,value in VALUES.items() if key!='MAPGEN_SHIFT_MIX'}
    def objects(self, world):
        # Generator cache is derived, scoped to each world, never persisted.
        if not hasattr(world,'_map_generator'):world._map_generator = MapGenerator()
        return {'map:generation':world._map_generator}
