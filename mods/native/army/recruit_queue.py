"""Prepaid recruitment; pending units reserve national militia capacity."""
from dataclasses import dataclass, replace

from engine.building import integer


@dataclass(frozen=True)
class RecruitOrder:
    id: int
    owner: str
    site: str
    kind: str
    total: int
    remaining: int
    period: int
    unit_costs: tuple


class RecruitmentQueue:
    def _reserved_militia(self, owner):
        return sum(o.remaining for o in self._recruit_queue.values() if o.owner==owner and o.kind=='native:militia')

    def reserved_costs(self, owner):
        costs={}
        for order in self._recruit_queue.values():
            if order.owner==owner:
                for key,value in order.unit_costs:costs[key]=costs.get(key,0)+value*order.remaining
        return tuple(costs.items())

    def enqueue_recruit(self, owner, site, kind, count=1):
        self._action(owner);integer(count,1)
        quote=self.recruit_quote(owner,site,kind,count,energy_short=self.grid_short(owner),for_queue=True)
        per=self.recruit_costs(owner,kind)
        order=RecruitOrder(self._next_recruit_order,owner,site,kind,count,count,self._period(),tuple(per.items()))
        if quote['costs']:self._call('native:resource-exchange','exchange',owner,quote['costs'],{})
        self._recruit_queue[order.id]=order;self._next_recruit_order+=1
        started=self.advance_recruit_orders(owner,site=site)
        current=self._recruit_queue.get(order.id)
        return {'id':order.id,'owner':owner,'site':site,'kind':kind,'count':count,
            'remaining':current.remaining if current else 0,'period':self._period(),
            'paid':quote['costs'],'position':list(quote['position']),
            'status':'queued' if current else 'completed','started':started}

    def cancel_recruit_order(self, owner, identity):
        self._action(owner);integer(identity,1)
        order=self._recruit_queue.get(identity)
        if order is None or order.owner!=owner:raise ValueError('未找到本国尚未完成的征兵队列')
        return self._refund_recruit_order(order)

    def _refund_recruit_order(self, order):
        costs={key:value*order.remaining for key,value in order.unit_costs}
        if costs:self._call('native:resource-exchange','exchange',order.owner,{},costs)
        del self._recruit_queue[order.id]
        return {'id':order.id,'owner':order.owner,'site':order.site,'kind':order.kind,
            'cancelled':order.remaining,'completed':order.total-order.remaining,'refunds':costs,'status':'effective','order_status':'cancelled'}

    def cleanup_recruit_orders(self, owner=None):
        self._write();cancelled=[]
        for order in tuple(self._recruit_queue.values()):
            if owner is not None and order.owner!=owner:continue
            if self._call('native:buildings','site',order.site).owner!=order.owner:
                cancelled.append(self._refund_recruit_order(order))
        return tuple(cancelled)

    def advance_recruit_orders(self, owner, *, site=None):
        self._action(owner);self.cleanup_recruit_orders(owner);started=[];blocked=set()
        for order in tuple(self._recruit_queue.values()):
            if order.owner!=owner or site is not None and order.site!=site:continue
            pool=(order.site,order.kind=='native:militia')
            if pool in blocked:continue
            try:
                units=self.recruit(owner,order.site,order.kind,order.remaining,energy_short=self.grid_short(owner),
                    _prepaid=dict(order.unit_costs))
            except ValueError:
                blocked.add(pool);continue
            count=len(units);remaining=order.remaining-count
            if remaining:self._recruit_queue[order.id]=replace(order,remaining=remaining);blocked.add(pool)
            else:del self._recruit_queue[order.id]
            started.append({'id':order.id,'site':order.site,'kind':order.kind,'count':count,
                'position':list(self._call('native:buildings','site',order.site).position),
                'costs':{key:value*count for key,value in order.unit_costs},'units':units})
        return tuple(started)

    def recruit_orders(self, owner):
        self._living(owner);rows=[];indices={}
        for order in self._recruit_queue.values():
            if order.owner!=owner:continue
            pool=(order.site,order.kind=='native:militia');index=indices.get(pool,0);indices[pool]=index+1
            reason='等待本设施前面的征兵条目' if index else ''
            if not reason:
                try:self.recruit_quote(owner,order.site,order.kind,1,energy_short=self.grid_short(owner),starting=True)
                except ValueError as exc:reason=str(exc)
            rows.append({'id':order.id,'owner':owner,'site':order.site,'kind':order.kind,
                'position':list(self._call('native:buildings','site',order.site).position),
                'count':order.total,'completed':order.total-order.remaining,'remaining':order.remaining,
                'period':order.period,'status':'waiting' if reason else 'queued','reason':reason,
                'line_position':index+1,'paid':{k:v*order.total for k,v in order.unit_costs},
                'reserved':{k:v*order.remaining for k,v in order.unit_costs},
                'refunds':{k:v*order.remaining for k,v in order.unit_costs}})
        return tuple(rows)

    def _save_recruit_orders(self):
        return [{'id':o.id,'owner':o.owner,'site':o.site,'kind':o.kind,'total':o.total,'remaining':o.remaining,
            'period':o.period,'unit_costs':dict(o.unit_costs)} for o in self._recruit_queue.values()]

    def _restore_recruit_orders(self, rows, next_id):
        from ..buildings.objects import amounts
        integer(next_id,1)
        if not isinstance(rows,list):raise ValueError('Invalid recruit queue')
        result={}
        for row in rows:
            if not isinstance(row,dict) or set(row)!={'id','owner','site','kind','total','remaining','period','unit_costs'}:
                raise ValueError('Invalid recruit order')
            identity=integer(row['id'],1);total=integer(row['total'],1);remaining=integer(row['remaining'],1)
            period=integer(row['period']);owner=row['owner'];self._living(owner)
            if identity in result or identity>=next_id or remaining>total or period>self._period():raise ValueError('Invalid recruit queue progress')
            if self._call('native:buildings','site',row['site']).owner!=owner:raise ValueError('Recruit order has lost its site')
            if row['kind'] not in self._units.type_ids:raise ValueError('Unknown queued unit kind')
            costs=amounts(row['unit_costs'])
            if costs!=self.recruit_costs(owner,row['kind']):raise ValueError('Recruit prepayment differs from its recipe')
            result[identity]=RecruitOrder(identity,owner,row['site'],row['kind'],total,remaining,period,tuple(costs.items()))
        return result
