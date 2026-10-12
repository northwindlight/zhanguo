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

    def __post_init__(self):
        content_id(self.resource)
        number(self.base_price, positive=True)
        integer(self.depth, 1)


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
    equilibrium: float

    def __post_init__(self):
        number(self.price, positive=True)
        number(self.equilibrium, positive=True)


class PricingEngine(ABC):
    """Stateless provider. Inputs/results are immutable; quote must be pure.

    Engine owns price evolution and supply/demand interpretation. Market owns
    inventory settlement, integer currency carry and transaction records.
    """
    @abstractmethod
    def walk(self, listing, price, quantity, side, participants):
        raise NotImplementedError

    @abstractmethod
    def settle(self, listing, price, production, consumption):
        raise NotImplementedError
