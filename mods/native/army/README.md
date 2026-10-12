# 军队模块：士兵对象化样板

本轮只推进部队对象，不把地块、建筑、国家或条约一次全部改成对象。

- `objects.py` 的 `Unit` 是 dataclass，定义兵种、生命值、位置、归属、编号、行动回合、交战与撤退状态；校验未知字段、非法类型、位置、编号和撤退状态。基础输出保持现有规则：与残血无关。
- `Units` 是集合门面，提供 `of(nation)`、`at(x,y)`、`by_id(aid,nation)`、创建、编号、脱战和显式持久化。派生军队名单、野人位置索引与失效标记留在集合内部，不占世界状态、不进存档。
- 军队模块的 `Owns` 从 9 个裸字段合并为 `units` 一个对象。编号计数器与守卫出生记录也由它拥有。旧的 `world.armies` 等属性是军队模块内部的过渡转口，不再登记成独立 state。
- 对象的字段与集合只由军队域修改。跨域修改用 `runtime.object_call(world, 'army.units', 'spawn', ...)` 等声明过的行为；调用者必须同时声明需要 `army.units` 和允许调用的方法。直接修改字段、列表或编号表会拒绝。
- `Unit.to_save / from_save`、`Units.to_save / from_save` 显式序列化，保留物化顺序与已有存档框架的字段布局。存档加载核对 Mod 依赖并校验部队，缓存重新构建。
- 字典式字段访问是本域旧战斗代码的过渡适配。权威数据是 dataclass 字段，不能追加任意键；部队登记后不能直接改编号、归属或兵种。

验收：`python -m unittest tests.test_unit_objects tests.test_troops tests.test_combat tests.test_determinism`。测试只用临时合成存档，包含写权限、非法字段、缓存更新、阵亡与回血、编号不复用及 `save → load → save` 字节相等。

后续仍需逐域推进对象接口：军队模块对其他域的 `Needs / Requests` 暂时保留现有声明，不能把本轮样板称为整个世界对象化完成。市场、央行、地图生成的进一步细分及客户端统一 API 留到后续任务。
