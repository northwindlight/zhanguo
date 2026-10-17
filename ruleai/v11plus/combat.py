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
        if any(u.get('retreat_cover') not in (None,100) for u in live):
            q,r=divmod(amount,len(live))
            for i,u in enumerate(live):u['hp']=max(0,u['hp']-(q+(i<r))*(u.get('retreat_cover') if u.get('retreat_cover') is not None else 100)//100)
        else:
            while amount>0 and live:
                q,r=divmod(amount,len(live))
                for i,u in enumerate(live):
                    take=min(u['hp'],q+(i<r));u['hp']-=take;amount-=take
                live=[u for u in live if u['hp']>0]
    def forecast(_player, cell, army):
        tile=tiles.get(tuple(cell))
        if tile is None:return {'known':False,'winnable':None,'rounds':None,'losses':None}
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
