"""Player-facing economy query API; tool schemas live in the distribution tool layer."""
from dataclasses import replace

from .objects import EconomicQueries


class EconomyAPI:
    def __init__(self, world):
        self.world = world
        self.queries = EconomicQueries(world.runtime, lambda cap, method, *args: world.runtime.object_call(world, cap, method, *args))

    def balance(self, actor):
        return self.queries.balance(actor)

    def report(self, actor, period=None, all=False):
        rows = self.queries.report(actor)
        if period is not None and all:
            raise ValueError('period 与 all 不能同时指定')
        if period is not None:
            if type(period) is not int or period < 0:
                raise ValueError('period 须为非负整数')
            selected = tuple(r for r in rows if r.end == period)
            if not selected:
                raise ValueError('指定周期没有已发布报表')
            return selected
        return rows if all else rows[-1:]

    def estimate(self, actor, kind=None, level=1, other_slots=0):
        if kind is None:
            return tuple(self.estimate(actor, key, level, other_slots) for key in sorted(self.queries.building_types()))
        policy = self.world.runtime.parameter(self.world, actor, 'build_cost_pct', 100)
        relation=(next((s for s in self.world.runtime.object_call(self.world,'native:diplomacy','snapshot',actor).vassals if s.subject==actor),None)
                  if 'native:diplomacy' in self.world.runtime.capabilities else None)
        return self.queries.estimate(kind, level, other_slots, policy_pct=policy,tribute_pct=relation.tribute_percent if relation else 0)

    def market(self, actor):
        return self.queries.market()

    def _trade(self, actor, resource, quantity, side):
        trade = self.world.runtime.object_call(self.world, 'native:market', side, actor, resource, quantity)
        stocks = self.world.runtime.object_call(self.world, 'native:resource-inspection', 'snapshot', actor)
        remaining = next((row.amount for row in stocks if getattr(row, 'id', None) == resource), 0)
        return replace(trade, remaining=remaining)

    def buy(self, actor, resource, quantity):
        return self._trade(actor, resource, quantity, 'buy')

    def sell(self, actor, resource, quantity):
        return self._trade(actor, resource, quantity, 'sell')
