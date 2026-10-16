"""Native distribution scenario and administrative APIs. No host game rules."""
import random
from engine.player import wire
from engine.session import SaveFormatError
from engine.scenario import GridSession
from .observer import NativeObserver

START_RES={'native:gold':1500,'native:food':20,'native:wood':60,'native:ore':10,
           'native:oil':0,'native:equipment':5,'native:supply':10}

class NativeSession(NativeObserver,GridSession):
    @property
    def arrival_distance(self):return 8 if self.grid()[0]>=32 else 3
    def entries(self):return self.cfg['nations']
    def identities(self):return tuple(n.id for n in self.world.nations.snapshot())
    def configure(self):
        seed=self.cfg.get('seed')
        if seed is None:seed=random.SystemRandom().randrange(2**31)
        self.world.territory.configure(self.cfg.get('map_size',60),seed)
    def grid(self):return self.world.territory.dimensions()
    def occupied(self):return [tile.tile.position for tile in self.world.territory.snapshot()]
    def validate_boundary(self):
        if any(wire(self.world.government.snapshot(a))['ended'] for a in self.actors()):
            raise SaveFormatError('Cannot save a partially played cycle')

    def finished(self):
        future=any(n.get('enabled',True) and n['id'] not in self.roster and n.get('appearance')=='scheduled' for n in self.entries())
        return len(self.actors())<=1 and len(self.roster)>1 and not future

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
        self.world.intelligence.emit('appearance',item['name']+' 登场',public=True)
        return actor
