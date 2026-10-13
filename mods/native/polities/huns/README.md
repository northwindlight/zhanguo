# 匈奴政体模块

`native:huns` 2.1.0 是国家作用域模块，按 [正式发行版模板](../../README.md) 提供开局配置、禁用项、参数覆盖、提示、规则和 `polity` 面板，不自建国家或调度客户端。

宿主先创建国家和所属领土地块，再调用 `runtime.activate_actor(world, actor, 'native:huns', site=站点ID)`；重复激活不重复发放。开局库存按清单重置为 1000 金、200 补给、6 骑兵；建造成本 130%，骑兵配方 8 食物、8 装备。外交、建盟、礼物、地图限制由 `Policy.Disable` 声明，经统一动作及工具入口执行。

通过资源、领土和军队公共接口初始化，不修改外域私有表。政策参数查询使用 `runtime.parameter`；作用域由引擎 `$engine.polity` 保存，亡国后生命周期模块撤销。卸载本模块不影响普通国家装配。预定出场仍由宿主提供声明和种子、负责场景安置；客户端出场由 GameHost 统一调度。验收见 `tests.test_world_domains`。

激活入口支持可选 start 配置（Gold / Supply / Cavalry），经校验后覆盖政体默认开局；宿主从国家配置的 start_gold / start_supply / start_cavalry 映射，玩家无法调用此初始化入口。
