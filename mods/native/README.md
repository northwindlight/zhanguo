# Native 正式发行版

2026-10-13：`native` 4.0.0 已完成当前领域模块装配，入口是仓库 `mods.toml`。按用户确认，本次完成领域模块；`mp_run`、LLM 玩家、规则 AI 和旧看海循环另做客户端接线。

## 模块与边界

共 18 个模块、50 个工具、17 个结算处理器、14 个发行版挂点。模块继续使用资源、建筑、国家的模板：`mod.toml` 声明身份、依赖、状态与接口，`mod.py` 装配对象、工具、面板和挂点，领域对象拥有业务状态与行为，显式保存与恢复。纯算法、查询和政体模块不虚设持久状态或玩家动作。

| 模块 | 负责内容 | 状态所有者 |
| --- | --- | --- |
| resources | 资源类型、账户、原子兑换 | resources |
| buildings | 站点、建筑、产销和落成 | buildings |
| market_pricing / market | 市场计价、交易 | market |
| economy_valuation / economy_reports | 估值、经济事实和报表 | economy |
| bank / economy_queries | 信贷、经济查询 | bank |
| [nations](nations/README.md) | 身份、展示名、存续登记 | nations |
| [government](government/README.md) | 周期、国策、小结、结束行动 | government |
| [map_generation](map_generation/README.md) | 可替换的确定性地图算法 | 派生缓存 |
| [territory](territory/README.md) | 地形、核心、坐标与站点关联 | territory |
| [diplomacy](diplomacy/README.md) | 联盟、条约、战争、表决、命令 | diplomacy |
| [vision](vision/README.md) | 同盟视野、建筑与资源隐私 | 无独立状态 |
| [army](army/README.md) | 部队、路径、征召、补给、战斗 | units / military |
| [succession](succession/README.md) | 市政厅亡国及跨域清理 | succession |
| [intelligence](intelligence/README.md) | 通信、运输、间谍、事件快照 | intelligence |
| [polities/huns](polities/huns/README.md) | 国家作用域、开局配置和政策覆盖 | 引擎 polity 作用域 |

跨域协作只调用声明过的公共能力及方法。静态资源、兵种、建筑、地形使用英文命名空间 ID；国家、联盟实例也有稳定 ID，展示名可变。当前土地归属只由建筑站点保存，领土与国家查询即时读取，避免重复所有者状态。

## 场景初始化与行动

```python
from engine.runtime import Runtime

runtime = Runtime.load('mods.toml')
world = runtime.world_class()()
world.territory.configure(8, 73)  # 物化地块前设置尺寸、种子
world.nations.create('scenario:qin', '秦',
                     {'native:gold':1000, 'native:wood':100})
world.territory.create('scenario:capital', 'scenario:qin', 1, 1,
                       terrain='native:plain', core='scenario:qin')
world.buildings.seed('scenario:capital', 'native:town-hall')
world.succession.enroll('scenario:qin')  # 初始化完成后加入市政厅亡国规则
world.market.set_participants(1)
world.call_tool('scenario:qin', 'plan', {'text':'发展经济并保持独立'})
world.call_tool('scenario:qin', 'build',
                {'site':'scenario:capital', 'kind':'native:farm'})
world.call_tool('scenario:qin', 'end_turn', {'summary':'本期建设已经完成'})
result = world.resolve_turn()
assert world.government.period() == 1
assert world.nations.get('scenario:qin').alive
world.phase = '行动'  # 宿主开启下一行动期；周期由 government 自动推进
```

坐标使用 0-based；工具行动者绑定现存国家 ID。`end_turn` 后写工具拒绝，查询仍可用。宿主负责初始剧本、市场参与者数量、行动轮转及显示语言。纯经济场景可不 `enroll`，先创建国家再布置首都也不会在初始化中被判亡国。

## 结算与持久化

挂点顺序：周期开始（投票计票、通信到达）→ 建筑生产 → 外交与发信 → 军事补给 → 战斗 → 撤离 → 占领 → 亡国 → 遣返 → 市场 → 建造落成 → 银行 → 报表 → 周期结束（事件通知、周期推进）。顺序由发行版声明，处理器依赖由引擎校验。

外交提交预留费用，外交阶段重验后才生效；联盟表决在下一周期统一计票。通信下一周期到达，间谍延迟三周期；共享地图采样于发出时，间谍采样于到达时。战斗、占领事件记录发生时的接收者，后来改变土地或视野不会重算历史收件范围。

宿主保存 `{'dependencies':runtime.dependency_manifest(), 'state':runtime.save_mod_state(world)}`。先 `check_save_dependencies`，再向空世界 `load_mod_state`。API 3 保存模块状态以及 `$engine` 的阶段、国家作用域；存档保留待结算动作、预留货物、投票、运输、事件和战斗随机状态。身份、模块版本、接口或挂点不同明确拒绝混载；恢复失败的候选世界由宿主丢弃，不替换当前世界。旧整局存档没有直接迁移器。

## 验收与旧客户端

```bash
.venv/bin/python -m unittest tests.test_world_domains tests.test_native_distribution tests.test_nation_objects tests.test_bank_objects tests.test_economy_objects tests.test_building_objects tests.test_market_objects tests.test_resource_objects tests.test_army_resources tests.test_runtime_content_hooks tests.test_mod_runtime.TestRuntime
```

176 项领域与通用引擎回归通过，其中 39 项覆盖剩余领域联动，13 项覆盖国家。场景包括模块替换、缺失依赖拒绝、私人信息过滤、政策范围、外交退款、表决、直接保障传导、和平付款、军队动作/补给/战斗/占领、亡国清理以及中途存读档后继续结算的一致性。仅使用合成世界与临时 JSON。

旧 `logic / schemas / tools / views / initial` 文件保留供客户端迁移参考，当前活动模块不加载它们的旧 mixin 或世界字典。旧声明位于 [历史发行版](../../docs/archive/native-distribution-2026-10-12.toml) 和 [历史领域声明](../../docs/archive/legacy-domain-declarations/)；军队早期夹具显式使用历史声明。旧 `TestDeclaredExtension` 使用旧身份/槽位，在此前发行版上也已失败，不计入当前通过数。旧客户端、旧全量测试和真实对局继续留待另做。

经济细节：[建筑](buildings/README.md)、[市场](market/README.md)、[报表](economy_reports/README.md)、[央行](bank/README.md)、[查询](economy_queries/README.md)。
