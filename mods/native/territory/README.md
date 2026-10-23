# 领土模块

`native:territory` 2.5.0 以 `Territory / Tile / Terrain / TileFact` 拥有地图尺寸、种子、已物化地块、地形、核心和坐标索引。按 [正式发行版模板](../README.md) 装配，工具和面板为只读 `land`。

公共接口包括 `configure / cell / create / create_claim / transfer / claim / owned / snapshot / snapshot_cores` 等。依赖地图生成、国家只读门面和建筑站点接口。`create` 先校验身份、坐标、归属、地形再注册站点；当前归属和资源容量由建筑域保存，不另存一份。军事占领无主地块分别通过 `create_claim`（新物化地块）和 `claim`（既有无主地块）将核心设为新领主；普通转移保留核心，和平结算时由 `snapshot_cores` 按实际控制重算。查询未物化地块不创建站点；调动军队也不转移土地。

静态地形通过内容注册表登记，防御、建造加价在地形对象内；坐标使用 0-based。停战可更新核心快照，战时盟友收复核心由军队调用归还。持久化校验站点、坐标、地形加价、核心与存续归属；验收见 `tests.test_world_domains`。

地形另声明 `passable / claimable`；高山、海洋均为 false，骑兵移动代价为 null。
领土创建、占领和恢复拒绝这类地形上的站点，查询仍为只读。原山地保持可通行。
对应生成、绕障和存取检查见 `tests.test_natural_boundaries`。
