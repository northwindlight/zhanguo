"""Player-scoped previews using the same army rules as command execution.

Only current player facts are supplied to navigation. Unknown terrain is an
optimistic attempt; purchased/historical maps never become current blockers.
"""
from . import navigation


class PlayerMap:
    def __init__(self, observation, size):
        self.owner=observation['actor'];self.period=observation['period'];self.size=size
        facts=observation['facts']
        self.tiles={tuple(t['position']):t for t in facts['view']}
        self.units=facts['army'];self.catalog=facts['unit_catalog']
        self.terrain={t['id']:t for t in facts['terrain_catalog']}
        self.relations={n['id']:n['relation'] for n in facts['foreign']['countries']}
        self._planned_paths={}

    def relation(self, owner):
        return 'own' if owner==self.owner else 'wild' if owner is None else self.relations.get(owner,'neutral')

    def at(self, p):
        return [u for u in self.units if (u['x'],u['y'])==p and u['hp']>0]

    def attacked(self, p):
        return any(u.get('engaged') and u['owner']!='野人' and self.relation(u['owner'])=='war' for u in self.at(p))

    def movable(self, p):
        if p not in self.tiles:return True
        hostile=any(u['owner']!='野人' and self.relation(u['owner'])=='war' for u in self.at(p))
        return navigation.can_pass(self.relation(self.tiles[p].get('owner')),hostile)

    def target_reason(self, p, attack):
        if p not in self.tiles:return ''
        tile=self.tiles[p];owner=tile.get('owner')
        busy=[self.relation(u['owner']) for u in self.at(p) if u.get('engaged') and u['owner'] not in (self.owner,'野人')]
        return navigation.target_reason(self.relation(owner),owner,self.terrain[tile['terrain']]['passable'],self.movable(p),busy,attack)

    def neighbors(self, p):
        # Match the territory interface ordering for stable route ties.
        return tuple((p[0]+dx,p[1]+dy) for dx in (-1,0,1) for dy in (-1,0,1)
            if (dx or dy) and 0<=p[0]+dx<self.size and 0<=p[1]+dy<self.size)

    def cost(self, unit, p, retreat=False):
        costs=self.catalog[unit['kind']]['move_cost']
        if p not in self.tiles:return 1 if retreat else min(c for c in costs.values() if c is not None)
        kind=self.tiles[p]['terrain']
        if not self.terrain[kind]['passable']:return None
        return 1 if retreat else costs.get(kind)

    def planned_route(self, unit, start, target, mode):
        """Standing-order itinerary over public facts, never the actual hidden map.

        Attack itineraries may plan beyond enemy cells. Actual steps still stop
        at the first combat endpoint and use the normal one-turn candidates.
        """
        cache=(unit['kind'],start,target,mode)
        if cache in self._planned_paths:return self._planned_paths[cache]
        attack=mode=='attack' and not (target in self.tiles and self.relation(self.tiles[target].get('owner')) in ('own','ally'))
        reason=self.target_reason(target,attack)
        if reason:return [],reason
        parents={};cost=lambda p:self.cost(unit,p)
        maximum=max(c for c in self.catalog[unit['kind']]['move_cost'].values() if c is not None)
        best=navigation.reachable(start,self.size*self.size*maximum,self.neighbors,cost,
            lambda p:self.movable(p) or mode=='attack' and not self.target_reason(p,True),
            lambda p:not self.target_reason(p,True),attack=mode=='attack',parents=parents,goal=target)
        result=(navigation.route(start,target,parents),'') if target in best else ([], '当前已知条件下没有可规划的通路')
        self._planned_paths[cache]=result
        return result

    def public_route(self, points):
        cells={tuple(p) for p in points}
        for a,b in zip(points,points[1:]):
            if a[0]!=b[0] and a[1]!=b[1]:cells.update(((a[0],b[1]),(b[0],a[1])))
        uncertain=not cells<=self.tiles.keys()
        return {'uncertain':uncertain,'points':[] if uncertain else points}

    def commands(self, u):
        p=(u['x'],u['y']);attacked=self.attacked(p)
        move=navigation.action_reason(u,self.period,attacked=attacked)
        attack=navigation.action_reason(u,self.period,attack=True)
        current=navigation.action_reason(u,self.period,attack=True,current=True) or self.target_reason(p,True)
        retreat=navigation.retreat_reason(u,self.period,attacked)
        disband='正在交战或受到进攻，须先撤退并等待结算' if u.get('engaged') or attacked else ''
        return {k:{'available':not reason,'reason':reason} for k,reason in
                (('move',move),('attack','' if not current else attack),('retreat',retreat),('disband',disband))}


def command_states(observation, size):
    view=PlayerMap(observation,size)
    return {u['id']:view.commands(u) for u in view.units if u['owner']==view.owner and u['hp']>0}


def candidates(observation, ids, mode, size):
    if mode not in ('move','attack','retreat'):raise ValueError('未知军令模式')
    if (not isinstance(ids,list) or not ids or any(type(i) is not int or i<1 for i in ids) or len(set(ids))!=len(ids)):
        raise ValueError('请选择不重复的本国军队编号')
    view=PlayerMap(observation,size)
    own={u['id']:u for u in view.units if u['owner']==view.owner and u['hp']>0}
    if any(i not in own for i in ids):raise ValueError('选中军队已不存在或不属于本国')
    paths={};reasons={}
    for aid in ids:
        u=own[aid];start=(u['x'],u['y']);definition=view.catalog[u['kind']]
        attack=mode=='attack';retreat=mode=='retreat'
        reason=navigation.retreat_reason(u,view.period,view.attacked(start)) if retreat else navigation.action_reason(u,view.period,attack=attack,attacked=view.attacked(start))
        reasons[aid]=reason
        speed=1 if retreat else definition['speed'];costs=definition['move_cost']
        cost=lambda p:view.cost(u,p,retreat)
        search=dict(start=start,speed=speed,neighbors=view.neighbors,cost=cost,movable=view.movable,
            target_ok=lambda p:p not in view.tiles or view.relation(view.tiles[p].get('owner'))!='neutral',attack=attack)
        parents={};best=navigation.reachable(**search,parents=parents)
        known_parents={};known=navigation.reachable(**search,visible=set(view.tiles),parents=known_parents) if start in view.tiles else {}
        paths[aid]={}
        for point in sorted(best):
            current=point==start
            if current and not attack:continue
            allowed=navigation.action_reason(u,view.period,attack=True,current=True) if attack and current else reason
            if allowed or view.target_reason(point,attack):continue
            points=navigation.route(start,point,known_parents if point in known else parents)
            cells={tuple(p) for p in points}
            for a,b in zip(points,points[1:]):
                if a[0]!=b[0] and a[1]!=b[1]:cells.update(((a[0],b[1]),(b[0],a[1])))
            uncertain=not cells<=view.tiles.keys()
            # A route through unknown cells is never advertised as verified.
            paths[aid][point]={'uncertain':uncertain,'points':[] if uncertain else points}
    targets=[]
    for point in sorted({p for rows in paths.values() for p in rows}):
        execute=[aid for aid in ids if point in paths[aid]]
        targets.append({'position':list(point),'ids':execute,
            'uncertain':any(paths[aid][point]['uncertain'] for aid in execute),
            'routes':[{'aid':aid,**paths[aid][point]} for aid in execute],
            'excluded':[{'aid':aid,'reason':reasons[aid] or '已知路径、目标条件或移动力不足'} for aid in ids if aid not in execute]})
    return {'mode':mode,'selected':list(ids),'targets':targets,
            'units':[{'aid':aid,'reason':reasons[aid],'commands':view.commands(own[aid])} for aid in ids]}
