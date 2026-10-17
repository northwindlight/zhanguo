"""Building instance template. Concrete types and simulation phases belong to mods."""
from dataclasses import dataclass
from types import MappingProxyType

from .identifiers import content_id
from .resource import Resource
from .runtime import ModError
from .state import active_unit


def integer(value, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError('Invalid building integer')
    return value


def recipe(values):
    from collections.abc import Mapping
    if not isinstance(values,Mapping):raise ValueError('Expected building recipe mapping')
    result = {}
    for key, amount in values.items():
        content_id(key)
        Resource.finite_number(amount)
        if amount <= 0:raise ValueError('Recipe amounts must be positive')
        result[key] = amount
    return MappingProxyType(result)


@dataclass(frozen=True)
class BuildingCycle:
    inputs: tuple = ()
    outputs: tuple = ()
    energy: int = 0

    def __post_init__(self):
        integer(self.energy)
        for field in ('inputs','outputs'):
            leg = getattr(self,field)
            if len(dict(leg)) != len(leg):raise ValueError('Duplicate cycle resource')
            recipe(dict(leg))
            object.__setattr__(self,field,tuple((key,amount) for key,amount in leg))


@dataclass(frozen=True)
class BuildingSnapshot:
    id: int
    kind: str
    site: str
    level: int
    remaining: int


class Building:
    TYPE_ID = ''
    NAME_KEY = ''
    ROLES = ()

    def __init__(self, identity, site, *, level=1, remaining=0):
        content_id(self.TYPE_ID);content_id(site)
        self._id = integer(identity, 1)
        self._site = site
        self._level = integer(level, 1)
        self._remaining = integer(remaining)
        self._domain = None

    @property
    def id(self):return self._id
    @property
    def kind(self):return self.TYPE_ID
    @property
    def site(self):return self._site
    @property
    def level(self):return self._level
    @property
    def remaining(self):return self._remaining
    @property
    def completed(self):return self.remaining == 0

    def complete(self):
        caller = active_unit.get()
        if self._domain is not None and caller is not None and caller != self._domain:
            raise ModError('Building completion belongs to its domain')
        self._remaining = 0

    def cycle(self, **context):
        raise NotImplementedError('Concrete buildings define their production cycle')

    def snapshot(self):
        return BuildingSnapshot(self.id, self.kind, self.site, self.level, self.remaining)

    def to_save(self):
        return {'id':self.id,'kind':self.kind,'site':self.site,'level':self.level,'remaining':self.remaining}

    @classmethod
    def from_save(cls, data):
        if not isinstance(data, dict) or set(data) != {'id','kind','site','level','remaining'} or data['kind'] != cls.TYPE_ID:
            raise ValueError('Invalid building record')
        return cls(data['id'],data['site'],level=data['level'],remaining=data['remaining'])
