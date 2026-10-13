"""Economic queries and explicit estimates over current registered content."""
from dataclasses import dataclass

from engine.resource import Resource
from mods.native.buildings.types import NativeBuilding
from mods.native.economy_reports.contracts import Money, TurnReport, integer


@dataclass(frozen=True)
class PublicMarket:
    currency: str
    period: int
    prices: tuple
    inventory: tuple
    reference: tuple
    buy_prices: tuple
    sell_prices: tuple


@dataclass(frozen=True)
class BalanceSheet:
    owner: str
    inventory: Money
    buildings: Money
    gross_assets: Money
    stocks: tuple
    latest_turn: TurnReport | None


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
    projected_slots: int
    academy_saved_per_period: float
    academy_density_per_period: float
    costs_by_level: tuple
    recipe_inputs: tuple
    recipe_outputs: tuple
    min_slots: int
    max_per_site: int | None
    self_build_limit: int | None
    site_resource: str | None
    effects: tuple


class EconomicQueries:
    def __init__(self, runtime, call):self._runtime,self._call = runtime,call

    def _value(self, amounts, side='mid'):
        known,missing = 0,[]
        for row in self._call('economy:valuation','quote',dict(amounts),side):
            if row.value is None:missing.append(row.resource)
            else:known += row.value
        return Money(known,tuple(missing))

    def market(self):
        row = self._call('native:market','snapshot')
        amounts={key:1 for key,_ in row.prices}
        buy=tuple((v.resource,v.price) for v in self._call('economy:valuation','quote',amounts,'buy'))
        sell=tuple((v.resource,v.price) for v in self._call('economy:valuation','quote',amounts,'sell'))
        return PublicMarket(row.currency,row.period,row.prices,row.inventory,row.reference,buy,sell)

    def balance(self, owner):
        stocks = self._call('native:resource-inspection','snapshot',owner)
        inventory = self._value({row.id:row.amount for row in stocks})
        buildings = self._value(self._call('native:buildings','replacement_costs',owner))
        turns = self._call('native:economy','turns',owner)
        return BalanceSheet(owner,inventory,buildings,inventory.plus(buildings),stocks,turns[-1] if turns else None)

    def report(self, owner):
        self._call('native:resource-inspection','snapshot',owner)
        return self._call('native:economy','reports',owner)

    def building_types(self):
        return {key:cls for key,cls in self._runtime.content.definitions()
                if isinstance(cls,type) and issubclass(cls,NativeBuilding)}

    def _energy_cost(self):
        types=self.building_types()
        # Preserve the native ROI benchmark: one wood powers two energy units.
        candidates=[('native:wood-power-plant',types['native:wood-power-plant'])] if 'native:wood-power-plant' in types else sorted(types.items())
        for key,cls in candidates:
            cycle = cls(1,'native:estimate').cycle()
            if cycle.energy:
                fuel = self._value(cycle.inputs,'buy')
                if fuel.complete:return fuel.total/cycle.energy,key
        return None,None

    def estimate(self, kind, level=1, other_slots=1, penalty=0, discount=0, policy_pct=100, existing=()):
        integer(other_slots)
        cls = self.building_types()[kind]
        cost = cls.construction_cost(level,penalty=penalty,discount=discount,policy_pct=policy_pct)
        projected_slots=3 if cls.EFFECTS.get('gold_per_slot') else 0
        cycle = cls(1,'native:estimate',level=level).cycle(other_slots=other_slots+projected_slots)
        basis='sell' if cls.PHASE=='gather' else 'buy' if cls.PHASE=='industry' else 'mid'
        construction,inputs,outputs = self._value(cost),self._value(cycle.inputs,'buy'),self._value(cycle.outputs,basis)
        outputs_sell=self._value(cycle.outputs,'sell')
        energy_cost,energy_reference = self._energy_cost()
        net = outputs.plus(inputs,-1)
        # Electricity is not tradable revenue: power plants only spend fuel.
        if cls.ENERGY_USE:
            net = None if energy_cost is None else net.plus(Money(-cls.ENERGY_USE*energy_cost))
        saved=density=0
        if cls.BUILD_DISCOUNT:
            types=self.building_types();n=len(existing)
            average=sum(types[key].construction_cost(lvl).get('native:gold',0) for key,lvl in existing)/n if n else 0
            disc=cls.BUILD_DISCOUNT/100
            saved=disc*n*average/30
            halls=[c for c in types.values() if c.EFFECTS.get('gold_per_slot')]
            hall=next(iter(halls),None)
            hall_ok=hall is not None and (any(key==hall.TYPE_ID for key,_ in existing) or n>=hall.MIN_SLOTS)
            density=disc*n*hall.EFFECTS['gold_per_slot'] if hall_ok else 0
            net=net.plus(Money(saved+density))
        payback = construction.total/net.total if (construction.complete and net is not None and net.complete and net.total>0) else None
        if payback is not None:Resource.finite_number(payback)
        return BuildingEstimate(kind,level,other_slots,construction,inputs,outputs,cls.ENERGY_USE,
                                cycle.energy,energy_cost,net,payback,outputs_sell,basis,energy_reference,
                                projected_slots,saved,density,
                                tuple(cls.construction_cost(l) for l in range(1,len(cls.GOLD_COSTS)+1)),
                                cycle.inputs,cycle.outputs,cls.MIN_SLOTS,cls.MAX_PER_SITE,cls.SELF_BUILD_LIMIT,
                                cls.SITE_RESOURCE,tuple(cls.EFFECTS.items()))
