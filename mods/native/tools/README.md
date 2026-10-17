# native tool 层

发行版级 toolcall 的唯一家：工具名、schema、描述、别名、参数适配、`owner/api/fact/panel/groups` 与回执成文。一个领域一个文件；模块只暴露 `player_api(world)`，不再返回 `Tool`。

## 契约

- `owner` 是**提供玩家 API 的能力名**（如 `native:army`、`economy:bank`），不是模块 ID。替换模块（`fixed_credit_bank`、`no_power`、`tea` 市场）沿用同一份声明。
- 每个 Tool 显式写 `api`（覆盖的 `<Module>API` 方法）；模块 `[PlayerAPI] Methods` 必须被至少一个 Tool 覆盖，漏封装在 `Runtime.load` 时直接报错。
- `fact=True` 进入 `observe()`，`panel=True` 进入统一 `query(panel=…)`；两者都必须 `read_only`。
- 写动作按命令收进 `commands.py` 的伞工具（`military`/`economy`/`relations`/`intelligence`）：内层是 tool 层自己的 `ActionSpec`（含 schema、策略门、adapter 与回执），**不进引擎工具表**；引擎只登记名称、capability/method、read_only 与 groups。伞工具暴露 `action` 参数并转发 `Runtime.call_action`。**脚本与规则 AI 不用这些名字**：它们走 `Runtime.api_call` / `BoundPlayer.call_api(capability, method, ...)`，能力与方法由模块 `[PlayerAPI]` 提供。
- `groups` 是策略组：政体 `Disable = ["group.<名>"]` 会展开成组内工具/动作的 `tool.<名>`、`action.<名>` 和声明的语义键（如 `accept.treaties`、`communication.gift`）；同组新增动作自动被禁。伞工具的 `action` 枚举由 `action_available` 过滤。
- 回执函数接收 UI 传入的 `ctx`（`ConsoleUI`/`ReceiptPresentation` 实例），只做协议与成文，不读领域内部对象、不做规则判断。
- 文案在 `locales/zh-CN.json` 与 `locales/en-US.json`，键前缀 `tool.` / `tools.<name>.description`；两边的占位符必须一致。

## 文件

- `__init__.py`：`build(runtime)` 汇总各领域 spec，按 `owner in runtime.capabilities` 过滤，追加统一 `query` 入口，再交给 `commands.wrap` 收伞。
- `commands.py`：伞工具表与生成器；`COMMANDS` 是一张显式动作清单。
- `nations.py` / `government.py` / `army.py` / `territory.py` / `vision.py` / `buildings.py` / `economy.py` / `bank.py` / `diplomacy.py` / `intelligence.py` / `handbooks.py`：领域 Tool 表 + `RECEIPTS` / `CAPTIONS` / `PANELS`。`nations.py` 的 `foreign` 面板组合本国/各国/关系/外交四块。
- `query.py`：从 `panel=True` 的工具生成只读分发器。
- `common` 逻辑暂复用 `mods/native/locations.py`。

新增一个动作：在模块 `api.py` 加方法 → 模块 `mod.toml` 的 `[PlayerAPI] Methods` 加名字 → 在对应 `tools/*.py` 加 `Tool` 与回执 → 补两份文案。发行版清单不需要再加工具名。
