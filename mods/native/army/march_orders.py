"""Persistent unit itineraries; execution reuses ordinary army commands."""
from dataclasses import dataclass,replace

from engine.building import integer
from .candidates import PlayerMap,candidates
from . import navigation


@dataclass(frozen=True)
class Waypoint:
    mode: str
    position: tuple


@dataclass(frozen=True)
class MarchOrder:
    owner: str
    aid: int
    nodes: tuple
    auto_heal: bool = True
    healing: bool = False
    last_period: int = -1


class MarchOrders:
    def has_march_orders(self, owner):return any(o.owner==owner for o in self._march_orders.values())
    def _waypoints(self, nodes):
        if not isinstance(nodes,list) or len(nodes)>64:raise ValueError('路径须为最多64个节点的数组')
        result=[]
        for node in nodes:
            if not isinstance(node,dict) or set(node)!={'mode','position'} or node['mode'] not in ('move','attack'):
                raise ValueError('节点需要mode（move/attack）与position坐标')
            point=node['position']
            if not isinstance(point,(list,tuple)) or len(point)!=2:raise ValueError('节点需要两个整数坐标')
            for n in point:integer(n)
            self._call('native:territory','check',*point)
            result.append(Waypoint(node['mode'],tuple(point)))
        return tuple(result)

    def enqueue_orders(self, owner, ids, nodes, observation, *, append=True, auto_heal=True, refresh=None):
        self._action(owner)
        if type(append) is not bool or type(auto_heal) is not bool:raise ValueError('append和auto_heal须为布尔值')
        units,items=self._selection(owner,ids);self._reject_batch(items)
        additions=self._waypoints(nodes);pending={};view=PlayerMap(observation,self._units.size)
        if view.owner!=owner:raise ValueError('军令规划必须使用本国观察')
        for node in additions:
            if node.position in view.tiles and not view.terrain[view.tiles[node.position]['terrain']]['passable']:
                raise ValueError('已知高山或海洋不可作为军令节点')
        for unit in units:
            old=self._march_orders.get((owner,unit.id))
            route=(old.nodes if append and old else ())+additions
            if not route or len(route)>64:raise ValueError('每支军队须保留1至64个节点')
            pending[(owner,unit.id)]=MarchOrder(owner,unit.id,route,auto_heal,
                bool(auto_heal and old and old.healing),old.last_period if append and old else -1)
        self._march_orders.update(pending)
        started=self.advance_march_orders(owner,observation,ids=ids,refresh=refresh)
        return {'status':'effective','units':list(ids),'started':started,'orders':self.march_orders(owner,refresh() if refresh else observation)}

    def clear_march_orders(self, owner, ids):
        self._write()
        for aid in ids:self._march_orders.pop((owner,aid),None)

    def cleanup_march_orders(self):
        self._write()
        for key in tuple(self._march_orders):
            unit=self._units.by_id(key[1],key[0])
            if unit is None or not unit.alive:self._march_orders.pop(key,None)

    def cancel_orders(self, owner, ids, node=None):
        self._action(owner);units,items=self._selection(owner,ids);self._reject_batch(items)
        if node is not None:integer(node,1)
        changes={}
        for u in units:
            order=self._march_orders.get((owner,u.id))
            if node is not None and (order is None or node>len(order.nodes)):raise ValueError('路径节点不存在；整批未修改')
            changes[(owner,u.id)]=replace(order,nodes=order.nodes[:node-1]+order.nodes[node:]) if node is not None else None
        for key,order in changes.items():
            if order is not None and order.nodes:self._march_orders[key]=order
            else:self._march_orders.pop(key,None)
        return {'status':'effective','units':list(ids),'node':node,'message':'已取消后续军令；已发生的移动与交战保留'}

    @staticmethod
    def _heal_wait(order, unit, definition):
        return order.auto_heal and (unit['hp']<30 or order.healing) and unit['hp']<definition['hp']

    def march_orders(self, owner, observation):
        view=PlayerMap(observation,self._units.size);rows=[];healing=None
        units={u['id']:u for u in view.units if u['owner']==owner and u['hp']>0}
        for order in self._march_orders.values():
            if order.owner!=owner or order.aid not in units:continue
            unit=units[order.aid];origin=(unit['x'],unit['y']);nodes=[]
            waiting=self._heal_wait(order,unit,view.catalog[unit['kind']])
            reason='等待正常补给恢复满血' if waiting else '交战中，等待战斗结束' if unit['engaged'] else ''
            if waiting:
                if healing is None:healing={row['aid']:row for row in self.supply_preview(owner)['units']}
                status=healing.get(order.aid,{}).get('healing_status')
                blocked={'engaged':'同格仍在交战，当前无法回血','supply_shortage':'当前补给不足，无法回血',
                         'equipment_shortage':'当前装备不足，恢复量受限'}.get(status)
                if blocked:reason+='；'+blocked
            if not reason and order.last_period==view.period:reason='本期自动军令已执行或尝试，等待下期'
            if not reason and order.nodes:
                first=order.nodes[0]
                reason=navigation.action_reason(unit,view.period,attack=first.mode=='attack',current=first.position==origin and first.mode=='attack',attacked=view.attacked(origin))
            for index,node in enumerate(order.nodes,1):
                points,blocked=view.planned_route(unit,origin,node.position,node.mode)
                nodes.append({'index':index,'mode':node.mode,'position':list(node.position),'origin':list(origin),
                    'reason':blocked,**view.public_route(points)})
                if index==1 and not reason:reason=blocked
                origin=node.position
            rows.append({'aid':order.aid,'position':[unit['x'],unit['y']],'auto_heal':order.auto_heal,
                'healing':waiting,'reason':reason,'nodes':nodes})
        return tuple(rows)

    def advance_march_orders(self, owner, observation, *, ids=None, refresh=None, on_start=None):
        self._action(owner);view=PlayerMap(observation,self._units.size)
        if view.owner!=owner:raise ValueError('军令规划必须使用本国观察')
        own={u['id']:u for u in view.units if u['owner']==owner and u['hp']>0};started=[]
        for key,order in tuple(self._march_orders.items()):
            if order.owner!=owner or ids is not None and order.aid not in ids:continue
            if order.aid not in own:self._march_orders.pop(key,None);continue
            unit=own[order.aid];definition=view.catalog[unit['kind']]
            if self._heal_wait(order,unit,definition):
                self._march_orders[key]=replace(order,healing=True);continue
            order=replace(order,healing=False);self._march_orders[key]=order
            if order.last_period==view.period:continue
            start=(unit['x'],unit['y'])
            while order.nodes:
                node=order.nodes[0]
                reached=start==node.position and (node.mode=='move' or node.position in view.tiles and view.relation(view.tiles[node.position].get('owner')) in ('own','ally'))
                if not reached:break
                order=replace(order,nodes=order.nodes[1:]);self._march_orders[key]=order
            if not order.nodes:self._march_orders.pop(key,None);continue
            if unit['engaged']:continue
            node=order.nodes[0];points,reason=view.planned_route(unit,start,node.position,node.mode)
            if reason:continue
            options={mode:{tuple(t['position']):t for t in candidates(observation,[order.aid],mode,view.size)['targets']} for mode in ('move','attack')}
            choice=None
            for point in points[1:] if len(points)>1 else points:
                point=tuple(point)
                mode='attack' if node.mode=='attack' and not view.target_reason(point,True) else 'move'
                if point in options[mode]:choice=(mode,point)
                if not view.movable(point):break
            if choice is None:continue
            mode,point=choice
            try:
                receipt=self.attack(owner,[order.aid],*point) if mode=='attack' else self.move_many(owner,[order.aid],*point)
            except ValueError:continue # No hidden obstacle or foreign unit is disclosed.
            if receipt.position_changed or receipt.consumed_action or receipt.engaged or receipt.occupied:
                order=replace(order,last_period=view.period)
                friendly=point in view.tiles and view.relation(view.tiles[point].get('owner')) in ('own','ally')
                if point==node.position and (node.mode=='move' or receipt.occupied or friendly):order=replace(order,nodes=order.nodes[1:])
                if order.nodes:self._march_orders[key]=order
                else:self._march_orders.pop(key,None)
                started.append({'aid':order.aid,'mode':mode,'position':list(point),'receipt':receipt})
                if on_start:on_start(started[-1])
                if refresh:
                    observation=refresh();view=PlayerMap(observation,self._units.size)
                    own={u['id']:u for u in view.units if u['owner']==owner and u['hp']>0}
        return tuple(started)

    def _save_march_orders(self):
        return [{'owner':o.owner,'aid':o.aid,'nodes':[{'mode':n.mode,'position':list(n.position)} for n in o.nodes],
            'auto_heal':o.auto_heal,'healing':o.healing,'last_period':o.last_period} for o in self._march_orders.values()]

    def _restore_march_orders(self, rows):
        if not isinstance(rows,list):raise ValueError('Invalid march orders')
        result={}
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'owner','aid','nodes','auto_heal','healing','last_period'}:raise ValueError('Invalid march order')
            self._living(row['owner']);integer(row['aid'],1);period=integer(row['last_period'],-1)
            if period>self._period() or any(type(row[k]) is not bool for k in ('auto_heal','healing')) or row['healing'] and not row['auto_heal']:
                raise ValueError('Invalid march order progress')
            nodes=self._waypoints(row['nodes']);key=(row['owner'],row['aid'])
            if not nodes or key in result:raise ValueError('Empty or duplicate march order')
            result[key]=MarchOrder(row['owner'],row['aid'],nodes,row['auto_heal'],row['healing'],period)
        return result
