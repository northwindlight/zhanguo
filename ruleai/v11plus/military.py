"""Use observed routes and native forecasts; production belongs to economy."""
from . import grouping,combat,targeting
from .pathfind import distance

def run(ledger,state,*,forecast=None):
    player=ledger.player;obs=player.observe();period=obs['period']
    armies=[u for u in obs['facts']['army'] if u['owner']==player.actor]
    groups=grouping.regroup(player,obs,state,armies,forecast=forecast)
    units={u['id']:u for u in armies}
    allies,_=targeting.relations(obs)
    knowledge={'view':obs['facts']['view'],'allies':allies,'unit_catalog':player.query('unit_catalog')}
    for cell,ids in groups:
        if ledger.full():break
        ready=[units[i] for i in ids if i in units and units[i]['born_turn']!=period and (not units[i]['engaged'] and units[i]['moved_turn']!=period or (units[i]['x'],units[i]['y'])==cell)]
        able=[u for u in ready if cell in {tuple(point) for point,_ in player.query('reachable',{'aid':u['id'],'attack':True})}]
        if able and combat.assess(player,cell,able,forecast=forecast)['winnable']:
            if ledger.do('attack',ids=[u['id'] for u in able],x=cell[0],y=cell[1]):continue
        for unit in ready:
            if unit['engaged'] or ledger.full():continue
            reach=player.query('reachable',{'aid':unit['id']})
            start=(unit['x'],unit['y'])
            options=[tuple(point) for point,_ in reach if tuple(point)!=start]
            if not options:continue
            target=min(options,key=lambda point:(distance(knowledge,unit['kind'],point,cell),point))
            if distance(knowledge,unit['kind'],target,cell)<distance(knowledge,unit['kind'],start,cell):ledger.do('move',aid=unit['id'],x=target[0],y=target[1])
