# 独立经济层验收发行包

正式发行版现已通过仓库 `mods.toml` 接线，见 [native](../native/README.md)。本目录继续保留为独立验收包。

加载本目录得到资源、建筑、可替换市场计价、市场、估值、经济报表、央行及玩家经济工具。央行作为成员模块加载，不通过开关启用。

根 `[[Hooks]]` 声明当前顺序：建筑生产 → 市场结算 → 建造落成 → 银行结算 → 经济报表。引擎核对声明的条件、阶段关系与唯一性；包作者可以插入阶段和调整符合依赖的顺序。建筑生产内部次序仍由建筑域决定。

```python
from engine.runtime import Runtime

runtime = Runtime.load('mods/economic_system')
world = runtime.world_class()()
world.resources.open('Qin', {'native:gold':1000})
world.buildings.register_site('example:capital', 'Qin', (0,0),
                              resources={'native:gold':10})
world.buildings.seed('example:capital', 'native:gold-mine')
context = world.resolve_turn()
print(context['economy_turns'][0].gdp.total)  # 10
world.phase = '行动'                         # 宿主进入下一行动阶段
print(runtime.call(world, 'Qin', 'borrow', {}).principal)  # 50
```

默认每 10 周期发布报表，每周期都有经济记录。市场参与者数量仍由宿主通过 `world.market.set_participants(count)` 或显式市场结算输入提供。

替换市场计价：将成员 `../native/market_pricing/mod.toml` 换成 `../examples/fixed_market_pricing/mod.toml`。替换银行：将 `../native/bank/mod.toml` 换成 `../examples/fixed_credit_bank/mod.toml`。查询、交易、资源和经济报表代码不变。建筑 2.2.0 同时提供正式 `build(site, kind)` 工具。新增资源及建筑通过注册表加入；tea 未上市时市场不创建行情，未估值时经济层明确保留缺失估值。

该包用于独立域验收。旧国家、地形、外交、军费生产者、完整玩家循环和真实存档未接线；军费和征兵费用通过已定义的经济事实合同输入。旧整局可运行性不在本轮范围。

```bash
.venv/bin/python -m unittest tests.test_bank_objects tests.test_economy_objects tests.test_building_objects tests.test_market_objects tests.test_resource_objects tests.test_army_resources tests.test_runtime_content_hooks
```

2026-10-12：上述独立测试 107 项通过，另有 10 项既有通用引擎回归，共 117 项。新经济系统新增 23 项验收。场景覆盖有/无银行成员、替换银行/市场、tea、金融与 GDP 分离、成熟贷款负现金、净资产、账户权限、只读工具、建筑估算、损坏存档拒绝和空世界恢复后续算一致；全部使用合成数据。
