"""Optional square-grid scenario policy, shared by native and mini distributions."""
import math
import random
from .runtime import ModError
from .session import Session


class GridSession(Session):
    def grid(self):
        """Return (size, seed)."""
        raise ModError('方格场景须实现 grid()')

    def occupied(self):
        """Return occupied (x, y) cells."""
        raise ModError('方格场景须实现 occupied()')

    def valid_position(self, position):
        """Optional distribution placement policy; no terrain rules here."""
        return True

    def _positions(self,count):
        size,_=self.grid()
        if size<3 and count>1:raise ValueError("地图太小，无法安置多个身份")
        if count==1:return [(size//2,size//2)]
        radius=size*.31
        return [(min(size-2,max(1,round(size/2+radius*math.sin(2*math.pi*i/count)))),
                 min(size-2,max(1,round(size/2+radius*-math.cos(2*math.pi*i/count))))) for i in range(count)]

    def add(self,name):
        item=next((n for n in self.entries() if name in (n['id'],n['name'])),None)
        if item is None:raise ValueError('该身份未在配置中声明')
        if item['id'] in self.roster:raise ValueError('身份已登场，历史身份不能复用')
        position=item.get('position')
        if position is None:
            size,seed=self.grid()
            positions=[(x,y) for x in range(1,size-1) for y in range(1,size-1)]
            random.Random(f'{seed}:{item["id"]}').shuffle(positions)
            occupied=tuple(self.occupied())
            position=next((p for p in positions if self.valid_position(p) and all(max(abs(p[0]-x),abs(p[1]-y))>=getattr(self,'arrival_distance',3) for x,y in occupied)),None)
        if position is None:raise ValueError('没有足够空间安置新身份')
        return self.join(item,position)

    def scheduled(self):
        pending=[item for item in self.entries() if item['id'] not in self.roster
                 and item.get('enabled',True) and item.get('appearance')=='scheduled']
        if not pending:return
        _,seed=self.grid();period=self.clock()
        for item in pending:
            lo=item.get('enable_turn')
            if lo is None:continue
            hi=item.get('enable_turn_max',lo)
            if type(lo) is not int or type(hi) is not int or lo<0 or hi<lo:raise ValueError('Invalid arrival period')
            if period>=random.Random(f'{seed}:{item["id"]}').randint(lo,hi):self.add(item['id'])
