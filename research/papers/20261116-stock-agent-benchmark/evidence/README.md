# 证据与复算：两局股市对局的账本

本目录是论文 [README.md](../README.md) 第 6 节（实证）与第 8 节（协议）的可复核材料。只读存档，不推进游戏、不调用模型。

## 数据来源

| 局 | 存档 | SHA-256 |
|---|---|---|
| Hydrogen（四席 `gemini-4-hydrogen-high`，2026-11-13） | [`../../game-records/stock-gemini-hydrogen-20261113/session.json.gz`](../../../game-records/stock-gemini-hydrogen-20261113/README.md) | `2345b9fd75b08c468d6f076f5aad990d53160bf0793502398faadf236ba4595c` |
| 3.8 Flash（四席 `gemini-3.8-flash-high`，2026-11-14） | [`../../game-records/stock-gemini38-flash-20261114/session.json.gz`](../../../game-records/stock-gemini38-flash-20261114/README.md) | `04e6d461c6c49b4c6629e1cae93ad1af42f1c9bf67bec373aad634bde3e862f0` |

审计记录来自同级 `session.json.audit.tar.gz`；脚本把 tar 内的分片与存档内的最近 2048 条审计按 `id` 合并，冲突即报错。原始文件不被修改。

## 口径

1. **会计恒等式**：`final_cash = 100000 + realized_pnl − compute_expenses − intelligence_expenses − borrow_fees − interest_paid`。`realized_pnl` 已含交易手续费（逐席校验通过）。
2. **逐股现金流**：按审计里每条 `stock_fill` 的方向记符号（buy/cover 支出含费，sell/short 收入扣费），再加上第 60 回合 `final_liquidations` 的平仓现金流；逐席加总必须等于 `realized_pnl`（8 席全部相等，误差 < 0.02）。
3. **成交额 / 手续费**：本目录 `behaviour.json` 的 turnover 含终局平仓；`report-metrics.json`（上游发布）不含，因此 Hydrogen 局相差 15,951.64（= 2,671.33 + 13,280.31 两笔终局平仓额）。论文正文引用的是 `report-metrics.json` 口径。
4. **价格与被动基准**：`history` 每只股票是「20 个锚定值 + 该股上市后逐回合收盘价」。被动等权持有 = 第 1 回合收盘等权买入 10 只初始股票、持有到第 59 回合收盘；IPO 股的区间收益从其上市回合起算。
5. **公开消息对齐**：以 `(round, tick, symbol, kind)` 为键，逐字段比较 `headline / cashflow_change_pct / effect`。存档只保留最近 160 条消息（本样本覆盖第 53—59 回合），更早的新闻两局都未入档，因此对齐结论仅限该窗口。
6. **研究/脚本/槽错误**：口径与上游 `experiments/stock_compare.py`（分支 `codex/stock-flash-comparison`）一致，用于交叉校验。

## 校验（19 项全部 PASS）

运行 `extract.py` 会打印并写入 [`checks.json`](checks.json)：

- 8 席会计恒等式（误差 < 0.005）；
- 差异分解合计 = 每席平均现金差（31,247.85 vs 31,247.86）；
- 8 席「成交现金流合计 = realized_pnl」；
- 公开消息 160/160 对齐，三个字段全部一致；
- 两局被动等权基准相差 < 2 个百分点。

另与上游 `report-metrics.json` 交叉核对：成交笔数 1,796 / 8,625、手续费 3,965.77 / 20,615.15、脚本修订 164 / 361、自动检查失败 1 / 40、研究运行 22 / 22、交易日槽失败 25 / 39、工具拒绝 0 / 17、工具动作 944 / 1,016——全部一致。

## 文件

| 文件 | 内容 |
|---|---|
| `extract.py` | 全部复算逻辑（标准库，只读） |
| `seats.json` | 每席终局、逐回合净值、回撤、峰值、恒等式校验 |
| `attribution.json` | 每席均值与「Hydrogen − Flash」五项分解 |
| `behaviour.json` | 工具动作、脚本修订/检查失败、研究、交易槽运行/失败（逐席 + 合计） |
| `per-symbol-cashflow.csv` | 逐股现金流（两局 × 四席 + 合计 + 差） |
| `news-and-prices.json` | 公开消息对齐统计、逐股价格相关与区间收益、被动基准 |
| `checks.json` | 19 项校验结果 |
| `make_figures.py` | 由存档重绘论文三张图（Pillow） |
| `claims-check.md` | 两局「结算厅复盘」自述数字与账本的逐条核对 |

## 复算

```bash
# 依赖：Python 3.10+（标准库即可）；绘图另需 Pillow
python evidence/extract.py
python evidence/make_figures.py
```

可用 `--hydrogen/--flash/--out` 指向其它存档或输出目录。
