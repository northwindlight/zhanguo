from engine.runtime import GameMod, ModError

from .objects import ResourceAccounts
from .types import RESOURCE_TYPES
from engine.resource import Resource


class ResourcesMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration, path)
        if tuple(declaration['ResourceTypes']) != tuple(cls.TYPE_ID for cls in RESOURCE_TYPES):
            raise ModError('Declared resource types do not match concrete classes')

    def mixins(self):
        return ()

    def content_definitions(self):
        return {cls.TYPE_ID: cls for cls in RESOURCE_TYPES}

    def resource_types(self):
        result = []
        for key, definition in self.runtime.content.definitions():
            if isinstance(definition, type) and issubclass(definition, Resource):
                if definition.TYPE_ID != key:
                    raise ModError(f'Resource class identity does not match registration: {key}')
                result.append(definition)
        return tuple(result)

    def state_keys(self):
        return ('resources',)

    def provides(self):
        return ('native:resources', 'native:resource-exchange', 'native:resource-inspection', 'native:bank-cash', *(cls.TYPE_ID for cls in self.resource_types()))

    def initialize_world(self, world):
        world.resources = ResourceAccounts(self.resource_types())

    def objects(self, world):
        return {'native:resources': world.resources,
                'native:resource-exchange': ResourceExchange(world.resources),
                'native:resource-inspection': ResourceInspection(world.resources),
                'native:bank-cash': BankCash(world.resources),
                **{key: world.resources.interface(key) for key in world.resources.ids}}

    def save_state(self, world):
        return world.resources.to_save()

    def load_state(self, world, state):
        world.resources.restore(state)


class ResourceExchange:
    """Separate transactional capability; no account opening or raw setters."""
    def __init__(self, accounts):self._accounts = accounts
    def exchange(self, owner, debits, credits):
        return self._accounts.exchange(owner, debits, credits)

    def exchange_batch(self, operations):
        return self._accounts.exchange_batch(operations)
    def snapshot(self, owner):
        return self._accounts.snapshot(owner)


class ResourceInspection:
    """Read-only accounting capability, separate from inventory write grants."""
    def __init__(self, accounts):self._accounts = accounts
    def owners(self):return self._accounts.owners()
    def snapshot(self, owner):return self._accounts.snapshot(owner)
    def value(self, resource_id, amount, unit_price):
        return self._accounts.value(resource_id,amount,unit_price)


class BankCash:
    def __init__(self, accounts):self._accounts = accounts
    def adjust(self, currency, changes):return self._accounts.adjust_cash(currency, changes)
