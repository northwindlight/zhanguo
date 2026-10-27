"""Owned market state: inventory, reference stock, refill and currency carry.

The market owns everything that changes over time; the pricing engine is a pure
curve it consults. Two numbers move: the inventory (trades in, outside refill
in) and the reference stock the price is measured against (net flow drifts it).
One quote is published per period and every buyer in that period gets it, so
arrival order can never move anybody's price; order size is priced by the curve
itself, because a deeper order sweeps further into the buffer.
"""
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
import math
from types import MappingProxyType

from engine.identifiers import content_id
from engine.runtime import DomainRejection, ModError
from engine.state import active_unit
from .contracts import BufferPolicy, PricePath, PriceSettlement, integer, number
from .automation import AutomaticTrading


@dataclass(frozen=True)
class Quote:
    owner: str | None
    resource: str
    side: str
    quantity: int
    gold: int
    exact_total: float
    price_before: float
    price_after: float
    carry_before: float
    carry_after: float


@dataclass(frozen=True)
class Trade(Quote):
    period: int


@dataclass(frozen=True)
class MarketSnapshot:
    currency: str
    period: int
    inventory: tuple
    reference: tuple
    prices: tuple
    carry: tuple
    trades: tuple


@dataclass(frozen=True)
class Churn:
    """本期同商品双向成交里的纯倒手部分。

    `quantity` 是重叠量，`loss` 是重叠量按两边成交均价折算的净亏（负值＝反倒赚了）。
    另外带上两侧各自的量与均价，回执才能把「买多少均价多少、卖多少均价多少」讲清楚，
    而不是只丢一个净亏数字。
    """
    quantity: int
    loss: float
    buy_quantity: int = 0
    buy_average: float = 0.0
    sell_quantity: int = 0
    sell_average: float = 0.0


@dataclass(frozen=True)
class TradeReceipt(Trade):
    """成交回执 = 成交本身 + 本期倒手量 + 成交后余量；`Trade` 本身仍是存档里的那条记录。

    倒手量由市场自己算；余量是本国库存，查询层拿到后回填（市场不持有资源账户）。
    """
    churn: Churn = Churn(0, 0.0)
    remaining: int = 0


class Market(AutomaticTrading):
    def __init__(self, listings, currency, policy, pricing_call, exchange_call, inspect_call=None):
        self._listings = MappingProxyType({item.resource: item for item in listings})
        if len(self._listings) != len(listings):
            raise ValueError('Duplicate market listing')
        self._currency = content_id(currency)
        if currency in self._listings:
            raise ValueError('Currency cannot be listed')
        if not isinstance(policy, BufferPolicy):
            raise ValueError('Invalid buffer policy')
        self._policy = policy
        self._pricing, self._exchange = pricing_call, exchange_call
        self._reference = {key: float(item.depth) for key, item in self._listings.items()}
        self._inventory = {key: item.initial for key, item in self._listings.items()}
        self._carry, self._trades, self._volume = {}, [], {}
        self._period = 0
        self._world, self._domain = None, None
        self._init_automation(inspect_call)
        self._prices = self._levels()

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('Market already belongs to another world')
        self._world, self._domain = world, world.runtime.state_owners[key]
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != self._domain:
            raise ModError(f'{caller} cannot write market; use its declared interface')

    @property
    def ids(self):return tuple(self._listings)
    @property
    def currency(self):return self._currency
    @property
    def period(self):return self._period
    @property
    def inventory(self):return dict(self._inventory)
    @property
    def reference(self):return dict(self._reference)

    def _listing(self, resource):
        content_id(resource)
        if resource not in self._listings:
            raise ValueError(f'Resource is not listed: {resource}')
        return self._listings[resource]

    def price(self, resource):
        self._listing(resource)
        return round(self._prices[resource], 1)

    @staticmethod
    def _owner(owner):
        if not isinstance(owner, str) or not owner:
            raise ValueError('Account owner must be nonempty')

    @staticmethod
    def _cash(exact, carry):
        total = number(exact + carry)
        gold = math.floor(total + 1e-9)
        remainder = total - gold
        return gold, 0.0 if remainder < 1e-9 else remainder

    def _settlement_cash(self, exact, carry, side):
        # Carry is fractional currency owed to this owner, across both sides.
        # Charging ceil on buys and crediting floor on sells prevents roundtrip
        # profit from independently flooring both legs.
        if side == 'sell':return self._cash(exact, carry)
        total = exact - carry
        gold = max(0, math.ceil(total - 1e-9))
        remainder = gold - total
        return gold, 0.0 if remainder < 1e-9 else remainder

    def _levels(self):
        # The buffer prices itself; the declaration's base price is only the
        # reference point a full buffer sits at.
        prices = {}
        for key, listing in self._listings.items():
            result = self._pricing('level', listing, self._inventory[key], self._reference[key], self._policy)
            if not isinstance(result, PriceSettlement):
                raise ValueError('Invalid pricing engine result')
            prices[key] = number(result.price, positive=True)
        return prices

    def _period_trades(self):
        """本期成交。成交按发生顺序追加，本期永远是列表的**尾段**——不问全史，
        否则每一期都要为整局的成交记录再付一次钱（6 个商品 × 全部历史）。"""
        rows=[]
        for row in reversed(self._trades):
            if row.period!=self._period:break
            rows.append(row)
        return rows

    def _drift(self, key):
        # Net flow moves the market's own scale: a market being bought out
        # expects to hold more, one being dumped expects to hold less. The
        # reference only ever moves here, at the period boundary, so it cannot
        # make one player's quote depend on another's timing.
        net = sum(row.quantity if row.side == 'buy' else -row.quantity
                  for row in self._period_trades() if row.resource == key)
        return max(1.0, self._reference[key] + self._policy.flow * net)

    def _refill(self, key):
        # The outside world closes part of the gap to the reference every
        # period. It never drains a surplus, and no nation's production is a
        # delivery.
        gap = self._reference[key] - self._inventory[key]
        if gap <= 0:return self._inventory[key]
        return self._inventory[key] + min(math.ceil(gap), math.ceil(gap * self._policy.refill))

    def quote(self, resource, quantity, side, owner=None):
        listing = self._listing(resource)
        integer(quantity, 1)
        if side not in ('buy', 'sell'):
            raise ValueError('Invalid trade side')
        if side == 'buy' and quantity > self._inventory[resource]:
            raise DomainRejection('市场库存不足', {'reason': 'market_inventory', 'resource': resource,
                                                   'requested': quantity, 'available': self._inventory[resource]})
        if owner is not None:self._owner(owner)
        # The period's quote is the same for everyone; only the size of this
        # order moves its own average price.
        price = self._prices[resource]
        volume = self._volume.get((owner, side, resource), 0) if owner is not None else 0
        path = self._pricing('walk', listing, price, quantity, side, volume, self._reference[resource], self._policy)
        if not isinstance(path, PricePath):
            raise ValueError('Invalid pricing engine walk result')
        # Validate even if a provider constructed an object without __init__.
        number(path.next_price, positive=True);number(path.exact_total)
        carry = self._carry.get(owner, 0.0) if owner is not None else 0.0
        gold, remainder = self._settlement_cash(path.exact_total, carry, side)
        return Quote(owner, resource, side, quantity, gold, path.exact_total,
                     price, path.next_price, carry, remainder)

    def equilibrium(self):
        """库存回到基准库存时的价格：现价与它比才知道贵贱。"""
        result = {}
        for key, listing in self._listings.items():
            settled = self._pricing('level', listing, self._reference[key], self._reference[key], self._policy)
            if not isinstance(settled, PriceSettlement):
                raise ValueError('Invalid pricing engine result')
            result[key] = round(number(settled.price, positive=True), 2)
        return result

    def trials(self, quantity=50):
        """固定数量大单的实际成交总额（买入/卖出各一），让模型不必自己沿曲线试算。"""
        integer(quantity, 1)
        rows = []
        for key in self._listings:
            affordable = min(quantity, self._inventory[key])
            buy = self.quote(key, affordable, 'buy').exact_total if affordable > 0 else None
            sell = self.quote(key, quantity, 'sell').exact_total
            rows.append((key, None if buy is None else round(buy, 2), round(sell, 2)))
        return tuple(rows)

    def unit_price(self, resource, side):
        """Marginal valuation price for the period quote, independent of carry."""
        listing=self._listing(resource)
        if side not in ('buy','sell'):raise ValueError('Invalid trade side')
        path=self._pricing('walk',listing,self._prices[resource],1,side,0,self._reference[resource],self._policy)
        if not isinstance(path,PricePath):raise ValueError('Invalid pricing engine walk result')
        return number(path.exact_total)

    def _trade(self, owner, resource, quantity, side):
        self._write()
        self._owner(owner)
        quote = self.quote(resource, quantity, side, owner)
        trade = Trade(**asdict(quote), period=self.period)
        cash, goods = {self.currency: quote.gold}, {resource: quantity}
        # Both resource legs validate and commit together. Everything which
        # can reject a trade is completed before advancing market state.
        self._exchange(owner, cash if side == 'buy' else goods, goods if side == 'buy' else cash)
        # The buffer moves, the published quote does not: it is re-priced at
        # the period boundary, from whatever is left.
        self._inventory[resource] += quantity if side == 'sell' else -quantity
        key = (owner, side, resource)
        self._volume[key] = self._volume.get(key, 0) + quantity
        self._carry[owner] = quote.carry_after
        self._trades.append(trade)
        return trade

    def _receipt(self, trade):
        """成交回执：内部只存纯 Trade，回执另外带本期同商品的倒手量。

        空转只有在本回合内被告知才有用——等到结算再播报，这一单已经改不了了。
        倒手量是回执而不是市场状态，因此不进 `_trades`，存档格式不变。
        """
        if trade.owner is None:return trade
        return TradeReceipt(**{f.name: getattr(trade, f.name) for f in fields(trade)},
                            churn=self.churn(trade.owner, trade.resource))

    def buy(self, owner, resource, quantity):return self._receipt(self._trade(owner, resource, quantity, 'buy'))
    def sell(self, owner, resource, quantity):return self._receipt(self._trade(owner, resource, quantity, 'sell'))

    def churn(self, owner, resource):
        self._owner(owner);self._listing(resource)
        rows = [row for row in self._period_trades() if row.owner == owner and row.resource == resource]
        buy_n = sum(row.quantity for row in rows if row.side == 'buy')
        sell_n = sum(row.quantity for row in rows if row.side == 'sell')
        overlap = min(buy_n, sell_n)
        if not overlap:return Churn(0, 0.0)
        buy_gold = sum(row.gold for row in rows if row.side == 'buy')
        sell_gold = sum(row.gold for row in rows if row.side == 'sell')
        buy_average, sell_average = buy_gold/buy_n, sell_gold/sell_n
        return Churn(overlap, overlap*(buy_average - sell_average),
                     buy_n, buy_average, sell_n, sell_average)

    def settle(self):
        """Close the period in three steps: this period's net flow moves the
        reference stock, the outside world refills towards it, and the surviving
        inventory is priced against it. The old quote is never carried forward."""
        self._write()
        for key in self._listings:
            self._reference[key] = self._drift(key)
            self._inventory[key] = self._refill(key)
        self._volume.clear()
        self._prices = self._levels()
        closed = tuple(self._period_trades())
        self._period += 1
        return closed

    def summary(self):
        return MarketSnapshot(self.currency,self.period,tuple(self._inventory.items()),tuple(self._reference.items()),tuple(self._prices.items()),tuple(self._carry.items()),())

    def snapshot(self):
        return MarketSnapshot(self.currency, self.period, tuple(self._inventory.items()),
                              tuple(self._reference.items()), tuple(self._prices.items()),
                              tuple(self._carry.items()), tuple(self._trades))

    def __deepcopy__(self, memo):
        # State readers get an immutable value without live callback/world refs.
        result = self.snapshot();memo[id(self)] = result
        return result

    def to_save(self):
        return {'currency': self.currency, 'period': self.period, 'reference': dict(self._reference),
                'inventory': dict(self._inventory), 'prices': dict(self._prices),
                'carry': dict(self._carry), 'trades': [asdict(row) for row in self._trades],
                'automation':self._automation_save()}

    def restore(self, data):
        self._write()
        if not isinstance(data, dict) or set(data) != {'currency','period','reference','inventory','prices','carry','trades','automation'}:
            raise ValueError('Invalid market save')
        if data['currency'] != self.currency:
            raise ValueError('Market currency mismatch')
        period = integer(data['period'])
        auto_rules,auto_runs=self._automation_restore(data['automation'],period)
        if not isinstance(data['reference'],dict) or set(data['reference']) != set(self.ids):
            raise ValueError('Market save does not match listings')
        reference = {key:number(data['reference'][key], positive=True) for key in self.ids}
        if not isinstance(data['inventory'],dict) or set(data['inventory']) != set(self.ids):
            raise ValueError('Market save does not match listings')
        inventory = {key:integer(data['inventory'][key]) for key in self.ids}
        raw = data['prices']
        if not isinstance(raw, dict) or set(raw) != set(self.ids):
            raise ValueError('Market save does not match listings')
        prices = {key:number(raw[key], positive=True) for key in self.ids}
        for value in prices.values():
            if not self._policy.low <= value <= self._policy.high:
                raise ValueError('Market price outside numerical limits')
        if not isinstance(data['carry'], dict) or not isinstance(data['trades'], list):
            raise ValueError('Invalid market carry/trades')
        carry = {}
        for owner, amount in data['carry'].items():
            self._owner(owner)
            if number(amount) >= 1:raise ValueError('Invalid currency carry')
            carry[owner] = amount
        trades = []
        for row in data['trades']:
            if not isinstance(row, dict) or set(row) != set(Trade.__dataclass_fields__):
                raise ValueError('Invalid trade record')
            self._owner(row['owner']);self._listing(row['resource'])
            integer(row['quantity'], 1);integer(row['gold']);integer(row['period'])
            if row['side'] not in ('buy', 'sell') or row['period'] > period:
                raise ValueError('Invalid trade side/period')
            number(row['exact_total'])
            for field in ('price_before','price_after'):number(row[field], positive=True)
            for field in ('carry_before','carry_after'):
                if number(row[field]) >= 1:raise ValueError('Invalid trade carry')
            expected_gold, expected_carry = self._settlement_cash(row['exact_total'], row['carry_before'], row['side'])
            if row['gold'] != expected_gold or not math.isclose(row['carry_after'], expected_carry, abs_tol=1e-9):
                raise ValueError('Inconsistent trade settlement')
            trades.append(Trade(**row))
        self._prices, self._inventory, self._reference = prices, inventory, reference
        self._carry, self._trades = carry, trades
        self._period = period
        self._auto_rules,self._auto_runs=auto_rules,auto_runs
        # Volume is derived state: rebuild it from the open period's trades.
        self._volume = {}
        for row in trades:
            if row.period != period:continue
            key = (row.owner, row.side, row.resource)
            self._volume[key] = self._volume.get(key, 0) + row.quantity
