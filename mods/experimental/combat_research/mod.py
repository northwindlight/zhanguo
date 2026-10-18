"""Private research capability. No player tools or model-facing schema."""
from copy import deepcopy
from engine.runtime import GameMod
from .solver import solve


class Forecast:
    def __init__(self,call,settings,die):self.call=call;self.settings=settings;self.die=die
    def assess(self,actor,ids,x,y):
        self.call('native:territory','check',x,y)
        if not isinstance(ids,(list,tuple)) or not ids or any(type(i) is not int for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('Unique own unit IDs required')
        visible=self.call('native:army','visible_units',actor)
        owned={u.id:u for u in visible if u.owner==actor and u.alive}
        if any(i not in owned for i in ids):raise ValueError('Own living units required')
        if not self.call('native:vision','visible',actor,x,y):
            return {'research':True,'known':False,'winnable':None,'probabilities':None}
        selected=[deepcopy(owned[i]) for i in ids]
        troops=[u for u in visible if (u.x,u.y)==(x,y) and u.alive and not (u.owner==actor and u.id in ids)]
        for u in selected:u.engaged=True
        troops+=selected;groups={}
        for u in troops:groups.setdefault(u.owner,[]).append(u)
        active={u.owner for u in troops if u.engaged and u.owner!='野人'}
        enemies={owner:tuple(other for other in groups if other!=owner and
                    ((owner=='野人' and other in active) or (other=='野人' and owner in active) or
                     owner!='野人' and other!='野人' and self.call('native:diplomacy','war_between',owner,other))) for owner in groups}
        tile=self.call('native:territory','cell',x,y);terrain=self.call('native:territory','terrain',x,y)
        castle=0
        if tile.tile.id:
            castle=self.call('native:buildings','effects',tile.tile.id,'native:castle').get('defense_per_level',0)
            castle*=self.call('native:buildings','count',tile.tile.id,'native:castle')
        order=tuple(sorted(groups));initial=[]
        for owner in order:
            defense=self.call('native:army','defense_pct',terrain.defense,castle if owner==tile.owner else 0)
            cover=defense if owner==tile.owner or owner=='野人' or owner not in active else 0
            initial.append(tuple((self.call('native:army','faction_power',[u]),u.hp,False,
                                  u.retreat_cover if u.retreat_cover is not None else 100,cover) for u in groups[owner]))
        prototype=selected[0]
        def spread(row,damage):
            if not row:return ()
            units=[]
            for token,hp,retreat,cover in row:
                u=deepcopy(prototype);u.hp=hp;u.retreat_cover=cover;units.append(u)
            self.call('native:army','spread_damage',damage,units)
            return tuple((row[i][0],u.hp,row[i][2],row[i][3]) for i,u in enumerate(units) if u.alive)
        def power(sig):
            total=sum(token for token,retreat in sig)
            return tuple(max(1,total*(100+self.die[face])//100) for face in sorted(self.die))
        odds=solve(order,enemies,tuple(initial),power,spread,
                   max_states=self.settings['MaxStates'],max_rounds=self.settings['MaxRounds'])
        return {'research':True,'known':True,'winnable':odds['p_win'][actor]>=self.settings['WinThreshold'],
                'probabilities':odds,'win_threshold':self.settings['WinThreshold']}


class CombatResearchMod(GameMod):
    def configure(self,declaration,path):
        super().configure(declaration,path)
        from engine.config import Field,Schema
        fields={'MaxStates':Field((int,),minimum=1,maximum=50000),
                'MaxRounds':Field((int,),minimum=1,maximum=128),
                'WinThreshold':Field((int,float),minimum=0,maximum=1)}
        settings=declaration.get('Research',{})
        if set(settings)!=set(fields):raise ValueError('Research must declare all DP limits')
        Schema(fields).normalize(settings,path='Research')

    def mixins(self):return ()
    def objects(self,world):
        return {'research:combat-probabilities':Forecast(
            lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),self.declaration['Research'],
            self.runtime.number('army.combat-die'))}
