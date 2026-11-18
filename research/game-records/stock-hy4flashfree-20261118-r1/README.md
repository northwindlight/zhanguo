# 股票模拟：四席 hy4-flash-free（同种子对照 · r1）

2026-11-17 至 11-18 实际运行的 stock 对局，**种子 `2026111303`**（与两局 Gemini 同种子），四席均为 `hy4-flash-free`，上下文配置 500,000 token。第 60 回合开始时完成最终清算，实际最后一个行动及交易回合为第 59 回合。

| 名次 | 玩家 | 最终现金 | 相对初始资金 | 最大回撤 |
| --- | --- | ---: | ---: | ---: |
| 1 | 量化甲 | 141,317.83 | **+41.32%** | 13.40% |
| 2 | 量化丁 | 132,739.19 | **+32.74%** | 9.60% |
| 3 | 量化乙 | 116,686.51 | **+16.69%** | 13.52% |
| 4 | 量化丙 | 109,400.14 | **+9.40%** | 7.22% |

四席平均最终现金 **125,035.92**，平均收益 **+25.04%**，中位数 124,712.85，盈利席位 4/4，平均最大收盘回撤 10.94%。模型累计调用耗时 13,511 s，成交 3,339 笔、手续费 10,023，29,360（算力+研究）+ 0（谍报）。

> **同一配置的另一个样本**：本局是 `hy4-flash-free × 种子 2026111303` 的重复运行之一，另一局见 [stock-hy4flashfree-20261118-r2](../stock-hy4flashfree-20261118-r2/README.md)。两局同种子、同参数、同代码，市场路径完全一致，因此两局之差就是模型侧不确定性（采样与工具调用序列），不含地图因素。同种子的两局 Gemini 对照见 [Gemini 3.8 Flash](../stock-gemini38-flash-20261114/README.md) 与 [Hydrogen](../stock-gemini-hydrogen-20261113/README.md)。

## 材料

- [session.json.gz](session.json.gz)：原始终局存档压缩副本，SHA-256 见下方 manifest 与 SHA256SUMS。
- [session.json.audit.tar.gz](session.json.audit.tar.gz)：存档旁的完整审计目录，保留原始路径及文件内容。
- [journal.md](journal.md)：原始对局日志，整局一份，未裁剪。
- [结算厅复盘.md](结算厅复盘.md)：终局后五轮对话，每位玩家各发言五次；原始模型发言保留，不等同于逐句核实的事实。
- [对照评估.md](对照评估.md)：与 Gemini 3.8 Flash 局（同种子）的收益、回撤、费用、脚本错误及模型用量对照。列名已按实际模型改写。
- [report-metrics.json](report-metrics.json)：完整指标、净值曲线及错误审计定位；存档路径改为相对引用。
- [run-settings.json](run-settings.json)：游戏与模型参数，已排除接入地址、密钥及本机存档路径。
- [manifest.json](manifest.json)：原始存档摘要及每个审计文件的大小与 SHA-256。
- [SHA256SUMS](SHA256SUMS)：本目录公开文件校验清单。

```bash
sha256sum -c SHA256SUMS
gzip -dc session.json.gz | sha256sum
```

## 运行条件与研究边界

游戏代码为 stock 分支分析版 `617cc70`（`zhanguo-stock-flash-comparison`），相对规则提交 `0585416` 只多实验分析脚本，未改游戏规则。参数沿用 Gemini 两局：四席阵容、温度 0.3、60 回合、500k 上下文、单次输出上限 16384、每回合最多 28 次调用、串行规划，费用与算力配方相同；`ctx_compact_tokens=8192`、`ctx_old_reasoning=strip` 一致。

模型接入走 opencode Go 订阅的免费模型 `hy4-flash-free`（`https://opencode.ai/inference/go/openai/v1`）。该端点要求每个请求带 `x-opencode-session`，缺了直接返回 HTTP 400 `MissingSessionID`；对局客户端不发自定义请求头，因此本机 5050 端口有一个只补这一个头的透传网关。归档中不含任何密钥或上游凭据。

第 5 回合因宿主机意外断电中断，恢复后从第 5 回合边界续跑，未重开、未丢回合。

对照报告由 `experiments/stock_compare.py` 生成；该脚本列名与标题写死为 Hydrogen / 3.8 Flash，本目录收录的是改写标签后的版本，数字未改。没有收录 `journal.thoughts.md`、`stdout.log`、终端录屏或私人接入配置。

此档格式为 `zhanguo.session/4`，需要上述历史 stock 代码读取，不应直接交给采用其他存档契约的当前主线加载。
