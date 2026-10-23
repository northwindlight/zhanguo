# stock：模拟炒股发行版

一套让 LLM 玩家**只能用 Python 做量化炒股**的回合制游戏发行版。

- 沿用原版 `experimental:python` 的受限 Python 沙盒（同一套回执形状、同一套预算口径）。
- **买卖下单没有工具**：模型能调用的只有脚本伞工具、只读事实、借贷与结束回合；
  唯一的下单通道是写进 Python 脚本，由脚本 `api.call("stock:market","buy",...)` 发出。
- 每回合两个窗口：**开盘 20 分钟**（打磨脚本 + 跑一次脚本下单）、**回合末统一工作 20 分钟**
  （跑完脚本后接着改脚本，改动留给下一回合）。
- **每 5 个交易日休市 2 天**：休市日不能买卖，但可以改脚本、规划、借贷。
- 回合开局，模型会看到自己上回合脚本的**打印输出与报错**（类型、行列、出错那行），
  以及自己的持仓、现金、负债与总财富。

## 跑起来

```bash
cp mp_config.stock.example.json mp_config.json   # 填 api_key
python3 mp_run.py --config mp_config.json
```

不想接模型就用离线配置（发行版自带 `sim` 客户端，不需要密钥）：

```bash
python3 mp_run.py --config mp_config.stock.offline.example.json
```

## 一个回合长什么样

| 阶段 | 谁在动 | 玩家看到什么 | 玩家能做什么 |
|---|---|---|---|
| 回合开局 | 引擎 | 上回合脚本结果（输出 + 报错）、持仓、现金、负债、总财富、行情、窗口额度 | 规划 |
| 开盘窗口 | 本玩家 | 同样的事实 | 写/改脚本、跑一次脚本、借贷 |
| 回合末统一工作 | 所有玩家同时 | 同样的事实 | 继续打磨脚本（改动留给下一回合） |
| 回合结算 | 引擎 | — | — 挂单统一撮合、行情滚动、计息、追保、强制平仓、破产判定 |

额度按模型侧工具调用计数，是墙钟之外的确定性口径；墙钟耗时也会报给模型。
窗口用满之后，脚本的写/改/跑会被拒绝并说明原因（`end_turn` 永远可用）。

## 模型能用的工具

| 工具 | 说明 |
|---|---|
| `script` | 伞工具：`write` 保存源码 / `patch` 精确改一处 / `check` 免费查语法 / `status` 查状态 |
| `script_status` | 只读事实：上回合脚本的打印输出、报错类型与行列 |
| `portfolio` | 只读事实：现金、负债、每只持仓的成本与浮盈亏、总财富、授信、追保线、净值曲线、强平记录 |
| `market` | 只读事实：现价、昨收、涨跌、`ma5/ma10/ma20`、动量、已实现波动率、冲击成本与手续费参数 |
| `round` | 只读事实：回合号、开市还是休市、离下次开盘几回合、当前窗口与额度 |
| `bank` | 伞工具：`borrow` 借钱 / `repay` 还钱 |
| `end_turn` | 结束本回合，提交一句小结 |

买卖工具被**封堵**：发行版注册了一个不出现在模型工具表里的 `stock_trade`，
真被按名字调用会直接回执拒绝，并说明正确路径是脚本。

## 脚本里能做什么

脚本运行在沙盒里，没有导入、文件、网络、随机与动态执行，也没有 `try/except`。
预算：单次运行 **800000 计算单位、2000 次 API 调用**（一次 API 调用计 100 步）。
可用能力：

```python
quotes  = api.call('stock:market', 'quotes')                       # 当前行情
history = api.call('stock:market', 'history', symbol='shanhai', limit=30)  # 历史收盘价
order   = api.call('stock:market', 'buy', symbol='shanhai', quantity=100)  # 下单（写）
book    = api.call('stock:portfolio', 'holdings')                  # 自己的持仓与财富
api.call('stock:portfolio', 'borrow', amount=10000)               # 借钱（写）
cal     = api.call('stock:clock', 'snapshot')                      # 回合日历
print('已挂单：' + order['data']['symbol'])
```

写方法的 `api.call` 返回的是**回执**（订单在 `receipt["data"]` 里）；被拒绝时脚本直接终止，
原因写进回执，回合开局就能看到。

## 规则口径

**行情**（`mods/stock/mods/market.toml` 的 `[Parameters]`）

| 参数 | 默认 | 含义 |
|---|---|---|
| `FloatShares` | 20000 | 每只股票的流通盘 |
| `ImpactRate` | 0.05 | 冲击成本：单量打满流通盘时价格被推开 5% |
| `ImpactCap` | 0.05 | 冲击成本封顶 |
| `Commission` | 0.001 | 手续费，单边 0.1% |
| `FlowImpact` | 0.03 | 订单流对下一回合价格的推动 |
| `Momentum` | 0.35 | 动量权重 |
| `Reversion` | 0.05 | 均值回归权重 |

价格引擎借鉴开源 [TraderSim](https://github.com/Sad-Sun678/TraderSim) 的分层做法：
均值回归 + 动量 + 波动率 + 订单流冲击 + 板块情绪 + 大盘情绪；
滑点用固定比例冲击成本近似（参考 [tliesnham/quant](https://github.com/tliesnham/quant) 的
`SlippageModel` 思路）。同一个 `seed` 的行情路径完全可复现。

**账户**（`mods/stock/mods/portfolio.toml` 的 `[Parameters]`）

| 参数 | 默认 | 含义 |
|---|---|---|
| `InitialCash` | 100000 | 初始资金 |
| `InterestRate` | 0.02 | 每回合利息（按未偿本金） |
| `MarginRatio` | 0.5 | 追保线：负债超过持仓市值的 50% 就强平 |
| `BaseCreditRatio` | 0.5 | 基础授信（初始资金的 50%） |

净值 = 现金 + 持仓市值 − 负债。授信额度 = 基础授信 + 追保比例 × 持仓市值。
**银行强制平仓**：负债击穿追保线时，银行按市价（同样吃冲击成本）从最大的持仓开始砍仓，
直到回到追保线以内；砍完仍资不抵债、净值跌破零，账户破产退市，不再参与回合。

**回合**（`mods/stock/mods/clock.toml` 的 `[Parameters]`）

| 参数 | 默认 | 含义 |
|---|---|---|
| `TradingDays` | 5 | 每 5 个交易日休市 |
| `RestDays` | 2 | 休市 2 天 |
| `MarketWindow` | 20 | 开盘窗口分钟数（= 动作额度） |
| `WorkWindow` | 20 | 回合末统一工作窗口分钟数（= 动作额度） |

## 目录

```
mods/stock/
  mod.toml          发行版清单（装哪些模块、给模型看什么、结算挂点）
  clock.py          回合时钟：回合号、交易日历、行动门、窗口额度
  market.py         行情、挂单簿、撮合、价格引擎
  portfolio.py      账户、持仓、借贷、计息、强制平仓、破产
  python.py         沙盒：沿用原版 experimental:python，只换时钟与额度口径
  tools.py          模型工具面（买卖封堵在这里）
  session.py        会话：开局、账户、回合记录
  client.py         模型客户端（开局简报）+ sim / replay 离线客户端
  config.py         配置契约
  mods/*.toml       四个模块的声明
```

## 改规则

- 换股票池 / 板块 / 初始价：改 `mods/stock/market.py` 的 `STOCKS`。
- 换交易日历：改 `mods/stock/mods/clock.toml` 的 `TradingDays` / `RestDays`。
- 换费率、初始资金、利率、追保线：改 `mods/stock/mods/market.toml` 与 `portfolio.toml` 的 `[Parameters]`。
- 加新能力：在对应模块的 `player_api` 上加方法 → 在 `[PlayerAPI] Methods` 里声明 →
  在 `mods/stock/tools.py` 里给模型一个工具（或用 `covers` 只给脚本用）。

## 验收

```bash
python3 -m unittest tests.test_stock_game -v
```

覆盖工具面（买卖只在脚本）、交易日历、脚本下单与回合末结算、休市拒单、
开局简报里的输出与报错、借贷与计息、银行强制平仓、破产退市、窗口额度、存档往返与同种子可复现。
