# Toolcall 与 0.1 的逐项对照

核对日期：2026-10-14。参照 `v0.1:mp_ai.py` 的实际 `TOOL_SCHEMAS`、派发与面板实现；当前参照运行时生成的玩家 schema、领域处理器、UI 呈现和客户端派发。仅使用合成世界，没有联网或加载真实存档。

原版核心工具 36 个，银行另有 loan、buy_report。当前普通国家 native 有 61 个公开工具（58 个模块工具、3 个玩家 API 查询），experimental 有 67 个；LLM 客户端另添加自己的 `memory_search`，不属于世界领域工具。

## 已补齐的差异

- 四篇策略指南由 `native:handbooks` 提供公开目录和规则正文；experimental 追加自己的议会与市场参考。君主、脚本和议长均读取标准 `player_rules`。默认 LLM 提示开局前20期提供基础指南，常备经济手册，战时增加战争手册，战略手册按需查询。历史长文保留为资料，不把过期数值当当前规则。
- 工具补充具体用途、费用、时序、对象和权限说明；规则支持兵营、战斗、撤退、宣战及指南标题等主题。无参或未知主题返回目录，`all` 返回正文。
- `report {}` 只返回最新已发布报表，`period` 指定结束周期，`all=true` 返回跨期；不查询未完成收益。
- LLM `memory_search(query,limit)` 检索本客户端历史发言、行动和回执，排除思考正文。历史独立于上下文压缩并随客户端保存；不读取其他国家记忆。
- `countries` 整合外交对象、关系、接壤、来信与求和代表；`threats` 列全视野他国军队、排除野人；`battle` 整合当前交战各方构成、逐支数量、基础输出、防御与盲战。盲战仅提供本国事实，未恢复按格预测查询。
- `disband` 支持 `aid` 或 `ids`，批量先校验整批；build/recruit 明确 site 与成对 x/y 二选一。求和种类有 enum，可附 `note` 正文。
- `land` 支持地形/资源筛选和 offset/limit 分页；`intel` 按 kind 筛选。仍只有 `tile` 查询接受坐标。
- 中文看海专门呈现指南、对象、威胁、交战、报表、外交和记忆结果；行情展示买卖单价，军队逐支展示数量。终端长回执截断，日志保留完整正文和原始数据。

不保留旧工具名字或参数兼容层；按下表提供等价能力，发行版与 UI 继续分离。验收包括离线合成世界、模拟模型和临时存档；未运行真实模型或真实存档。

## 核心工具逐项映射

以下按功能映射，不承诺旧调用名、参数或坐标兼容。当前动作坐标从 0 开始，内容优先短键值，军队用整数编号。

| 0.1 | 当前对应 | 核对结果 |
| --- | --- | --- |
| query | 分域只读查询、player_state、默认 UI | 见后面的面板表；当前仅 tile 查询接收坐标 |
| report | report | 默认最新；period 指定结束周期；all 查全部 |
| countries | countries | 关系、接壤、来信、实体与求和代表 |
| rules | player_rules | 目录、主题别名和四篇策略指南均可查询 |
| econ(building) | building_economics(kind) | 无参一次全建筑；无地点参数 |
| build(tile,building) | build(x,y,kind) | 直接校验、扣款、回执；成本与门槛另有全国目录 |
| recruit(tile,n,kind) | recruit(x,y,count,kind) | 征兵成本、额度校验；新兵本期不能行动 |
| move(army_id,x,y) | move(aid,x,y) | 纯移动不占地；说明行动额度 |
| attack(army_ids,x,y) | attack(ids,x,y) | 进入交战或直接占领；实际结果看回执 |
| retreat(army_id,x,y) | retreat(aid,x,y) | 提交后仍参加本期战斗，结算后撤离 |
| disband(army_ids) | disband(aid 或 ids) | 支持整批预校验；无返还，交战中拒绝 |
| buy(good,qty) | buy(resource,quantity) | 实际成交回执；experimental 有库存约束 |
| sell(good,qty) | sell(resource,quantity) | 实际成交回执 |
| send_letter(to,content) | send_letter(target,text) | 说明按字数收费、提交扣费和延迟送达 |
| gift(to,good,qty) | gift(target,resource,quantity) | 预留资源、结算派送，不能把提交当送达 |
| share_map(to) | share_map(target) | 延迟送达带日期的地图情报；盟友免基础费 |
| spy(to) | spy(target) | 说明收费、延迟、采样日期与信息边界 |
| plan(content) | plan(text) | 长期战略与 end_turn 小结分工已恢复提示 |
| bloc_found(name,tos) | bloc_found(bloc,name,tos) | 说明自定义命名空间 ID、名称及全体接受条件 |
| bloc_join(name) | bloc_join(bloc) | 使用现有联盟 ID；说明投票流程 |
| bloc_leave | bloc_leave | 明确战时限制 |
| bloc_rename(name) | bloc_rename(name) | 明确盟主权限 |
| bloc_transfer(to) | bloc_transfer(target) | 盟主向现有成员转交 |
| bloc_dissolve | bloc_dissolve | 明确盟主权限与战时限制 |
| vote(vote_id,choice) | vote(vote,accept) | 说明票据编号、布尔选择与计票时序 |
| propose(to,kind) | propose_defense(target) | 原版仅共同防御，当前独立动作 |
| respond_proposal(id,accept) | accept_proposal / reject_proposal | 接受与拒绝拆开；不是缺功能 |
| break_defense(to) | break_defense(target) | 说明主体、表决、战时限制与生效时序 |
| guarantee(to) | guarantee(target) | 说明主体、表决、战时限制与生效时序 |
| cancel_guarantee(to) | cancel_guarantee(target) | 说明主体、表决、战时限制与生效时序 |
| declare_war(to) | declare_war(target) | 说明待结算、表决、休战与直接防守条约传导 |
| offer_peace(to,kind,gold,truce,note) | offer_peace(target,kind,gold,truce,note) | 类型 enum、附言与战争主导实体代表说明 |
| accept_peace(offer_id) | accept_peace(offer) | 说明实体代表和联盟投票流程 |
| reject_peace(offer_id) | reject_peace(offer) | 说明编号、权限与战争继续 |
| memory_search(query,limit) | LLM 客户端 memory_search | 本国行动历史，排除思考，随档保存 |
| end_turn(summary) | end_turn(summary) | 有效国策前置；一两句结果提示已恢复 |
| loan（银行） | borrow | 额度与期限由银行提供 |
| buy_report（银行） | buy_report | 买目标最新已发布报表 |

## 原 query 面板逐项映射

| 0.1 面板 | 当前对应 | 差异 |
| --- | --- | --- |
| all | player_state | 发行版组装全部默认事实，UI 增加决策提示 |
| res | econ.stocks、bank | 库存、现金、负债已提供 |
| plan | government | 国策、有效期、结束状态和小结 |
| land | land、building_sites | 全本国站点与建筑；land 支持筛选/分页 |
| tile | tile(x,y) | 唯一按坐标查询；迷雾拒查、外国信息过滤 |
| grid | view + UI 文字/图形地图 | 数据和渲染拆开，没有旧 grid 工具名 |
| army | army + unit_catalog | 逐支数量、位置、撤退与交战状态 |
| market | market | 展示本期报价、买卖单价与缓冲库存；报价与先后手无关，无独立查价工具 |
| econ | econ + building_economics + report | 区分资产、预期收益和已发布报表；支持期选择 |
| intel | intel 中 kind=map | 有日期的共享地图情报 |
| spy | intel(kind=spy) | 延迟间谍结果，保留采样日期 |
| mail | intel(kind=letter)、player_events | 按信件类别筛选，观察者消息在事件流 |
| countries | countries | 全国外交对象表 |
| diplomacy | diplomacy | 联盟、条约、战争、休战、提议、投票、待办及结果 |
| news | news、player_events(cursor) | 可按事件游标查询新增事件 |
| threats | threats | 全视野他国军队与关系，排除野人 |
| battle | battle | 当前交战构成/输出/防御/盲战，无按格预测 |
