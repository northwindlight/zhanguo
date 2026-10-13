# 地图生成模块

`native:map-generation` 1.0.0 提供 `map:generation.cell(seed, size, x, y, terrain=None)`，返回不可变 `MapCell`：位置、命名空间地形、资源容量。按 [正式发行版模板](../README.md) 声明接口与算法数值；纯算法无玩家工具、结算阶段和持久状态。

`algorithm.py` 从原地图算法移入，参数由本模块维护；保持相同种子、尺寸和地形的生成结果。中文算法内部名称在公开边界转换为 `native:plain / forest / hill / mountain / desert` 和资源 ID。每世界的派生缓存不保存，不改变领土物化状态。

只换发行版成员即可替换地图机制，领土模块不导入具体算法。固定地图提供者替换与确定性查询由 `tests.test_world_domains` 验收；宿主在物化前配置领土尺寸和种子。
