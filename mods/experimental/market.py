"""Finite commodity buffer and multiplicative inventory feedback pricing."""
from collections.abc import Mapping
from dataclasses import dataclass
import math

from engine.runtime import GameMod, RuleSection
from mods.native.market.contracts import PricePath, PriceSettlement, integer, number
from mods.native.market.mod import MarketMod
from mods.native.market.objects import Market, MarketSnapshot
from mods.native.economy_queries.mod import QueriesMod
from mods.native.economy_queries.objects import EconomicQueries, PublicMarket


class InventoryPricing:
    def walk(self, listing, price, quantity, side, participants, spread):
        # Quotes stay fixed within the period; inventory feeds next period's price.
        return PricePath(price, price * quantity * (1 + spread / 2 if side == 'buy' else 1 - spread / 2))

    def settle(self, listing, price, inventory, target, eta, low, high):
        gap = (target - inventory) / target
        # Absolute numerical guardrails, unrelated to the opening base price.
        log_price = math.log(price) + eta * gap
        next_price = (low if log_price <= math.log(low) else
                      high if log_price >= math.log(high) else math.exp(log_price))
        return PriceSettlement(next_price, next_price)


class InventoryPricingMod(GameMod):
    def mixins(self):return ()
    def provides(self):return ('market:pricing',)
    def objects(self, world):return {'market:pricing':InventoryPricing()}


@dataclass(frozen=True)
class BufferSnapshot(MarketSnapshot):
    inventory: tuple
    targets: tuple


class InventoryMarket(Market):
    def _settlement_cash(self, exact, carry, side):
        # Carry is fractional currency owed to this owner, across both sides.
        # Charging ceil on buys and crediting floor on sells prevents roundtrip
        # profit from independently flooring both legs.
        if side == 'sell':return self._cash(exact, carry)
        total = exact - carry
        gold = max(0, math.ceil(total - 1e-9))
        remainder = gold - total
        return gold, 0.0 if remainder < 1e-9 else remainder

    def configure_buffer(self, targets, initial, policy):
        self._write()
        self._targets = dict(targets)
        self._inventory = dict(initial)
        self._policy = dict(policy)

    def quote(self, resource, quantity, side, owner=None):
        self._listing(resource)
        integer(quantity, 1)
        if side == 'buy' and quantity > self._inventory[resource]:
            raise ValueError(f'Market inventory insufficient: {resource}; available={self._inventory[resource]}')
        return super().quote(resource, quantity, side, owner)

    def _trade(self, owner, resource, quantity, side):
        # Parent validates the entire resource exchange before committing.
        trade = super()._trade(owner, resource, quantity, side)
        self._inventory[resource] += quantity if side == 'sell' else -quantity
        return trade

    def settle(self, production, consumption, *, participants=None):
        self._write()
        if not isinstance(production, Mapping) or not isinstance(consumption, Mapping):
            raise ValueError('Supply/demand inputs must be mappings')
        count = self.participants if participants is None else integer(participants)
        prices = {}
        for key, listing in self._listings.items():
            # Validate reported economic facts, but never treat them as deliveries.
            number(production.get(key, 0));number(consumption.get(key, 0))
            result = self._pricing('settle', listing, self._prices[key], self._inventory[key],
                                   self._targets[key], self._policy['Eta'],
                                   self._policy['MinPrice'], self._policy['MaxPrice'])
            if not isinstance(result, PriceSettlement):
                raise ValueError('Invalid inventory pricing settlement')
            prices[key] = number(result.price, positive=True)
        closed = tuple(row for row in self._trades if row.period == self.period)
        self._prices, self._equilibrium = prices, dict(prices)
        self._participants = count
        self._period += 1
        return closed

    def snapshot(self):
        row = super().snapshot()
        return BufferSnapshot(**row.__dict__, inventory=tuple(self._inventory.items()),
                              targets=tuple(self._targets.items()))

    def to_save(self):
        return {**super().to_save(), 'inventory':dict(self._inventory),
                'targets':dict(self._targets), 'buffer_policy':dict(self._policy)}

    def restore(self, data):
        self._write()
        if not isinstance(data, dict):raise ValueError('Invalid buffer save')
        core = dict(data)
        inventory = core.pop('inventory', None)
        targets = core.pop('targets', None)
        policy = core.pop('buffer_policy', None)
        if targets != self._targets or policy != self._policy:
            raise ValueError('Market buffer configuration mismatch')
        if not isinstance(inventory, dict) or set(inventory) != set(self.ids):
            raise ValueError('Invalid buffer inventory')
        inventory = {key:integer(inventory[key]) for key in self.ids}
        if not isinstance(core.get('prices'), dict):raise ValueError('Invalid market prices')
        for value in core['prices'].values():
            if not self._policy['MinPrice'] <= number(value, positive=True) <= self._policy['MaxPrice']:
                raise ValueError('Market price outside numerical limits')
        # Parent restore validates every field before writing any state.
        super().restore(core)
        self._inventory = inventory


class InventoryMarketMod(MarketMod):
    def configure(self, declaration, path):
        super().configure(declaration, path)
        self.targets = {row['Resource']:integer(row['TargetInventory'], 1) for row in declaration['Listings']}
        self.initial = {row['Resource']:integer(row['InitialInventory']) for row in declaration['Listings']}
        self.policy = dict(declaration['Buffer'])
        if set(self.policy) != {'Eta','Spread','MinPrice','MaxPrice'}:
            raise ValueError('Invalid buffer policy')
        for value in self.policy.values():number(value, positive=True)
        if self.policy['Spread'] >= 2 or self.policy['MinPrice'] >= self.policy['MaxPrice']:
            raise ValueError('Invalid spread or price limits')
        for listing in self.listings:
            if not self.policy['MinPrice'] <= listing.base_price <= self.policy['MaxPrice']:
                raise ValueError('Opening price outside numerical limits')

    def initialize_world(self, world):
        # Reuse native registration/tradeability validation.
        super().initialize_world(world)
        def pricing(method, *args):
            if method == 'walk':args = (*args, self.policy['Spread'])
            return self.runtime.object_call(world, 'market:pricing', method, *args)
        world.market = InventoryMarket(self.listings, self.currency, pricing,
            lambda *args:self.runtime.object_call(world, 'native:resource-exchange', 'exchange', *args))
        world.market.configure_buffer(self.targets, self.initial, self.policy)

    def rule_sections(self, world=None):
        targets = ', '.join(f'{key}={value}' for key, value in self.targets.items())
        return (RuleSection('库存市场',
            f'市场是有限商品缓冲。买入扣市场库存，卖出补市场库存；库存不足整单拒绝，不部分成交。'
            f'生产、消费、转账不等于交付市场，不直接改变市场库存。目标库存：{targets}；'
            f'开局库存：{self.initial}。目标固定，不随国家数量或回合补货。'
            f'每期结算 p_next=p*exp(eta*(target-I)/target)，eta={self.policy["Eta"]}。'
            f'本期成交不改报价，买卖分别在报价加减 {self.policy["Spread"]/2:.0%}。'
            f'不回归基准价；偏离目标但没有交易仍会持续调价。'
            f'绝对数值边界为 {self.policy["MinPrice"]} 到 {self.policy["MaxPrice"]}。'
            'market 显示库存和目标，market_quote 返回完整报价；金币零头逐账户累积。'
            '金币结算沿用外部市场流动性，不设市场金币储备；商品交付严格受库存限制。'
            'equilibrium 字段仅为下期报价的接口镜像，不代表固定均衡价。',
            ('市场','价格','库存','交易','market'), '有限库存与每回合库存反馈报价'),)


@dataclass(frozen=True)
class PublicBufferMarket(PublicMarket):
    inventory: tuple
    targets: tuple


class InventoryEconomicQueries(EconomicQueries):
    def market(self):
        row = self._call('native:market', 'snapshot')
        return PublicBufferMarket(row.currency, row.period, row.prices, row.equilibrium,
                                  row.inventory, row.targets)


class InventoryQueriesMod(QueriesMod):
    def _queries(self, world):
        return InventoryEconomicQueries(self.runtime,
            lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))

    def objects(self, world):return {'economy:queries':self._queries(world)}
