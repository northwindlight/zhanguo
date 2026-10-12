# 军队模块：部队对象与具体资源依赖

当前模块与部队集合门面 ID：`native:army`；具体兵种使用 `native:infantry / native:cavalry / native:militia`。展示文字来自语言文件，兵种配方中的资源使用 `native:` 内容 ID。

- `Unit` 有明确字段、校验、行动、伤害、回血与脱战行为。多字段修改先校验后提交；编号、归属与兵种登记后不可直接改写。
- `Units` 拥有部队、编号计数器和守卫出生记录，缓存留在集合内部。跨域写部队使用声明过的 `native:army` 对象接口。
- `content.py` 将本域的数值表发布到 `runtime.content`，并解释所有域登记的 `category = "unit"` 定义。`Units` 持有只读兵种定义，第四种兵无需修改引擎或原三兵种表；创建、征兵、输出、回血、存档恢复和动态征兵 schema 均读取登记内容。
- army 的资源读取/消费/计价走具体的 `native:gold / native:food / native:equipment / native:supply` 门面。清单声明各资源及允许的方法，已移除资源相关 `method.res / method.add_res / method._mval` 依赖；黄金面值计价由资源子类重写。
- `Unit / Units` 显式持久化，只保存状态，不保存缓存或复制兵种定义。恢复时提供当前内容定义，未知兵种拒绝加载。

本轮用户明确：未改造区域不要求接线，整局不要求运行。军队仍保留建筑、地形、外交、账户外账本等旧域依赖；旧消费者的 `army.units` 转口未迁移。它们不能作为新对象接口的正确性证据。

当前验收：`.venv/bin/python -m unittest tests.test_runtime_content_hooks tests.test_resource_objects tests.test_army_resources`。详见 [资源对象与注册表](../resources/README.md)。[阶段 A 改造记录](../../../docs/阶段A改造记录.md) 保留为本轮前的历史基线；其整局测试结果不代表当前未接线分支仍能运行。
