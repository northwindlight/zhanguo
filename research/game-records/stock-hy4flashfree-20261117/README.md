# 股票模拟：四席 hy4-flash-free（opencode Go）

2026-11-16 深夜到 2026-11-17 凌晨实际运行的 stock 对局，**随机种子 `126775603`**，四席均为 `hy4-flash-free`，上下文配置 500,000 token。第 60 回合开始时完成最终清算，实际最后一个行动及交易回合为第 59 回合。整局从 22:33 跑到 03:28，约 5 小时。

| 名次 | 玩家 | 最终现金 | 相对初始资金 |
| --- | --- | ---: | ---: |
| 1 | 量化甲 | 127742.00 | +27.74% |
| 2 | 量化乙 | 88588.97 | -11.41% |
| 3 | 量化丙 | 85401.33 | -14.60% |
| 4 | 量化丁 | 77316.17 | -22.68% |

四席平均最终现金 **94762.12**，平均收益 **-5.24%**，中位数 86995.15，盈利席位 1/4，平均最大收盘回撤 14.05%。

> **这不是同条件模型对照。** 本局种子是随机取的（`126775603`），而这里收录的两局 Gemini（[Hydrogen](../stock-gemini-hydrogen-20261113/README.md)、[3.8 Flash](../stock-gemini38-flash-20261114/README.md)）都用 `2026111303`。市场路径不同，收益差异同时包含种子与模型两个因素，不能据此推导模型能力排序。要严格对照需让另一方在同一种子上重跑。

## 材料

- [session.json.gz](session.json.gz)：原始终局存档压缩副本，解压后 9,424,258 字节，SHA-256 为 `c6dddd46eda6778708847b0909d64397358da610ad50ecf6a696f0ae04deeeba`。
- [session.json.audit.tar.gz](session.json.audit.tar.gz)：存档旁的完整审计目录，共 59 个文件，保留原始路径及文件内容。
- [journal.md](journal.md)：原始对局日志，整局一份，未裁剪。
- [结算厅复盘.md](结算厅复盘.md)：终局后五轮对话，每位玩家各发言五次；原始模型发言保留，内容不等同于已逐句核实的游戏事实。
- [对照评估.md](对照评估.md)：本局与 Gemini 3.8 Flash 局的收益、回撤、费用、脚本错误及模型用量对照（列名已按实际模型改写，见下文）。
- [report-metrics.json](report-metrics.json)：对照报告的完整指标、净值曲线及错误审计定位；仅将本机存档路径改为相对引用。
- [run-settings.json](run-settings.json)：游戏与模型参数，已排除接入地址、密钥及本机存档路径。
- [manifest.json](manifest.json)：原始存档摘要及每个审计文件的大小和 SHA-256。
- [SHA256SUMS](SHA256SUMS)：本目录公开文件校验清单。

原始存档、审计与对局日志逐字节保留。压缩包在空目录内解开后，得到 `session.json` 和同级 `session.json.audit/`。校验归档文件：

```bash
sha256sum -c SHA256SUMS
gzip -dc session.json.gz | sha256sum
```

## 运行条件与研究边界

游戏代码为 stock 分支分析版 `617cc70`（`zhanguo-stock-flash-comparison`），相对规则提交 `0585416` 的改动只涉及实验分析脚本，没有修改游戏规则代码。参数沿用两局 Gemini 的配方：四席阵容、温度 0.3、60 回合、500k 上下文、单次输出上限 16384、每回合最多 28 次调用、串行规划，以及相同的费用与算力配方；`ctx_compact_tokens=8192`、`ctx_old_reasoning=strip` 也一致，只有模型与种子不同。

模型接入走 opencode Go 订阅的免费模型 `hy4-flash-free`（`https://opencode.ai/inference/go/openai/v1`）。该端点要求每个请求带 `x-opencode-session`，缺了直接返回 HTTP 400 `MissingSessionID`；对局客户端不发自定义请求头，因此本机 5050 端口有一个只补这一个头的透传网关（`opencode-go-gateway`），Authorization 由它注入。归档中不含任何密钥或上游凭据。

对照报告由本仓库 `experiments/stock_compare.py` 生成，该脚本的列名与标题写死为 Hydrogen / 3.8 Flash，本目录收录的是改写过标签的版本：基线列实为 `gemini-3.8-flash-high` 局，候选列实为本局，其余数字未改动。未收录改写前的原始生成稿。

没有收录 `journal.thoughts.md`（思考日志）、`stdout.log`、终端录屏或私人接入配置。存档中的模型记忆不等于开局以来的全部推理对话。

此档格式为 `zhanguo.session/4`，需要上述历史 stock 代码读取，不应直接交给采用其他存档契约的当前主线加载。
