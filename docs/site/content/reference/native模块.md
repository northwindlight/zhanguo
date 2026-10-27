---
title: native 模块清单
section: API 参考
summary: native 每个模块的能力、状态与规则正文。
---

# native 模块清单

native 是最完整的发行版：它覆盖了所有玩法领域，是写新模块时的参照。


## `native:economy-queries`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.economy_queries.mod:QueriesMod` |
| 状态键 | — |
| 提供能力 | economy:queries |
| 需要能力 | native:resource-inspection、native:buildings、native:market、native:economy、economy:valuation |
| 依赖模块 | native:resources、native:buildings、native:market、native:economy |


## `native:economy-valuation`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.economy_valuation.mod:ValuationMod` |
| 状态键 | — |
| 提供能力 | economy:valuation |
| 需要能力 | native:resource-inspection、native:market |
| 依赖模块 | native:resources、native:market |


## `native:huns`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.polities.huns.mod:HunsMod` |
| 状态键 | — |
| 提供能力 | — |
| 需要能力 | native:resource-inspection、native:resource-exchange、native:territory、native:army |
| 依赖模块 | native:resources、native:territory、native:army |


规则正文：


**匈奴政体**

金矿优先，以战养战；分兵抢无主地，集中夺市政厅，时间不等人。骑兵征召8粮8装、建造130%，补给耗尽前主动缩军。可宣战求和、写信与间谍，不参加联盟、保障、共同防御、送礼或共享地图；只向允许谈判的对象谈条件。


## `native:manuals`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.manuals.mod:ManualsMod` |
| 状态键 | — |
| 提供能力 | native:manuals |
| 需要能力 | — |
| 依赖模块 | — |


## `native:map-generation`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.map_generation.mod:MapGenerationMod` |
| 状态键 | — |
| 提供能力 | map:generation |
| 需要能力 | — |
| 依赖模块 | — |


## `native:market-pricing`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.market_pricing.mod:InventoryPricingMod` |
| 状态键 | — |
| 提供能力 | market:pricing |
| 需要能力 | — |
| 依赖模块 | — |


## `native:query`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.query.mod:QueryMod` |
| 状态键 | — |
| 提供能力 | native:query |
| 需要能力 | — |
| 依赖模块 | — |


## `native:resources`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.resources.mod:ResourcesMod` |
| 状态键 | resources |
| 提供能力 | native:resources、native:resource-exchange、native:resource-inspection、native:bank-cash、native:gold、native:food、native:wood、native:ore、native:oil、native:equipment、native:supply |
| 需要能力 | — |
| 依赖模块 | — |


## `native:vision`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.vision.mod:VisionMod` |
| 状态键 | — |
| 提供能力 | native:vision |
| 需要能力 | native:territory、native:diplomacy、native:buildings |
| 依赖模块 | native:territory、native:diplomacy、native:buildings |


规则正文：


**视野**

本国和同盟土地周围一格可见，哨塔扩展视野。他国可见地块只显示已落成的市政厅和城堡，不公开库存、资源容量或其他建筑。


## `ui:standard`

| 项 | 值 |
|---|---|
| 入口 | `mods.ui.mod:UIMod` |
| 状态键 | — |
| 提供能力 | ui:presentation |
| 需要能力 | — |
| 依赖模块 | — |


## `native:buildings`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.buildings.mod:BuildingsMod` |
| 状态键 | buildings |
| 提供能力 | native:buildings |
| 需要能力 | native:resource-exchange |
| 依赖模块 | native:resources |


规则正文：


**建筑与能源**

building_catalog 一次返回全部建筑的基础造价（各等级）、投入产出、耗电发电、效果和建造门槛。
building_economics 不带参数时一次返回所有建筑的完整收益报告，不接受地块参数（需加载经济查询模块）。
build 才会扣除材料并动工；每地块每回合只能动工一次，施工占建筑位，结算后落成，从下一周期开始生产。
demolish(id) 按 building_sites 提供的本国实例编号拆除。未完工返还原实际支付材料的50%（可含半单位），已完工零返还；撤销不恢复当期施工额度。城堡从最高一级开始拆；最后市政厅消失可能导致亡国。
采集建筑受本地资源容量限制；兵营等建筑须满足已有建筑门槛。地形只提高金币造价，已落成工程院减免本格后续建筑金币造价25%，木材不变。
电力不储存、不交易，燃料不足的发电厂无法发电，电网不足会使耗电建筑停工；仅有燃料不代表已有电厂。


## `native:market`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.market.mod:MarketMod` |
| 状态键 | market |
| 提供能力 | native:market |
| 需要能力 | native:resource-exchange、market:pricing |
| 依赖模块 | native:resources |


规则正文：


**库存市场**

市场是有限商品缓冲，名义深度：native:food=1920, native:wood=1920, native:ore=1280, native:oil=960, native:equipment=640, native:supply=960；开局库存：native:food=1920, native:wood=1920, native:ore=1280, native:oil=960, native:equipment=640, native:supply=960。市场有两个会变的量：库存（买卖加减）和基准库存（每期按本期净流量偏移 0.25 倍，从名义深度起步）。每期开盘按这两个量给一个公开报价：p=基准价×exp(弹性×(基准库存−库存)/基准库存)，弹性=1.0。同一期内所有国家拿到同一报价，报价与先后手无关；单笔订单越大均价越高（卖出越低），因为大单扫过的缓冲更深。大额买入会把基准库存推高（市场要维持更大的书），大额卖出会把它压低，所以价格随流量逐步偏移，不会只靠挤压残余库存而失控。买入扣市场库存，卖出补市场库存；库存不足整单拒绝，不部分成交。外部不回补：开局库存是市场唯一一次发货，之后库存只由买卖改变，积压也不会被消化；生产、消费、转账不等于交付市场，不直接改变市场库存。买卖分别在报价加减 5% 价差，金币零头逐账户累积。绝对数值边界为 1e-06 到 1000000000.0。金币结算沿用外部市场流动性，不设市场金币储备；商品交付严格受库存限制。


**自动交易**

通过 economy(action=auto_trade,resource=...,...) 或 economy:queries.auto_trade 设置本国某资源的常设交易条件。buy_below 为补货目标，sell_above 为出售保留量，null关闭该方向，买入目标不得高于出售保留量。每国成功结束行动后、统一世界结算之前执行一次，先卖盈余再买短缺；不增加或重排原有结算阶段。buy_limit/sell_limit 限制每期数量，默认50；reserve_gold 保留金币；max_buy_price/min_sell_price 限制含价差和滑点的整单均价，null不限。商品、资金或均价不满足时只成交满足条件的整数数量，不借贷、不凭空补货。设置本身不立即成交；enabled=false暂停，remove_auto_trade删除，已有成交不撤销。query(panel=market)只展示本国设置和最近结果，设置及执行标记随存档保存。


## `native:nations`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.nations.mod:NationsMod` |
| 状态键 | nations |
| 提供能力 | native:nations、native:nation-inspection |
| 需要能力 | native:resources、native:buildings |
| 依赖模块 | native:resources、native:buildings |


## `native:economy`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.economy_reports.mod:EconomyMod` |
| 状态键 | economy |
| 提供能力 | native:economy |
| 需要能力 | native:resource-inspection、native:buildings、economy:valuation |
| 依赖模块 | native:resources、native:buildings、native:market |


规则正文：


**经济报表**

每10期发布本国经济报表，列明覆盖期、GDP、建设、征兵、军需折价、进出口、资产、负债及增长率。GDP是产出扣除生产投入后的增加值，不等于现金收入；军需折价是已消耗材料的估值，不额外扣同额金币。query(panel=report)免费查本国最新已发布报表，period选择报表结束期，all=true查看历期；尚未发布时没有正式报表。


## `native:government`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.government.mod:GovernmentMod` |
| 状态键 | government |
| 提供能力 | native:government |
| 需要能力 | native:nation-inspection |
| 依赖模块 | native:nations |


规则正文：


**国策与回合**

end_turn 结束本国行动；世界结算仍由宿主统一调用。AI 国策写跨回合的经济、军事、情报与外交战略，到期或战略变化时更新；AI 回合小结只用一两句记录实际执行结果，详细行动已有回执，不重复长期国策。小结须满足清单声明长度。正式绑定的真人席位免国策、小结和议会流程，直接结束本国行动；不得通过请求参数自行声明真人身份。


## `native:territory`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.territory.mod:TerritoryMod` |
| 状态键 | territory |
| 提供能力 | native:territory |
| 需要能力 | map:generation、native:nation-inspection、native:buildings |
| 依赖模块 | native:map-generation、native:nations、native:buildings |


规则正文：


**领土**

地块保存地形和核心，当前归属由建筑站点维护。高山与海洋不可通行、占领、建造，不产资源也没有野人；山地仍可通行。地图外缘由连续高山和海洋封闭。查询未物化地块不改变世界；部队调动不改变土地归属。所有坐标统一 0-based。


## `native:army`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.army.mod:ArmyMod` |
| 状态键 | units、military |
| 提供能力 | native:army |
| 需要能力 | native:nation-inspection、native:government、native:territory、native:vision、native:diplomacy、native:buildings、native:resource-exchange、native:gold、native:food、native:equipment、native:supply |
| 依赖模块 | native:resources、native:government、native:nations、native:territory、native:vision、native:diplomacy、native:buildings |


规则正文：


**军队**

行动：所有坐标统一 0-based。移动、异地进攻、撤退共用每支军队每期一次行动额度；新兵当期不能行动，原地进攻不消耗位移额度。move 只调动、不占地；attack 才会占领或进入交战。reachable 给出可见合法路线和代价，路程不能超过兵种移动力。高山和海洋不可通行、进攻或占领，也不能从障碍侧面的斜角挤过；原有山地仍可通行。unit_catalog 的移动代价 null 表示不可通行。本国和盟国领土可通行；攻击他国必须已处于战争，外交 submitted 不代表战争已生效。

征兵：正规军只能在自有已落成兵营征召，停电时不能征召；民兵只能在自有已落成军屯征召。同格兵营的每期额度相加；民兵还受全国军屯编制上限限制。编制上限和每期征召额度是两个不同数值，查 building_catalog 的效果。当前目录中，每座兵营每期可征1支正规军；每座军屯提供2支全国民兵编制、该格每期可征1支民兵。费用、满编HP、攻击、移动力和每支补给量查 unit_catalog；材料或额度不足时按可征数量缩减，返回实际新兵。在建兵营、军屯本期不能征兵；建兵营的已有建筑门槛统计该格现有建筑数量（含在建），不是地块可容纳的建筑位上限；每格每期仍只能动工一次。

补给：统一结算时先补给与回血、再战斗、再撤退落地。新兵当期也要补给，伤兵仍按一支计。驻守本国军屯格的本国民兵，在该格军屯民兵容量内免耗补给，正规军不免供。补给不足时，所有非免供军队各损失 max(1, 35×缺口÷需求向下取整) HP；完全断供每支损失35，交战军队也会断粮，HP归零阵亡。logistics 军需面板逐军队列补给需求、免供、断粮损失、回血装备需求与预计消耗、预计恢复HP及阻碍原因，再汇总补给与装备库存、需求、消耗与缺口。不计后续生产、行动与外交变化，不实际扣资源。补给全国统扣，缺口使所有非免供军队受损，不按军队先后分粮。

伤兵恢复：本国本期补给全部满足，且军队所在格没有任何交战标记时，存活伤兵每支自动恢复至多30HP，不超过该兵种满编。正规军伤兵平均分享装备提供的恢复量：每件装备提供30HP总量，最多均分给本期合格伤兵，每支至多恢复30HP；全军按实际恢复量向上取整扣装备。民兵独立免费恢复。满血、断粮或交战部队不进入回血分配。战斗结束在补给阶段之后，本期刚打完的伤兵不会追补回血；下期满足条件再恢复。

战斗：工具提交进攻后不会立即结算有守军的战斗；同一格各方在统一结算时同时承伤。本方基础输出为存活军队攻击力之和，伤兵只要存活就保留该兵种基础攻击，不按剩余HP线性折算。每国每场战斗掷一次1—6点骰，对应输出修正-25%、-15%、-5%、+5%、+15%、+25%。多敌国时输出均分给敌方；守方享受地形减伤，领土所有者另享城堡减伤，两种减伤乘法叠加，进攻方不因站在目标格而取得守方地形待遇。伤害在同方军队间分摊，阵亡后的剩余伤害继续分配；静态输出不是胜率或必赢承诺。守军清除后才裁决占领，以战报和占领事件为准。

撤退：本期未行动、不是新兵，且正在交战或受攻击的军队，才能申请撤至相邻合法格。申请占用行动，本期仍参加战斗，输出降低80%；守方撤退只承受分配伤害的50%，攻方撤退承受全部分配伤害。幸存军队在战斗后落地；原目标受阻会寻找相邻合法格，无处可退则原地留守。在本国地块上参战者按守方撤退规则处理。交战或受到进攻的军队不能遣散；合法遣散立即移除军队、停止维持，不返还材料。


## `native:bank`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.bank.mod:BankMod` |
| 状态键 | bank |
| 提供能力 | economy:bank |
| 需要能力 | native:resource-inspection、native:bank-cash、native:economy |
| 依赖模块 | native:resources、native:economy |


规则正文：


**央行、贷款与报表**

储蓄利率范围 -50% 至 50%，贷款加息 2%。额度为最近已结算 GDP 的已估值部分×5，每国最多一笔，期限 5 周期；到期强制扣款，可使现金为负。借款成功才收外交费。买别国报表每次 20 金，turn 可指定已公布期，本国 report 免费。亡国账户停止结息与生成新报表。


## `native:diplomacy`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.diplomacy.mod:DiplomacyMod` |
| 状态键 | diplomacy |
| 提供能力 | native:diplomacy |
| 需要能力 | native:nation-inspection、native:government、native:resource-inspection、native:resource-exchange、native:buildings、native:territory |
| 依赖模块 | native:nations、native:government、native:resources、native:buildings、native:territory |


规则正文：


**附庸与生产上贡**

普通附庸每期将实际生产的15%无条件交给宗主，军队、国土和经济仍自行经营，但完全没有独立外交权，无表决权；随宗主参战，宗主代表处理条约和议和。和平时宗主可 release_vassal 释放，附庸不能和平自行脱离，只能 independence_war 发动真实独立战争。独立战争立即脱离旧战线、停止上贡，与宗主及其其他附庸交战，不能借此令其他国家停战；外交费、战斗与补给照常。propose_vassalage 由宗主邀请，submit_vassalage 主动称臣，普通关系对方 accept_proposal 后成立；宗主未入盟时可以带其普通附庸加入或创建联盟，普通附庸不占联盟表决席位；附庸关系禁止成环；普通宗主灭国或成为别国普通附庸时，原附庸在回合结算释放，恢复独立外交并停止上贡。普通附庸外交绑定宗主的外交实体，不是只禁止按键；独立战争在回合末爆发后立即视为独立国家、恢复全部外交权。offer_peace 的 kind=submit 为本国向对方称臣，kind=subjugate 为要求对方成为本国附庸，均须对方接受、无额外赔款；仍由回合末统一结算。所有外交关系变更和自动拒绝在全体国家行动结束后生效；宣战在其他外交命令后最后处理，并按届时实际阵营校验。剧本可通过轻量钩子为指定宗主配置特殊附庸联盟、贡率和原附庸自动移交转换；特殊附庸保留正常盟友全部权力。上贡逐项从实际建筑产出中先扣，余量才可供本期后续生产链使用；每国每期各类资源按全国实际总产出乘贡率，小数按附庸、宗主及资源跨期累计，累计额向下取整扣取，余数继续结转；不对单座建筑分别取整。同一双方的余额随存档保存；关系解除期间不新增、不扣取，恢复同一双方关系后继续累计；更换宗主分别记账。贡品在本期生产完成后统一到宗主账户，本期不能用来生产。库存、市场交易、赠礼、贷款、利息和收到的贡品不重复计贡，电力不计贡。停工无产出就无上贡；新附庸关系从下期生产开始收费，退出生效前本期已扣贡品不返还。生产/GDP按实际毛产出记录，上贡和收贡另列转移，不重复计为生产、消费或贸易。


**外交实体与提交**

独立国家、联盟是条约实体。保障、撤销保障、共同防御、解除共同防御、宣战、求和的 target 接受国家键、联盟 id 或 bloc/联盟键、nation/国家键，系统按当前代表办理；称臣、附庸及转让盟主仍须具体国家键。外交邀约的 response_actor 指明回应代表，can_accept/can_reject 标明本国是否可直接回应，vote_ids 给出本国相关内部表决编号；普通联盟成员不能直接接受或拒绝和平。外交在行动期提交并预留费用，在外交挂点重新校验后生效或退款。创盟提议固定收取100金，不受双方外交中心减免；成功发布后即使被拒也不退费。接受和拒绝共同防御、创盟或和平邀约均免费，议和赔款另按条款结算；本期收到且可回应的邀约在收件国成功结束回合时，未回应的提交免费自动拒绝，在全体国家行动结束后的外交阶段生效；已提交的回应和已接受的创盟邀约保留，不要求每回合重复回应。对联盟的外部邀约由其代表负责，普通成员只处理内部表决。新邀约结算送达后等待收件国下一次行动，不因其本期已结束而立即拒绝。其他费用以回执为准，不能只拿手续费衡量入盟代价。联盟表决在 T 发起、T+1 开放投票并在该周期末统一计票；内部议案及未生效的宣战不自动告知联盟外目标国；已收到的正式入盟申请被否决会向申请外交实体答复，已收到的条约/和平邀约被否决则由本方外交代表正式拒绝并关闭邀约，双方实体收到结果，但不收到对方内部票单；开票前只能看到自己的选票，他国票向、是否已投和累计票数均隐藏；开票后向全体选民公开记名选票、赞成/反对/弃权票数与表决结果。外交信件可自由拉票和承诺，承诺不代替选票；发起人没有自动赞成票，也无盟主否决或提前结票，本期结束回合仍未投票则自动反对，显式弃权保留，赞成票多于反对票通过。


**联盟权责与退出**

入盟后联盟是一个外交实体：保障、共同防御、宣战、议和须联盟表决，不能个人决定；原个人保障/共同防御全部作废，退盟不恢复；盟内互不攻击、共享视野、互通领土。任一成员交战，全盟不能退盟或解散；盟主须先转让才能退出。创盟须所有受邀国明确接受，任何受邀国拒绝则该邀约取消；尚未全部接受时仅是待定提议，已经接受也不视为入盟。发起国撤回、参与国开战或入盟资格失效时提议关闭，向全体国家公告联盟创建失败及原因；提议关闭后其他回复会失败，应重新查看 diplomacy 和 news。申请加入既有联盟须现成员表决。个人休战在盟内暂停适用，双方恢复独立后仍未到期的才按原到期日继续适用，期限不重置。退出后向原盟任一成员宣战，仍须面对其全盟；原个人保障作废不等于联盟防守失效。联盟成员的经济、国土和军队仍分属各国。结盟是否构成胜利，由当前剧本的胜利程序决定，不能自行宣布已锁定胜利。


**战争义务与议和**

向联盟任一成员宣战，双方整个外交实体参战；防守联盟无需另投票。保障条约 A→B 表示 A 是保障人、B 是受保护实体：第三方宣战 B 时 A 承担防守义务，宣战 A 不会因此召来 B，B 主动进攻也不因此召来 A。共同防御才是双向防守义务，都不替伙伴主动进攻；仅原防守实体的直接条约传导一跳，不递归；被卷入的防守方不再触发它自己的保障/共同防御。援军与攻方有休战时，休战优先，不强拉参战。宣战一刻战线冻结：此后对同一战线的 attack 不重复触发条约、也不追加参战方；只有新的合法宣战仍可能向既有战线增援。query(panel=war) 可按当前状态推演一次宣战的实际参战名单。求和与接受/拒绝和平只能由双方战争主导实体的代表办理，联盟还须表决，普通跟随方不能单独议和；接受后整条战线停战。休战 period < until 时有效，到 until 当期已到期。


## `native:intelligence`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.intelligence.mod:IntelligenceMod` |
| 状态键 | intelligence |
| 提供能力 | native:intelligence |
| 需要能力 | native:territory、native:nation-inspection、native:government、native:resource-exchange、native:resource-inspection、native:buildings、native:diplomacy、native:vision、native:army |
| 依赖模块 | native:nations、native:government、native:resources、native:buildings、native:diplomacy、native:vision、native:army |


规则正文：


**历史地图与购图**

query(panel=map_offers) 查询各国地图商在开局及最近完整回合边界发布的全国测绘：本国土地及邻接无主地，含采样时地形、资源、归属、建筑与在建数量，不含军队分布。intelligence(action=buy_map,target=国家) 购买指定版本，费用20金加每格2金，记行政消费；同一买方同一版本只买一次。成功后立即取得历史地图，不获得实时视野。共享图和间谍图只在真正送达时合入知识。query(panel=map_knowledge) 保留逐字段日期与来源；旧报告不覆盖新观察，未提供的字段不表示拆除。


## `native:succession`

| 项 | 值 |
|---|---|
| 入口 | `mods.native.succession.mod:SuccessionMod` |
| 状态键 | succession |
| 提供能力 | native:succession |
| 需要能力 | native:nation-inspection、native:nations、native:buildings、native:diplomacy、native:army |
| 依赖模块 | native:nations、native:army、native:diplomacy、native:buildings |


## `scenario:eight-nations`

| 项 | 值 |
|---|---|
| 入口 | `mods.scenarios.eight_nations.mod:EightNationsMod` |
| 状态键 | — |
| 提供能力 | — |
| 需要能力 | native:diplomacy、native:nation-inspection、native:government、native:intelligence |
| 依赖模块 | native:diplomacy、native:nations、native:government、native:intelligence |
