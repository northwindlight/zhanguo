# stock 2.0：交互式量化市场

> **分支规约（2026-10-24 起）**：stock 在 `stock` 分支上独立演进，**不得进入 main**、
> **不得修改 `mods/native/`（及 engine/clients）的任何文件**。只读引用（import）允许；
> 需要改 native 的行为时，把要改的部分**复制 fork 到 `mods/stock/` 名下**再改，
> native 保持与 main 完全一致。当前对 native 的唯一依赖是
> `mods/stock/tools.py` 里 `from mods.native.tools import commands`（只读）。

玩家用受限 Python 编写策略，在 **PAMS 0.3.0 的真实订单簿**里共同交易。
基本面由种子确定的多因子动力学驱动：市场与行业因子、平静/风暴波动状态、
每只股票自己的波动率、贝塔、消息敏感度、价格吸收速度、漂移和盘口流动性都不同。
有限资金和库存的做市者、只根据已公布消息交易的基本面代理、噪声/动量流及玩家订单共同形成价格。
每交易日 **20000 个逻辑 tick**，宿主尽快计算，无需等待现实 20 分钟。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-stock.txt
.venv/bin/python mp_run.py --config mp_config.stock.offline.example.json
# 或 ./start.sh --config mp_config.stock.offline.example.json
```

模型配置复制 `mp_config.stock.example.json` 为本地 `mp_config.stock.local.json` 后填写密钥，再通过 `--config` 指定；离线配置使用内置 `sim`，不需要密钥。
示例使用独立的 `stock_v2_save.json` / `stock_v2_journal.md`，不会覆盖旧 stock 1.0 的文件。
Windows 用 `start.ps1 --config mp_config.stock.offline.example.json`，也可手动安装依赖后运行 mp_run。

这是新的发行版状态协议，市场动力学 2.2.0（存档带 dynamics 标记与个股个性/股票池状态，旧存档拒绝混载，不自动迁移）。
原版 native/experimental 不需要 PAMS。

## 一个回合

盘前模型读取上期报告、改脚本、租用本回合算力与交易槽、借贷、购买商业谍报，并按次付费运行研究槽。
所有玩家 `end_turn` 后锁定策略，沿共同时间线运行；收盘计费、风险检查、推进日历及续租。
每 5 个交易日后休市 2 天，休市仍可研究和规划，每日租金照常结算。

同 tick 的策略看到同一公共时间截面。委托及撤单最早在下一 tick 生效；同 tick 先处理撤单，新增委托按轮换账户顺序处理。
成交立即进入现金和持仓，下一次回调能看到自己的新成交。各槽共用一个交易身份，禁止跨槽自成交。

## 自动交易与行动内研究

`script` 支持 `write / patch / check / status / run`，`slot` 用字符串 `"1"`～`"4"` 或 `"research"`。
普通源码每天执行一次；定义无参数 `on_tick()` 时，默认每 100 tick 回调。
`state` 是每槽独立、跨日持久的 JSON 字典（最多 64KB），成功回调提交；错误不会撤销已受理动作。

```python
def on_tick():
    calendar = api.call('stock:clock', 'snapshot')
    if not calendar['trading_day']:
        return
    quotes = api.call('stock:market', 'quotes')
    book = api.call('stock:portfolio', 'holdings')
    if not state.get('bought') and book['available_cash'] > 1000:
        api.call('stock:market', 'buy', symbol='shanhai', quantity=10)
        state['bought'] = True
    api.call('stock:clock', 'sleep', ticks=100)
```

`sleep` 只设置下一次回调，不阻塞、不直接推进行情，也不按等待 tick 扣计算量。
**不要写 `while + sleep` 等待市场**；回调应返回。每回调上限 50000 计算单位、200 次 API，耗尽即停止该槽当天执行。
沙盒沿用通用 Machine，按运算、容器规模与载荷计费，一次 API 基础计 100 单位。
禁止导入、文件、网络、反射、随机和动态执行。每日额度是上限，只运行实际需要的计算。

研究槽按次付费，在自己的行动阶段手动运行：

```json
{"action":"write","slot":"research","source":"print(inputs['x'] * 7)"}
{"action":"run","slot":"research","inputs":{"x":3}}
```

研究槽每次 1000 现金、每回合最多 3 次；单次最多 100 万单位、1000 次 API，不占共享算力池。
它只能读取已知行情、自己的账户及已购报告；不能下单、借贷、购买资源、发动谍报、嵌套运行脚本或推进行情。
源码、结果、错误位置、每槽用量及私人 state 随完整回合存档。

### 沙盒 Python 子集（写策略前先看）

支持：`def`/`for`/`while`/`if`、列表与字典推导、切片、三元表达式、增强赋值；
`sorted/min/max` 接受 **def 出来的具名函数作 `key=`**（这是按字段排序的正道）。

不支持（会被即时拒绝并在报错里附一句改法）：f-string（用 `+` 拼接或 `%` 格式化）、
lambda、set 与集合推导、生成器表达式、try/except、class、import、解包与海象运算符；
方法仅限 get/items/keys/values/append/extend/pop/copy/count/index/sort/reverse 与字符串处理；
内建仅限 abs/all/any/bool/dict/enumerate/float/int/len/list/max/min/print/range/reversed/
round/sorted/str/sum/tuple/zip。`script(action=check)` 免费预检，被拒写法会得到改法提示。

```python
def on_tick():
    rows = api.call('stock:market', 'quotes')
    def chg(r): return r['change_pct']          # 具名函数代替 lambda
    top = sorted(rows, key=chg, reverse=True)[0]
    print('最强 ' + top['symbol'] + ' ' + str(top['price']))   # 用 + 而不是 f-string
    api.call('stock:clock', 'sleep', ticks=200)
```

### 模型每回合看到什么

盘前简报自动附带：回合与日历、账户与持仓、行情（含每只股票的已公布消息和财报日程）、
**上一交易日的执行回执**（委托各状态笔数、成交笔数、买卖股数与金额、手续费——
用来核对策略是否按预期执行）、上回合脚本输出与报错、算力与谍报状态。
脚本报错带行号和改法提示；脚本里也可 `api.call('stock:market','day_summary')` 查询执行回执。

可运行示例：[公开消息策略](examples/news_strategy.py)、[只读研究](examples/research.py)、[策略动物园](examples/strategy_zoo.py)。

## 算力池与计算槽

免费额度是 **每账户每回合 400 万计算单位、4000 次 API、1 个自动交易槽**。
算力池与交易槽**只在当回合有效，新回合归零**，要用就重新买（明码标价）：

| `compute(action=buy,kind=...)` | 内容 | 价格（每回合） |
| --- | --- | --- |
| `pool` | 增购一档 400 万单位 / 4000 次 API，最多 4 档 | 第 2/3/4 档 600 / 1200 / 2400 |
| `slot` | 增购一个自动交易槽，最多 4 个 | 第 2/3/4 个 400 / 800 / 1600 |

购买立即生效、按整回合计费；`compute(action=release,kind=...)` 退租不退钱，资源用到收盘。
回合边界把档位归零、用量清零，源码与已存 state 保留。

**研究槽按次计费**，不需要购买：每次 `script(action=run,slot="research")` 固定 **1000 现金**，
每回合最多 **3 次**；研究不占共享算力池，单次上限 100 万步 / 1000 次 API。
研究只能读行情、账户与已购报告，不能下单、借贷或推进行情。
费用从真实游戏现金扣，进入 `compute_expenses`；`compute_status` 返回档位、用量、剩余研究次数与各档价格。

## 行情与订单 API

模型没有直接买卖工具，交易必须写入自动交易脚本：

```python
api.call('stock:market', 'quotes')
api.call('stock:market', 'history', symbol='shanhai', limit=60)   # 每日收盘价，整局保留
api.call('stock:market', 'ticks', symbol='shanhai', limit=200)    # 盘中采样 {round,tick,price}，整局保留
api.call('stock:market', 'ticks', symbol='shanhai', limit=100, round=3)  # 只看第 3 回合
api.call('stock:market', 'orders')  # 本账户最近100笔委托
api.call('stock:market', 'fills')   # 有界近期成交，仅本账户
api.call('stock:market', 'buy', symbol='shanhai', quantity=10)           # 开多
api.call('stock:market', 'sell', symbol='shanhai', quantity=10)          # 平多
api.call('stock:market', 'short', symbol='shanhai', quantity=10)         # 借券开空
api.call('stock:market', 'cover', symbol='shanhai', quantity=10)         # 回补还券
api.call('stock:market', 'buy', symbol='shanhai', quantity=10, price=40) # Day限价单
api.call('stock:market', 'cancel', order_id=123)
```

不填 price 时采用有 2% 价格保护的市价意图，保护范围内无法成交的剩余量到期；限价单收盘到期。
状态为 accepted、partially_filled、filled、cancelled、expired 或 rejected；成功受理不等于保证成交。
挂单先冻结现金/股票/保证金，撤单或到期释放。成交、研究、采购及谍报回执进入逐动作审计，成交另有 `stock_fill`。

**PAMS 撮合规则**：同批匹配量按最后匹配对确定的统一清算价成交。大单跨档不会逐档分别成交；不能将其当作某真实交易所的完整复刻。
盘前盘口及路径是上期收盘的历史快照；盘中只公开已经走到的路径、盘口、消息，没有未来价格或 seed。

**市场动力学（2.2.0 起）**：基本面水平持续演化，受市场因子（带平静/风暴状态切换，风暴同时放大波动与点差）、
行业因子（联动会衰减，适合配对）和个股特质项（瞬态噪声 + 持久漂移）驱动。
公开消息六类：**市场快讯**（每日 tick≈1/3 处全行业按贝塔传导）、**公司快讯**（每只股票错峰发布在日内 45%~67% 区间）、
**定期财报**（每 5 个交易日每股轮到一次，日程公开见 `next_report_round`，当日 `report_tick` 也可见，内容事先未知）、
**公司事件**（监管调查/技术突破/产品事故/做市扩容/行业政策，盘中随机触发；除跳空外会**永久改变该股个性**——
波动率、贝塔、收敛速度、流动性、点差、敏感度或漂移，变化随新闻 `effect` 字段公开）、
**上市公告**与**退市公告**（见下）。
每只股票的收敛速度不同：慢股票的消息缺口会存续更久，快股票要求更早反应；流动性差的股票盘口浅、点差宽，大单冲击更大。
因子状态、波动状态和未公布事件不公开；玩家只能通过价格、盘口和已公布消息推断。

**动态股票池（2.2.0 起）**：初始 10 只 + 候选池 4 只新股。新股按种子排定的日程挂牌——
首只在第 7 个交易日，之后每 6 个交易日一只；`api.call('stock:market','pool')`（或 `pool` 工具/盘前简报）
公布下一只的名称、行业、发行价与挂牌回合，挂牌日 tick 1 发布上市公告，背景做市账户注入流通盘后正常撮合。
退市规则公开可算：收盘价连续 5 个交易日低于发行锚价（quotes 的 `anchor`）的 35% 触发**退市风险警示**
（quotes 显示 `delist_review_round`），再 5 个交易日复核，仍低于则按收盘价 5% 折价（空头溢价）
场外强制回购全部持仓后摘牌——该股从行情消失，脚本引用会报"没有这只股票"。
复核日收复则摘帽；股票池下限 8 只，跌破下限时退市暂缓。退市个股的公告与成交记录保留在审计里。

## 账户与风险

`净值 = 现金 + 多头市值 − 空头市值 − 现金贷款`。
买卖立即入账，成交 ID 去重；开仓成本含开仓手续费，部分平仓按比例结转成本，实现盈亏扣平仓手续费。贷款只计一次现金利息。
净值标记使用实际成交价，尚无当日成交时沿用昨收；未成交极端挂单不能抬高净值。
同一股票不能隐式翻转多空方向，先平相反方向仓位，再开新仓。

默认每股有 5000 股可借券源，包含未成交开空的预留数量。卖空收入及额外 50% 初始保证金受限；
维持保证金 30%，每日按空头市值计 0.02% 借券费。
价格变化后检查风险：撤销风险委托、使用可用现金还债，必要时由有限资金/库存的清算银行按标记价及 0.1% 清算折价接盘。
清算真实转移现金与股票，不产生免费收益；流动性不足时保持风险冻结，只允许减仓还款，后续重试。
净值非正破产，历史和终局面板保留。

## 商业谍报

`intel_status` 列出公开玩家名单、快照与源码偷取价格及自己最近购得的报告。
`intel(action=steal,target=玩家id)` 支付 **1000** 游戏现金，确定取得调用时的：

- 现金、可用现金、银行贷款余额；
- 多头/空头市值、各股票持仓数量和方向。

`intel(action=source,target=玩家id)` 支付 **2500** 游戏现金，随机偷取对方一段策略源码：
从对方已保存源码的全部交易槽/研究槽中随机挑一个，再随机截取连续至多 6 行。
报告注明槽位、源码版本与行号范围（第X～Y行/共N行）；同一目标可重复偷取不同片段，
对方修订后版本号变化；源码很短（≤6 行）时可能整段被偷走。

两种报告都注明回合、tick、阶段；不会随着对手后续操作自动更新。不会提供研究 state、
平均成本或其他未声明资料。
普通 API 仍然绑定自己的身份；不能对自己、背景做市者或清算银行发动谍报；
对没有保存过源码的目标发动 source 会被拒且不扣费。
研究与交易脚本可读 `api.call('stock:intelligence','status')` 分析已购快照与源码片段，
不能发起新谍报。
谍报费用进入净值及 `intelligence_expenses`。

## 并发规划

盘前规划阶段各量化员互相独立，模型请求默认并发发出：三个 LLM 玩家的网络等待重叠而不是
相加，回合墙钟时间约等于最慢的那一个。世界变更（下单、借贷、买谍报、改脚本）全部经
会话锁串行入账，回执按先手轮换顺序归并，同一种子的世界状态与串行完全一致——
并发的只是等待，不是任何市场语义。观战终端里不同量化员的输出行会交替出现，
每行原子完整。配置 `"planning_workers": 1` 可退回严格串行；未设置时按人数并发。

## 看海（人类观战）

模型盘前的规划动作（改脚本、租算力、买谍报、借贷、end_turn）照常逐条出现在终端与日志。
交易日 20000 tick 的脚本执行不再逐笔汇报：下单受理、成交、清算回执只进完整审计
（存档可查），终端和日志在每个玩家收盘后只多一行结算摘要——
`槽1 effective，259步/3次API；委托2笔（成交2），成交2笔，买100股/卖0股，手续费0.42；财富 100123（+0.12%）`。
失败的槽会把报错首行带进摘要；面板（`board` 命令）仍随时可看全网财富榜。

## 开发与验收

参数均在 `mods/*.toml` 中。后端只在 [backend.py](backend.py) 使用固定版本 PAMS 的内部接口；
[portfolio.py](portfolio.py) 是唯一权威账本，第三方代理账户不重复记账。
PAMS 的软件出处与许可证见 [第三方说明](THIRD_PARTY.md)。

```bash
.venv/bin/python -m unittest discover -s tests -p test_stock_game.py -v
.venv/bin/python probe_review.py
.venv/bin/python probe_review.py --causal-controls
```

公开消息对照在种子 1～6 各跑一个完整交易日：同场顺向、反向和持币策略，分别在每只股票自己的
第二条已公布消息出现后按消息方向交易 50 股并持有到收盘。顺向策略 5/6 胜过反向（种子2为整体
无信号日，顺反向都接近零），扣费后收盘净值变化均值分别约 +319.16、−408.91，持币为 0。
这是基本信号及策略差异的验收；尚未进行真实 LLM 间的统计评测，不把单局盈利当成模型能力排名。
顺向对照是纯多头敞口：单日市场大跌时消息方向正确也会被市场因子压过——对冲与择时正是引擎
留给策略的空间。

风格基准见 [策略动物园](examples/strategy_zoo.py)：跟消息、日内轮动（动量/均值回归两个方向）、
等权持有四类策略在同一市场的结果分布明显分开——高频轮动被佣金和点差稳定惩罚，
逆消息的均值回归持续亏损，跟消息策略多数种子盈利且回撤小，等权持有完全暴露在该种子的市场方向上。
