"""Resource template: identity, amount invariants and inventory behavior.

Concrete content belongs to mods. No native resource IDs or translated names
are known here. Subclasses may override validation and valuation.
"""
from dataclasses import dataclass
import math

from .identifiers import content_id
from .state import active_unit
from .runtime import ModError, DomainRejection


@dataclass(frozen=True)
class ResourceSnapshot:
    id: str
    owner: str
    amount: int | float


class Resource:
    TYPE_ID = ''
    NAME_KEY = ''
    TRADEABLE = True

    def __init__(self, owner: str, amount: int | float = 0):
        content_id(self.TYPE_ID)
        if not isinstance(owner, str) or not owner:
            raise ValueError('Resource owner must be nonempty')
        self.validate_amount(amount)
        self._owner = owner
        self._amount = amount
        self._domain = None

    @staticmethod
    def finite_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Resource amount must be a finite number')

    @classmethod
    def validate_amount(cls, amount):
        cls.finite_number(amount)
        if amount < 0:
            raise ValueError('Resource stock cannot be negative')

    @classmethod
    def value_of(cls, amount, unit_price):
        cls.finite_number(amount)
        cls.finite_number(unit_price)
        if amount < 0 or unit_price < 0:
            raise ValueError('Valuation inputs cannot be negative')
        result = amount * unit_price
        cls.finite_number(result)
        return result

    @property
    def id(self):
        return self.TYPE_ID

    @property
    def owner(self):
        return self._owner

    @property
    def amount(self):
        return self._amount

    def _write(self):
        caller = active_unit.get()
        if self._domain is not None and caller is not None and caller != self._domain:
            raise ModError(f'{caller} cannot write {self.id}; use its declared interface')

    def set_amount(self, amount):
        self._write()
        self.validate_amount(amount)
        self._amount = amount

    def credit(self, amount):
        self._write()
        self.finite_number(amount)
        if amount < 0:
            raise ValueError('Credit cannot be negative')
        self.set_amount(self.amount + amount)

    def can_consume(self, amount):
        self.finite_number(amount)
        if amount < 0:
            raise ValueError('Consumption cannot be negative')
        return amount == 0 or self.amount >= amount

    def consume(self, amount):
        self._write()
        if not self.can_consume(amount):
            raise DomainRejection('资源不足',{'reason':'insufficient_resource','resource':self.id,'required':amount,'available':self.amount})
        self.set_amount(self.amount - amount)

    def snapshot(self):
        return ResourceSnapshot(self.id, self.owner, self.amount)

    def to_save(self):
        return {'id': self.id, 'amount': self.amount}

    @classmethod
    def from_save(cls, owner, data):
        if not isinstance(data, dict) or set(data) != {'id', 'amount'} or data['id'] != cls.TYPE_ID:
            raise ValueError('Invalid resource record')
        return cls(owner, data['amount'])
