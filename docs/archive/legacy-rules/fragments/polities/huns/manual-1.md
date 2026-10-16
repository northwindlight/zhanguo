## 十、政体：匈奴

**第六十七条　政体限制。** 政体修正只作用于其指定成本、初始配置和权限。未指定事项适用通则；政体限制不得通过别名或非正式入口绕过。

<!-- AUTO:polity BEGIN -->

| 匈奴政体 | 值 |
|---|---|
| 建造金价 | ×1.3（与地形惩罚、工程院折扣乘算） |
| 骑兵征召特价 | 8 粮食 + 8 装备/支（寻常国家 12 粮食 + 12 装备） |
| 缺省开局 | 黄金 1000、补给 200、骑兵 6 支（可用配置里的 `start_*` 逐项覆盖） |

**外交限制（硬性）**：匈奴不参与结盟那套关系网。被禁的工具：`bloc_dissolve`、`bloc_found`、`bloc_join`、`bloc_leave`、`bloc_rename`、`bloc_transfer`、`break_defense`、`cancel_guarantee`、`gift`、`guarantee`、`propose`、`respond_proposal`、`share_map`、`vote`。可用的只剩：`send_letter`（写信勒索）、`spy`（刺探）、`declare_war`（宣战）、`offer_peace` / `accept_peace` / `reject_peace`（求和与议和）。
<!-- AUTO:polity END -->
