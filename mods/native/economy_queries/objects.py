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
    equilibrium: tuple


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


class EconomicQueries:
    def __init__(self, runtime, call):self._runtime,self._call = runtime,call

    def _value(self, amounts):
        known,missing = 0,[]
        for row in self._call('economy:valuation','quote',dict(amounts)):
            if row.value is None:missing.append(row.resource)
            else:known += row.value
        return Money(known,tuple(missing))

    def market(self):
        row = self._call('native:market','snapshot')
        return PublicMarket(row.currency,row.period,row.prices,row.equilibrium)

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
        costs = []
        for key,cls in self.building_types().items():
            cycle = cls(1,'native:estimate').cycle()
            if cycle.energy:
                fuel = self._value(cycle.inputs)
                if fuel.complete:costs.append(fuel.total/cycle.energy)
        return min(costs) if costs else None

    def estimate(self, kind, level=1, other_slots=0, penalty=0, discount=0, policy_pct=100):
        integer(other_slots)
        cls = self.building_types()[kind]
        cost = cls.construction_cost(level,penalty=penalty,discount=discount,policy_pct=policy_pct)
        cycle = cls(1,'native:estimate',level=level).cycle(other_slots=other_slots)
        construction,inputs,outputs = self._value(cost),self._value(cycle.inputs),self._value(cycle.outputs)
        energy_cost = self._energy_cost()
        net = outputs.plus(inputs,-1)
        if cycle.energy or cls.ENERGY_USE:
            net = None if energy_cost is None else net.plus(Money((cycle.energy-cls.ENERGY_USE)*energy_cost))
        payback = construction.total/net.total if (construction.complete and net is not None and net.complete and net.total>0) else None
        if payback is not None:Resource.finite_number(payback)
        return BuildingEstimate(kind,level,other_slots,construction,inputs,outputs,cls.ENERGY_USE,
                                cycle.energy,energy_cost,net,payback)
