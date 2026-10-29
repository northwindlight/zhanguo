# Agent 开发接手入口

适用于本仓库。先读本文件及适用的上层指令，再核对当前用户要求。用户的新指令优先；计划中的功能不代表已实现，也不代表用户本轮要求全部开工。

## 先做这几件事

1. 查看 `git status -sb`、`git log -5 --oneline`，确认所在分支、工作区和实际代码；保留他人修改。
2. 阅读 [README](README.md) 与[游戏说明书](mods/native/docs/游戏说明书.md)，先理解玩法和已接受的规则，再看实现。
3. 接手 GUI 时先读[两版实施安排](docs/plans/GUI两版实施-20261028.md)，再读[下一轮计划](docs/plans/GUI下一轮试玩改进-20261027.md)、[规则复用审计](docs/reviews/GUI规则复用审计-20261028.md)和[鼠标军令设计](docs/design/军队选择与鼠标军令.md)；涉及并发／未来联机，再读[动态回合与联机调度](docs/design/动态回合与联机调度.md)。其他任务从[文档索引](docs/README.md)找对应领域。
4. 区分当前行为、目标设计和历史复核。旧报告只代表当时版本；通过源码与当前测试核实，不能拿旧待办覆盖后续定案。

## 架构与查找入口

| 层次 | 主要入口 | 职责 |
| --- | --- | --- |
| 通用引擎 | `engine/runtime.py`、`engine/player.py`、`engine/session.py`、`engine/config.py` | 模块装配、绑定玩家 API、Session、配置与状态契约 |
| 宿主与发行版 | `host.py`、`mods.toml`、`mods.experimental.toml`、各发行版 `mod.toml`／`session.py` | 选择发行版、场景、客户端、服务与存读档 |
| 战国规则 | `mods/native/<领域>/api.py`、领域对象及内容声明 | 建设、军令、市场、银行、外交、视野、结算等正式语义 |
| 真人应用接口 | `mods/native/human.py`、[接口说明](docs/真人玩家接口.md) | 绑定真人身份、操作目录、查询和正式提交 |
| 桌面宿主与投影 | `ui/desktop.py`、`mods/ui/desktop_player.py`、`desktop_human.py`、`desktop_catalog.py` | 输入协议、玩家快照、演出数据、卡片呈现 |
| Godot GUI | `mods/ui/desktop/Scripts/`，重点 `Desktop.Human*.cs`、`WorldMap*.cs`、`MainMenu*.cs` | 选择、输入、面板、地图、动画与设置 |
| 启动与打包 | `desktop.py`、`scripts/package_windows.py`、`scripts/verify_windows_package.ps1`、`.github/workflows/` | 源码启动、Windows 便携包及 CI |

通用引擎不应添加 native 专属规则。用户说“改引擎”时，先找该规则所属领域；例如瞭望塔限建属于 `mods/native/buildings/types.py` 及建筑校验链。

## 核心原则：先查引擎 API，再做增删

- 优先核对领域的 `api.py`、`mod.toml` 的 API 声明、规划／报价／目录及正式回执。现有 API 语义足够就复用，GUI负责呈现与交互。
- 可达性、地形代价、行动资格、施工额度、材料价格和攻防规则由正式规则层决定。不要在 GUI 另写一套搜索、门槛或公式，再把合法操作挡在引擎之前。
- 现有建设／征兵走 `planning`，市场整单价走 `trade_quote`，贷款走 `quote(borrow)`，报表走 `report`。先看现有结果能否透传，再确认真实缺口。
- `reachable` 单支与批量返回结构不同；几何可达不等于本期还有行动资格。移动、攻击、原地攻击、撤退和迷雾尝试各有语义，按实际契约处理。
- v0.6.3已修复审计中两种误拦截及本国受攻击地通行差异，候选与执行共用`army/navigation.py`。历史审计保留为证据；v0.7的防御HUD和自动交易预验已共用领域纯规则，见[收尾记录](docs/reviews/银行邮箱与规则收尾-20261029.md)。审计清单内六处已处理，其他呈现派生仍按职责判断。
- 规则侧是单一事实来源，但玩家权限仍须保留。真人查询、地图、预览和演出使用绑定玩家信息；不要用观察者全图补足迷雾或外国私有事实。
- 发行版根据正式清单与配置装配，GUI按实际能力显示。native与experimental共享基础模块是正常复用；不能靠隐藏按钮假装停用了实验服务。

## 当前边界与待办状态

- 当前支持单机一位真人与 AI 混合，保留原18阶段结算顺序。未经当前任务要求，不扩多人／联网，不改成按国家整段结算。
- 未来联机必须支持动态／并行回合：和平并行行动，战争有序行动，回合末仍统一结算。优先参考stock分支已有并发规划；联机设计已记录，当前尚未实现，不与本轮单人GUI范围混淆。
- v0.6.2界面输入队列仍在，提交前不扣款。v0.7.0开发中已接正式建筑／征兵队列：入队预付、容量预占、按行动期推进、锁价与存档；待开工全退、在建50%、完工0%，部分征兵只退未生成部分。UI默认队列模式，可切回直接操作；全国生产列表管理退款。
- 队列／军令改变依赖契约，不能用新包直接读旧档。`scripts/upgrade_native_save.py`对已知v0.6.3、生产队列开发提交230e97d及持续军令提交50244cb的native／experimental清单提供校验后的新文件升级，复制相邻audit历史；未知清单和混用发行版仍拒绝，桌面存档列表也提供升级为新副本，真实局不自动迁移。
- v0.7.0开发已接跨驻地勾选、固定已选清单入口、按军队编号的光环与部分编队分组；右栏显示持续军令节点数／回血标记，未选中也保留，悬停读正式下一目标与等待原因。地图右键移动、Ctrl右键攻击、Shift追加移动/攻击节点，旧按钮保留。规则侧`army/march_orders.py`持有路线与回血锁定：低于绝对30生命暂停，**正常补给恢复满血后**续行，各军队独立判断。原18阶段不变；自动军令在轮到该国时由`NativeSession.before_actor`推进，并在每支实际动作后记录快照。规划只用绑定玩家观察与共享导航，不用观察者全图；未知路径仅示意。
- 灭国残影已在第二版开发中修复：A持有B旧地图，A灭B后旧地图仍铺B国色。投影复用公开国家存续API，将已灭国的历史归属标为失效并去掉国色，保留历史字段和日期，显示“现归属待确认”；不读取视野外新领主，不切全图。该修复不在已发布v0.6.3包内。
- v0.6.3已实现瞭望塔同格一座，含在建；旧档原有多塔保留，新增严格限建，视野不叠加。v0.7开发已接显式发行版与议会／脚本／战斗研究三个真实装配开关，未指定时全开；规则AI启用议会需选择审核模型，真人免审。新局摘要、Esc与存档列表显示实际规则，续档不采用当前菜单的新局选择。该组源码a72b9b2已通过1063项及Windows八组验收，见[记录](docs/reviews/发行版开关与旧档副本-20261028.md)；银行与审计剩余规则已在最后一组接入，最终发布状态见下文。
- 存档有严格依赖检查，改模块／装配／状态前先评估兼容性。不要擅自迁移真实存档或删除旧建筑；旧档重复塔仅在恢复既有实例时豁免限建，不能在新订单中继续新增。
- 用户若只要求记录计划，本轮仅更新文档与交接；需要实施时，以当轮要求和最新计划确定范围。
- v0.7地图交互组已接简易地形图／图例、普通地表辨识、正式容量空位组合筛选、紧凑地块状态图标、地形费用分项、右键返回偏好与玩家API指南。空间读取`building_sites.capacities`／`planning.space`，费用读取`cost_breakdown`；均为派生只读字段，存档依赖不变。源码7a4a996通过1069项及Windows八组，见[验收](docs/reviews/地形地图与操作指南-20261028.md)。演出采集用`HumanAPI.state(controls=False)`保留相同玩家事实与投影，跳过完整管理面板，正常状态查询不变。银行／报表邮箱和防御／自动交易规则复用已收尾，见下一条。

- v0.7.0单机计划已完成并[预发布](https://github.com/northwindlight/zhanguo/releases/tag/v0.7.0)，源码c940304的CI40818268137通过1075项及Windows九组；发布附件与标签保持该提交，后续不覆盖。独立银行复用`quote(borrow)`的`credit / can_borrow`；邮箱只读本国已发布与正式已购报表，已读记录在存档旁`.desktop-mail.json`，不改世界。军团防御读取`squad_defenses`；自动交易读`auto_trade_options`，实际保存仍走原API。见[验收](docs/reviews/银行邮箱与规则收尾-20261029.md)。联机／动态回合仍未实现。

## 验证与运行

按改动选择相关检查；文档改动检查链接与差异即可。行为变更要用独立离线合成局验证引擎结果和 GUI 表现是否一致，不能只验证“运行成功”。本仓库现有 CI 命令是主要依据：

```bash
# 常见真人接口／GUI投影／资源明细定向检查
python3 -m unittest tests.test_human tests.test_human_planning tests.test_desktop_player tests.test_resource_breakdown -q
# 规则变更及交付前全量离线检查
python3 -m unittest discover -s tests -q
# 修改API后先生成参考，再检查；纯核对用 --check
python3 scripts/api_doc.py --check
python3 docs/site/build.py
# C#与不依赖窗口的布局检查，需要.NET 8 SDK
dotnet build mods/ui/desktop/Zhanguo.Desktop.csproj
dotnet run --project tests/desktop-layout/Checks.csproj -- mods/ui/desktop/Samples/terrain-v2.json
```

源码桌面入口是 `python3 desktop.py`，仅构建用 `--build-only`；需要 Godot .NET 版和.NET SDK，详见[桌面说明](mods/ui/desktop/README.md)。Windows 使用 `python`。依赖装在项目虚拟环境，不修改系统 Python。

GUI变更还要检查实际窗口和相关尺寸、输入与状态切换。Linux软件渲染、Windows CI窗口、实体显卡或Vulkan测试各自说明，不能相互冒充。API参考由脚本生成，不手工改生成页。

## 本机、提交和发布

- 中文沟通、中文提交信息；任务完成并检查后默认 commit＋push，保留他人改动。独立工作树可用于隔离实现。
- 用专门的离线测试配置和目录。维护者本机可能运行真实 `zg`／`stock` 对局；本任务没有明确要求时，不读写其私人配置／存档、不启停服务、不调用付费模型。
- 测试与发布产物放项目忽略目录 `archive/previews/`、`archive/releases/<版本>/`；不把交付 ZIP 放到主目录，不将真实档、API配置、缓存打入公开包。
- 用户要求完成版本由 CI 验收成功后直接公开 **Pre-release**，附 Windows ZIP、源码 ZIP 与 `SHA256SUMS`，试玩确认后再转正式。自动发布已接入`ci.yml`：`VERSION`对应待发标签时，全部检查及Windows便携包验收成功后发预发布。脚本核对包内提交／版本和上传摘要，已公开附件不覆盖。
- 构建范围由`scripts/ci_release_scope.py`核对，包含所有随包规则源码。已发布版本后的日常代码改动仅构建工件；完成下一版时递增`VERSION`并补`docs/releases/v<版本>.md`。失败可重跑同提交，草稿续传；不要修改已发布标签。
- 仅计划／说明文档更新不另发游戏版本。发布新实现使用新版本，不静默改已发布标签或附件；转正式须遵从用户试玩确认。
- 发布步骤和当前下载入口见[Windows便携包](docs/Windows便携包.md)，CI定义见 [ci.yml](.github/workflows/ci.yml) 与 [windows-portable.yml](.github/workflows/windows-portable.yml)。

任务结束时更新相关计划／验收记录，清楚说明实际改动、验证结果和未完成项。此文件保持接手入口规模；详细设计与历史放在链接文档中。
