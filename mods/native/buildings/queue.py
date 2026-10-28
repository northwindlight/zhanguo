"""Prepaid construction orders, owned by the building domain."""
from dataclasses import dataclass

from engine.building import integer


@dataclass(frozen=True)
class ConstructionOrder:
    id: int
    owner: str
    site: str
    kind: str
    level: int
    period: int
    costs: tuple
    penalty: int
    discount: int
    policy_pct: int

    @property
    def completed(self):return False


class ConstructionQueue:
    def _pending_at(self, site):
        return tuple(o for o in self._queued.values() if o.site==site)

    def reserved_costs(self, owner):
        result={}
        for order in self._queued.values():
            if order.owner==owner:
                for key,value in order.costs:result[key]=result.get(key,0)+value
        return tuple(result.items())

    def _reserved_self_built(self, owner, kind):
        return sum(o.owner==owner and o.kind==kind for o in self._queued.values())

    def enqueue(self, owner, site_id, kind, *, policy_pct=100):
        self._action()
        quote=self._quote(owner,site_id,kind,policy_pct=policy_pct,queued=True)
        costs=quote['costs']
        order=ConstructionOrder(self._next_id,owner,site_id,kind,quote['level'],self.period,
            tuple(costs.items()),quote['penalty'],quote['discount'],quote['policy_pct'])
        if costs:self._resources('exchange',owner,costs,{})
        self._queued[order.id]=order;self._next_id+=1
        started=self.advance_orders(owner,site_id=site_id)
        return {'id':order.id,'owner':owner,'site':site_id,'kind':kind,'level':order.level,
            'period':self.period,'costs':dict(order.costs),'paid':dict(order.costs),
            'position':list(self._sites[site_id].position),
            'status':'queued' if order.id in self._queued else 'construction',
            'started':started}

    def _queued_start_quote(self, order):
        quote=self._quote(order.owner,order.site,order.kind,policy_pct=order.policy_pct,starting=True)
        if quote['level']!=order.level:raise ValueError('城堡前置等级已变化，请取消后重新安排')
        return quote

    def advance_orders(self, owner, *, site_id=None):
        """Action-boundary scheduler; reads and construction settlement never call it."""
        self._action();started=[];seen=set()
        for order in tuple(self._queued.values()):
            if order.owner!=owner or site_id is not None and order.site!=site_id:continue
            if order.site in seen:continue
            seen.add(order.site)
            if self._sites[order.site].owner!=owner:
                self._cancel_site_orders(order.site);continue
            try:self._queued_start_quote(order)
            except ValueError:continue
            receipt=self._construct(order.owner,order.site,order.kind,order.level,dict(order.costs),
                                    identity=order.id,prepaid=True)
            del self._queued[order.id];started.append(receipt)
        return tuple(started)

    def _cancel_site_orders(self, site_id):
        rows=self._pending_at(site_id)
        if not rows:return
        owners={}
        for order in rows:
            costs=owners.setdefault(order.owner,{})
            for key,value in order.costs:costs[key]=costs.get(key,0)+value
        operations=[{'owner':owner,'debits':{},'credits':costs} for owner,costs in owners.items() if costs]
        if operations:self._resources('exchange_batch',operations)
        for order in rows:del self._queued[order.id]

    def cancel_order(self, owner, identity):
        self._action();integer(identity,1)
        if identity not in self._queued:raise ValueError('待建条目已开工或不存在；在建拆除须按实际状态另行确认')
        return self.demolish(owner,identity)

    def construction_orders(self, owner):
        rows=[];indices={}
        for order in self._queued.values():
            if order.owner!=owner:continue
            index=indices.get(order.site,0);indices[order.site]=index+1
            reason='';available=False
            if index:reason='等待本格前面的建设条目'
            else:
                try:self._queued_start_quote(order);available=True
                except ValueError as exc:reason={
                    'One construction order per site per period':'本格本期已开工，等待下期',
                    'Owner self-build limit reached':'已达本国自建上限',
                    'Site does not belong to actor':'已失去施工地块',
                }.get(str(exc),str(exc))
            cancellation=self.demolition_quote(owner,order.id)
            rows.append({'id':order.id,'owner':owner,'site':order.site,
                'position':list(self._sites[order.site].position),'kind':order.kind,'level':order.level,
                'queued_period':order.period,'status':'queued' if available or self._orders[order.site]==self.period or index else 'blocked',
                'reason':reason,'line_position':index+1,'paid':dict(order.costs),'refunds':dict(order.costs),
                'can_cancel':cancellation['available'],'cancel_reason':cancellation['reason'],
                'price':{'costs':dict(order.costs),'penalty':order.penalty,'discount':order.discount,'policy_pct':order.policy_pct},
                'can_start':available})
        for item in self._instances.values():
            if item.completed or self._sites[item.site].owner!=owner:continue
            receipt=next((r for r in self._construction if r.id==item.id),None)
            cancellation=self.demolition_quote(owner,item.id)
            rows.append({'id':item.id,'owner':owner,'site':item.site,'position':list(self._sites[item.site].position),
                'kind':item.kind,'level':item.level,'status':'construction','reason':'本期施工中，结算后完成',
                'paid':dict(receipt.costs) if receipt else {},'refunds':cancellation['refunds'],
                'can_cancel':cancellation['available'],'cancel_reason':cancellation['reason'],
                'can_start':False})
        return tuple(sorted(rows,key=lambda r:(r['position'],r['status']!='construction',r.get('line_position',0))))

    def _save_orders(self):
        return [{'id':o.id,'owner':o.owner,'site':o.site,'kind':o.kind,'level':o.level,'period':o.period,
            'costs':dict(o.costs),'penalty':o.penalty,'discount':o.discount,'policy_pct':o.policy_pct}
            for o in self._queued.values()]

    def _restore_orders(self, rows, period, next_id, occupied):
        from .objects import amounts, owner_name
        if not isinstance(rows,list):raise ValueError('Invalid construction queue')
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'id','owner','site','kind','level','period','costs','penalty','discount','policy_pct'}:
                raise ValueError('Invalid construction order')
            identity=integer(row['id'],1);owner=owner_name(row['owner']);queued=integer(row['period'])
            if identity>=next_id or identity in occupied or identity in self._queued or queued>period:
                raise ValueError('Invalid construction order sequence or period')
            site=self._sites.get(row['site']);cls=self._type(row['kind'])
            if site is None or site.owner!=owner:raise ValueError('Construction order has lost its site')
            existing,same=self._check_slots(site,cls)
            # Cancelling an earlier prerequisite can legitimately block an
            # already-paid order. Preserve that order across save/load.
            built=self._self_built.get((owner,cls.TYPE_ID),0)+self._reserved_self_built(owner,cls.TYPE_ID)
            if cls.SELF_BUILD_LIMIT is not None and built>=cls.SELF_BUILD_LIMIT:raise ValueError('Construction order exceeds national limit')
            level=integer(row['level'],1)
            if level!=(same+1 if len(cls.GOLD_COSTS)>1 else 1):raise ValueError('Construction queue levels have a gap')
            penalty=integer(row['penalty']);discount=integer(row['discount']);policy=integer(row['policy_pct'])
            if discount>100:raise ValueError('Invalid queued construction discount')
            costs=amounts(row['costs'])
            if costs!=amounts(cls.construction_cost(level,penalty=penalty,discount=discount,policy_pct=policy)):
                raise ValueError('Construction prepayment differs from its locked quote')
            self._resources('snapshot',owner)
            self._queued[identity]=ConstructionOrder(identity,owner,site.id,cls.TYPE_ID,level,queued,
                tuple(costs.items()),penalty,discount,policy)
