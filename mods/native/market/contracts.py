"""Market/pricing contract. No concrete algorithm is imported by the market."""
from abc import ABC, abstractmethod
from dataclasses import dataclass

from engine.identifiers import content_id
from engine.resource import Resource


def number(value, *, positive=False):
    Resource.finite_number(value)
    if value < 0 or positive and value == 0:
        raise ValueError('Expected a positive number' if positive else 'Expected a nonnegative number')
    return value


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid integer')
    return value


@dataclass(frozen=True)
class Listing:
    resource: str
    base_price: float
    depth: int
    initial: int = 0

    def __post_init__(self):
        content_id(self.resource)
        number(self.base_price, positive=True)
        integer(self.depth, 1)
        integer(self.initial)


@dataclass(frozen=True)
class BufferPolicy:
    """Reference stock is what the price is measured against and what the
    outside world refills towards. It starts at the declared Depth and drifts
    with the period's net flow, so demand moves the market's own scale."""
    elasticity: float
    flow: float
    refill: float
    spread: float
    low: float
    high: float

    def __post_init__(self):
        for key in ('elasticity','flow','refill','spread'):number(getattr(self,key))
        for key in ('low','high'):number(getattr(self,key), positive=True)
        if self.elasticity <= 0:raise ValueError('Invalid price elasticity')
        if not 0 < self.flow <= 1:raise ValueError('Invalid flow coefficient')
        if not 0 <= self.spread < 2:raise ValueError('Invalid spread')
        if not 0 < self.refill <= 1:raise ValueError('Invalid refill share')
        if self.low >= self.high:raise ValueError('Invalid price limits')


@dataclass(frozen=True)
class PricePath:
    next_price: float
    exact_total: float

    def __post_init__(self):
        number(self.next_price, positive=True)
        number(self.exact_total)


@dataclass(frozen=True)
class PriceSettlement:
    price: float

    def __post_init__(self):
        number(self.price, positive=True)


class PricingEngine(ABC):
    """Stateless provider. Inputs/results are immutable; both methods are pure.

    The engine is the price curve and nothing else: it maps a listing plus the
    buffer to prices. The market owns inventory, outside refill, the period's
    quote, integer currency carry and trade records.
    """
    @abstractmethod
    def walk(self, listing, price, quantity, side, volume, reference, policy):
        """Cost of one order against the period's quote. Volume is what this
        owner has already traded on that side this period, reference is the
        stock the period prices against: splitting an order cannot dodge the
        premium, and nobody else's trades can move it."""
        raise NotImplementedError

    @abstractmethod
    def level(self, listing, inventory, reference, policy):
        """The next period's quote, priced from inventory against reference."""
        raise NotImplementedError
