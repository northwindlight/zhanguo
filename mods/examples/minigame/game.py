"""最小可玩游戏：从零写一套能打完一局的规则。

内容规格：
    资源 3 种    钱 mini:gold、粮 mini:food、木 mini:wood
    地形 1 种    mini:plain
    建筑 5 种    市政厅（产金）、林场（产木）、农场（产粮）、金矿（产金）、兵营（解锁征兵）
    兵种 1 种    mini:infantry
    外交 2 种    宣战、和平
    规则        失了全部市政厅即亡国；在同格或相邻格可以进攻正在交战的国家

本文件把九个模块写在一起，一个屏幕看得完。每个类的 `mod.toml` 在同目录 `mods/` 下。

内容 ID 归本发行版所有；开局由 MiniSession 投放 mini: 资源和建筑。
"""
from dataclasses import dataclass, field
from types import MappingProxyType

from engine.identifiers import content_id
from engine.resource import Resource
from engine.runtime import GameMod, ModError, RuleSection, Stage, Tool
from engine.state import active_unit

GOLD, FOOD, WOOD = 'mini:gold', 'mini:food', 'mini:wood'
PLAIN = 'mini:plain'
TOWN_HALL = 'mini:town-hall'

# 建筑表：造价（金/木）、每期产出、是否解锁征兵。
BUILDINGS = MappingProxyType({
    'mini:lumber-camp': {'name': '林场', 'gold': 30, 'wood': 0, 'output': {WOOD: 4}},
    'mini:farm':        {'name': '农场', 'gold': 40, 'wood': 10, 'output': {FOOD: 5}},
    'mini:gold-mine':   {'name': '金矿', 'gold': 50, 'wood': 15, 'output': {GOLD: 8}},
    'mini:town-hall':   {'name': '市政厅', 'gold': 80, 'wood': 20, 'output': {GOLD: 6}},
    'mini:barracks':    {'name': '兵营', 'gold': 60, 'wood': 20, 'output': {}},
})
BARRACKS = 'mini:barracks'

# 兵种表：一个兵种，征兵耗粮与金，属性固定。
INFANTRY = {'name': '步兵', 'food': 6, 'gold': 4, 'hp': 100, 'atk': 40, 'upkeep': 1}


# ── 资源 ──────────────────────────────────────────────────────────────
class Coin(Resource):
    TYPE_ID = GOLD
    NAME_KEY = 'minigame.gold'


class Grain(Resource):
    TYPE_ID = FOOD
    NAME_KEY = 'minigame.food'


class Timber(Resource):
    TYPE_ID = WOOD
    NAME_KEY = 'minigame.wood'


RESOURCES = (Coin, Grain, Timber)


class Accounts:
    """三个资源账户；所有增减都走 exchange，一步原子提交。"""

    def __init__(self, types):
        self._types = MappingProxyType({cls.TYPE_ID: cls for cls in types})
        self._rows = {}
        self._world = None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('Accounts already bound')
        self._world = world
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != 'mini:resources':
            raise ModError('Use the declared resources interface')

    def open(self, owner, amounts=None):
        """开户并给定本发行版注册资源的初始库存。"""
        self._write()
        if owner in self._rows:
            raise ValueError('Account already exists')
        amounts = amounts or {}
        if set(amounts)-set(self._types):raise ValueError('Unknown mini resource')
        self._rows[owner] = {key: cls(owner, amounts.get(key, 0)) for key, cls in self._types.items()}

    def ids(self):
        return tuple(self._types)

    def balance(self, owner, resource):
        return self._rows[owner][resource].amount

    def totals(self, owner):
        return {key: row.amount for key, row in self._rows[owner].items()}

    def owners(self):
        return tuple(self._rows)

    def exchange(self, owner, debits, credits):
        """先全部校验再统一提交，任一条腿不成立则账户不变。"""
        self._write()
        if owner not in self._rows:
            raise ValueError('Unknown account')
        for leg in (debits, credits):
            if set(leg) - set(self._types):
                raise ValueError('Unknown resource')
            if any(amount < 0 for amount in leg.values()):
                raise ValueError('Negative amount')
        for key, amount in debits.items():
            if self._rows[owner][key].amount < amount:
                raise ValueError(f'余额不足：{key}')
        for key, amount in debits.items():
            self._rows[owner][key].set_amount(self._rows[owner][key].amount - amount)
        for key, amount in credits.items():
            self._rows[owner][key].set_amount(self._rows[owner][key].amount + amount)

    def to_save(self):
        return {owner: {key: row.amount for key, row in stocks.items()}
                for owner, stocks in self._rows.items()}

    def restore(self, state):
        if not isinstance(state, dict):
            raise ValueError('Invalid accounts')
        rows = {}
        for owner, stocks in state.items():
            if not isinstance(stocks, dict) or set(stocks) != set(self._types):
                raise ValueError('Invalid account record')
            rows[owner] = {key: self._types[key](owner, amount) for key, amount in stocks.items()}
        self._rows = rows


class ResourcesMod(GameMod):
    def mixins(self):
        return ()

    def content_definitions(self):
        return {cls.TYPE_ID: cls for cls in RESOURCES}

    def state_keys(self):
        return ('accounts',)

    def provides(self):
        return ('mini:resources', 'mini:resource-exchange')

    def initialize_world(self, world):
        world.accounts = Accounts(RESOURCES)

    def objects(self, world):
        return {'mini:resources': world.accounts, 'mini:resource-exchange': world.accounts}

    def save_state(self, world):
        return world.accounts.to_save()

    def load_state(self, world, state):
        world.accounts.restore(state)


# ── 领土 ──────────────────────────────────────────────────────────────
class Territory:
    def __init__(self):
        self.size = 0
        self.seed = 0
        self._tiles = {}
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def configure(self, size, seed):
        self.size, self.seed = int(size), int(seed)

    def dimensions(self):
        return (self.size, self.seed)

    def check(self, x, y):
        if not (0 <= x < self.size and 0 <= y < self.size):
            raise ValueError('坐标越界')

    def cell(self, x, y):
        self.check(x, y)
        entry = self._tiles.get((x, y))
        return Cell(entry[0] if entry else None, (x, y), entry[1] if entry else None)

    def create(self, site, owner, x, y, **kwargs):
        content_id(site)
        self.check(x, y)
        if (x, y) in self._tiles:
            raise ValueError('地块已被占用')
        self._tiles[(x, y)] = (site, owner)

    def owner(self, x, y):
        entry = self._tiles.get((x, y))
        return entry[1] if entry else None

    def site(self, x, y):
        entry = self._tiles.get((x, y))
        return entry[0] if entry else None

    def owned(self, owner):
        return tuple(cell for cell in self.cells() if cell.owner == owner)

    def cells(self):
        return tuple(Cell(entry[0], (x, y), entry[1])
                     for (x, y), entry in sorted(self._tiles.items()))

    def snapshot(self):
        return self.cells()

    def transfer(self, x, y, owner):
        site, _ = self._tiles[(x, y)]
        self._tiles[(x, y)] = (site, owner)

    def neighbours(self, x, y):
        return tuple((x + dx, y + dy) for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1))
                     if 0 <= x + dx < self.size and 0 <= y + dy < self.size)

    def to_save(self):
        return {'size': self.size, 'seed': self.seed,
                'tiles': [[x, y, site, owner] for (x, y), (site, owner) in sorted(self._tiles.items())]}

    def restore(self, state):
        self.size, self.seed = int(state['size']), int(state['seed'])
        self._tiles = {(x, y): (site, owner) for x, y, site, owner in state['tiles']}


@dataclass(frozen=True)
class Cell:
    site: str | None
    position: tuple
    owner: str | None = None
    terrain: str = PLAIN

    @property
    def tile(self):
        return Tile(self.site, self.position)


@dataclass(frozen=True)
class Tile:
    id: str | None
    position: tuple
    terrain: str = PLAIN


class TerritoryMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('territory',)

    def provides(self):
        return ('mini:territory',)

    def initialize_world(self, world):
        world.territory = Territory()

    def objects(self, world):
        return {'mini:territory': world.territory}

    def tools(self):
        return (Tool('land', lambda world, actor: [{'site': cell.site, **self._fact(world, cell)}
                                                   for cell in world.territory.cells()], read_only=True),)

    @staticmethod
    def _fact(world, cell):
        return {'position': list(cell.position), 'owner': world.territory.owner(*cell.position),
                'terrain': PLAIN, 'buildings': world.buildings.at(*cell.position)}

    def save_state(self, world):
        return world.territory.to_save()

    def load_state(self, world, state):
        world.territory.restore(state)


# ── 建筑 ──────────────────────────────────────────────────────────────
class Buildings:
    def __init__(self, call):
        self._call = call
        self._by_site = {}
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != 'mini:buildings':
            raise ModError('Use the declared buildings interface')

    def seed(self, site, kind):
        self._write()
        if kind not in BUILDINGS:
            raise ValueError(f'未知建筑：{kind}')
        self._by_site.setdefault(site, {})
        self._by_site[site][kind] = self._by_site[site].get(kind, 0) + 1

    def at(self, x, y):
        site = self._world.territory.site(x, y)
        return dict(self._by_site.get(site, {})) if site else {}

    def count(self, owner, kind):
        total = 0
        for cell in self._world.territory.owned(owner):
            total += self._by_site.get(cell.site, {}).get(kind, 0)
        return total

    def build(self, actor, x, y, kind):
        self._write()
        if kind not in BUILDINGS:
            raise ValueError(f'未知建筑：{kind}')
        if self._world.territory.owner(x, y) != actor:
            raise ValueError('只能在自己的地块上建造')
        table = BUILDINGS[kind]
        self._call('mini:resource-exchange', 'exchange', actor,
                   {GOLD: table['gold'], WOOD: table['wood']}, {})
        self.seed(self._world.territory.site(x, y), kind)
        return {'site': self._world.territory.site(x, y), 'kind': kind}

    def produce(self):
        """按建筑产出资源，返回本期各国产出。"""
        report = {}
        for cell in self._world.territory.cells():
            owner = cell.owner
            for kind, count in self._by_site.get(cell.site, {}).items():
                for resource, amount in BUILDINGS[kind]['output'].items():
                    report.setdefault(owner, {}).setdefault(resource, 0)
                    report[owner][resource] += amount * count
        for owner, gained in report.items():
            self._call('mini:resource-exchange', 'exchange', owner, {}, dict(gained))
        return report

    def to_save(self):
        return {site: dict(kinds) for site, kinds in self._by_site.items()}

    def restore(self, state):
        self._by_site = {site: dict(kinds) for site, kinds in state.items()}


class BuildingsMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('buildings',)

    def provides(self):
        return ('mini:buildings',)

    def needs(self):
        return ('mini:resource-exchange',)

    def initialize_world(self, world):
        world.buildings = Buildings(lambda capability, method, *args:
                                    self.runtime.object_call(world, capability, method, *args))

    def objects(self, world):
        return {'mini:buildings': world.buildings}

    def tools(self):
        return (Tool('build', self._build),)

    @staticmethod
    def _build(world, actor, x: int, y: int, kind: str):
        return world.buildings.build(actor, x, y, kind)

    def stages(self):
        return (Stage('mini:buildings.produce', lambda world, context:
                      context.update(production=world.buildings.produce()),
                      hook='cycle.start', provides=('production.done',)),)

    def rule_sections(self, world=None):
        rows = '；'.join(f'{table["name"]} 金{table["gold"]} 木{table["wood"]}'
                         + (f' 产{"/".join(f"{k.split(":")[1]} {v}" for k, v in table["output"].items())}/期'
                            if table['output'] else '')
                         for table in BUILDINGS.values())
        return (RuleSection('建筑', f'{rows}。', ('建筑',)),)

    def save_state(self, world):
        return world.buildings.to_save()

    def load_state(self, world, state):
        world.buildings.restore(state)


# ── 国家 ──────────────────────────────────────────────────────────────
@dataclass
class Nation:
    id: str
    name: str
    alive: bool = True


class Nations:
    def __init__(self, call):
        self._call = call
        self._rows = {}
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != 'mini:nations':
            raise ModError('Use the declared nations interface')

    def create(self, actor, name, amounts=None):
        self._call('mini:resources', 'open', actor, amounts or {})
        self._rows[actor] = Nation(actor, name)

    def get(self, actor):
        content_id(actor)
        if actor not in self._rows:
            raise ValueError('国家不存在')
        return self._rows[actor]

    def ids(self):
        return tuple(actor for actor, row in self._rows.items() if row.alive)

    def snapshot(self):
        return tuple(self._rows.values())

    def extinguish(self, actor):
        self._write()
        self._rows[actor].alive = False

    def to_save(self):
        return {actor: {'name': row.name, 'alive': row.alive} for actor, row in self._rows.items()}

    def restore(self, state):
        self._rows = {actor: Nation(actor, row['name'], row['alive']) for actor, row in state.items()}


class ActorScope:
    """混入 World 的类：self 就是世界本身。"""

    @property
    def nation_actors(self):
        return self.nations.ids()


class NationsMod(GameMod):
    def mixins(self):
        return (ActorScope,)

    def state_keys(self):
        return ('nations',)

    def provides(self):
        return ('mini:nations', 'mini:nation-inspection')

    def needs(self):
        return ('mini:resources',)

    def initialize_world(self, world):
        world.nations = Nations(lambda capability, method, *args:
                                self.runtime.object_call(world, capability, method, *args))

    def objects(self, world):
        return {'mini:nations': world.nations, 'mini:nation-inspection': world.nations}

    def tools(self):
        return (Tool('nations', lambda world, actor: world.nations.snapshot(), read_only=True),)

    def save_state(self, world):
        return world.nations.to_save()

    def load_state(self, world, state):
        world.nations.restore(state)


# ── 军队 ──────────────────────────────────────────────────────────────
@dataclass
class Unit:
    id: int
    owner: str
    kind: str
    hp: int
    x: int
    y: int

    def to_save(self):
        return {'id': self.id, 'owner': self.owner, 'kind': self.kind, 'hp': self.hp,
                'x': self.x, 'y': self.y}


class Military:
    def __init__(self, call):
        self._call = call
        self._units = {}
        self._next = 1
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != 'mini:army':
            raise ModError('Use the declared army interface')

    def seed(self, owner, kind, x, y, count=1):
        self._write()
        for _ in range(count):
            self._units[self._next] = Unit(self._next, owner, kind, INFANTRY['hp'], x, y)
            self._next += 1

    def snapshot(self, owner=None):
        return tuple(unit for unit in self._units.values() if owner is None or unit.owner == owner)

    def of(self, owner):
        return self.snapshot(owner)

    def recruit(self, owner, x, y):
        self._write()
        if self._world.territory.owner(x, y) != owner:
            raise ValueError('只能在自己的地块上征兵')
        if not self._world.buildings.at(x, y).get(BARRACKS):
            raise ValueError('该地块没有兵营')
        self._call('mini:resource-exchange', 'exchange', owner,
                   {FOOD: INFANTRY['food'], GOLD: INFANTRY['gold']}, {})
        self.seed(owner, 'mini:infantry', x, y)
        return self.snapshot(owner)[-1]

    def move(self, owner, unit_id, x, y):
        self._write()
        unit = self._unit(owner, unit_id)
        if (x, y) not in self._world.territory.neighbours(unit.x, unit.y):
            raise ValueError('只能移动到相邻格')
        unit.x, unit.y = x, y
        return unit

    def attack(self, owner, unit_id, x, y):
        """相邻格进攻：交战状态才允许；无守军则占领该格。"""
        self._write()
        unit = self._unit(owner, unit_id)
        if (x, y) not in self._world.territory.neighbours(unit.x, unit.y):
            raise ValueError('只能进攻相邻格')
        target_owner = self._world.territory.owner(x, y)
        defenders = [row for row in self._units.values()
                     if (row.x, row.y) == (x, y) and row.owner != owner]
        if defenders:
            if not self._call('mini:diplomacy', 'at_war', owner, defenders[0].owner):
                raise ValueError('与该国并未交战')
            defender = defenders[0]
            defender.hp -= INFANTRY['atk']
            killed = defender.hp <= 0
            if killed:
                del self._units[defender.id]
            return {'battle': True, 'defender_hp': max(defender.hp, 0), 'killed': killed}
        if target_owner == owner:
            raise ValueError('该格已归你所有')
        if target_owner is not None and not self._call('mini:diplomacy', 'at_war', owner, target_owner):
            raise ValueError('与该国并未交战')
        # 写入别人的域走接口：土地归属归领土域所有。
        self._call('mini:territory', 'transfer', x, y, owner)
        return {'battle': False, 'captured': [x, y]}

    def _unit(self, owner, unit_id):
        unit = self._units.get(int(unit_id))
        if unit is None or unit.owner != owner:
            raise ValueError('没有这支部队')
        return unit

    def to_save(self):
        return {'next': self._next, 'units': [unit.to_save() for unit in self._units.values()]}

    def restore(self, state):
        self._next = int(state['next'])
        self._units = {}
        for row in state['units']:
            self._units[row['id']] = Unit(row['id'], row['owner'], row['kind'],
                                          row['hp'], row['x'], row['y'])


class ArmyMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('military',)

    def provides(self):
        return ('mini:army',)

    def needs(self):
        return ('mini:resource-exchange', 'mini:territory', 'mini:buildings', 'mini:diplomacy')

    def initialize_world(self, world):
        world.military = Military(lambda capability, method, *args:
                                  self.runtime.object_call(world, capability, method, *args))

    def objects(self, world):
        return {'mini:army': world.military}

    def tools(self):
        return (Tool('recruit', self._recruit),
                Tool('move_army', self._move),
                Tool('attack', self._attack),
                Tool('army', lambda world, actor: world.military.of(actor), read_only=True))

    @staticmethod
    def _recruit(world, actor, x: int, y: int):
        return world.military.recruit(actor, x, y)

    @staticmethod
    def _move(world, actor, unit: int, x: int, y: int):
        return world.military.move(actor, unit, x, y)

    @staticmethod
    def _attack(world, actor, unit: int, x: int, y: int):
        return world.military.attack(actor, unit, x, y)

    def rule_sections(self, world=None):
        return (RuleSection('军队',
                            f'步兵：征募耗粮 {INFANTRY["food"]}、金 {INFANTRY["gold"]}，'
                            f'HP {INFANTRY["hp"]}，攻击 {INFANTRY["atk"]}，需当地有兵营。'
                            '部队可移动到相邻格；对交战国的相邻格进攻，无守军即占领该格。',
                            ('军队', '征兵')),)

    def save_state(self, world):
        return world.military.to_save()

    def load_state(self, world, state):
        world.military.restore(state)


# ── 外交 ──────────────────────────────────────────────────────────────
class Diplomacy:
    def __init__(self):
        self._wars = set()
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != 'mini:diplomacy':
            raise ModError('Use the declared diplomacy interface')

    def _pair(self, a, b):
        return tuple(sorted((a, b)))

    def at_war(self, a, b):
        return a != b and self._pair(a, b) in self._wars

    def declare_war(self, actor, target):
        self._write()
        self._world.nations.get(target)
        if actor == target:
            raise ValueError('不能对自己宣战')
        self._wars.add(self._pair(actor, target))
        return {'war': True, 'with': target}

    def make_peace(self, actor, target):
        self._write()
        self._world.nations.get(target)
        self._wars.discard(self._pair(actor, target))
        return {'war': False, 'with': target}

    def wars(self):
        return tuple(sorted(self._wars))

    def to_save(self):
        return {'wars': [list(pair) for pair in sorted(self._wars)]}

    def restore(self, state):
        self._wars = {tuple(pair) for pair in state['wars']}


class DiplomacyMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('diplomacy',)

    def provides(self):
        return ('mini:diplomacy',)

    def needs(self):
        return ('mini:nations',)

    def initialize_world(self, world):
        world.diplomacy = Diplomacy()

    def objects(self, world):
        return {'mini:diplomacy': world.diplomacy}

    def tools(self):
        return (Tool('declare_war', self._war), Tool('make_peace', self._peace),
                Tool('diplomacy', lambda world, actor: {'wars': world.diplomacy.wars()},
                     read_only=True))

    @staticmethod
    def _war(world, actor, target: str):
        return world.diplomacy.declare_war(actor, target)

    @staticmethod
    def _peace(world, actor, target: str):
        return world.diplomacy.make_peace(actor, target)

    def rule_sections(self, world=None):
        return (RuleSection('外交', 'declare_war 宣战，make_peace 议和（单方面生效）。'
                                    '只有交战国之间才能进攻对方的土地。', ('外交', '宣战')),)

    def save_state(self, world):
        return world.diplomacy.to_save()

    def load_state(self, world, state):
        world.diplomacy.restore(state)


# ── 政体（回合） ──────────────────────────────────────────────────────
class Government:
    def __init__(self):
        self._period = 0
        self._ended = set()
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def _write(self):
        caller = active_unit.get()
        if self._world is not None and caller is not None and caller != 'mini:government':
            raise ModError('Use the declared government interface')

    def period(self):
        return self._period

    def can_act(self, actor):
        return actor in self._world.nations.ids() and actor not in self._ended

    def advance(self):
        self._write()
        self._period += 1
        self._ended.clear()
        return self._period

    def end_turn(self, actor):
        self._write()
        if actor in self._ended:
            raise ValueError('本国本回合已经结束')
        self._ended.add(actor)
        return {'period': self._period, 'ended': True}

    def snapshot(self, actor):
        return {'period': self._period, 'ended': actor in self._ended}

    def to_save(self):
        return {'period': self._period, 'ended': sorted(self._ended)}

    def restore(self, state):
        self._period = int(state['period'])
        self._ended = set(state['ended'])


class GovernmentMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('government',)

    def provides(self):
        return ('mini:government',)

    def needs(self):
        return ('mini:nations',)

    def initialize_world(self, world):
        world.government = Government()

    def objects(self, world):
        return {'mini:government': world.government}

    def tools(self):
        return (Tool('end_turn', lambda world, actor: world.government.end_turn(actor)),
                Tool('government', lambda world, actor: world.government.snapshot(actor),
                     read_only=True))

    def stages(self):
        return (Stage('mini:government.advance',
                      lambda world, context: context.update(period=world.government.advance()),
                      hook='cycle.end'),)

    def rule_sections(self, world=None):
        return (RuleSection('回合', '每国在自己的回合内自由行动，结束时调用 end_turn；'
                                    '全部国家结束后由宿主结算。', ('回合',)),)

    def save_state(self, world):
        return world.government.to_save()

    def load_state(self, world, state):
        world.government.restore(state)


# ── 亡国 ──────────────────────────────────────────────────────────────
class Succession:
    def __init__(self, call):
        self._call = call
        self._enrolled = []
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def enroll(self, actor):
        if actor not in self._enrolled:
            self._enrolled.append(actor)

    def settle(self):
        """没有市政厅的国家亡国。"""
        dead = []
        for actor in list(self._call('mini:nations', 'ids')):
            if self._call('mini:buildings', 'count', actor, TOWN_HALL) == 0:
                self._call('mini:nations', 'extinguish', actor)
                dead.append(actor)
                self._call('mini:intelligence', 'emit', 'extinct',
                           f'{actor} 失去了全部市政厅', True)
        return dead

    def to_save(self):
        return list(self._enrolled)

    def restore(self, state):
        self._enrolled = list(state)


class SuccessionMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('succession',)

    def provides(self):
        return ('mini:succession',)

    def needs(self):
        return ('mini:nations', 'mini:buildings', 'mini:intelligence')

    def initialize_world(self, world):
        world.succession = Succession(lambda capability, method, *args:
                                      self.runtime.object_call(world, capability, method, *args))

    def stages(self):
        return (Stage('mini:succession.settle',
                      lambda world, context: context.update(extinct=world.succession.settle()),
                      hook='cycle.end', provides=('succession.settled',),
                      before=('mini:government.advance',)),)

    def rule_sections(self, world=None):
        return (RuleSection('存亡', '失去全部市政厅即亡国，其土地成为无主之地。', ('存亡', '亡国')),)

    def save_state(self, world):
        return world.succession.to_save()

    def load_state(self, world, state):
        world.succession.restore(state)


class Intelligence:
    def __init__(self):
        self._events = []
        self._world = None

    def bind_state(self, world, key):
        self._world = world
        return self

    def emit(self, kind, text, public=False, parties=()):
        self._events.append({'id': len(self._events) + 1, 'period': self._world.government.period(),
                             'kind': kind, 'text': text, 'public': bool(public), 'parties': list(parties)})

    def events(self):
        return tuple(self._events)

    def to_save(self):
        return {'events': list(self._events), 'messages': []}

    def restore(self, state):
        self._events = [dict(row) for row in state['events']]


class IntelligenceMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ('intelligence',)

    def provides(self):
        return ('mini:intelligence',)

    def initialize_world(self, world):
        world.intelligence = Intelligence()

    def objects(self, world):
        return {'mini:intelligence': world.intelligence}

    def tools(self):
        return (Tool('news', lambda world, actor: [dict(row) for row in world.intelligence.events() if row['public'] or actor in row['parties']],
                     read_only=True),)

    def save_state(self, world):
        return world.intelligence.to_save()

    def load_state(self, world, state):
        world.intelligence.restore(state)
