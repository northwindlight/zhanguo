# 资源对象与内容身份

当前进展（2026-10-13）：native 4.0.0 已接入其余领域，详见 [正式发行版](../README.md)。下文早期阶段的“未接线”描述保留为历史记录；旧客户端仍另做。

2026-10-12：本轮授权覆盖资源对象、army 的资源依赖、命名空间内容注册和发行版挂点。未改造区域不要求接线，也不以整局运行作验收。

## 资源模板与子类

`engine/resource.py` 的 `Resource` 是模板：有资源类型 ID、所属账户、库存数量、数量校验、补充、消耗、消费资格、计价、只读快照及显式序列化。引擎模板不包含 native 资源名称或数量表。

`types.py` 显式定义具体子类：

| 类 | 内容 ID | 行为 |
|---|---|---|
| Gold | `native:gold` | 非可交易货物，重写现金计价：消费 50 金就是 50；允许显式记录透支，消费仍检查可用余额。 |
| Food | `native:food` | 食物库存，继承非负有限数量与按价格计价。 |
| Wood | `native:wood` | 木材库存，同上。 |
| Ore | `native:ore` | 矿石库存，同上。 |
| Oil | `native:oil` | 石油库存，同上。 |
| Equipment | `native:equipment` | 装备库存，同上。 |
| Supply | `native:supply` | 补给库存，同上。 |

子类覆盖 `TYPE_ID / NAME_KEY`，并可重写 `validate_amount / value_of` 等行为。规则数字继续属于各域的声明式数值表，资源对象负责数量与消费行为。

## 注册与所有权

各域通过 `GameMod.content_definitions()` 返回 `{完整 ID: 定义}`，加载时累加到统一的 `runtime.content`。例如：

```python
runtime.content['native']['gold']       # Gold 类
runtime.content['native']['infantry']   # army 声明的兵种定义
runtime.content.definition('native:gold')
runtime.content.definitions()           # 可枚举 (完整 ID, 定义)
```

引擎校验英文命名空间 ID、全局不重名、注册者已加载和批次原子性；它不理解定义的业务种类。映射/序列容器只读；其他定义对象由所属域约束。登记前不需要修改引擎的资源或兵种列表。

资源域解释注册表中的 `Resource` 子类，核对登记 ID 与类的 `TYPE_ID` 一致。`ResourceAccounts` 拥有全部资源账户与库存；`Owns = ["resources"]`。每个资源的能力门面只暴露该资源，例如 `native:food` 的 `balance(owner) / consume(owner, amount) / snapshot(owner)`，不能通过它访问另一种资源。

新增资源可由另一域提供：

```python
class Mana(Resource):
    TYPE_ID = 'example:mana'
    NAME_KEY = 'resource.example:mana.name'

class ExtraContent(GameMod):
    def mixins(self):
        return ()

    def content_definitions(self):
        return {'example:mana': Mana}
```

把该域加入发行版后，资源域从统一注册表实例化新资源、提供它的能力、枚举并保存库存。定义必须在世界初始化前完成注册；本批没有运行中新增库存类型的协议。

## army 的具体依赖

army 模块与部队集合门面 ID 为 `native:army`；兵种内容 ID 为 `native:infantry / native:cavalry / native:militia`。army 声明 `native:gold / native:food / native:equipment / native:supply`，每种仅允许 `balance / consume / value`。征兵、补给、回血与军费计价都走这些对象接口，已退出 `World.res / add_res / _mval`。

第四种兵通过相同注册表声明 `category = "unit"`、名称键、HP、速度、补给、攻击、征兵资源配方和移动代价。army 负责验证字段，部队集合、征兵、伤害、回血、查询与存档使用登记的定义。世界对应的征兵 schema 枚举全部登记兵种。

新增配方若使用 army 尚未声明的资源，要在 army 的 `Needs / Interfaces` 中增加该具体依赖；注册内容不授予消费权限。

## 语言、存档与当前边界

内部资源键和兵种键只接受内容 ID；不会把中文名或裸英文名自动解释成 ID。`locales/zh-CN.json` 与 `locales/en-US.json` 提供展示名称，定义只存翻译键。切换展示语言不修改资源账户、配方、部队类型或存档 ID。

资源持久化协议是 `accounts → owner + stocks → id + amount`，保留账户/库存顺序。未知资源、重复资源/账户、缺失资源、非法数量、额外字段均拒绝。加载先完整构造并校验，再替换现场账户。缺失账户不会被查询隐式创建；`open` 为未指定的已登记资源初始化 0 库存。

运行环境清单记录内容 ID 与注册域、域版本和发行版挂点；改业务定义时仍需提升域版本。登记表不把任意 Python 定义反射成存档。

经济/建筑/外交/开局、旧政体、旧玩家入口及旧全局资源字典尚未迁移。发行版保留它们的旧声明，本轮没有为其补兼容转口；当前整局装配可能因旧 `army.units` 能力引用而拒绝。建筑、地形等未改造域的旧中文标识也未全面迁移。旧整局测试的历史绿灯不能作为本轮接线验收。

## 发行版挂点

`mods/native/mod.toml` 的根级 `[[Hooks]]` 有序列表声明挂点 `Id / Name`。引擎读取此列表，没有固定槽位数；展示名称由发行版提供。阶段引用未知挂点、重名挂点、缺失条件/阶段依赖、反向跨挂点依赖及同挂点循环都会拒绝。空挂点允许保留作扩展位置；同挂点的阶段按依赖排序，同级按阶段 ID 稳定排序。

## 独立验收

```bash
.venv/bin/python -m unittest tests.test_runtime_content_hooks tests.test_resource_objects tests.test_army_resources
```

新增场景验证继承重写、注册批次原子性、同一局部 ID 的不同命名空间、非法/重名 ID 拒绝、资源与 army 的权限、征兵扣料、补给/回血/民兵免供、语言切换、库存及部队显式往返、外域新增资源、第四种兵、动态 schema 和任意数量的发行版挂点。旧域在测试中是明确的局部夹具，不是生产适配器。

资源/army 首批 29 项新增独立测试与 10 项既有通用引擎测试通过，合计 39 项；测试不读取真实对局存档。

后续市场批新增 `native:resource-exchange.exchange(owner, debits, credits)`：完整验证具体资源子类的扣款与入库行为后原子提交，任一腿失败则所有库存不变。该交易能力单独授权，不扩大 army 的逐资源消费权限；资源域版本提升为 1.1.0。市场与可替换计价模块的验收、tea 未上市时的边界和目前合计 55 项验证见 [市场说明](../market/README.md)。


建筑批将资源域升级到 1.2.0：兑换允许纯扣款或纯产出，新增 `exchange_batch(operations)` 跨账户原子批次及兑换门面的只读 `snapshot(owner)`。至少一腿非空，所有候选操作依序完成校验才统一提交；市场的两腿路径与 army 的权限保持原合同。建筑与市场联动、tea 产出及目前 75 项验证见 [建筑说明](../buildings/README.md)。


经济批将资源域升级为 1.3.0，新增独立只读 `native:resource-inspection` 能力，暴露 `owners / snapshot / value`，支持核算而不授予库存写权限。GDP、资产、tea 未估值与当前 94 项验收见 [经济报表说明](../economy_reports/README.md)。

经济完成批升级为 1.4.0：新增单独授权的 `native:bank-cash.adjust(currency, changes)`，验证所有账户及具体现金子类的最终数量后统一提交。只接受 `Gold` 子类，允许银行强制偿付形成负现金；普通兑换与军队消费仍检查资金。完整经济层及 117 项验收见 [经济层说明](../../economic_system/README.md)。
