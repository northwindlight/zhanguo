"""地块持有地形、地名和核心事实；当前归属仅由建筑站点维护。"""
from dataclasses import dataclass, replace
from engine.coordinates import coordinates
from engine.building import integer
from engine.identifiers import content_id
from engine.runtime import ModError
from engine.state import active_unit


@dataclass(frozen=True)
class Terrain:
    id: str
    defense: int
    build_penalty: int
    cavalry_cost: int
    glyph: str = '?'


@dataclass(frozen=True)
class Tile:
    id: str
    position: tuple
    terrain: str
    core: str | None
    name: str


@dataclass(frozen=True)
class TileFact:
    tile: Tile
    owner: str | None
    resources: tuple


class Territory:
    def __init__(self, types, call, size=80, seed=0):
        self._types = {t.id:t for t in types};self._call = call
        self._size,self._seed = integer(size,1),integer(seed)
        self._tiles = {};self._positions = {};self._cells = {}
        self._world,self._domain = None,None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Territory already bound')
        self._world,self._domain = world,world.runtime.state_owners[key]
        return self

    def _write(self):
        if active_unit.get() not in (None,self._domain):raise ModError('Use territory interface')

    def configure(self, size, seed):
        self._write();integer(size,1);integer(seed)
        if self._tiles:raise ValueError('Cannot replace map with materialized tiles')
        self._size,self._seed = size,seed;self._cells.clear()

    def dimensions(self):return self._size,self._seed

    def check(self, x, y):
        integer(x);integer(y)
        if x>=self._size or y>=self._size:raise ValueError(f'坐标超出地图范围：地图是 {self._size}×{self._size}，坐标从 0 开始')
        return x,y

    def neighbors(self, x, y):
        self.check(x,y)
        return tuple((x+dx,y+dy) for dx in (-1,0,1) for dy in (-1,0,1)
                     if (dx or dy) and 0<=x+dx<self._size and 0<=y+dy<self._size)

    def resolve(self, text):
        """地块引用：坐标串（"4 12"、"4,12"）或地名，两者都行。

        玩家面向的坐标从 0 开始，是内部坐标；地名由地图决定（全图唯一），
        所以"用地名"和"用坐标"指的是同一格。
        """
        if not isinstance(text,str) or not text.strip():
            raise ValueError('地块引用必须是地名或 "x y" 坐标串')
        point = coordinates(text)
        if point is not None:
            self.check(*point);return point
        position = self._call('map:generation','where',self._seed,self._size,text.strip())
        if position is None:raise ValueError(f'没有这个地名：{text.strip()}')
        if self.owner(*position) is None:raise ValueError('无主地块只能用坐标引用')
        return position

    def cell(self, x, y):
        self.check(x,y)
        key = self._positions.get((x,y))
        if key is not None:return self.get(key)
        if (x,y) not in self._cells:
            cell = self._call('map:generation','cell',self._seed,self._size,x,y)
            self._cells[x,y] = TileFact(Tile('',cell.position,cell.terrain,None,cell.name),None,cell.resources)
        return self._cells[x,y]

    def terrain(self, x, y):return self._types[self.cell(x,y).tile.terrain]
    def owner(self, x, y):return self.cell(x,y).owner

    def create(self, tile_id, owner, x, y, *, terrain=None, core=None):
        self._write();content_id(tile_id);self.check(x,y)
        if tile_id in self._tiles or (x,y) in self._positions:raise ValueError('Duplicate tile identity or position')
        if owner is not None and owner not in self._call('native:nation-inspection','ids'):raise ValueError('Unknown living owner')
        if core is not None:self._call('native:nation-inspection','get',core)
        if terrain is not None and terrain not in self._types:raise ValueError('Unknown terrain')
        cell = self._call('map:generation','cell',self._seed,self._size,x,y,terrain)
        # 地名是地图的属性（见 map_generation/names.py），不随地块存档走。
        tile = Tile(tile_id,(x,y),cell.terrain,core,cell.name)
        self._call('native:buildings','register_site',tile_id,owner,cell.position,
                   resources=dict(cell.resources),penalty=self._types[cell.terrain].build_penalty)
        self._tiles[tile_id] = tile;self._positions[cell.position] = tile_id
        return self.get(tile_id)

    def create_claim(self, tile_id, owner, x, y):
        """创建被占领的无主地块，并将核心设为占领者。"""
        return self.create(tile_id,owner,x,y,core=owner)

    def get(self, tile_id):
        tile = self._tiles[tile_id];site=self._call('native:buildings','site',tile_id)
        return TileFact(tile,site.owner,site.resources)

    def transfer(self, tile_id, owner):
        self._write();self._tiles[tile_id]
        if owner is not None and owner not in self._call('native:nation-inspection','ids'):raise ValueError('Unknown living owner')
        self._call('native:buildings','transfer_site',tile_id,owner)
        return self.get(tile_id)

    def claim(self, tile_id, owner):
        """占领无主地块，并按旧规则将该地块设为新领主的核心。"""
        self._write();self._tiles[tile_id]
        if owner not in self._call('native:nation-inspection','ids'):raise ValueError('Unknown living owner')
        if self.get(tile_id).owner is not None:raise ValueError('Cannot claim an owned tile')
        self._call('native:buildings','transfer_site',tile_id,owner)
        self._tiles[tile_id] = replace(self._tiles[tile_id],core=owner)
        return self.get(tile_id)

    def snapshot(self):return tuple(self.get(key) for key in self._tiles)
    def owned(self, owner):return tuple(sorted((t for t in self.snapshot() if t.owner==owner),key=lambda t:t.tile.position))

    def snapshot_cores(self, owners):
        self._write()
        owners = set(owners)
        for key,tile in tuple(self._tiles.items()):
            owner = self.get(key).owner
            if owner in owners:self._tiles[key] = replace(tile,core=owner)

    def release(self, owner):
        self._write()
        for fact in self.owned(owner):self.transfer(fact.tile.id,None)

    def to_save(self):
        return {'size':self._size,'seed':self._seed,'tiles':[
            {'id':t.id,'position':list(t.position),'terrain':t.terrain,'core':t.core}
            for t in self._tiles.values()]}

    def restore(self, data):
        self._write()
        if not isinstance(data,dict) or set(data)!={'size','seed','tiles'} or not isinstance(data['tiles'],list):raise ValueError('Invalid territory save')
        size,seed = integer(data['size'],1),integer(data['seed']);tiles,positions = {},{}
        for row in data['tiles']:
            if not isinstance(row,dict) or set(row)!={'id','position','terrain','core'}:raise ValueError('Invalid tile record')
            content_id(row['id'])
            if not isinstance(row['position'],list) or len(row['position'])!=2:raise ValueError('Invalid tile position')
            pos = tuple(integer(v) for v in row['position'])
            if any(v>=size for v in pos) or row['id'] in tiles or pos in positions or row['terrain'] not in self._types:raise ValueError('Invalid tile identity/terrain')
            if row['core'] is not None:self._call('native:nation-inspection','get',row['core'])
            site = self._call('native:buildings','site',row['id'])
            if site.position!=pos or site.penalty!=self._types[row['terrain']].build_penalty:raise ValueError('Tile/site facts disagree')
            if site.owner is not None and site.owner not in self._call('native:nation-inspection','ids'):raise ValueError('Unknown tile owner')
            # Terrain is stored because a start can override it; the name is the
            # map's own property and is read back from the map.
            name = self._call('map:generation','cell',seed,size,pos[0],pos[1]).name
            tiles[row['id']] = Tile(row['id'],pos,row['terrain'],row['core'],name);positions[pos]=row['id']
        self._size,self._seed,self._tiles,self._positions = size,seed,tiles,positions
        self._cells.clear()

    def __deepcopy__(self,memo):return self.snapshot()
