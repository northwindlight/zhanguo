# 政权模块

`native:government` 2.3.0 按 [正式发行版模板](../README.md) 装配。`Government` 拥有周期、国策、每周期小结和已结束行动者；`Plan / Summary / GovernmentSnapshot` 是不可变记录。

工具 `plan / end_turn / government`，面板 `government`。默认国策有效 10 周期，小结至少 4 字，数值由 `mod.toml` 声明。结束行动只关闭本国写工具，世界由宿主统一结算；发行版的 `ActionGate` 调用 `can_act` 裁决其他领域动作。

接口 `native:government` 提供周期、查询和动作；只依赖国家只读门面。`cycle.end` 在经济报表后推进周期并清空结束标记；不代持经济、外交或客户端记忆。保存和恢复校验国策期限、小结重复、结束标记及国家引用。验收见 `tests.test_world_domains`。

2.3.0 正式支持宿主绑定的真人身份（experimental 对应 3.1.0-parliament）。真人无需国策或小结即可结束行动，不生成占位文本，也不参加议会审核。身份写入模块存档，并与整局配置核对；玩家参数不能自行选择豁免。外交待办仍由原有结束流程处理，AI 的国策、小结和审核要求保持。见[单机真人接口](../../../docs/真人玩家接口.md)。
