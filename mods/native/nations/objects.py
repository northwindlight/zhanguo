"""国家身份与存续记录；库存、建筑与政体仍由各自领域拥有。"""
from dataclasses import dataclass, replace

from engine.identifiers import content_id
from engine.runtime import ModError
from engine.state import active_unit


def display_name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Nation display name must be nonempty')
    return value


@dataclass(frozen=True)
class Nation:
    id: str
    name: str
    alive: bool = True
    extinction_reason: str | None = None

    def __post_init__(self):
        content_id(self.id)
        display_name(self.name)
        if type(self.alive) is not bool:
            raise ValueError('Invalid nation lifecycle flag')
        if self.alive:
            if self.extinction_reason is not None:
                raise ValueError('Living nation cannot have extinction reason')
        else:
            display_name(self.extinction_reason)

    @property
    def account(self):
        return self.id


@dataclass(frozen=True)
class NationOverview:
    nation: Nation
    sites: tuple


class Nations:
    def __init__(self, resource_call, building_call):
        self._resources, self._buildings = resource_call, building_call
        self._records = {}
        self._world, self._domain = None, None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('Nations already belong to another world')
        self._world, self._domain = world, world.runtime.state_owners[key]
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != self._domain:
            raise ModError('Use declared nations interface')

    def create(self, nation_id, name, amounts=None):
        """宿主/剧本创建国家及同 ID 账户；不凭空生成领土或市政厅。"""
        self._write()
        nation = Nation(nation_id, name)
        if nation_id in self._records:
            raise ValueError('Nation ID already exists, including extinct nations')
        # ResourceAccounts.open validates the entire inventory before committing.
        self._resources('open', nation_id, amounts)
        self._records[nation_id] = nation
        return nation

    def get(self, nation_id):
        content_id(nation_id)
        return self._records[nation_id]

    def ids(self, *, alive_only=True):
        return tuple(nation.id for nation in self._records.values() if nation.alive or not alive_only)

    def snapshot(self):
        return tuple(self._records.values())

    def _living(self, nation_id):
        nation = self.get(nation_id)
        if not nation.alive:
            raise ValueError('Nation is extinct')
        return nation

    def rename(self, nation_id, name):
        self._write()
        nation = replace(self._living(nation_id), name=name)
        self._records[nation_id] = nation
        return nation

    def sites(self, nation_id):
        self.get(nation_id)
        return tuple(site for site in self._buildings('snapshot').sites if site.owner == nation_id)

    def overview(self, nation_id):
        return NationOverview(self.get(nation_id), self.sites(nation_id))

    def extinguish(self, nation_id, reason):
        """登记宿主已裁决的灭亡；站点处置须由其所有域先完成。"""
        self._write()
        nation = replace(self._living(nation_id), alive=False, extinction_reason=reason)
        if self.sites(nation_id):
            raise ValueError('Dispose of nation-owned sites before recording extinction')
        self._records[nation_id] = nation
        return nation

    def _validate_links(self, records):
        sites = self._buildings('snapshot').sites
        for nation in records.values():
            self._resources('snapshot', nation.account)
            if not nation.alive and any(site.owner == nation.id for site in sites):
                raise ValueError('Extinct nation still owns sites')

    def to_save(self):
        self._validate_links(self._records)
        return {'nations': [{'id': nation.id, 'name': nation.name, 'alive': nation.alive,
                             'extinction_reason': nation.extinction_reason}
                            for nation in self._records.values()]}

    def restore(self, data):
        self._write()
        if not isinstance(data, dict) or set(data) != {'nations'} or not isinstance(data['nations'], list):
            raise ValueError('Invalid nations save')
        candidate = {}
        for row in data['nations']:
            if not isinstance(row, dict) or set(row) != {'id', 'name', 'alive', 'extinction_reason'}:
                raise ValueError('Invalid nation record')
            nation = Nation(**row)
            if nation.id in candidate:
                raise ValueError('Duplicate nation ID')
            candidate[nation.id] = nation
        self._validate_links(candidate)
        self._records = candidate

    def __deepcopy__(self, memo):
        result = self.snapshot()
        memo[id(self)] = result
        return result


class NationInspection:
    """给消费者的只读国家事实，不授予创建或灭亡权限。"""
    def __init__(self, nations):
        self._nations = nations

    def get(self, nation_id):return self._nations.get(nation_id)
    def ids(self):return self._nations.ids()
    def snapshot(self):return self._nations.snapshot()
    def overview(self, nation_id):return self._nations.overview(nation_id)
