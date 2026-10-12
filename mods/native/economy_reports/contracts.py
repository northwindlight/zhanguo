"""Immutable economic facts and values; totals never hide unpriced resources."""
from dataclasses import dataclass
from engine.identifiers import content_id
from engine.resource import Resource

CATEGORIES = ('production','construction','import','export','recruit','military')


def integer(value, minimum=0):
    if type(value) is not int or value<minimum:raise ValueError('Invalid economy integer')
    return value


def owner_name(value):
    if not isinstance(value,str) or not value:raise ValueError('Invalid economy owner')
    return value


def leg(values):
    if not isinstance(values,(tuple,list)):raise ValueError('Invalid economic resource leg')
    result = []
    for pair in values:
        if not isinstance(pair,(tuple,list)) or len(pair)!=2:raise ValueError('Invalid economic resource entry')
        key,amount = pair
        content_id(key);Resource.finite_number(amount)
        if amount<0:raise ValueError('Negative economic flow')
        result.append((key,amount))
    if len(dict(result))!=len(result):raise ValueError('Duplicate economic resource')
    return tuple(result)


@dataclass(frozen=True)
class Valuation:
    resource: str
    amount: int | float
    price: int | float | None
    value: int | float | None
    source: str


@dataclass(frozen=True)
class EconomicFact:
    id: str
    owner: str
    period: int
    category: str
    inputs: tuple = ()
    outputs: tuple = ()
    cash: int = 0

    def __post_init__(self):
        content_id(self.id);owner_name(self.owner);integer(self.period);integer(self.cash)
        if self.category not in CATEGORIES:raise ValueError('Unknown economic category')
        object.__setattr__(self,'inputs',leg(self.inputs))
        object.__setattr__(self,'outputs',leg(self.outputs))
        if self.category not in ('import','export') and self.cash:
            raise ValueError('Cash field is reserved for executed trades')


@dataclass(frozen=True)
class Money:
    known: float = 0
    unpriced: tuple = ()

    def __post_init__(self):
        Resource.finite_number(self.known)
        keys = tuple(self.unpriced)
        for key in keys:content_id(key)
        if len(set(keys))!=len(keys):raise ValueError('Duplicate unpriced resource')
        object.__setattr__(self,'unpriced',keys)

    @property
    def total(self):return None if self.unpriced else self.known

    @property
    def complete(self):return not self.unpriced

    def plus(self, other, sign=1):
        return Money(self.known+sign*other.known,tuple(dict.fromkeys((*self.unpriced,*other.unpriced))))


@dataclass(frozen=True)
class ValuedFact:
    fact: EconomicFact
    inputs: Money
    outputs: Money


@dataclass(frozen=True)
class TurnReport:
    owner: str
    period: int
    production: Money
    inputs: Money
    cash_production: float
    gdp: Money
    investment: Money
    recruitment: Money
    military: Money
    imports: float
    exports: float
    inventory_assets: Money
    building_assets: Money
    total_assets: Money
    spending: Money
    cumulative_spending: Money
    liabilities: int = 0
    net_assets: Money = Money()


@dataclass(frozen=True)
class PeriodReport:
    owner: str
    start: int
    end: int
    span: int
    gdp: Money
    investment: Money
    recruitment: Money
    military: Money
    imports: float
    exports: float
    trade_ratio: float | None
    inventory_assets: Money
    building_assets: Money
    total_assets: Money
    gdp_growth: float | None
    investment_growth: float | None
    assets_growth: float | None
    military_ratio: float | None
    liabilities: int = 0
    net_assets: Money = Money()


@dataclass(frozen=True)
class EconomySnapshot:
    next_period: int
    events: tuple
    turns: tuple
    reports: tuple
