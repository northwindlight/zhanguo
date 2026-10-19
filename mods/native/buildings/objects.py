"""Owned building sites/instances and explicit production facts."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from types import MappingProxyType

from engine.building import BuildingCycle, integer, recipe
from engine.coordinates import coordinates
from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import ModError
from engine.state import active_unit

# Building slots per site; the only source for this limit.
SITE_CAPACITY = 20


def owner_name(value):
    if not isinstance(value,str) or not value:raise ValueError('Owner must be nonempty')
    return value


def amounts(values):
    if not isinstance(values,Mapping):raise ValueError('Expected resource mapping')
    result = {}
    for key,amount in values.items():
        content_id(key);Resource.finite_number(amount)
        if amount < 0:raise ValueError('Negative resource amount')
        result[key] = amount
    return result


@dataclass(frozen=True)
class Site:
    id: str
    owner: str | None
    position: tuple
    resources: tuple
    capacity: int
    penalty: int


@dataclass(frozen=True)
class BuildReceipt:
    id: int
    owner: str
    site: str
    kind: str
    period: int
    costs: tuple


@dataclass(frozen=True)
class ProductionRecord:
    id: int
    owner: str | None
    site: str
    kind: str
    status: str
    inputs: tuple
    outputs: tuple
    energy: int


@dataclass(frozen=True)
class EnergyReport:
    owner: str
    generated: int
    external: int
    demand: int
    short: bool


@dataclass(frozen=True)
class ProductionReport:
    period: int
    records: tuple
    energy: tuple
    inputs: tuple
    outputs: tuple
    construction: tuple
    transfers: tuple = ()


@dataclass(frozen=True)
class BuildingsSnapshot:
    period: int
    produced: bool
    sites: tuple
    instances: tuple
    construction: tuple
    self_built: tuple


class Buildings:
    def __init__(self, building_types, resource_call, place_call=None):
        self._types = MappingProxyType({cls.TYPE_ID:cls for cls in building_types})
        if len(self._types)!=len(building_types):raise ValueError('Duplicate building type')
        self._resources, self._place = resource_call, place_call
        self._sites, self._instances, self._orders, self._self_built = {}, {}, {}, {}
        self._construction = []
        self._tribute_remainders = {}
        self._by_site = None
        self._period, self._next_id, self._produced = 0, 1, False
        self._world, self._domain = None, None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:raise ModError('Buildings already bound')
        self._world, self._domain = world, world.runtime.state_owners[key]
        for building in self._instances.values():building._domain = self._domain
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != self._domain:
            raise ModError('Use declared buildings interface')

    def _action(self):
        self._write()
        if self._produced:raise ValueError('Building production already settled; complete the period first')

    @property
    def ids(self):return tuple(self._types)
    @property
    def period(self):return self._period

    def _type(self, kind):
        content_id(kind)
        if kind not in self._types:raise ValueError(f'Unknown building type: {kind}')
        return self._types[kind]

    def register_site(self, site_id, owner, position, *, resources=None, capacity=SITE_CAPACITY, penalty=0):
        self._write();content_id(site_id)
        if owner is not None:owner_name(owner)
        if site_id in self._sites:raise ValueError('Duplicate building site')
        if not isinstance(position,(tuple,list)) or len(position)!=2:raise ValueError('Invalid position')
        position = tuple(integer(value) for value in position)
        if any(site.position==position for site in self._sites.values()):raise ValueError('Duplicate site position')
        caps = amounts({} if resources is None else resources)
        if any(type(value) is not int for value in caps.values()):raise ValueError('Site capacities must be integers')
        site = Site(site_id,owner,position,tuple(caps.items()),integer(capacity,1),integer(penalty))
        self._sites[site_id] = site
        self._orders[site_id] = -1
        return site

    def transfer_site(self, site_id, owner):
        self._write()
        if owner is not None:owner_name(owner)
        self._sites[site_id] = replace(self._sites[site_id],owner=owner)
        return self._sites[site_id]

    def site(self, site_id):return self._sites[site_id]

    def resolve_site(self, actor, site=None, x=None, y=None):
        if site is not None:
            if x is not None or y is not None:raise ValueError('站点 ID 与坐标只能选一种')
            if isinstance(site,str) and site not in self._sites:
                # 玩家地块引用：坐标串（"5 6" / "5,6" / "(5,6)" / "[5,6]"）或地名。
                point=coordinates(site)
                text=site.strip()
                if point is not None:site=list(point)
                else:
                    position=self._place(text) if self._place else None
                    if position is None:raise ValueError(f'没有这个地名，也没有这个站点 ID：{text}')
                    site=list(position)
            if isinstance(site,(list,tuple)):
                if len(site)!=2:raise ValueError('坐标需要两个整数')
                x,y=map(integer,site)
                candidate=next((s for s in self._sites.values() if s.position==(x,y)),None)
            else:candidate=self._sites.get(site)
        else:
            if x is None or y is None:raise ValueError('请提供 x、y 坐标')
            integer(x);integer(y)
            candidate=next((s for s in self._sites.values() if s.position==(x,y)),None)
        if candidate is None or candidate.owner!=actor:raise ValueError('此位置不是本国建筑站点')
        return candidate.id
    def _index(self):
        if self._by_site is None:
            self._by_site = {}
            for item in self._instances.values():self._by_site.setdefault(item.site, []).append(item)
        return self._by_site

    def instances(self, site_id=None):
        if site_id is not None:self._sites[site_id]
        return tuple(item.snapshot() for item in (self._instances.values() if site_id is None else self._index().get(site_id, ())))

    def count(self, site_id, kind, *, completed_only=True):
        self._sites[site_id];self._type(kind)
        return sum(item.site==site_id and item.kind==kind and (item.completed or not completed_only)
                   for item in self._index().get(site_id, ()))

    def effects(self, site_id, kind):
        count=self.count(site_id,kind)
        additive={'recruit_cap','militia_cap','militia_per_turn'}
        return MappingProxyType({key:value*count if key in additive else value for key,value in self._type(kind).EFFECTS.items()}) if count else MappingProxyType({})

    def roles_of(self, kind):
        return tuple(getattr(self._type(kind), 'ROLES', ()) or ())

    def kinds_with_role(self, role):
        if not isinstance(role, str):raise ValueError('Invalid building role')
        result=tuple(kind for kind, cls in self._types.items() if role in (getattr(cls,'ROLES',()) or ()))
        if not result:raise ValueError(f'没有建筑声明角色：{role}')
        return result

    def kind_with_role(self, role):
        """The single building kind for a role that must be unique (e.g. recruiter)."""
        kinds=self.kinds_with_role(role)
        if len(kinds)!=1:raise ValueError(f'角色 {role} 必须恰好有一个建筑：{kinds}')
        return kinds[0]

    def has_role(self, site_id, role, *, completed_only=True):
        self._sites[site_id]
        kinds=set(self.kinds_with_role(role))
        return any(item.site==site_id and (item.completed or not completed_only) and item.kind in kinds
                   for item in self._index().get(site_id,()))

    def count_role(self, site_id, role, *, completed_only=True):
        kinds=set(self.kinds_with_role(role))
        return sum(item.site==site_id and (item.completed or not completed_only) and item.kind in kinds
                   for item in self._index().get(site_id,()))

    def effect_role(self, site_id, role, key, default=0):
        """Aggregate a per-instance effect across every kind declaring the role."""
        total=0
        for kind in self.kinds_with_role(role):
            total+=(self._type(kind).EFFECTS.get(key,0) or 0)*self.count(site_id,kind)
        return total if total else default

    def _check_slots(self, site, cls, count=1):
        existing = list(self._index().get(site.id, ()))
        same = sum(item.kind==cls.TYPE_ID for item in existing)
        if len(existing)+count>site.capacity:raise ValueError('Building slots full')
        if cls.MAX_PER_SITE is not None and same+count>cls.MAX_PER_SITE:raise ValueError('Per-site building limit')
        if cls.SITE_RESOURCE is not None and same+count>dict(site.resources).get(cls.SITE_RESOURCE,0):
            raise ValueError('Insufficient local building resource capacity')
        return existing,same

    def seed(self, site_id, kind, count=1, *, completed=True):
        """Explicit initialization/import; does not count as player construction."""
        self._action();integer(count,1)
        if type(completed) is not bool:raise ValueError('Invalid completion flag')
        site,cls = self._sites[site_id],self._type(kind)
        _,same = self._check_slots(site,cls,count)
        candidates = [cls(self._next_id+offset,site_id,
                          level=same+offset+1 if len(cls.GOLD_COSTS)>1 else 1,
                          remaining=0 if completed else 1) for offset in range(count)]
        for item in candidates:item._domain = self._domain
        self._instances.update((item.id,item) for item in candidates)
        self._by_site = None
        self._next_id += count
        return tuple(item.snapshot() for item in candidates)

    def quote(self, owner, site_id, kind, *, policy_pct=100):
        owner_name(owner);integer(policy_pct)
        site,cls = self._sites[site_id],self._type(kind)
        if site.owner!=owner:raise ValueError('Site does not belong to actor')
        if self._orders[site_id]==self.period:raise ValueError('One construction order per site per period')
        existing,same = self._check_slots(site,cls)
        if len(existing)<cls.MIN_SLOTS:raise ValueError('Insufficient existing building slots')
        built = self._self_built.get((owner,kind),0)
        if cls.SELF_BUILD_LIMIT is not None and built>=cls.SELF_BUILD_LIMIT:
            raise ValueError('Owner self-build limit reached')
        discount = 0 if cls.BUILD_DISCOUNT else max((item.BUILD_DISCOUNT for item in existing if item.completed),default=0)
        level = same+1 if len(cls.GOLD_COSTS)>1 else 1
        costs = amounts(cls.construction_cost(level,penalty=site.penalty,discount=discount,policy_pct=policy_pct))
        # Validate account even for a free custom building.
        self._resources('snapshot',owner)
        return {'site':site_id,'kind':kind,'level':level,'costs':dict(costs),
                'other_slots':len(existing),'penalty':site.penalty,'discount':discount,'policy_pct':policy_pct}

    def build(self, owner, site_id, kind, *, policy_pct=100):
        self._action()
        quote=self.quote(owner,site_id,kind,policy_pct=policy_pct)
        cls=self._type(kind);level=quote['level'];costs=quote['costs']
        built=self._self_built.get((owner,kind),0)
        candidate = cls(self._next_id,site_id,level=level,remaining=1)
        candidate._domain = self._domain
        receipt = BuildReceipt(candidate.id,owner,site_id,kind,self.period,tuple(costs.items()))
        if costs:self._resources('exchange',owner,costs,{})
        self._instances[candidate.id] = candidate
        self._by_site = None
        self._next_id += 1
        self._orders[site_id] = self.period
        self._self_built[(owner,kind)] = built+1
        self._construction.append(receipt)
        return receipt

    def replacement_costs(self, owner):
        """Completed building replacement materials, without local discounts.

        Each castle level is an instance and includes its own gold and wood.
        Captured buildings belong to the current site owner.
        """
        owner_name(owner)
        result = {}
        for item in self._instances.values():
            if not item.completed or self._sites[item.site].owner!=owner:continue
            for key,amount in amounts(type(item).construction_cost(item.level)).items():
                result[key] = result.get(key,0)+amount
                Resource.finite_number(result[key])
        return tuple(result.items())

    def industry_upkeep(self, owner):
        owner_name(owner)
        need = {}
        for item in self._instances.values():
            if not item.completed or self._sites[item.site].owner!=owner:continue
            if item.PHASE not in ('power','industry'):continue
            cycle = item.cycle(other_slots=self._other_slots(item))
            for key,amount in cycle.inputs:need[key] = need.get(key,0)+amount
        return MappingProxyType(need)

    def demand_preview(self, owner):
        """已落成建筑按真实 cycle 配方汇总的满负荷需求，不执行生产。"""
        owner_name(owner)
        inputs, outputs, capacity, demand = {}, {}, 0, 0
        for item in self._instances.values():
            if not item.completed or self._sites[item.site].owner != owner:continue
            cycle = item.cycle(other_slots=self._other_slots(item))
            for key, amount in cycle.inputs:inputs[key] = inputs.get(key, 0) + amount
            for key, amount in cycle.outputs:outputs[key] = outputs.get(key, 0) + amount
            capacity += cycle.energy
            demand += item.ENERGY_USE
        return {'inputs': inputs, 'outputs': outputs,
                'energy': {'capacity': capacity, 'demand': demand, 'capacity_gap': max(0, demand-capacity)}}

    def _other_slots(self, item):
        return sum(other.completed and other.site==item.site and other.kind!=item.kind
                   for other in self._index().get(item.site, ()))

    @staticmethod
    def _tribute_due(amount, percent, remainder):
        # 十进制累计，避免 0.1 连加十次落在 1 的下方而少扣一期。
        accrued=remainder+Decimal(str(amount))*percent/100
        due=int(accrued)
        return due,accrued-due

    def tribute_balances(self, owner, recipient):
        owner_name(owner);owner_name(recipient)
        return {key:float(value) for (source,target,key),value in sorted(self._tribute_remainders.items())
                if source==owner and target==recipient}

    def tribute_preview(self, owner, recipient, percent):
        """下一次生产的满负荷预演；与结算共用累计取整，不改变余额。"""
        owner_name(owner);owner_name(recipient);integer(percent)
        if owner==recipient or percent>100:raise ValueError('Invalid production allocation')
        outputs=self.demand_preview(owner)['outputs']
        carried={key:value for (source,target,key),value in self._tribute_remainders.items()
                 if source==owner and target==recipient}
        outgoing,remaining={},{}
        for key in sorted(outputs.keys()|carried.keys()):
            outgoing[key],balance=self._tribute_due(outputs.get(key,0),percent,carried.get(key,Decimal(0)))
            remaining[key]=float(balance)
        return {'overlord':recipient,'percent':percent,
                'carried_remainders':{key:float(value) for key,value in sorted(carried.items())},
                'full_load_outgoing':outgoing,
                'retained_outputs':{key:value-outgoing[key] for key,value in outputs.items()},
                'full_load_remainders':remaining}

    def produce(self, *, external_energy=None, production_shares=None):
        self._action()
        energy_input = {} if external_energy is None else external_energy
        if not isinstance(energy_input,Mapping):raise ValueError('Expected external energy mapping')
        for owner,value in energy_input.items():owner_name(owner);integer(value)
        owners = tuple(dict.fromkeys(site.owner for site in self._sites.values() if site.owner is not None))
        balances = {owner:{stock.id:stock.amount for stock in self._resources('snapshot',owner)} for owner in owners}
        shares={} if production_shares is None else production_shares
        if not isinstance(shares,Mapping):raise ValueError('Expected production shares mapping')
        for owner,share in shares.items():
            if not isinstance(share,Mapping) or set(share)!={'recipient','percent'}:
                raise ValueError('Invalid production allocation')
            if share['recipient']==owner:raise ValueError('Invalid production recipient')
            for account in (owner,share['recipient']):
                if account not in balances:
                    balances[account]={stock.id:stock.amount for stock in self._resources('snapshot',account)}
            integer(share['percent'])
            if share['percent']>100:raise ValueError('Production allocation exceeds output')
        transfers={}
        remainders=dict(self._tribute_remainders)
        operations,records = [],{}
        generated,demand = {owner:0 for owner in owners},{owner:0 for owner in owners}
        cycles = {}
        site_order = {key:index for index,key in enumerate(self._sites)}
        type_order = {key:index for index,key in enumerate(self._types)}
        simulation = sorted(self._instances.values(),key=lambda item:(site_order[item.site],type_order[item.kind],item.id))
        for item in self._instances.values():
            owner = self._sites[item.site].owner
            if owner is None:
                records[item.id] = ProductionRecord(item.id,owner,item.site,item.kind,'unowned',(),(),0)
                continue
            if not item.completed:
                records[item.id] = ProductionRecord(item.id,owner,item.site,item.kind,'under-construction',(),(),0)
                continue
            cycle = item.cycle(other_slots=self._other_slots(item))
            if not isinstance(cycle,BuildingCycle):raise ValueError('Invalid building cycle')
            # Concrete overrides can declare dynamic resource recipes.
            recipe(dict(cycle.inputs));recipe(dict(cycle.outputs));integer(cycle.energy)
            for key,_ in (*cycle.inputs,*cycle.outputs):
                if key not in balances[owner]:raise ValueError(f'Unknown cycle resource: {key}')
            cycles[item.id] = cycle
            demand[owner] += item.ENERGY_USE

        def run(item, powered=True):
            owner = self._sites[item.site].owner
            cycle = cycles[item.id]
            if not powered:
                status = 'energy-shortage'
            elif any(balances[owner][key]<amount for key,amount in cycle.inputs):
                status = 'missing-input'
            else:
                status = 'running' if cycle.inputs or cycle.outputs or cycle.energy else 'idle'
            inputs,outputs,energy = (),(),0
            if status in ('running','idle'):
                inputs,outputs,energy = cycle.inputs,cycle.outputs,cycle.energy
                for key,amount in inputs:balances[owner][key] = balances[owner][key]-amount
                credits={}
                for key,amount in outputs:
                    share=shares.get(owner)
                    tribute=0
                    if share:
                        identity=(owner,share['recipient'],key)
                        tribute,remainder=self._tribute_due(amount,share['percent'],remainders.get(identity,Decimal(0)))
                        if remainder:remainders[identity]=remainder
                        else:remainders.pop(identity,None)
                    credits[key]=amount-tribute
                    if tribute:
                        identity=(owner,share['recipient'],key)
                        transfers[identity]=transfers.get(identity,0)+tribute
                    balances[owner][key] = balances[owner][key]+credits[key]
                    Resource.finite_number(balances[owner][key])
                if inputs or outputs:
                    operations.append({'owner':owner,'debits':dict(inputs),'credits':credits})
            records[item.id] = ProductionRecord(item.id,owner,item.site,item.kind,status,inputs,outputs,energy)
            return energy

        for phase in ('gather','power'):
            for item in simulation:
                if item.completed and item.id in cycles and item.PHASE==phase:
                    value = run(item)
                    generated[self._sites[item.site].owner] += value
        reports = tuple(EnergyReport(owner,generated[owner],energy_input.get(owner,0),demand[owner],
                                     generated[owner]+energy_input.get(owner,0)<demand[owner]) for owner in owners)
        short = {row.owner:row.short for row in reports}
        for phase in ('industry','income','passive'):
            for item in simulation:
                if item.completed and item.id in cycles and item.PHASE==phase:
                    run(item,not (item.ENERGY_USE and short[self._sites[item.site].owner]))
        consumed,produced = {},{}
        for receipt in self._construction:
            for key,value in receipt.costs:consumed[key] = consumed.get(key,0)+value
        ordered = tuple(records[item.id] for item in self._instances.values())
        for row in ordered:
            for key,value in row.inputs:consumed[key] = consumed.get(key,0)+value
            for key,value in row.outputs:produced[key] = produced.get(key,0)+value
        # Credit receipts only after every producer has completed its own
        # production: tribute cannot itself produce output or be taxed again.
        for (_,recipient,key),amount in sorted(transfers.items()):
            operations.append({'owner':recipient,'debits':{},'credits':{key:amount}})
        report = ProductionReport(self.period,ordered,reports,tuple(consumed.items()),tuple(produced.items()),tuple(self._construction),
                                  tuple((source,target,key,amount) for (source,target,key),amount in sorted(transfers.items())))
        if operations:self._resources('exchange_batch',operations)
        # 资源批次成功才提交小数账；失败不能消耗或重复增加累计额。
        self._tribute_remainders=remainders
        self._produced = True
        return report

    def complete_construction(self):
        self._write()
        if not self._produced:raise ValueError('Production must precede construction completion')
        pending = [item for item in self._instances.values() if not item.completed]
        for item in pending:item.complete()
        self._construction = []
        self._period += 1
        self._produced = False
        return tuple(item.snapshot() for item in pending)

    def snapshot(self):
        def calculate():return BuildingsSnapshot(self.period,self._produced,tuple(self._sites.values()),self.instances(),
                                 tuple(self._construction),tuple((owner,kind,count) for (owner,kind),count in self._self_built.items()))
        return self._world.runtime.derived(self._world,('buildings.snapshot',),calculate) if self._world else calculate()

    def __deepcopy__(self,memo):
        result = self.snapshot();memo[id(self)] = result
        return result

    def to_save(self):
        return {'period':self.period,'produced':self._produced,'next_id':self._next_id,
                'sites':[{'id':site.id,'owner':site.owner,'position':list(site.position),
                          'resources':dict(site.resources),'capacity':site.capacity,'penalty':site.penalty,
                          'last_order':self._orders[site.id]} for site in self._sites.values()],
                'instances':[item.to_save() for item in self._instances.values()],
                'self_built':[{'owner':owner,'kind':kind,'count':count} for (owner,kind),count in self._self_built.items()],
                'tribute_remainders':[{'source':source,'recipient':recipient,'resource':key,'remainder':str(value)}
                                      for (source,recipient,key),value in sorted(self._tribute_remainders.items())],
                'construction':[{'id':row.id,'owner':row.owner,'site':row.site,'kind':row.kind,
                                 'period':row.period,'costs':dict(row.costs)} for row in self._construction]}

    def restore(self, data):
        self._write()
        expected = {'period','produced','next_id','sites','instances','self_built','construction','tribute_remainders'}
        if not isinstance(data,dict) or set(data)!=expected:raise ValueError('Invalid buildings save')
        if type(data['produced']) is not bool:raise ValueError('Invalid production marker')
        period,next_id = integer(data['period']),integer(data['next_id'],1)
        for field in ('sites','instances','self_built','construction','tribute_remainders'):
            if not isinstance(data[field],list):raise ValueError('Invalid building records')
        candidate = Buildings(tuple(self._types.values()),self._resources)
        for row in data['tribute_remainders']:
            if not isinstance(row,dict) or set(row)!={'source','recipient','resource','remainder'}:
                raise ValueError('Invalid tribute remainder record')
            source,recipient=owner_name(row['source']),owner_name(row['recipient'])
            content_id(row['resource'])
            if source==recipient or not isinstance(row['remainder'],str):raise ValueError('Invalid tribute remainder')
            try:value=Decimal(row['remainder'])
            except InvalidOperation as exc:raise ValueError('Invalid tribute remainder') from exc
            if not value.is_finite() or not 0<value<1:raise ValueError('Invalid tribute remainder')
            identity=(source,recipient,row['resource'])
            if identity in candidate._tribute_remainders:raise ValueError('Duplicate tribute remainder')
            candidate._tribute_remainders[identity]=value
        for row in data['sites']:
            if not isinstance(row,dict) or set(row)!={'id','owner','position','resources','capacity','penalty','last_order'}:
                raise ValueError('Invalid building site record')
            candidate.register_site(row['id'],row['owner'],row['position'],resources=row['resources'],
                                    capacity=row['capacity'],penalty=row['penalty'])
            order = integer(row['last_order'],-1)
            if order>period:raise ValueError('Future construction order')
            candidate._orders[row['id']] = order
        for row in data['instances']:
            if not isinstance(row,dict):raise ValueError('Invalid building instance')
            item = candidate._type(row.get('kind')).from_save(row)
            if item.id in candidate._instances or item.id>=next_id or item.remaining>1:
                raise ValueError('Invalid building sequence/progress')
            site = candidate._sites.get(item.site)
            if site is None:raise ValueError('Building site missing')
            candidate._check_slots(site,type(item))
            expected_level = candidate.count(item.site,item.kind,completed_only=False)+1 if len(item.GOLD_COSTS)>1 else 1
            if item.level!=expected_level:raise ValueError('Invalid building level')
            item._domain = self._domain
            candidate._instances[item.id] = item
            candidate._by_site = None
        for row in data['self_built']:
            if not isinstance(row,dict) or set(row)!={'owner','kind','count'}:raise ValueError('Invalid self-build counter')
            owner_name(row['owner']);cls = candidate._type(row['kind']);count = integer(row['count'],1)
            key = (row['owner'],row['kind'])
            if key in candidate._self_built or cls.SELF_BUILD_LIMIT is not None and count>cls.SELF_BUILD_LIMIT:
                raise ValueError('Duplicate or exceeded self-build counter')
            candidate._self_built[key] = count
        seen = set()
        for row in data['construction']:
            if not isinstance(row,dict) or set(row)!={'id','owner','site','kind','period','costs'}:
                raise ValueError('Invalid construction receipt')
            item = candidate._instances.get(integer(row['id'],1))
            owner_name(row['owner']);integer(row['period'])
            if item is None or item.completed or item.id in seen or row['kind']!=item.kind or row['site']!=item.site or row['period']!=period:
                raise ValueError('Inconsistent construction receipt')
            if candidate._orders[item.site]!=period or candidate._self_built.get((row['owner'],item.kind),0)<1:
                raise ValueError('Construction receipt has no order/counter')
            costs = amounts(row['costs'])
            seen.add(item.id)
            candidate._construction.append(BuildReceipt(item.id,row['owner'],item.site,item.kind,period,tuple(costs.items())))
        self._sites,self._instances,self._orders,self._self_built = candidate._sites,candidate._instances,candidate._orders,candidate._self_built
        self._by_site = None
        self._construction = candidate._construction
        self._tribute_remainders = candidate._tribute_remainders
        self._period,self._next_id,self._produced = period,next_id,data['produced']
