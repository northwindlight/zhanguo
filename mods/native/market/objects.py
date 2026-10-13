"""Owned market state; inventory and pricing are declared object interfaces."""
from collections.abc import Mapping
from dataclasses import asdict, dataclass
import math
from types import MappingProxyType

from engine.identifiers import content_id
from engine.runtime import ModError
from engine.state import active_unit
from .contracts import PricePath, PriceSettlement, integer, number


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
    participants: int
    prices: tuple
    equilibrium: tuple
    carry: tuple
    trades: tuple


@dataclass(frozen=True)
class Churn:
    quantity: int
    loss: float


class Market:
    def __init__(self, listings, currency, pricing_call, exchange_call):
        self._listings = MappingProxyType({item.resource: item for item in listings})
        if len(self._listings) != len(listings):
            raise ValueError('Duplicate market listing')
        self._currency = content_id(currency)
        if currency in self._listings:
            raise ValueError('Currency cannot be listed')
        self._pricing, self._exchange = pricing_call, exchange_call
        self._prices = {key: item.base_price for key, item in self._listings.items()}
        self._equilibrium = dict(self._prices)
        self._carry, self._trades = {}, []
        self._period, self._participants = 0, 1
        self._world, self._domain = None, None

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
    def participants(self):return self._participants

    def set_participants(self, count):
        self._write()
        self._participants = integer(count)

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
        return self._cash(exact, carry)

    def quote(self, resource, quantity, side, owner=None):
        listing = self._listing(resource)
        integer(quantity, 1)
        if side not in ('buy', 'sell'):
            raise ValueError('Invalid trade side')
        if owner is not None:self._owner(owner)
        path = self._pricing('walk', listing, self._prices[resource], quantity, side, self.participants)
        if not isinstance(path, PricePath):
            raise ValueError('Invalid pricing engine walk result')
        # Validate even if a provider constructed an object without __init__.
        number(path.next_price, positive=True);number(path.exact_total)
        carry = self._carry.get(owner, 0.0) if owner is not None else 0.0
        gold, remainder = self._settlement_cash(path.exact_total, carry, side)
        return Quote(owner, resource, side, quantity, gold, path.exact_total,
                     self._prices[resource], path.next_price, carry, remainder)

    def _trade(self, owner, resource, quantity, side):
        self._write()
        self._owner(owner)
        quote = self.quote(resource, quantity, side, owner)
        trade = Trade(**asdict(quote), period=self.period)
        cash, goods = {self.currency: quote.gold}, {resource: quantity}
        # Both resource legs validate and commit together. Everything which
        # can reject a trade is completed before advancing market state.
        self._exchange(owner, cash if side == 'buy' else goods, goods if side == 'buy' else cash)
        self._prices[resource] = quote.price_after
        self._carry[owner] = quote.carry_after
        self._trades.append(trade)
        return trade

    def buy(self, owner, resource, quantity):return self._trade(owner, resource, quantity, 'buy')
    def sell(self, owner, resource, quantity):return self._trade(owner, resource, quantity, 'sell')

    def churn(self, owner, resource):
        self._owner(owner);self._listing(resource)
        rows = [row for row in self._trades if row.period == self.period and
                row.owner == owner and row.resource == resource]
        buy_n = sum(row.quantity for row in rows if row.side == 'buy')
        sell_n = sum(row.quantity for row in rows if row.side == 'sell')
        overlap = min(buy_n, sell_n)
        if not overlap:return Churn(0, 0.0)
        buy_gold = sum(row.gold for row in rows if row.side == 'buy')
        sell_gold = sum(row.gold for row in rows if row.side == 'sell')
        return Churn(overlap, overlap*(buy_gold/buy_n - sell_gold/sell_n))

    def settle(self, production, consumption, *, participants=None):
        self._write()
        if not isinstance(production, Mapping) or not isinstance(consumption, Mapping):
            raise ValueError('Supply/demand inputs must be mappings')
        count = self.participants if participants is None else integer(participants)
        prices, equilibrium = {}, {}
        # Only listed resources are relevant. An unlisted registered resource
        # such as tea is ignored, including its supply/demand entries.
        for key, listing in self._listings.items():
            prod, use = number(production.get(key, 0)), number(consumption.get(key, 0))
            result = self._pricing('settle', listing, self._prices[key], prod, use)
            if not isinstance(result, PriceSettlement):
                raise ValueError('Invalid pricing engine settlement result')
            prices[key] = number(result.price, positive=True)
            equilibrium[key] = number(result.equilibrium, positive=True)
        closed = tuple(row for row in self._trades if row.period == self.period)
        self._prices, self._equilibrium = prices, equilibrium
        self._participants = count
        self._period += 1
        return closed

    def snapshot(self):
        return MarketSnapshot(self.currency, self.period, self.participants,
                              tuple(self._prices.items()), tuple(self._equilibrium.items()),
                              tuple(self._carry.items()), tuple(self._trades))

    def __deepcopy__(self, memo):
        # State readers get an immutable value without live callback/world refs.
        result = self.snapshot();memo[id(self)] = result
        return result

    def to_save(self):
        return {'currency': self.currency, 'period': self.period, 'participants': self.participants,
                'prices': dict(self._prices), 'equilibrium': dict(self._equilibrium),
                'carry': dict(self._carry), 'trades': [asdict(row) for row in self._trades]}

    def restore(self, data):
        self._write()
        if not isinstance(data, dict) or set(data) != {'currency','period','participants','prices','equilibrium','carry','trades'}:
            raise ValueError('Invalid market save')
        if data['currency'] != self.currency:
            raise ValueError('Market currency mismatch')
        period, participants = integer(data['period']), integer(data['participants'])
        values = []
        for field in ('prices', 'equilibrium'):
            raw = data[field]
            if not isinstance(raw, dict) or set(raw) != set(self.ids):
                raise ValueError('Market save does not match listings')
            values.append({key: number(raw[key], positive=True) for key in self.ids})
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
            if row['side'] not in ('buy','sell') or row['period'] > period:
                raise ValueError('Invalid trade side/period')
            number(row['exact_total'])
            for field in ('price_before','price_after'):number(row[field], positive=True)
            for field in ('carry_before','carry_after'):
                if number(row[field]) >= 1:raise ValueError('Invalid trade carry')
            expected_gold, expected_carry = self._settlement_cash(row['exact_total'], row['carry_before'], row['side'])
            if row['gold'] != expected_gold or not math.isclose(row['carry_after'], expected_carry, abs_tol=1e-9):
                raise ValueError('Inconsistent trade settlement')
            trades.append(Trade(**row))
        self._prices, self._equilibrium = values
        self._carry, self._trades = carry, trades
        self._period, self._participants = period, participants
