# 国家模块

`native:nations` 1.0.0 拥有 `nations` 状态，由 `Nation / Nations` 表达国家身份与存续记录。已装入正式发行版 native 4.0.0；也可单独加载 `mods/nations_acceptance`（仅国家、资源、建筑三个模块）。

## 身份与领域关系

- 国家实例 ID 使用英文命名空间，如 `scenario:qin`。ID 与展示名分离；重名不合并国家，改名不改变工具行动者、账户或建筑归属。国家实例属于世界状态，不注册为静态内容定义。
- 每国创建同 ID 的资源账户；库存仅由资源域保存。创建先校验国家，再调用资源域原子开户；重复国家 ID、已有同名账户、错误资源或金额均在变更前拒绝。黄金是否允许负数由资源类决定。
- 建筑站点的 `owner` 引用国家 ID。国家查询即时读取建筑域的站点事实；转移站点后查询随之变化，国家存档不复制站点或库存。普通账户和普通账户所属站点仍可供其他领域使用，不自动成为国家。
- `Nation` 是不可变记录，含 `id / name / alive / extinction_reason`；`account` 派生为 ID。`Nations` 提供有所有权保护的创建、改名、灭亡与恢复接口。

```python
from engine.runtime import Runtime

runtime = Runtime.load('mods.toml')
world = runtime.world_class()()
world.nations.create('scenario:qin', '秦', {'native:gold':1000, 'native:wood':100})
world.nations.create('scenario:chu', '楚', {'native:gold':100})
world.buildings.register_site('scenario:capital', 'scenario:qin', (0,0),
                              resources={'native:farmland':10})
world.nations.rename('scenario:qin', '秦国')
world.call_tool('scenario:qin', 'build', {'site':'scenario:capital', 'kind':'native:farm'})
overview = world.call_tool('scenario:qin', 'nation', {})
assert overview.nation.name == '秦国'
assert overview.sites[0].owner == 'scenario:qin'
world.resolve_turn()
```

## 行动者与查询

正式发行版声明 `ActorCollection = "nation_actors"`，按国家创建顺序列出现存国家。宿主/剧本调用 `create / rename / extinguish`；玩家工具不提供这些管理权限。

`nation()` 返回本国身份及站点；`nations()` 返回公开的全体国家身份与存续事实（含已灭亡国家），不暴露他国库存、站点或资产。两者为只读工具，结算后仍可查询。未知 ID、展示名、非国家资源账户及已灭亡国家均不能作为正式工具的行动者。

领域消费者使用 `native:nation-inspection` 的 `get / ids / snapshot / overview` 读取事实；该门面不提供写方法。治理或生命周期消费者可声明 `native:nations` 的具体写方法。调用须同时声明能力依赖与 `[Interfaces]`，不能直接从别的模块改写 `world.nations`。

## 存续与灭亡边界

`extinguish(id, reason)` 登记宿主已经裁决的灭亡，要求非空原因且名下已无站点；仍有站点则整次拒绝。灭亡后国家退出行动者集合，身份及账户保留，ID 永不在正常接口中复用；本轮不提供复国接口。

此接口不判断“没有市政厅就是亡国”。先创建、后安置站点的剧本流程也不会自动亡国。正式发行版的领土、军队、外交与生命周期模块现已接入；剧本安置完成后显式加入 `succession.enroll`，由其负责站点无主处置、部队清理、条约取消、强制休战。普通资源账户可表达暂存或其他非国家主体，其具体无主归属规则尚未定义。

灭亡不会清空库存、取消贷款或删除报表；现有经济/银行仍按账户维护历史账务，账户仍可能结息并生成报表。这是本轮接口边界，后续亡国清算政策须由相关领域决定。宿主不能通过重新指派站点给已灭亡国家绕过存续契约；这样的状态在国家保存/恢复时拒绝。

市场参与者数量和世界行动轮转仍由宿主提供；本模块没有偷偷增加结算阶段或改动市场规模。

## 持久化与验收

国家只保存有序身份记录；声明在资源、建筑之后加载，恢复时验证每国账户存在、已灭亡国家没有站点。错误记录、重复 ID、非法存续字段与悬空账户在提交国家状态前拒绝。使用运行时依赖清单校验后，恢复到空世界；native 3.0.0 的依赖清单与新发行版不同，会明确拒绝混载。

```bash
.venv/bin/python -m unittest tests.test_nation_objects tests.test_native_distribution
```

13 项国家验收通过；与正式入口及其他领域、通用引擎回归合计 176 项通过。测试仅使用合成世界和临时 JSON；覆盖改名后的库存和建造归属、重名国家、非法开户无部分变更、账户与玩家分离、灭亡前置条件、ID 保留、不可变查询、跨域权限、损坏存档拒绝、灭亡后的读档与十周期经济续算一致。
