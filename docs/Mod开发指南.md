# Mod 开发指南

本指南面向要给战国添加内容或规则的人：先按任务分成若干条配方，每条给出可运行的示例文件；
后半部分是声明字段与接口参考。

示例全部在 `mods/examples/` 下，每条配方对应一个目录，可单独装载运行。
一次跑完全部配方：

```bash
.venv/bin/python mods/examples/tour.py
```

## 与其他文档的分工

| 文档 | 讲什么 |
| --- | --- |
| 本指南 | 要加一样东西时怎么写、装在哪儿、怎么验 |
| [模块化口径.md](../模块化口径.md)、[对象化口径.md](../对象化口径.md) | 领域划分与对象设计的目标与判据 |
| [客户端接线.md](客户端接线.md) | 玩家 API 与客户端契约 |
| [mods/native/README.md](../mods/native/README.md) | native 发行版的模块清单与现状 |

`模块化口径.md` 与 `对象化口径.md` 写于改造之前，其中引用的 `mods/nativemod/`、
`mp_ai.py` 等路径已退役；这些文档中的领域所有权与依赖方向仍是设计口径。当前接口与路径以源码和本指南为准，不能用现状中的限制替代设计目标。

---

## 一、三层结构

| 层 | 是什么 | 在哪 |
| --- | --- | --- |
| 引擎 | 机制：挂点顺序、依赖解析、状态所有权、能力裁决、工具通道、存档 | `engine/` |
| 模块 | 一件事：它拥有的状态、它的工具、它的结算阶段、它的面板与规则文字 | `mods/` 下各目录 |
| 发行版 | 清单：装哪些模块、挂点按什么顺序排 | `mods.toml`、`mods/native/mod.toml` |

依赖方向单一：**引擎 ← 模块 ← 发行版**。模块不导入发行版，也不互相导入；
模块之间只通过声明的能力对话。

引擎不含任何游戏词汇。`engine/` 里不出现"建筑""补给""市场"这类词，
它们全在模块里。

一个模块由两个文件描述：

```
mods/examples/hello/
  mod.toml    声明：身份、依赖、状态、能力
  mod.py      实现：继承 GameMod 的类
```

`mod.toml` 里的 `Entry = "包路径:类名"` 指向实现类。包路径按仓库根解析，
各层目录**不需要 `__init__.py`**（Python 命名空间包）。

---

## 二、配方一：最小 Mod

模块必须实现 `mixins()`（多数返回空元组）；状态、工具、结算阶段均按职责选用，不是必填。纯内容模块可以只注册内容，UI Mod 可以只注册展示工厂。
见 [mods/examples/hello/mod.py](../mods/examples/hello/mod.py)。

`mod.toml`：

```toml
[Mod]
Id = "example:hello"
Version = "1.0.0"
Entry = "mods.examples.hello.mod:HelloMod"

[State]
Owns = ["hello"]

[Capabilities]
Provides = ["example:hello"]
```

`mod.py` 的骨架：

```python
from engine.runtime import GameMod, RuleSection, Stage, Tool

class HelloMod(GameMod):
    def mixins(self):
        return ()                       # 必填；需要给世界挂属性时才返回类

    def state_keys(self):
        return ('hello',)               # 本模块独占写入的状态键

    def provides(self):
        return ('example:hello',)       # 对外提供的能力名

    def initialize_world(self, world):
        world.hello = {}                # 世界构造完成后调用一次

    def tools(self):
        return (Tool('hello_add', self._add, ('打招呼',)),
                Tool('hello_total', self._total, read_only=True))

    @staticmethod
    def _add(world, actor, amount: int):
        world.hello[actor] = world.hello.get(actor, 0) + amount
        return {'actor': actor, 'count': world.hello[actor]}

    def stages(self):
        return (Stage('example:hello.settle', self._settle),)

    @staticmethod
    def _settle(world, context):
        context['hello_total'] = sum(world.hello.values())

    def rule_sections(self, world=None):
        return (RuleSection('打招呼', '正文', ('hello',)),)

    def save_state(self, world):
        return dict(world.hello)

    def load_state(self, world, state):
        world.hello = dict(state)
```

装载一个模块有两种方式：

```python
from engine.runtime import Runtime, Unit
from mods.examples.hello.mod import HelloMod

# ① 进程内，不落文件：适合单模块开发与测试
runtime = Runtime([Unit('example:hello', HelloMod(), version='1.0.0')])
world = runtime.world_class()()
runtime.call(world, 'demo', 'hello_add', {'amount': 3})
runtime.settle(world)

# ② 从目录：目录里只有模块声明，没有发行版清单也可运行
runtime = Runtime.load('mods/examples/hello')
```

工具处理函数的前两个参数固定为 `(world, actor)`，其余参数由类型标注生成 JSON Schema，
并在调用时转换类型（字符串 `"4"` 转为整数 `4`）。`aliases` 里的名字与本名等价。
`read_only=True` 的工具可以被查询，不受"仅行动阶段可提交动作"的限制。

---

## 三、配方二：添加一种资源

资源用**类**注册。见 [mods/examples/tea/mod.py](../mods/examples/tea/mod.py)。

```python
from engine.resource import Resource
from engine.runtime import GameMod

class Tea(Resource):
    TYPE_ID = 'example:tea'
    NAME_KEY = 'resource.example:tea.name'

class TeaMod(GameMod):
    def mixins(self):
        return ()

    def content_definitions(self):
        return {Tea.TYPE_ID: Tea}
```

资源域会枚举内容表里所有 `Resource` 子类，自动为其建立账户、库存、只读接口与存档，
`mods/native/resources/` 不需要改动。定义用 `NAME_KEY` 保留展示名键；模块须另外提供对应语言文案。当前资源注册不会自动装载本地化文件。
`TRADEABLE = False` 的资源不能上市，账户与存档不受影响。

**注册不等于上市。** 商品清单由市场模块自己的 `[[Listings]]` 声明决定，
引擎不合并来自别处的商品。要让新资源可以买卖，需要一份包含它的市场声明，
办法是在发行版里换掉市场模块（见 [mods/examples/tea/market.toml](../mods/examples/tea/market.toml)）：

```toml
Currency = "native:gold"

[[Listings]]
Resource = "example:tea"
BasePrice = 3
Depth = 10

[Mod]
Id = "native:market"          # 保持原 Id：别的模块在 Requires 里按 Id 引用它
Version = "1.1.0-tea"
Entry = "mods.examples.tea.market:TeaMarketMod"
```

```python
from mods.native.market.mod import MarketMod

class TeaMarketMod(MarketMod):
    """行为不变，只是多上市一样商品。"""
```

未上市的资源在结算时被忽略，但报价、买入、卖出会报
`Resource is not listed: <id>`。声明一个未注册的资源则在世界初始化时报
`Market resource is not registered`。

发行版见 [mods/examples/tea_dist/mod.toml](../mods/examples/tea_dist/mod.toml)。

---

## 四、配方三：插入一个新阶段

结算骨架的位置（挂点）由**发行版**声明，模块只往上挂。

第一步，在发行版**顶层**（不是 `[Distribution]` 段里）加一条挂点：

```toml
[[Hooks]]
Id = "example.harvest"
```

`Id` 只允许 `[a-zA-Z0-9_.:-]`，且全局唯一。可选 `Name` 字段用于结算顺序表的显示。
没有阶段挂在上面的空挂点是允许的。

第二步，模块声明指向它的阶段。见 [mods/examples/harvest/mod.py](../mods/examples/harvest/mod.py)。

```python
def stages(self):
    return (Stage('example:harvest.reap', self._reap,
                  hook='example.harvest', provides=('harvest.reaped',)),)
```

可用的字段：

| 字段 | 含义 |
| --- | --- |
| `name` | 阶段 Id，全局唯一 |
| `handler` | 可调用对象，或**世界上的方法名**（由某个 mixin 提供） |
| `hook` | 发行版挂点 Id |
| `needs` | 本阶段消费的条件；条件名必须已被某个阶段 `provides`，且提供者处于同一或更早的挂点；同一挂点内按该条件排顺序 |
| `provides` | 本阶段发布的条件，全局唯一 |
| `requires` | 声明必须存在的阶段 Id；有挂点时用 `needs` 或 `after` 声明执行依赖，不发布条件 |
| `after` / `before` | 相对其他阶段的顺序 |
| `scope` | `'world'`（默认，处理函数收 `(world, context)`）或 `'actor'`（每个参与者收 `(world, actor, context)`） |

全部阶段共享一个 `context` 字典，这是阶段之间传递数据的唯一途径：
**处理函数的返回值会被忽略**。`resolve_turn()` 返回 `context` 中名为 `result` 的项，
没有则返回整个 `context`。

跨域写入通过声明的对象接口：

```python
actors = world.runtime.object_call(world, 'native:nation-inspection', 'ids')
world.runtime.object_call(world, 'native:resource-exchange', 'exchange_batch', operations)
```

`object_call` 会检查调用方在 `needs()` 里声明了该能力、并在 `[Interfaces]` 里列出了该方法。
方法名以下划线开头的不可调用。

---

## 五、配方四：添加一个兵种

兵种用**映射**注册（与资源、建筑的类不同）。见
[mods/examples/elephants/mod.py](../mods/examples/elephants/mod.py)。

```python
WAR_ELEPHANT = {
    'category': 'unit',
    'name_key': 'unit.example:war-elephant.name',
    'hp': 200,
    'speed': 1,
    'supply': 3,
    'atk': 90,
    'recruit': {'native:food': 20, 'native:equipment': 10, 'native:gold': 60},
    'move_cost': {'native:plain': 2, 'native:forest': 3, 'native:hill': 3,
                  'native:mountain': 4, 'native:desert': 2},
}

class ElephantsMod(GameMod):
    def mixins(self):
        return ()

    def content_definitions(self):
        return {'example:war-elephant': dict(WAR_ELEPHANT)}
```

字段要求：

| 字段 | 要求 |
| --- | --- |
| `category` | 必须是 `'unit'`，否则不被视为兵种 |
| `name_key` | 非空字符串 |
| `hp` / `speed` | 整数，`hp ≥ 1`、`speed ≥ 1` |
| `supply` / `atk` | 整数，`≥ 0` |
| `recruit` | 非空映射：资源 ID → 正整数（征兵消耗） |
| `move_cost` | 非空映射：**地形 ID** → 整数 `≥ 1`（进入该地形的移动力消耗） |

`move_cost` 的键是地形内容 ID（`native:plain`、`native:forest`、`native:hill`、
`native:mountain`、`native:desert`），不是地形中文名。运行期以
`territory.terrain(x, y).id` 索引该表。

注册后，兵种自动进入征兵工具的枚举、兵种目录、面板与存档依赖清单，
`mods/native/army/` 无需改动。军费、移动代价、战斗等既有规则对新兵种同样生效，
因此征兵仍受兵营配额与电网状态约束。

若新兵种的征兵配方使用了 army 尚未声明的资源，需要该资源已被注册为 `Resource` 子类。

---

## 六、配方五：给某个兵种添加结算阶段

`scope='actor'` 的参与者是**政体**（`ActorCollection` 里的行动者），不是兵种。
"只对某个兵种生效"的做法是 `scope='world'`，在处理函数里按 `unit.kind` 过滤。

读别的域的部队数据通过对象接口：

```toml
[Capabilities]
Needs = ["native:army", "native:resource-exchange"]

[Interfaces]
"native:army" = ["snapshot"]
"native:resource-exchange" = ["exchange"]
```

```python
def stages(self):
    # 挂在已有挂点上，用 needs 排到 army 自己的补给阶段之后。
    return (Stage('example:elephants.upkeep', self._upkeep,
                  hook='military.supply', needs=('military.supplied',),
                  provides=('elephants.fed',)),)

def _read_heads(self, world):
    rows = world.runtime.object_call(world, 'native:army', 'snapshot')
    heads = {}
    for unit in rows:
        if unit.kind == 'example:war-elephant':
            heads[unit.owner] = heads.get(unit.owner, 0) + 1
    return heads
```

部队的写入权属于 army 域（`state_keys = ('units', 'military')`）。其他模块只能读；
要修改必须由 army 的 `apply_state_change` 放行，并在本模块的
`[State] Requests` 里声明。因此"兵种专属阶段"的通常形态是**读部队 + 写自己域的状态**，
或者通过资源兑换施加影响。

完整的阶段顺序：

```python
runtime.stage_order.index('example:elephants.upkeep')
```

---

## 七、配方六：替换已有内容

添加内容与替换内容走同一个入口 `content_definitions()`：注册一个**同 ID** 的定义把它顶掉。
内容表是累加的，同一 ID 注册两次会被拒绝，所以替换的前提是原来的那一份不再被注册 ——
也就是换掉拥有它的模块。见 [mods/examples/no_power/buildings.py](../mods/examples/no_power/buildings.py)。

```python
from mods.native.buildings.mod import BuildingsMod
from mods.native.buildings.types import BUILDING_TYPES

POWER_PLANTS = ('native:wood-power-plant', 'native:oil-power-plant')

def _without_energy(cls):
    return type(cls.__name__, (cls,), {'ENERGY_USE': 0})

class NoPowerBuildingsMod(BuildingsMod):
    def content_definitions(self):
        return {cls.TYPE_ID: _without_energy(cls)
                for cls in BUILDING_TYPES if cls.TYPE_ID not in POWER_PLANTS}
```

配套的 `buildings.toml` 与 native 的逐字相同，只改 `Version` 与 `Entry` 两行，
`Id` 与 `[Capabilities] Provides` 保持不变 —— 别的模块按 Id 与能力名引用它。
发行版里把 `native/buildings/mod.toml` 换成这一份即可。

关于电力：单个建筑只在自身 `ENERGY_USE` 非零、且所在国电网短缺时停摆；
而**征兵**看的是全国电网口径（`army.grid_short`），只要国内任何一个耗电建筑
得不到电，征兵就会被拦。所以去掉电力系统需要把所有建筑的 `ENERGY_USE` 归零
（并删掉电厂），只改兵营是不够的。

---

## 八、配方七：提供一个客户端

发行版可声明任意客户端类型，也可覆盖默认的 `llm` / `rule`：

```toml
[Distribution.ClientFactories.llm]
Entry = "mods.examples.idle_client.client:IdleClient"
```

此例配置写 `client = "llm"`，客户端的 `kind` 也为 `llm`；独立 mini 示例声明 `ClientFactories.mini`，配置与 `kind` 均为 `mini`。native 的默认客户端选择仍为有 API key 用 llm，否则 rule；mini 默认使用自己声明的 `mini`。

客户端需要实现：

| 成员 | 说明 |
| --- | --- |
| `kind` | 字符串，与配置的 `client` 一致；用于存档类型校验与轮转分支 |
| `__init__(cfg, *, record=None, services=None)` | `record` 是存档里的 `{'kind','version','state'}`，新局为 `None` |
| `play(player, *, emit, head, on_call, on_result)` | 返回回执列表 |
| `to_save()` | 返回 `{'kind', 'version', 'state'}`，键必须恰好这三个 |
| `on_settlement(player)` | 可选 |

回执项的形状与 `player.submit()` 的返回值相同，至少需要
`ok` / `status` / `code` / `message` / `data`。返回值经 `wire()` 处理，
允许的类型见 §十二。

客户端获得 `services['ui']`，额外的对象能力可由工厂的 `Services` 显式授权。国策裁决属于发行版行为，现通过 `Distribution.Session.Services` 装配，不放在客户端里；直接调用和客户端调用走同一玩家 API。

完整 Session、服务、授权与控制权契约见 [发行版 Session API](发行版Session.md)。

示例见 [mods/examples/idle_client/client.py](../mods/examples/idle_client/client.py)。

---

## 九、配方八：制作发行版

发行版是两份清单，粒度不同：

| 形式 | 文件 | 内容 |
| --- | --- | --- |
| 入口 | `mods.toml`、`mods.playable.toml` | `[Load] Packages = ["mods/native"]`，每条是一个含 `mod.toml` 的目录 |
| 清单 | `mods/native/mod.toml` | `[Distribution]` + `Members` + `[[Hooks]]` |

装载方式：`Runtime.load(路径)`。路径是文件时按入口解析，是目录时收集目录下所有 `*.toml`
（其中带 `[Distribution]` 的作为清单，其余作为模块声明）。

玩家选择发行版的方式是配置顶层的 `mods` 键：

```json
{ "mods": "mods.playable.toml", "nations": [ ... ] }
```

缺省为 `mods.toml`（native）。

### 装配方式

`Members` 里引用别处的模块，只把要修改或新增的那一个放在自己家。
新增内容就追加一条；替换实现就把对应的那一条换成本地的，且保持 `Id` 与
`[Capabilities] Provides` 不变。

```toml
[Distribution]
Id = "example:tea-dist"
Version = "1.0.0"
Members = [
  "../../native/resources/mod.toml",
  "../tea/market.toml",            # 替换：多上市一样商品
  "../tea/mod.toml",               # 追加：注册该资源
  # …其余成员照旧引用
]
```

### `[Distribution]` 字段

| 键 | 必需 | 含义 |
| --- | --- | --- |
| `Id` / `Version` | 是 | 发行版身份，进入存档依赖清单 |
| `Members` | 是 | 模块声明路径列表，相对本文件所在目录 |
| `RequiredState` | 否 | 必须有人拥有的状态键；缺失时在装载期报 `state 无主` |
| `ActorCollection` | 否 | 世界上的行动者集合属性名，默认 `actors` |
| `ActionGate` | 否 | `{Capability, Method}`，判定行动者本回合能否继续行动 |
| `PlayerClock` | 否 | `{Capability, Method}`，提供玩家 API 的 `period` |
| `PlayerState` | 否 | 只读工具名列表，`observe()` 逐项取来作为 `facts` |
| `PlayerEvents` | 否 | 一个只读工具名，提供玩家事件流 |
| `ManualOrder` | 否 | 规则章节的排序 |
| `Session` | 整局必需 | `{Entry, Services}`，发行版的场景、管理与玩家 API 提供入口 |
| `ClientFactories` | 否 | `{<kind>: {Entry, Services}}`，覆盖客户端实现 |

`PlayerState` / `PlayerEvents` / `ActionGate` / `PlayerClock` 引用的工具与能力必须存在，
否则在装载期报错。

### `[Mod]` 字段

| 键 | 必需 | 含义 |
| --- | --- | --- |
| `Id` | 是 | 模块身份，全局唯一；`Requires` 按它引用 |
| `Version` | 是 | 进入存档依赖清单 |
| `Entry` | 是 | `"包路径:类名"` |
| `Requires` | 否 | 必须同时装载的模块 Id |
| `After` / `Before` | 否 | 相对其他模块的装载顺序 |
| `Kind` | 否 | 设为 `actor` 表示政体模块（见 §十一） |
| `Aliases` | 否 | 政体的别名 |

其余 `[Section]`（如 `[Harvest]`、`[Policy]`）是模块自己的参数，
通过 `self.declaration['Harvest']` 读取。

### 装配检查清单

1. 每个模块的 `[Mod] Id` 唯一，`Requires` 指向已装载的模块。
2. 每个状态键只有一个所有者；`RequiredState` 里的键都有主。
3. 每个能力只有一个提供方；所有 `Needs` 都有提供者。
4. 工具名与别名全局唯一，且不占用玩家 API 的保留名（`player_state`、`player_rules`、`player_events`）。
5. 阶段名唯一；每个 `hook` 都在挂点表里；`needs` 的条件都有提供者，位于同一或更早挂点，且阶段依赖无环。
6. `PlayerState` 里每一项都是存在且只读的工具。

以上任何一条不满足，都在 `Runtime.load()` 或世界构造时抛出 `ModError`，
不会等到运行中途。

---

## 十、配方九：一个最小可玩游戏

最小局有两种做法：**从零写一套小规则**，或在 native 上做减法。前者规模可控、一眼看得完，
适合作为起点；后者受既有模块的声明约束，能减掉的很有限。

### 从零写一个

[mods/examples/minigame](../mods/examples/minigame) 是一个完整的可玩游戏，
九个模块，全部规则在一份文件里：

| 内容 | 规模 |
| --- | --- |
| 资源 | 3 种：钱、粮、木 |
| 地形 | 1 种：平原 |
| 建筑 | 5 种：市政厅、林场、农场、金矿、兵营 |
| 兵种 | 1 种：步兵 |
| 外交 | 2 种动作：宣战、议和 |
| 规则 | 建筑每期产出资源；兵营所在格可征兵；对交战国相邻格进攻，无守军即占领；失去全部市政厅即亡国 |

模块划分：

```
资源 mini:resources      账户与原子兑换        territory   地块归属
建筑 mini:buildings      五类建筑与产出        army        部队、征兵、移动、进攻
国家 mini:nations        身份与存亡标记        diplomacy   交战关系
政体 mini:government     回合推进              succession  亡国判定
情报 mini:intelligence   事件与可见性
```

装载与运行：

```bash
.venv/bin/python -c "from host import GameHost; h=GameHost.new({'mods':'mods.minigame.toml','map_size':12,'seed':3,'nations':[{'name':'甲','client':'mini'}]}); h.cycle(); print(h.clock())"
```

发行版入口为 `mods.minigame.toml`，配置顶层写 `"mods": "mods.minigame.toml"`。
该发行版声明 `mini` 客户端工厂，用自带的简易策略驱动，因此**离线、无需 API key** 即可跑完整局。

### 发行版拥有开局规则

`Distribution.Session.Entry` 指向 [MiniSession](../mods/examples/minigame/session.py)，由它投放 `mini:gold / mini:food / mini:wood`、`mini:plain` 与 `mini:town-hall`，定义落位、登场与管理 API。未知资源直接拒绝，不再忽略 host 注入的资源。

`ActorCollection`、`ActionGate` 与 `PlayerClock` 仍是通用引擎的声明。host 不要求任何固定领域对象名或 native 内容 ID；mini 不再需要占位市场。替换整套规则只改发行版清单和实现。完整契约见 [发行版 Session API](发行版Session.md)。

### 在 native 上做减法

见 [mods/examples/playable_min/mod.toml](../mods/examples/playable_min/mod.toml)。

标准发行版当前装载 19 个模块。其中与核心链无关、可以去掉的是
`polities/huns`、`bank`、`economy_queries` 与表现层 `ui`，剩下 **15 个**
即可开局、行动、结算、存档：

```bash
.venv/bin/python -c "
from engine.runtime import Runtime
runtime = Runtime.load('mods.playable.toml')
print(len(runtime.order), runtime.order)"
```

其余 15 个不能去掉，原因分两类：

**native 场景使用。** `mods/native/session.py` 调用以下域的接口：
`territory`（开局落位）、`government`（周期与结束行动）、`nations`（创建国家）、
`buildings`（开局市政厅）、`succession`、`market`、`intelligence`。

**模块之间声明。** 这些依赖写在模块里，删掉其中一个会连带报错：

| 去掉 | 报错 |
| --- | --- |
| `resources` | `state 无主：resources` |
| `buildings` | `native:economy 缺少依赖 native:buildings` |
| `market_pricing` | `native:market 所需能力无提供者：market:pricing` |
| `market` | `native:economy-valuation 缺少依赖 native:market` |
| `economy_valuation` | `native:economy 所需能力无提供者：economy:valuation` |
| `economy_reports` | `native:government.advance 挂点条件无人提供：economy.reported` |
| `nations` | `native:government 缺少依赖 native:nations` |
| `government` | `native:diplomacy 缺少依赖 native:government` |
| `map_generation` | `native:territory 缺少依赖 native:map-generation` |
| `territory` | `native:vision 缺少依赖 native:territory` |
| `diplomacy` | `native:vision 缺少依赖 native:diplomacy` |
| `vision` | `native:army 缺少依赖 native:vision` |
| `army` | `native:succession 缺少依赖 native:army` |
| `succession` | `native:army.repatriate 挂点条件无人提供：actors.eliminated` |
| `intelligence` | `AttributeError: 'World' object has no attribute 'intelligence'` |

因此经济链、外交、视野都由声明固定在 15 个模块的下界上。要把规模压得更低，
只有一个办法：**替换**声明链上的模块（如 §七 换建筑模块，parliament 换 `government`），
或者自行提供宿主点名的那些接口。

此外，`PlayerState` 必须同步删除那些不再存在的工具，否则报
`玩家状态必须声明唯一的只读工具`。

规则 AI 客户端（v11plus）会读取 `econ`、`market` 两项事实与若干报价工具，
这些都属于 `economy_queries`；若要接规则 AI，需把该模块加回，最小集变为 16 个。
`mods/examples/playable_min` 使用最小客户端，因此 15 个即可离线运行。

---

## 十一、跨模块协作

模块之间只有三种交互方式。

**读别人的数据**：通过对象接口。

```toml
[Capabilities]
Needs = ["native:buildings"]

[Interfaces]
"native:buildings" = ["snapshot", "count"]
```

```python
world.runtime.object_call(world, 'native:buildings', 'count', actor, kind)
```

`Needs` 声明能力，`[Interfaces]` 列出允许调用的方法，两者都缺一不可。

**读别人发布的数值口径**：政体可以覆盖若干通用数值，读取走统一入口。

```python
world.runtime.parameter(world, actor, 'build_cost_pct', 100)
```

该方法检查能力名 `"number." + key` 是否被该政体禁用，然后取政体
`[Policy.Overrides]` 里的值，没有则返回默认值。行动者没有政体时返回默认值。

**写别人的状态**：默认不允许。若确有必要，在发起方声明：

```toml
[State]
Requests = ["polity"]
```

由状态所有者的 `apply_state_change` 决定是否放行。系统状态 `polity` 与 `phase`
由引擎拥有（记为 `$engine`），任何人都可以声明写入。

`numbers()` 返回本模块的数值表，供发行版与文档层一次枚举；
它与 `parameter()` 是两套独立机制，`parameter()` 不读 `numbers()`。

---

## 十二、政体模块

政体是作用域限定在**单个国家**上的模块：声明方式与其他模块相同，
只多一个 `Kind = "actor"`。

```toml
[Mod]
Id = "native:huns"
Kind = "actor"
Aliases = ["huns", "匈奴"]

[Policy]
Tag = "native:huns"
Rejection = "该政体不允许此外交动作"
Appearance = "scheduled"
Disable = ["tool.guarantee", "action.guarantee", "communication.gift"]
[Policy.Overrides]
build_cost_pct = 130
```

| 键 | 作用 |
| --- | --- |
| `Tag` / `Aliases` | 配置里 `polity` 字段的取值 |
| `Enable` / `Disable` | 显式启停能力名：`tool.<工具名>`、`action.<动作>`、`stage.<阶段名>`、`hook.<挂点>`、`panel.<面板名>`、`number.<数值键>`、`accept.treaties` 等 |
| `Overrides` | 数值覆盖表，由 `runtime.parameter()` 读取 |
| `Appearance` | `initial`（开局在场）/ `scheduled`（按配置的 `enable_turn` 登场）/ `manual` |
| `Rejection` | 被禁用时返回给玩家的文案 |

作用域裁决集中在引擎，规则代码不再重复判断：

```python
runtime.in_scope(world, actor, unit)      # 该模块是否作用于该行动者
runtime.permits(world, actor, capability) # 该能力是否被政体禁用
```

政体可以可选实现的方法：

| 方法 | 用途 |
| --- | --- |
| `activate_actor(world, actor, **kwargs)` | 登场时的一次性动作（初始资源、初始军队） |
| `parameter_overrides()` | 动态计算数值覆盖（默认读 `[Policy.Overrides]`） |
| `prompt(world, actor)` | 给该国的额外提示文字 |

新增一个政体不需要改动引擎或其他模块：写一个模块，在发行版 `Members` 里加一行，
在场景配置里给国家填 `polity` 即可。摘掉政体模块，标准局照常开局，只是那个国家不在。

---

## 十三、存档与版本

**1.0 之前不承诺旧档兼容，不提供兼容适配或自动迁移。** 存档按当前发行版声明严格核对，环境不符即拒绝装载。

每个模块显式负责自己的存取：

```python
def save_state(self, world):
    return world.buildings.to_save()

def load_state(self, world, state):
    world.buildings.restore(state)
```

`save_state` 的返回值必须能被 JSON 序列化。`load_state` 应当校验输入并拒绝不认识的数据；
基类默认实现对非空状态抛出 `ModError`，因此有状态的模块必须实现它。

整局存档（`zhanguo.session/2`）保存发行版依赖、各模块状态、客户端与发行版服务状态、逐动作审计及控制权上下文；完整契约见 [发行版 Session API](发行版Session.md)。

装载时先核对依赖清单：

```python
runtime.check_save_dependencies(manifest)
```

清单记录发行版全部字段、每个模块的 Id/版本/依赖/声明原文、阶段契约、状态所有者、
全部工具 JSON Schema、面板清单与内容注册表。任何一项不同都拒绝装载。

**何时必须提升 `[Mod] Version`：** 模块的声明、状态格式、工具 schema、
阶段契约、内容定义或依赖关系发生变化时。仅修改内部实现、不改变任何对外接口时不必提升。

**新增内容也会改变依赖清单**（内容注册表在清单里），因此给发行版加一种资源或兵种后，
旧存档会被拒绝。这是有意的：旧档里不会存在新内容的库存或部队。

世界状态 `phase` 与 `polity` 由引擎拥有，在存档里记为 `$engine`。
`load_state` 会校验存档里的模块清单与当前装载的模块集合完全一致。

恢复失败的候选世界由宿主丢弃，不会替换当前世界。

---

## 十四、测试

测试用 `unittest`，放在 `tests/` 下，使用合成配置与临时目录，不读写真实对局存档。

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m unittest tests.test_example_mods -v
```

建议覆盖的五项：

1. **装得上**：`Runtime.load(发行版)` 不报错，模块与阶段顺序符合预期。
2. **工具可用**：通过 `player.actions()` 确认工具存在、schema 正确；
   非法参数被拒绝。
3. **阶段生效**：跑一轮结算，检查写进 `context` 的数据，
   并把**数量对账**做掉（库存的变化等于声明的产出或消耗）。
4. **存取往返**：`save → load → save` 得到相同结果；改变模块版本后旧档被拒绝。
5. **摘得掉**：去掉一个模块，装载期报出 `state 无主` / `缺少依赖` / `所需能力无提供者`
   三类错误之一，而不是 `ImportError`。

示例见 [tests/test_example_mods.py](../tests/test_example_mods.py)。

---

## 十五、报错速查

装载期错误全部由 `engine/runtime.py` 抛出。

| 报错 | 原因 | 处理 |
| --- | --- | --- |
| `{模块} 缺少依赖 {Id}` | `Requires` 指向未装载的模块 | 加进 `Members`，或去掉该依赖 |
| `依赖存在环：…` | `After` / `Before` 成环 | 打破环 |
| `重复的 Mod ID` | 两个模块 Id 相同 | 换 Id，或去掉重复装载 |
| `模块声明缺失：{路径}` | `Members` 里的路径不存在 | 检查相对路径 |
| `没有 Mod 声明：{目录}` | 目录里没有模块声明 | 检查装载路径 |
| `同时加载多个发行版` | 同名目录下有两份 `[Distribution]` | 一个目录只放一份清单 |
| `{路径} 未实现 GameMod` | 实现类没有继承 `GameMod` | 修正 `Entry` |
| `state 有多个写入所有者：{键}` | 两个模块声明同一个状态键 | 只留一个所有者 |
| `state 无主：{键}` | `RequiredState` 里的键无人声明 | 补模块，或去掉该键 |
| `能力有多个提供方：{能力}` | 两个模块提供同一能力 | 只留一个提供方 |
| `{模块} 所需能力无提供者：{能力}` | `Needs` 无人满足 | 装载提供方 |
| `数值有多个所有者：{键}` | 两个模块声明同名数值 | 换键名 |
| `工具名称/别名冲突：{名}` | 工具名或别名重复 | 换名 |
| `模块工具不能占用玩家 API 保留名称` | 工具名与 `player_state` 等冲突 | 换名 |
| `结算阶段冲突：{名}` | 阶段名重复 | 换名 |
| `未知挂点：{Id}` | 阶段的 `hook` 不在挂点表里 | 在发行版加 `[[Hooks]]` |
| `{阶段} 挂点条件无人提供：{条件}` | `needs` 没有提供者 | 补提供者，或去掉该条件 |
| `条件有多个提供方：{条件}` | 两个阶段发布同一条件 | 只留一个 |
| `条件位于消费挂点之后：{条件}` | 提供者排在消费者之后 | 调整挂点顺序 |
| `阶段依赖违背发行版挂点顺序：{a} → {b}` | `after` / `before` 与挂点顺序矛盾 | 调整其一 |
| `未知作用域：{scope}` | `scope` 不是 `world` / `actor` | 修正 |
| `玩家状态必须声明唯一的只读工具` | `PlayerState` 里有不存在或非只读的工具 | 同步增删 |
| `玩家事件必须引用只读工具` | `PlayerEvents` 指向的不是只读工具 | 修正 |
| `行动门声明缺少公共能力或方法` | `ActionGate` 引用了私有方法或不存在的能力 | 修正 |
| `玩家时钟必须引用公共能力和方法` | `PlayerClock` 同上 | 修正 |
| `面板名称冲突：{名}` | 两个模块声明同名面板 | 换名 |
| `Mod 方法冲突：{名} ({A} / {B})` | 两个 mixin 定义了同名成员 | 换名 |
| `多个 Mod 定义世界构造器` | 两个 mixin 都定义了 `__init__` | 改用 `initialize_world` |
| `对象接口无提供者：{能力}` | 调用了不存在的能力 | 检查拼写与装载 |
| `{模块} 未声明对象接口调用：{能力}.{方法}` | 缺 `Needs` 或 `[Interfaces]` 条目 | 补声明 |
| `对象接口不能调用私有方法` | 方法名以下划线开头 | 用公开方法 |
| `{模块} 未声明向 {所有者} 请求写入 state：{键}` | 跨模块写入未声明 | 在 `[State] Requests` 补上 |
| `保留的运行时状态 ID：$engine` | 模块 Id 用了 `$engine` | 换 Id |
| `Hooks 必须是发行版的有序列表` / `挂点 Id 非法` / `重复的挂点 Id：{Id}` | 挂点声明有误 | 修正 |
| `内容注册者未加载：{Id}` | 内容由未装载的模块注册 | 检查装载 |
| `Duplicate content ID: {Id}` | 同一 ID 被注册两次 | 替换内容需先换掉原模块（见 §七） |
| `存档 Mod 依赖/版本/接口与当前加载环境不相同，拒绝加载` | 存档与当前发行版不一致 | 用对应版本续局，或开新档 |
| `模块声明缺失：…（挂点或 state 无主）` | 见 `缺少依赖` 与 `state 无主` | —— |

运行期的拒绝走回执，不中断对局：

| 回执 code | 含义 |
| --- | --- |
| `unknown_action` | 工具不存在 |
| `unavailable_action` | 该工具对当前行动者不可用（作用域或政体禁用） |
| `invalid_arguments` | 参数类型或结构不符 |
| `domain_rejected` | 领域规则拒绝（余额不足、条件不满足等） |
| `action_failed` | 领域逻辑返回了失败状态 |
| `inactive_actor` | 行动者不存在或已灭亡 |

参数绑定与转换错误由 `ToolArgumentError` 转成 `invalid_arguments` 回执；领域的 `ValueError` / `KeyError` 转成拒绝回执。工具内部的 `TypeError` 等未捕获程序错误会向宿主传播并停止运行。

---

## 附录 A：`GameMod` 可覆写的方法

| 方法 | 何时调用 | 返回 |
| --- | --- | --- |
| `mixins()` | 构造世界类时 | 混入 `World` 的类；可返回空元组 |
| `state_keys()` | 装载时登记所有权 | 本模块独占写入的状态键 |
| `needs()` | 装载时校验 | 需要的能力名 |
| `provides()` | 装载时登记 | 提供的能力名 |
| `numbers()` | 枚举数值时 | 本模块的数值表 |
| `content_definitions()` | 装载时注册内容 | `{命名空间 ID: 定义}` |
| `objects(world)` | 调用对象接口时 | `{能力名: 对象}` |
| `tools()` | 登记工具 | `Tool(...)` 元组 |
| `schema_for_actor(world, actor, schema)` | 生成玩家动作表时 | 按行动者调整后的 schema（须自行深拷贝） |
| `panels()` | 登记面板 | `Panel(...)` 元组 |
| `presentations()` | 登记只读展示接口 | `{接口名: 值或可调用对象}`；UI Mod 注册 `ui:create(cfg)` |
| `stages()` | 登记结算阶段 | `Stage(...)` 元组 |
| `initialize_world(world)` | 世界构造完成后一次 | 无 |
| `configure(declaration, path)` | 装载时 | 覆写后须调用 `super()` |
| `save_state(world)` / `load_state(world, state)` | 存档 / 读档 | 见 §十三 |
| `rule_sections(world)` | 生成规则与讲义 | `RuleSection(...)` 元组 |
| `apply_state_change(world, key, operation)` | 跨模块写入本模块状态时 | 放行则返回 `operation()` |
| `before_tool` / `after_tool` | 工具调用前后 | 后者须返回（可变换的）结果 |

由引擎按约定查找、但不是基类方法的有：`install_presentations(exports)`、
`finish_world(world)`、`command_actors()`；政体专用的是 §十二 里的三个。

`presentations()` 是现役只读展示注册接口；独立 `ui:standard` 通过它注册 `ui:create`，见 [UI Mod](../mods/ui/README.md)。`command_actors()` 是遗留扩展约定。

---

## 附录 B：`Tool` 与 `Stage` 字段

```python
Tool(name, handler, aliases=(), description="", adapter=None,
     parameters=None, argument_types={}, read_only=False)
```

`handler(world, actor, **arguments)`；`argument_types` 用于在 schema 缺失时声明参数类型；
`adapter(world, actor, arguments)` 可在转换前改写参数（用于别名或旧参数兼容）；
`parameters` 直接给定 JSON Schema，省略时按函数签名生成。

```python
Stage(name, handler, requires=(), after=(), before=(),
      title="", description="", hook="", needs=(), provides=(), scope="world")
```

`title` 与 `description` 用于结算顺序表；`hook` 为空时该阶段只按 `requires`/`after`/`before` 排序
（单模块开发时可用）。

---

## 附录 C：示例索引

| 配方 | 示例 |
| --- | --- |
| 最小 Mod | [mods/examples/hello](../mods/examples/hello) |
| 添加资源 | [mods/examples/tea](../mods/examples/tea)、发行版 [tea_dist](../mods/examples/tea_dist) |
| 插入新阶段 | [mods/examples/harvest](../mods/examples/harvest) |
| 添加兵种 / 兵种专属阶段 | [mods/examples/elephants](../mods/examples/elephants) |
| 以上两条的发行版 | [mods/examples/recipes_dist](../mods/examples/recipes_dist) |
| 替换已有内容 | [mods/examples/no_power](../mods/examples/no_power)、发行版 [no_power_dist](../mods/examples/no_power_dist) |
| 提供客户端 | [mods/examples/idle_client](../mods/examples/idle_client) |
| 最小可玩游戏 | [mods/examples/playable_min](../mods/examples/playable_min)、入口 `mods.playable.toml` |
| 全部跑一遍 | [mods/examples/tour.py](../mods/examples/tour.py) |
| 验收测试 | [tests/test_example_mods.py](../tests/test_example_mods.py) |
