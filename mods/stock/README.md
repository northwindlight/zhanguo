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

这是新的发行版状态协议，市场动力学 2.1.0（存档带 dynamics 标记，2.1.0 之前的 stock 2.0 存档拒绝混载，不自动迁移）。
原版 native/experimental 不需要 PAMS。

## 一个回合

盘前模型读取上期报告、改脚本、租用计算资源、借贷、购买商业谍报，并可使用已解锁研究槽。
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

研究槽解锁后，在自己的行动阶段手动运行：

```json
{"action":"write","slot":"research","source":"print(inputs['x'] * 7)"}
{"action":"run","slot":"research","inputs":{"x":3}}
```

研究槽可反复运行，每次最多 100 万单位、1000 次 API，全部计入账户共享预算。
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

免费额度是 **每账户每天 400 万计算单位、4000 次 API、1 个自动交易槽**。
所有交易槽和研究槽共享总额，不能通过改代码、换槽、重跑、退租再购买重置已用量。

| `compute(action=buy,kind=...)` | 内容 | 价格 |
| --- | --- | --- |
| `pool` | 增购一档 400 万单位 / 4000 次 API，最多 4 档 | 额外档位每日依次 100 / 200 / 400 |
| `slot` | 增购一个自动交易槽，最多 4 个 | 每额外槽每日 50 |
| `research` | 解锁一个行动内只读研究槽 | 首次解锁 500，启用期间每日 50 |

购买立即生效，当日按整日租金补差额。`compute(action=release,kind=...)` 退租，不退当日费用，源码及研究解锁资格保留。
每日边界重置预算并预扣新一天租金；可用现金不足或处于风控冻结时退回免费池与免费槽、停用研究槽。
租金从真实游戏现金扣，不能动用委托冻结或空头抵押资金，费用进入净值和 `compute_expenses`。
前一日收盘净值曲线记录在新日续租扣款之前，新日账户会另反映该笔续租费用。
`compute_status` 返回档位、费用、剩余额度和停租通知。

## 行情与订单 API

模型没有直接买卖工具，交易必须写入自动交易脚本：

```python
api.call('stock:market', 'quotes')
api.call('stock:market', 'history', symbol='shanhai', limit=60)
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

**市场动力学（2.1.0 起）**：基本面水平持续演化，受市场因子（带平静/风暴状态切换，风暴同时放大波动与点差）、
行业因子（联动会衰减，适合配对）和个股特质项（瞬态噪声 + 持久漂移）驱动。
公开消息三类：**市场快讯**（每日 tick≈1/3 处全行业按贝塔传导）、**公司快讯**（每只股票错峰发布在日内 45%~67% 区间）、
**定期财报**（每 5 个交易日每股轮到一次，日程公开见 `next_report_round`，当日 `report_tick` 也可见，内容事先未知）。
每只股票的收敛速度不同：慢股票的消息缺口会存续更久，快股票要求更早反应；流动性差的股票盘口浅、点差宽，大单冲击更大。
因子状态、波动状态和未公布事件不公开；玩家只能通过价格、盘口和已公布消息推断。

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

`intel_status` 列出公开玩家名单、报告价格及自己最近购得的报告。
`intel(action=steal,target=玩家id)` 支付 **1000** 游戏现金，确定取得调用时的：

- 现金、可用现金、银行贷款余额；
- 多头/空头市值、各股票持仓数量和方向。

报告注明回合、tick、阶段；不会随着对手后续操作自动更新。不会提供源码、研究 state、平均成本或其他未声明资料。
普通 API 仍然绑定自己的身份；不能对自己、背景做市者或清算银行发动谍报。
研究与交易脚本可读 `api.call('stock:intelligence','status')` 分析已购快照，不能发起新谍报。
谍报费用进入净值及 `intelligence_expenses`。

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
第二条已公布消息出现后按消息方向交易 50 股。顺向策略 6/6 胜过反向，扣费后收盘净值变化均值分别约
+380.62、−470.31，持币为 0。这是基本信号及策略差异的验收；尚未进行真实 LLM 间的统计评测，
不把单局盈利当成模型能力排名。

风格基准见 [策略动物园](examples/strategy_zoo.py)：跟消息、日内轮动（动量/均值回归两个方向）、
等权持有四类策略在同一市场的结果分布明显分开——高频轮动被佣金和点差稳定惩罚，
逆消息的均值回归持续亏损，跟消息策略多数种子盈利且回撤小，等权持有完全暴露在该种子的市场方向上。
