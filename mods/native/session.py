"""Native distribution scenario and administrative APIs. No host game rules."""
from copy import deepcopy
import hashlib
import math
import random
from engine.identifiers import content_id
from engine.player import wire
from engine.session import Session,SaveFormatError

START_RES={'native:gold':1500,'native:food':20,'native:wood':60,'native:ore':10,
           'native:oil':0,'native:equipment':5,'native:supply':10}

def nation_id(item):
    ident=item.get('id')
    if ident is None:ident='scenario:nation-'+hashlib.sha256(item['name'].encode()).hexdigest()[:12]
    return content_id(ident)


def normalize_config(cfg):
    cfg=deepcopy(cfg)
    if not isinstance(cfg.get('nations'),list) or not cfg['nations']:raise ValueError('nations must be a nonempty list')
    ids=set();names=set()
    for item in cfg['nations']:
        if not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].strip():raise ValueError('Nation requires a display name')
        item['id']=nation_id(item)
        if item['id'] in ids or item['name'] in names:raise ValueError('Duplicate configured nation')
        ids.add(item['id']);names.add(item['name'])
        kind=item.get('client') or ('llm' if item.get('api_key') else 'rule')
        item['client']=kind
        # Context settings and transport defaults are inherited without storing
        # credentials in the session save.
        for key,value in cfg.items():
            if key.startswith('ctx_') or key in ('rule_ai','provider','max_steps','parliament','language','ui_catalog','vision','map_max_px','map_estimated_tokens','ui_mods'):
                item.setdefault(key,value)
    return cfg


class NativeSession(Session):
    def normalize(self,cfg):return normalize_config(cfg)
    def entries(self):return self.cfg['nations']
    def actors(self):return tuple(self.world.nation_actors)
    def identities(self):return tuple(n.id for n in self.world.nations.snapshot())
    def clock(self):return self.world.government.period()
    def configure(self):
        seed=self.cfg.get('seed')
        if seed is None:seed=random.SystemRandom().randrange(2**31)
        self.world.territory.configure(self.cfg.get('map_size',60),seed)
    def after_spawn(self):self.world.market.set_participants(len(self.actors()))
    def validate_boundary(self):
        if any(wire(self.world.government.snapshot(a))['ended'] for a in self.actors()):
            raise SaveFormatError('Cannot save a partially played cycle')
    def records(self):return self.world.intelligence.to_save()
    def announce(self,text):return self.world.intelligence.emit('announcement',text,public=True)
    def send(self,actor,text):return self.world.intelligence.emit('observer-letter',text,parties=(actor,))

    def _positions(self,count):
        size,_=self.world.territory.dimensions()
        if count==1:return [(size//2,size//2)]
        points=[]
        radius=size*.31
        for i in range(count):
            angle=2*math.pi*i/count
            points.append((min(size-2,max(1,round(size/2+radius*math.cos(angle)))),
                           min(size-2,max(1,round(size/2+radius*math.sin(angle))))))
        return points


    def add(self,name):
        item=next((n for n in self.entries() if name in (n['id'],n['name'])),None)
        if item is None:raise ValueError('国家未在配置中声明')
        if item['id'] in self.roster:raise ValueError('国家已登场，历史身份不能复用')
        size,seed=self.world.territory.dimensions();rng=random.Random(f'{seed}:{item["id"]}')
        positions=[(x,y) for x in range(1,size-1) for y in range(1,size-1)]
        rng.shuffle(positions)
        occupied=[t.tile.position for t in self.world.territory.snapshot()]
        position=item.get('position') or next((p for p in positions if all(max(abs(p[0]-x),abs(p[1]-y))>=3 for x,y in occupied)),None)
        if position is None:raise ValueError('没有足够空间安置新国家')
        return self.join(item,position)


    def scheduled(self):
        size,seed=self.world.territory.dimensions();period=self.world.government.period()
        for item in self.entries():
            if item['id'] in self.roster or not item.get('enabled',True) or item.get('appearance')!='scheduled':continue
            lo=item.get('enable_turn')
            if lo is None:continue
            hi=item.get('enable_turn_max',lo)
            if type(lo) is not int or type(hi) is not int or lo<0 or hi<lo:raise ValueError('Invalid arrival period')
            due=random.Random(f'{seed}:{item["id"]}').randint(lo,hi)
            if period>=due:self.add(item['id'])


    def board_rows(self):
        rows=[]
        for actor in self.world.nation_actors:
            obs=self.player(actor).observe();stocks={s['id']:s['amount'] for s in obs['facts']['econ']['stocks']}
            rows.append({'name':self.roster[actor]['name'],'land':len(obs['facts']['land']),
                         'army':sum(u['owner']==actor for u in obs['facts']['army']),'gold':stocks.get('native:gold',0)})
        return rows


    def spawn(self,item,position):
        actor=item['id'];size,_=self.world.territory.dimensions()
        if not isinstance(position,(tuple,list)) or len(position)!=2 or any(type(v) is not int for v in position):raise ValueError('Invalid start position')
        x,y=position;self.world.territory.check(x,y)
        points=[(x+dx,y+dy) for dx,dy in ((0,0),(1,0),(-1,0),(0,1),(0,-1)) if 0<=x+dx<size and 0<=y+dy<size]
        if any(self.world.territory.cell(*point).tile.id for point in points):raise ValueError('Start overlaps existing territory')
        polity=item.get('polity')
        if polity and self.runtime.find_policy(polity) is None:raise ValueError('Polity module missing')
        period=self.clock()
        self.world.nations.create(actor,item['name'],{**START_RES,**item.get('resources',{})})
        for tx,ty in points:
            site=f'{actor}/land-{tx}-{ty}'
            self.world.territory.create(site,actor,tx,ty,terrain='native:plain' if (tx,ty)==(x,y) else None,core=actor)
            if (tx,ty)==(x,y):
                self.world.buildings.seed(site,'native:town-hall')
                capital=site
        if polity:
            start={key:item[field] for key,field in (('Gold','start_gold'),('Supply','start_supply'),('Cavalry','start_cavalry')) if field in item}
            self.runtime.activate_actor(self.world,actor,polity,site=capital,start=start)
        self.world.succession.enroll(actor)
        self.world.market.set_participants(len(self.world.nation_actors))
        self.world.intelligence.emit('appearance',item['name']+' 登场',public=True)
        return actor
