# Tool 层改造：把散落的 toolcall 收成一层

日期：2026-10-17。前置：[架构复核](../reviews/架构复核-20261017.md)。本文只定边界与结构，不改规则。

**实施状态（2026-10-18）**：T0 引擎、T1 nations/government/army、T2 经济/建筑/土地、T3 外交/情报/手册/查询入口与 experimental 脚本已落地；native 61 个工具全部由 `mods/native/tools/` 注册，模块只留 `player_api`；tool 层自带 zh-CN/en-US 文案目录。T4（工具面收敛、`groups` 接政体策略）尚未做，目标集合待定。

## 一、目标

用户口径：**做一个 tool 层，接管全部可用 API，手工封装全部 toolcall。**

拆成三句：

1. **API 归模块**：每个领域模块只暴露一个玩家 API 对象（动作 + 只读事实），不直接产出模型工具。
2. **toolcall 归 tool 层**：工具名、别名、参数 schema、描述、参数规范化、所属模块、策略组、是否只读/事实/面板、回执成文，全部集中在一个可审阅的层里。
3. **手工封装**：每个 tool 到 API 的映射显式写出，不自动扫描、不反射生成；用加载期覆盖检查保证「没有漏」。

这样做的直接收益：

- 现在散在 20 个 `mod.py` 里的 61 个工具，收成一张表；
- `PlayerState`、`PANELS`、`ReadOnlyPanelKeep` 从这张表派生，不再手工维护三份名单；
- UI 不再按动作名分叉（69 处），改为调用 tool 层的回执；
- 模型工具面收敛（64→34 之类）只改这张表，不改领域模块。

## 二、边界：谁拥有什么

| 层 | 拥有 | 不拥有 |
|---|---|---|
| 领域模块 | 对象、状态、规则、数值、领域事件；一个 `player_api` | 工具名、模型 schema、参数别名、回执措辞 |
| tool 层 | 工具表、schema、描述、别名、参数适配、owner/groups/fact/panel、回执与面板成文 | 规则判断、状态写入、游戏数值 |
| 引擎 | 工具注册、权限门、状态所有权、阶段调度、把 tool 层的工具交给客户端 | 任何游戏词、任何具体工具 |
| UI | LLM 提示编排、终端、地图、语言选择、分节与裁剪、未知工具兜底 | 每个动作的回执分支 |

关键约束：**tool 层可以调用模块的 `player_api`，不 import 模块内部对象；模块不 import tool 层，也不 import UI。** 方向是 `引擎 ← 模块 ← tool 层 ← 装配/UI`，与现在的模块依赖方向一致。

## 三、结构

### 3.1 目录

```text
engine/runtime.py            Tool 增加 owner/groups/fact/panel 等元数据；
                             Distribution.Tools 入口；覆盖检查
mods/native/
  mod.toml                   [Distribution.Tools] Entry = "mods.native.tools:build"
  army/
    api.py                   ArmyAPI：recruit/move/attack/view/logistics/...
    mod.toml                 [PlayerAPI] Methods = ["recruit","move",...]
  market/api.py
  economy/api.py
  ...
  tools/
    __init__.py              build(runtime) -> tuple[Tool, ...]
    army.py                  army 的 ToolSpec + handler + receipt
    market.py
    economy.py
    diplomacy.py
    ...
    query.py                 由 panel=True 的工具自动生成的统一查询入口
    common.py                地点/坐标/参数适配、通用格式化
    locales/                 tool 层自己的语言目录（按工具/字段命名空间）
```

`tools/` 是发行版的一部分，不是引擎的一部分。experimental 显式复用：

```python
# mods/experimental/tools.py
from mods.native.tools import build as native_build
EXTRA = (...)
def build(runtime):
    return (*native_build(runtime), *EXTRA)
```

不复制 20 个模块的清单，也不做自动扫描。

### 3.2 引擎侧最小改动

```python
@dataclass(frozen=True)
class Tool:
    name: str
    owner: str                 # 提供玩家 API 的能力名，例如 "native:army"（不是模块 ID）
    api: tuple[str, ...]       # 覆盖的 player_api 方法名，例如 ("recruit",)
    handler: Callable          # (world, actor, **args)
    aliases: tuple[str, ...] = ()
    description: str = ""
    parameters: dict | None = None
    argument_types: dict = field(default_factory=dict)
    read_only: bool = False
    fact: bool = False         # 进入 observe() 的玩家事实
    panel: bool = False        # 进入 query(panel=...)
    hidden: bool = False
    groups: tuple[str, ...] = ()   # 策略类别，如 "diplomacy.alliance"
    content_parameters: tuple[str, ...] = ()
    adapter: Callable | None = None
    schema_for_actor: Callable | None = None
```

`Runtime`：

- `Distribution.Tools.Entry` 存在时，`build(runtime)` 返回工具；否则回退到各模块 `tools()`（迁移期兼容）。
- `tool_owners[name]` 由 `Tool.owner` 解析：能力名 → `runtime.capabilities[owner]` → 模块 ID → mod，不再由「哪个模块返回了它」推断。用能力名而不是模块 ID，是为了让 `fixed_credit_bank` 这类替换模块沿用同一份 tool 表。
- `Runtime.call` 始终在 `unit_context(解析出的模块)` 里执行；`before_tool/after_tool` 仍调 owner 模块。
- `schema_for_actor` 从模块方法改为 `Tool` 字段（缺省仍可回落到 owner 模块）；模型可见的 enum、描述由 tool 层负责。
- 加载期校验：
  - `owner` 必须是已提供的能力，且能解析到已加载模块；
  - 解析出的模块 `[PlayerAPI] Methods` 必须全部被覆盖：每个方法至少出现在一个 Tool 的 `api` 里；需要合并工具面时，一个 Tool 可以覆盖多个方法；
  - `Tool.api` 里的每个名字必须是该模块已声明的方法；
  - `fact` / `panel` 必须是 `read_only`；
  - `groups` 必须是已声明的策略类别。
- `PlayerSession.observe()` 的事实列表从 `fact=True` 的工具派生；`query` 的面板表从 `panel=True` 派生。
- `Tool.hidden` 由 tool 层决定：panel 端点默认 hidden，由统一的 `query` 暴露；不再需要 `ReadOnlyPanel/ReadOnlyPanelKeep` 两张发行版名单。
- 迁移期允许两套并存：未迁移模块仍返回 `tools()`；一旦模块声明 `[PlayerAPI]` 并由 tool 层接管，它就不能再返回 `tools()`，同名工具在装载期报错。

### 3.3 模块侧：`player_api`

模块只暴露一个玩家 API 对象，方法就是「可用 API」的唯一清单：

```python
# mods/native/army/api.py
class ArmyAPI:
    def __init__(self, world): self.world = world

    def recruit(self, actor, site=None, kind='native:infantry', count=1, x=None, y=None):
        ...
    def move(self, actor, ids, x, y):
        ...
    def view(self, actor):
        ...
    def logistics(self, actor):
        ...
```

`mod.toml`：

```toml
[PlayerAPI]
Methods = ["recruit", "move", "attack", "retreat", "disband", "view",
           "reachable", "frontier", "logistics", "unit_catalog", "threats", "battle", "tile"]
```

模块不再写 `Tool(...)`；`GameMod.player_api(world)` 返回 API 对象。API 方法内部仍然走领域对象和已声明的跨域接口。

### 3.4 tool 层：手工表

```python
# mods/native/tools/army.py
from engine.runtime import Tool
from .common import place_arguments, location_parameters

def specs(runtime):
    def call(method):
        return lambda world, actor, **args: runtime.player_api(world, 'native:army').__getattr__(method)(actor, **args)

    return (
        Tool('recruit', owner='native:army', handler=call('recruit'),
             parameters=..., adapter=..., content_parameters=('kind',),
             description='...', groups=('army.recruit',)),
        Tool('move', owner='native:army', handler=call('move'),
             parameters=place_parameters(...), adapter=place_arguments,
             description='...', groups=('army.move',)),
        Tool('army', owner='native:army', handler=call('view'),
             read_only=True, fact=True, panel=True,
             description='...', groups=('army.view',)),
        ...
    )

def receipt(action, args, data, actor, ctx):
    if action == 'recruit': ...
    if action in ('move', 'attack', 'retreat'): ...
    if action == 'army': ...
```

`tools/__init__.py`：

```python
def build(runtime):
    tools = []
    for module in (army, market, economy, ...):
        tools.extend(module.specs(runtime))
    tools.append(query_spec(tools))   # 统一只读入口
    return tuple(tools)
```

每个领域一个文件，单文件只装「这一事的 toolcall + 回执」，避免再造一个 200KB 的 `mp_ai`。

### 3.5 回执与面板

- tool 层提供 `receipt(action, args, data, actor, ctx)`；`ctx` 提供 `text/num/pos/name/resources/format`，由 UI 注入。tool 层不 import UI。
- `runtime.tool_receipt(action)` 返回该工具的回执函数；UI 的 `ReceiptPresentation.receipt_body` 只做：找工具 → 调回执 → 没有就通用格式。
- 面板（`panel=True`）的成文也放在对应领域的 `tools/*.py`；`queries.py` 的 7 个分支删除，改为统一分发。
- 规则文本仍归模块 `rule_sections()`；briefing 的分节仍归 UI/模块，不塞进 tool 层。tool 层只负责「这次工具调用怎么讲」。

## 四、数据流

```text
LLM tool_call
  → Client（clients/llm.py）按名字调 PlayerSession.submit
  → Runtime.call：校验可见性/阶段/政体 → unit_context(tool.owner)
  → Tool.handler → runtime.player_api(world, owner).method(actor, ...)
  → 领域对象 / 已声明的跨域接口
  → 返回公开数据
  → Tool 回执（tool 层）→ UI 成文 → 模型
```

规则 AI 走同一个 `PlayerSession.submit`，因此与 LLM 共享同一套工具、权限和事实；它不需要 tool 层的 schema，但需要同一份动作名与语义。

## 五、它解决复核里的哪些问题

| 复核问题 | tool 层怎么解决 |
|---|---|
| 3.1 声明双份 | 模块的 `[PlayerAPI]` 是 API 唯一清单；tool 层的表是 adapter 唯一清单；覆盖检查保证两边不漂。TOML 的 `[Capabilities]` 仍建议按 P0 收口。 |
| 3.2 UI 是第二个规则实现 | 动作回执/面板成文移到 tool 层；UI 不再按动作名分叉。规则数值仍在模块，tool 层不重算。 |
| 3.3 三张手工名单 | `fact/panel/groups` 是 Tool 元数据；`PlayerState`、`PANELS`、`ReadOnlyPanelKeep` 派生；漏登记变加载错误。 |
| 3.4 内容 ID 语义 | `groups` 解决策略名单；内容角色（capital/recruiter/castle）仍需单独做，tool 层不替代。 |
| 3.5 跨模块 import | tool 层只调 `player_api`；模块之间仍走能力接口；tool 层不 import 领域内部对象。 |
| 3.6 文档数值 | 不直接解决；手册占位与 `runtime.number` 仍按复核 P5。 |

## 六、迁移步骤

| 阶段 | 内容 | 验收 |
|---|---|---|
| T0 | 引擎：`Tool.owner/groups/fact/panel`、`Distribution.Tools`、覆盖检查、事实/面板派生；保留模块 `tools()` 回退 | 现有发行版行为不变；加一个未覆盖的 `[PlayerAPI]` 方法，装载报错 |
| T1 | 选 **army** 做样板：`army/api.py` + `tools/army.py`；把 army 的 Tool 和回执从 `mod.py`/`receipts.py` 迁出 | 同一存档、同一动作的回执逐句对比；UI 删除 army 分支；army 的 13 个工具全部由 tool 层注册 |
| T2 | 迁 market / economy / bank / buildings；`PlayerState` 与 `PANELS` 改为派生；删除 `mods/native/query/mod.py` 的手写表 | 加一个只读面板只改 tool 层；发行版名单 diff 为 0 |
| T3 | 迁其余模块；删除各模块 `tools()`；UI 的 `receipts/queries/console` 动作分支清零 | `grep "if action" mods/ui` 为空；模型工具面只剩 tool 层一张表 |
| T4 | 工具面收敛（64→34）：在 tool 层合并/隐藏；`groups` 接入政体策略，huns 的 31 条名单缩成组 | 模型可见工具数达标；huns 新动作自动被禁 |
| T5 | 内容角色、跨模块契约、文档数值同源 | 按复核 P4/P5 |

T0 是引擎改动，必须先做；T1 用一个模块验证表结构与回执协议，避免一次性搬 61 个工具。

## 七、覆盖检查（防「散的」复发）

加载期或独立测试执行：

1. 每个模块有 `[PlayerAPI] Methods`；每个方法至少被一个 Tool 的 `api` 覆盖。
2. 每个 Tool 的 `owner` 已加载，`api` 里的方法存在。
3. 每个 `fact`/`panel` 工具都是 `read_only`。
4. 每个 `panel` 工具要么 hidden（由 query 暴露），要么明确 keep。
5. 每个 `groups` 都在发行版的策略类别表里。
6. 同一工具名/别名只出现一次。
7. 工具层不出现：`native:` 内容 ID 的规则判断、`world.<domain>` 直接写入、数值常量。

第 7 条靠代码评审 + 简单的 grep 检查（tool 层允许 `native:` 出现在 enum/描述，不允许出现在分支判断里）。

## 八、风险与不做

- **风险：tool 层变成新的 `mp_ai` 上帝对象。** 约束：一个领域一个文件；tool 层只做协议与成文，不做规则；不 import 领域内部对象；单文件超过约 300 行就拆。
- **风险：模块 `[PlayerAPI]` 与 tool 表两份声明漂移。** 用覆盖检查兜底；这是有意的两份：一份是 API，一份是 adapter。
- **不做自动扫描/反射生成 tool。** 用户口径是手工封装；自动生成会回到「魔法」。
- **不把规则文本、数值、面板数据塞进 tool 层。** 它们仍归模块。
- **不为旧档做兼容层。** tool 表变化是否废档，按复核 P5 的存档清单拆分裁决。
- **不一次迁完。** T1 样板通过后再铺开。

## 九、待裁决

1. **回执归 tool 层还是模块？** 本文建议归 tool 层（与 toolcall 同文件），规则文本归模块。若你希望模块自带回执，tool 层只保留 schema/args，也成立。
2. **`player_api` 是否新增对象？** 本文建议新增，作为「全部可用 API」的唯一清单；若不想加一层，可让 tool 层直接调领域对象的公开方法，但会失去覆盖检查的锚点。
3. **存档清单是否拆出 tool 表？** 加只读面板不应废档；建议拆，但这是规则契约的口径变化。
4. **experimental 是复用 native tool 层还是独立一份？** 本文建议显式复用 + 追加，不复制清单。
