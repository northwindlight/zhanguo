"""Economic queries and explicit estimates over current registered content."""
from dataclasses import dataclass, field

from engine.building import Building
from engine.resource import Resource
from mods.native.contracts.economy import Money, TurnReport, PeriodReport, integer

# 旧版市场行给的是「卖50≈79金 买50≈89金」——固定 50 单位的大单试算。
MARKET_TRIAL_UNITS = 50


@dataclass(frozen=True)
class PublicMarket:
    currency: str
    period: int
    prices: tuple
    inventory: tuple
    reference: tuple
    buy_prices: tuple
    sell_prices: tuple
    # 均衡价 = 库存回到基准库存时的价格；trial = 50 单位大单的成交总额（买/卖）。
    equilibrium: tuple = ()
    trials: tuple = ()
    trial_units: int = 50


@dataclass(frozen=True)
class BalanceSheet:
    owner: str
    inventory: Money
    buildings: Money
    gross_assets: Money
    stocks: tuple
    latest_turn: TurnReport | None
    latest_flows: dict = field(default_factory=dict)
    preview: dict = field(default_factory=dict)
    latest_report: PeriodReport | None = None


@dataclass(frozen=True)
class BuildingEstimate:
    kind: str
    level: int
    other_slots: int
    construction: Money
    inputs: Money
    outputs: Money
    energy_used: int
    energy_produced: int
    energy_unit_cost: float | None
    net_per_period: Money | None
    payback_periods: float | None
    outputs_sell: Money
    output_basis: str
    energy_reference: str | None
    costs_by_level: tuple
    recipe_inputs: tuple
    recipe_outputs: tuple
    min_slots: int
    max_per_site: int | None
    self_build_limit: int | None
    site_resource: str | None
    effects: tuple
    projected_slots: int
    assumptions: tuple


class EconomicQueries:
    def __init__(self, runtime, call):self._runtime,self._call = runtime,call

    def _value(self, amounts, side='mid'):
        known,missing = 0,[]
        for row in self._call('economy:valuation','quote',dict(amounts),side):
            if row.value is None:missing.append(row.resource)
            else:known += row.value
        return Money(known,tuple(missing))

    def market(self):
        row = self._call('native:market','summary')
        amounts={key:1 for key,_ in row.prices}
        buy=tuple((v.resource,v.price) for v in self._call('economy:valuation','quote',amounts,'buy'))
        sell=tuple((v.resource,v.price) for v in self._call('economy:valuation','quote',amounts,'sell'))
        # 旧版市场行是「现价/均衡/买/卖 + 大单试算」；没有均衡价，模型无法判断贵贱。
        equilibrium=self._call('native:market','equilibrium')
        trials=self._call('native:market','trials',MARKET_TRIAL_UNITS)
        return PublicMarket(row.currency,row.period,row.prices,row.inventory,row.reference,buy,sell,
                            tuple(sorted(equilibrium.items())),tuple(trials),MARKET_TRIAL_UNITS)

    def balance(self, owner):
        stocks = self._call('native:resource-inspection','snapshot',owner)
        inventory = self._value({row.id:row.amount for row in stocks})
        buildings = self._value(self._call('native:buildings','replacement_costs',owner))
        turns = self._call('native:economy','turns',owner)
        flows = self._call('native:economy','production',owner)
        preview = dict(self._call('native:buildings', 'demand_preview', owner))
        if 'native:diplomacy' in self._runtime.capabilities:
            relations=self._call('native:diplomacy','snapshot',owner).vassals
            relation=next((s for s in relations if s.subject==owner),None)
            if relation:
                preview['tribute']=self._call('native:buildings','tribute_preview',owner,relation.overlord,relation.tribute_percent)
            receivables=[{'subject':s.subject,'percent':s.tribute_percent,
                          'remainders':self._call('native:buildings','tribute_balances',s.subject,owner)}
                         for s in relations if s.overlord==owner]
            if receivables:preview['tribute_receivables']=receivables
        inputs = dict(preview['inputs'])
        military = (self._call('native:army', 'supply_preview', owner)
                    if 'native:army' in self._runtime.capabilities else {})
        if military:
            inputs['native:supply'] = inputs.get('native:supply', 0) + military['need']
            inputs['native:equipment'] = inputs.get('native:equipment', 0) + military['equipment_need']
        balances = {row.id: row.amount for row in stocks}
        preview.update(inputs=inputs, military=military,
                       shortfalls={key:amount-balances.get(key,0) for key,amount in inputs.items() if amount>balances.get(key,0)})
        reports = self.report(owner)
        return BalanceSheet(owner,inventory,buildings,inventory.plus(buildings),stocks,
                            turns[-1] if turns else None,flows,preview,
                            reports[-1] if reports else None)

    def report(self, owner):
        self._call('native:resource-inspection','snapshot',owner)
        return self._call('native:economy','reports',owner)

    def building_types(self):
        # 只依赖建造契约（engine Building + 建造属性），不 import native 实现类。
        required=('TYPE_ID','NAME_KEY','GOLD_COSTS','WOOD_COST','MIN_SLOTS','MAX_PER_SITE',
                  'PHASE','EFFECTS','ENERGY_USE','ENERGY_OUTPUT','BUILD_DISCOUNT','SELF_BUILD_LIMIT')
        result={}
        for key,cls in self._runtime.content.definitions():
            if not isinstance(cls,type) or not issubclass(cls,Building) or getattr(cls,'TYPE_ID',None)!=key:
                continue
            if any(not hasattr(cls,attr) for attr in required) or not callable(getattr(cls,'construction_cost',None)):
                raise ValueError(f'建筑缺少经济估算契约：{key}')
            result[key]=cls
        return result

    def _energy_cost(self):
        types=self.building_types()
        # Preserve the native ROI benchmark: one wood powers two energy units.
        candidates=[(key,types[key]) for key in self._runtime.content.role('power') if key in types] or sorted(types.items())
        for key,cls in candidates:
            cycle = cls(1,'native:estimate').cycle()
            if cycle.energy:
                fuel = self._value(cycle.inputs,'buy')
                if fuel.complete:return fuel.total/cycle.energy,key
        return None,None

    def estimate(self, kind, level=1, other_slots=0, penalty=0, discount=0, policy_pct=100, existing=(), tribute_pct=0):
        integer(other_slots)
        cls = self.building_types()[kind]
        cost = cls.construction_cost(level,penalty=penalty,discount=discount,policy_pct=policy_pct)
        roles=self._runtime.content.roles_of(kind)
        slot_bonus=getattr(cls,'SLOT_BONUS',0)
        projected_slots=other_slots+slot_bonus if 'capital' in roles else other_slots
        assumptions=[]
        if 'capital' in roles and slot_bonus:
            assumptions.append(f'首都类建筑按另增{slot_bonus}座建筑的乐观密度估算，实际结算仍按已落成座数')
        cycle = cls(1,'native:estimate',level=level).cycle(other_slots=projected_slots)
        basis='sell' if cls.PHASE=='gather' else 'buy' if cls.PHASE=='industry' else 'mid'
        retained={key:round(amount*(100-tribute_pct)/100,10) for key,amount in cycle.outputs}
        construction,inputs,outputs = self._value(cost),self._value(cycle.inputs,'buy'),self._value(retained,basis)
        outputs_sell=self._value(retained,'sell')
        if tribute_pct:assumptions.append(f'按上贡{tribute_pct}%估算长期产出估值与净收益；实际贡赋小数按双方及资源跨期累计，满1向下取整扣取，余数结转；建筑原始配方仍列毛产出')
        energy_cost,energy_reference = self._energy_cost()
        net = outputs.plus(inputs,-1)
        # Electricity is not tradable revenue: power plants only spend fuel.
        if cls.ENERGY_USE:
            net = None if energy_cost is None else net.plus(Money(-cls.ENERGY_USE*energy_cost))
        if 'academy' in roles:
            amort=getattr(cls,'AMORT_TURNS')
            types=self.building_types();n=max(other_slots,cls.MIN_SLOTS)
            values=[types[row.kind].GOLD_COSTS[0] for row in existing if row.kind in types]
            if not values:values=[c.GOLD_COSTS[0] for k,c in types.items() if k!=kind]
            average=sum(values)/len(values) if values else 0
            discount_rate=cls.BUILD_DISCOUNT/100
            capitals=self._runtime.content.role('capital')
            hall=types.get(capitals[0]) if len(capitals)==1 else None
            hall_bonus=getattr(hall,'SLOT_BONUS',0)
            density=discount_rate*n*hall.EFFECTS.get('gold_per_slot',0) if hall and n+hall_bonus>=hall.MIN_SLOTS else 0
            density*=1-tribute_pct/100
            net=Money(discount_rate*n*average/amort+density)
            assumptions.append(f'工程院按{n}座密度、平均基础金价{average:g}、{amort}周期摊销及厅密度联动估算；不是保证收益')
        payback = construction.total/net.total if (construction.complete and net is not None and net.complete and net.total>0) else None
        if payback is not None:Resource.finite_number(payback)
        return BuildingEstimate(kind,level,other_slots,construction,inputs,outputs,cls.ENERGY_USE,
                                cycle.energy,energy_cost,net,payback,outputs_sell,basis,energy_reference,
                                tuple(cls.construction_cost(l) for l in range(1,len(cls.GOLD_COSTS)+1)),
                                cycle.inputs,cycle.outputs,cls.MIN_SLOTS,cls.MAX_PER_SITE,cls.SELF_BUILD_LIMIT,
                                cls.SITE_RESOURCE,tuple(cls.EFFECTS.items()),projected_slots,tuple(assumptions))
