"""Mini owns its scenario, administration and player API assembly."""
from copy import deepcopy
import hashlib
import math
import random
from engine.identifiers import content_id
from engine.session import Session, SaveFormatError
from .game import GOLD, FOOD, WOOD, PLAIN, TOWN_HALL


class MiniSession(Session):
    def normalize(self,cfg):
        cfg=deepcopy(cfg)
        if not isinstance(cfg.get('nations'),list) or not cfg['nations']:
            raise ValueError('Mini requires a nonempty nations list')
        ids=set();names=set()
        for item in cfg['nations']:
            if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].strip():
                raise ValueError('Mini actor requires a display name')
            item['id']=content_id(item.get('id') or 'mini:actor-'+hashlib.sha256(item['name'].encode()).hexdigest()[:12])
            if item['id'] in ids or item['name'] in names:raise ValueError('Duplicate mini actor')
            ids.add(item['id']);names.add(item['name'])
            item.setdefault('client','mini')
        return cfg

    def entries(self):return self.cfg['nations']
    def actors(self):return tuple(self.world.nation_actors)
    def identities(self):return tuple(n.id for n in self.world.nations.snapshot())
    def clock(self):return self.world.government.period()
    def configure(self):self.world.territory.configure(self.cfg.get('map_size',16),self.cfg.get('seed',0))
    def validate_boundary(self):
        if any(self.world.government.snapshot(actor)['ended'] for actor in self.actors()):
            raise SaveFormatError('Cannot save a partially played mini cycle')
    def records(self):return self.world.intelligence.to_save()
    def announce(self,text):return self.world.intelligence.emit('announcement',text,public=True)
    def send(self,actor,text):return self.world.intelligence.emit('observer-letter',text,parties=(actor,))

    def _positions(self,count):
        size,_=self.world.territory.dimensions()
        if count==1:return [(size//2,size//2)]
        return [(max(1,min(size-2,round(size/2+size*.31*math.cos(2*math.pi*i/count)))),
                 max(1,min(size-2,round(size/2+size*.31*math.sin(2*math.pi*i/count))))) for i in range(count)]

    def spawn(self,item,position):
        if not isinstance(position,(list,tuple)) or len(position)!=2 or any(type(v) is not int for v in position):
            raise ValueError('Invalid mini start position')
        x,y=position;self.world.territory.check(x,y)
        size,_=self.world.territory.dimensions()
        points=[(x+dx,y+dy) for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1)) if 0<=x+dx<size and 0<=y+dy<size]
        if any(self.world.territory.cell(*p).tile.id for p in points):raise ValueError('Mini start overlaps territory')
        actor=item['id']
        self.world.nations.create(actor,item['name'],{GOLD:1500,FOOD:20,WOOD:60,**item.get('resources',{})})
        for tx,ty in points:
            site=f'{actor}/land-{tx}-{ty}'
            self.world.territory.create(site,actor,tx,ty,terrain=PLAIN,core=actor)
            if (tx,ty)==(x,y):self.world.buildings.seed(site,TOWN_HALL)
        self.world.succession.enroll(actor)
        self.world.intelligence.emit('appearance',item['name']+' 登场',public=True)
        return actor

    def add(self,name):
        item=next((n for n in self.entries() if name in (n['id'],n['name'])),None)
        if item is None:raise ValueError('Mini actor is not configured')
        if item['id'] in self.roster:raise ValueError('Mini actor already joined')
        size,seed=self.world.territory.dimensions()
        positions=[(x,y) for x in range(1,size-1) for y in range(1,size-1)]
        random.Random(f'{seed}:{item["id"]}').shuffle(positions)
        occupied=[cell.tile.position for cell in self.world.territory.snapshot()]
        position=item.get('position') or next((p for p in positions if all(max(abs(p[0]-x),abs(p[1]-y))>=3 for x,y in occupied)),None)
        if position is None:raise ValueError('No room for mini actor')
        return self.join(item,position)

    def scheduled(self):
        for item in self.entries():
            if item['id'] not in self.roster and item.get('enabled',True) and item['appearance']=='scheduled':
                due=item.get('enable_turn')
                if due is not None:
                    if type(due) is not int or due<0:raise ValueError('Invalid mini arrival period')
                    if self.clock()>=due:self.add(item['id'])

    def board_rows(self):
        return [{'name':self.roster[a]['name'],'land':len(self.world.territory.owned(a)),
                 'army':len(self.world.military.of(a)),'gold':self.world.accounts.balance(a,GOLD)} for a in self.actors()]
