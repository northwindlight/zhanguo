# Native 正式发行版

2026-10-13：`native` 3.0.0 正式装配已改造的经济域，根入口为仓库 `mods.toml`。与 `mods/economic_system` 的独立验收包采用相同领域模块；正式发行版自己的成员、必需状态及挂点在本目录 `mod.toml` 声明。

## 已装配模块

资源、建筑、市场计价、市场、经济估值、经济报表、央行、经济查询共 8 个模块。资源、建筑、市场、经济和银行分别拥有各自的状态；行动者为资源账户。央行随成员模块加载，无启用开关。

11 个工具：`build / buy / sell / market / market_quote / econ / report / building_economics / bank / borrow / buy_report`。内容参数保持命名空间 ID；建筑工具从注册表枚举类型，扣款、归属、容量与延迟落成仍由建筑对象裁决。

发行包声明 5 个挂点：建筑生产 → 市场结算 → 建造落成 → 银行结算 → 经济报表。引擎校验声明的依赖和唯一性，不固定槽位数量。建筑生产内部次序仍由建筑域决定。

## 使用正式入口

```python
from engine.runtime import Runtime

runtime = Runtime.load('mods.toml')
world = runtime.world_class()()
# 宿主/剧本创建账户、站点和初始建筑。
world.resources.open('Qin', {'native:gold':1000, 'native:wood':100})
world.market.set_participants(1)
world.buildings.register_site('native:capital', 'Qin', (0,0),
                              resources={'native:gold':10, 'native:farmland':10})
world.buildings.seed('native:capital', 'native:gold-mine')
# 玩家动作统一走工具接口。
world.call_tool('Qin', 'build', {'site':'native:capital', 'kind':'native:farm'})
result = world.resolve_turn()
assert world.economy.turns('Qin')[-1].gdp.total == 10
assert world.buildings.count('native:capital', 'native:farm') == 1
world.phase = '行动'  # 宿主开始下一周期；不重置域内时钟。
loan = world.call_tool('Qin', 'borrow', {})
assert loan.principal == 50
```

10 周期发布一次报表，每周期都记录经济结果。宿主负责初始内容、行动轮转、市场参与者数量和显示语言；不借用旧世界的国家或资源字典。

更换计价/央行规则只需更换对应 `Members`，示例分别是 `../examples/fixed_market_pricing/mod.toml` 与 `../examples/fixed_credit_bank/mod.toml`。其他领域和工具代码不变。模块身份、政策及内容契约不同的存档不能直接混载。

## 持久化与验收

宿主保存 `{dependencies: runtime.dependency_manifest(), state: runtime.save_mod_state(world)}`。恢复时先 `runtime.check_save_dependencies`，再创建空世界并 `runtime.load_mod_state`；文件路径由宿主决定。本轮验收使用临时合成 JSON，不读取真实对局存档。

```bash
.venv/bin/python -m unittest tests.test_native_distribution
```

7 项正式入口验收通过：入口一致性、状态/工具装配、建造与交易的库存一致性、归属与阶段校验、默认发布周期、银行收支和 GDP 分离、依赖检查后的读档续算、替换成员以及 tea 注册但未上市。加上领域与通用引擎回归，当前共 124 项通过。

## 旧整局边界

按本轮确认，旧领土、外交、政体、军队全流程、玩家面板、`mp_run.py`/旧看海循环暂不装入本发行版，另做整局接线。军队对象与资源协作的既有独立测试继续保留。旧实现文件没有删除；旧发行版声明存于 [历史清单](../../docs/archive/native-distribution-2026-10-12.toml)，仅作后续迁移依据，不能作为当前可运行包加载。

领域细节：[建筑](buildings/README.md)、[市场](market/README.md)、[报表](economy_reports/README.md)、[央行](bank/README.md)、[查询](economy_queries/README.md)。
