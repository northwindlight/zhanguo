"""Owned resource accounts and narrow, per-resource public interfaces."""
from collections.abc import Mapping

from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import ModError
from engine.state import active_unit


class ResourceAccounts:
    def __init__(self, resource_types):
        self._types = {}
        for cls in resource_types:
            if not isinstance(cls, type) or not issubclass(cls, Resource):
                raise ValueError('A resource type must extend Resource')
            key = content_id(cls.TYPE_ID)
            if key in self._types:
                raise ValueError(f'Duplicate resource type: {key}')
            self._types[key] = cls
        self._accounts = {}
        self._world = None
        self._domain = None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('Resource accounts already belong to another world')
        self._world = world
        self._domain = world.runtime.state_owners[key]
        for stocks in self._accounts.values():
            for stock in stocks.values():
                stock._domain = self._domain
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != self._domain:
            raise ModError(f'{caller} cannot write resource accounts')

    @property
    def ids(self):
        return tuple(self._types)

    def open(self, owner, amounts=None):
        self._write()
        if not isinstance(owner, str) or not owner:
            raise ValueError('Resource owner must be nonempty')
        if owner in self._accounts:
            raise ValueError('Resource account already exists')
        amounts = {} if amounts is None else amounts
        if not isinstance(amounts, Mapping) or set(amounts) - self._types.keys():
            raise ValueError('Unknown resource in account')
        stocks = {key: cls(owner, amounts.get(key, 0)) for key, cls in self._types.items()}
        for stock in stocks.values():
            stock._domain = self._domain
        self._accounts[owner] = stocks

    def _stock(self, owner, resource_id):
        # Missing accounts/IDs are errors; never invent stock or fallback aliases.
        return self._accounts[owner][resource_id]

    def balance(self, owner, resource_id):
        return self._stock(owner, resource_id).amount

    def credit(self, owner, resource_id, amount):
        self._write()
        self._stock(owner, resource_id).credit(amount)

    def consume(self, owner, resource_id, amount):
        self._write()
        self._stock(owner, resource_id).consume(amount)

    def exchange(self, owner, debits, credits):
        return self.exchange_batch(({'owner':owner,'debits':debits,'credits':credits},))

    def exchange_batch(self, operations):
        """Run all legs on candidate stocks, then commit the whole batch.

        Pure debit/credit is supported; each operation must contain a leg.
        Later operations see earlier candidate balances (production chains).
        """
        self._write()
        if not isinstance(operations, (list,tuple)) or not operations:
            raise ValueError('Exchange batch must be a nonempty sequence')
        candidates = {}
        for operation in operations:
            if not isinstance(operation, Mapping) or set(operation) != {'owner','debits','credits'}:
                raise ValueError('Invalid exchange operation')
            owner, debits, credits = operation['owner'], operation['debits'], operation['credits']
            if not isinstance(owner,str) or not owner or owner not in self._accounts:
                raise ValueError('Unknown resource owner')
            if not isinstance(debits, Mapping) or not isinstance(credits, Mapping) or not (debits or credits):
                raise ValueError('Exchange needs at least one leg')
            for leg in (debits, credits):
                for key, amount in leg.items():
                    self._stock(owner, key)
                    Resource.finite_number(amount)
                    if amount < 0:raise ValueError('Exchange amount cannot be negative')
            for key in dict.fromkeys((*debits,*credits)):
                identity = (owner,key)
                if identity not in candidates:
                    stock = self._stock(owner,key)
                    candidates[identity] = type(stock).from_save(owner,stock.to_save())
                candidate = candidates[identity]
                if key in debits:candidate.consume(debits[key])
                if key in credits:candidate.credit(credits[key])
                candidate.validate_amount(candidate.amount)
        for (owner,key),candidate in candidates.items():
            self._stock(owner,key)._amount = candidate.amount
        return tuple(self._stock(owner,key).snapshot() for owner,key in candidates)

    def owners(self):
        return tuple(self._accounts)

    def adjust_cash(self, currency, changes):
        """Atomic signed cash settlement, separately granted to financial modules."""
        from .types import Gold
        self._write()
        if currency not in self._types or not issubclass(self._types[currency], Gold):
            raise ValueError('Bank settlement requires a cash resource')
        if not isinstance(changes, Mapping):raise ValueError('Expected cash changes')
        candidates = {}
        for owner, delta in changes.items():
            Resource.finite_number(delta)
            stock = self._stock(owner, currency)
            candidate = type(stock).from_save(owner, stock.to_save())
            candidate.set_amount(stock.amount + delta)
            candidates[owner] = candidate
        for owner, candidate in candidates.items():
            self._stock(owner, currency)._amount = candidate.amount
        return tuple(self._stock(owner, currency).snapshot() for owner in candidates)

    def value(self, resource_id, amount, unit_price):
        return self._types[resource_id].value_of(amount,unit_price)

    def snapshot(self, owner):
        return tuple(stock.snapshot() for stock in self._accounts[owner].values())

    def interface(self, resource_id):
        if resource_id not in self._types:
            raise ValueError('Unknown resource type')
        return ResourceInterface(self, resource_id)

    def to_save(self):
        return {'accounts': [{'owner': owner, 'stocks': [stock.to_save() for stock in stocks.values()]}
                             for owner, stocks in self._accounts.items()]}

    @classmethod
    def from_save(cls, data, *, resource_types):
        result = cls(resource_types)
        if not isinstance(data, dict) or set(data) != {'accounts'} or not isinstance(data['accounts'], list):
            raise ValueError('Invalid resource accounts')
        for account in data['accounts']:
            if not isinstance(account, dict) or set(account) != {'owner', 'stocks'}:
                raise ValueError('Invalid resource account record')
            owner, records = account['owner'], account['stocks']
            if not isinstance(owner, str) or not owner or owner in result._accounts or not isinstance(records, list):
                raise ValueError('Invalid or duplicate resource owner')
            stocks = {}
            for record in records:
                if not isinstance(record, dict) or not isinstance(record.get('id'), str) or record['id'] not in result._types:
                    raise ValueError('Unknown resource record')
                key = record['id']
                if key in stocks:
                    raise ValueError('Duplicate resource record')
                stocks[key] = result._types[key].from_save(owner, record)
            if set(stocks) != set(result._types):
                raise ValueError('Resource account does not match registered types')
            result._accounts[owner] = stocks
        return result

    def restore(self, data):
        self._write()
        restored = self.from_save(data, resource_types=self._types.values())
        for stocks in restored._accounts.values():
            for stock in stocks.values():
                stock._domain = self._domain
        self._accounts = restored._accounts

    def __deepcopy__(self, memo):
        result = self.from_save(self.to_save(), resource_types=self._types.values())
        memo[id(self)] = result
        return result


class ResourceInterface:
    """The capability native:food gives access only to Food stock."""
    def __init__(self, accounts, resource_id):
        self._accounts, self._id = accounts, resource_id

    def balance(self, owner):
        return self._accounts.balance(owner, self._id)

    def credit(self, owner, amount):
        self._accounts.credit(owner, self._id, amount)

    def consume(self, owner, amount):
        self._accounts.consume(owner, self._id, amount)

    def snapshot(self, owner):
        return self._accounts._stock(owner, self._id).snapshot()

    def value(self, amount, unit_price):
        return self._accounts._types[self._id].value_of(amount, unit_price)
