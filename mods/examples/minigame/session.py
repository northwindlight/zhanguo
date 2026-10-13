"""Mini owns its scenario, administration and player API assembly."""
from engine.session import SaveFormatError
from engine.scenario import GridSession
from .game import GOLD, FOOD, WOOD, PLAIN, TOWN_HALL


class MiniSession(GridSession):
    def entries(self):return self.cfg['nations']
    def identities(self):return tuple(n.id for n in self.world.nations.snapshot())
    def configure(self):self.world.territory.configure(self.cfg.get('map_size',16),self.cfg.get('seed',0))
    def grid(self):return self.world.territory.dimensions()
    def occupied(self):return [cell.tile.position for cell in self.world.territory.snapshot()]
    def validate_boundary(self):
        if any(self.world.government.snapshot(actor)['ended'] for actor in self.actors()):
            raise SaveFormatError('Cannot save a partially played mini cycle')

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

    def board_rows(self):
        return [{'name':self.roster[a]['name'],'land':len(self.world.territory.owned(a)),
                 'army':len(self.world.military.of(a)),'gold':self.world.accounts.balance(a,GOLD)} for a in self.actors()]
