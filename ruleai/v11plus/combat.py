"""Client forecasts over public facts; optional research service is injected."""
from . import apimap
from copy import deepcopy
from .targeting import relations


def forecaster(player, observation):
    facts=observation['facts'];_,enemies=relations(observation)
    tiles={tuple(t['position']):t for t in facts['view']}
    units=facts.get('unit_catalog') or apimap.query(player,'unit_catalog')
    terrains={t['id']:t for t in facts.get('terrain_catalog') or apimap.query(player,'terrain_catalog')}
    buildings={b['id']:b for b in facts.get('building_catalog') or apimap.query(player,'building_catalog')}
    # These modifiers are published by the army catalog, rather than imported
    # from a domain module; the forecasting client owns only its approximation.
    def power(group):
        return sum(max(1,units[u['kind']]['atk']*(100-units[u['kind']]['retreat_attack_penalty'])//100)
                   if u.get('retreat_to') else units[u['kind']]['atk'] for u in group if u['hp']>0)
    def damage(amount, group):
        live=[u for u in group if u['hp']>0]
        if not live:return
        ratios=[(u.get('retreat_cover') if u.get('retreat_cover') is not None else 100)/100 for u in live]
        limits=[float('inf') if ratio==0 else u['hp']/ratio for u,ratio in zip(live,ratios)]
        shares=[0.0]*len(live);active={i for i,limit in enumerate(limits) if limit>0};remaining=float(amount)
        while active and remaining>1e-12:
            share=remaining/len(active);capped=[i for i in active if limits[i]-shares[i]<=share+1e-12]
            if not capped:
                for i in active:shares[i]+=share
                break
            spent=0.0
            for i in capped:
                take=min(share,max(0.0,limits[i]-shares[i]));shares[i]+=take;spent+=take;active.remove(i)
            remaining=max(0.0,remaining-spent)
        for u,share,ratio in zip(live,shares,ratios):u['hp']=max(0,u['hp']-share*ratio)
    def forecast(_player, cell, army):
        tile=tiles.get(tuple(cell))
        if tile is None:return {'known':False,'winnable':None,'rounds':None,'losses':None}
        if not terrains[tile['terrain']].get('claimable',True):return {'known':True,'winnable':False,'rounds':None,'losses':None,'reason':'impassable'}
        attackers=deepcopy(army)
        defenders=deepcopy([u for u in facts['army'] if (u['x'],u['y'])==tuple(cell) and u['hp']>0 and (u['owner']=='野人' or u['owner'] in enemies)])
        initial=sum(u['hp'] for u in attackers)
        for turn in range(13):
            if not any(u['hp']>0 for u in defenders):return {'known':True,'winnable':True,'rounds':turn,'losses':initial-sum(u['hp'] for u in attackers)}
            if not any(u['hp']>0 for u in attackers):break
            groups={}
            for u in defenders:
                if u['hp']>0:groups.setdefault(u['owner'],[]).append(u)
            outgoing=power(attackers);incoming=sum(power(group) for group in groups.values())
            for owner,group in groups.items():
                castle=dict(tile['buildings']).get('native:castle',0)*buildings.get('native:castle',{}).get('effects',{}).get('defense_per_level',0) if owner==tile['owner'] else 0
                defense=100-(100-terrains[tile['terrain']]['defense'])*(100-castle)//100
                damage(max(1,round(outgoing/len(groups)*(100-defense)/100)),group)
            damage(incoming,attackers)
        return {'known':True,'winnable':False,'rounds':None,'losses':initial}
    return forecast


def assess(player, cell, units, *, forecast=None):
    return (forecast or forecaster(player,player.observe()))(player,cell,units)


def needed(player, cell, units, *, forecast=None):
    forecast=forecast or forecaster(player,player.observe())
    for count in range(1,len(units)+1):
        result=forecast(player,cell,units[:count])
        if result['known'] and result['winnable']:return count,True
    return len(units)+1,False
