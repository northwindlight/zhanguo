# 真实游戏日志与完整存档

这里收录对局提供者实际游玩战国时留下的完整存档和原始日志，公开供研究与参考。其中两局来自旧版单体游戏，`20261018` 局来自 `main` 的 experimental 发行版；均非合成测试。

| 对局 | 存档回合 | 模型 | 状态 | 存档 | 日志 | 报告 |
| --- | --- | --- | --- | --- | --- | --- |
| 八国终局，种子 20260921 | 258 | `qwen3.8flash`、DeepSeek | 已终局，燕为最后存续国家 | [state.json](eight-nations-258/state.json) | [journal.md](eight-nations-258/journal.md) | — |
| 八国，种子 20261009 | 158 | `qwen3.8flash` | 当前公开快照，日志含之后的未完成回合及调用失败 | [state.json](eight-nations-158/state.json) | [journal.md](eight-nations-158/journal.md) | — |
| 八国，种子 20261018 | 50 | Gemini 3.8 Flash / DeepSeek V4.1 Flash Free / Qwen 3.8 Flash | 第 50 回合结算后中断；**effort 不对称，非模型对照** | [state.json.gz](eight-nations-20261018/state.json.gz) | [journal.md](eight-nations-20261018/journal.md) | [report.md](eight-nations-20261018/report.md) |

三局均为 24×24 地图，初始国家为秦、魏、韩、赵、燕、齐、楚、周。模型名称由对局提供者说明。258 局确实使用过 DeepSeek，原始存档中部分 `reasoning_signature.model` 为 `deepseek-flash[1m]`；这些字段原样保留。没有整理或推断各国、各回合的完整模型切换表，不能把该局当作单一模型的受控实验。

`20261018` 局是 3 Gemini / 3 DeepSeek / 2 Qwen 的混合阵容，且 Gemini 为 `high`、其余为 `low`（`max_tokens` 也不同）；该局的跨组差异**不能**用于模型能力排序，详见其 `report.md` 第 1、8 节。

## 文件与完整性

每局包含完整 JSON 存档和整份原始文本日志 `journal.md`。文件只改了存放目录与文件名，内容逐字节保留，没有裁剪、重写、补齐回合或修改世界状态。258/158 局直接收录 `state.json`；`20261018` 局的原始存档 107,275,643 B，超过 GitHub 单文件 100 MiB 限制，收录为 `state.json.gz`（14,183,770 B），解压后哈希见该目录 README 与 `manifest.json`。

[manifest.json](manifest.json) 记录原始文件名、保存回合、地图参数、模型来源、文件大小和 SHA-256。[SHA256SUMS](SHA256SUMS) 可直接校验：

```bash
cd research/game-records
sha256sum -c SHA256SUMS
```

日志可能包含重启、重复回合、模型错误或尚未结算的行动。保存回合以 JSON 顶层 `turn` 为准；日志末尾的文字提示不一定与该字段一致。原始日志中未记录的内容不另行补造，存档中的模型记忆也不等于开局以来的全部推理对话。

## 版本与研究用途

258/158 局的 `version` 为 4，来自现已保存在 [`legacy` 分支](https://github.com/northwindlight/zhanguo/tree/legacy) 的旧版代码体系；`20261018` 局的存档格式为 `zhanguo.session/4`，来自 `main` @ `ec5946c`（0.4.0）的 experimental 发行版 0.8.0。两代格式不互通：旧存档需对照生成它的历史版本研究或续局，不能直接交给当前 `main` 或 experimental 加载；`20261018` 局也不保证能被未来的 `main` 加载。

可用这些资料观察长期经济经营、战争、外交、国策修改、工具错误与记忆变化。公开资料保留了真实过程，包含失败与人工干预的可能影响；它们是研究参考材料，不提供标准化排行榜结论。

258/158 局在 main 和 legacy 中各保留一份相同资料；`20261018` 局只存在于 main。没有收录含接入凭据的私人配置；公开资料不包含复现付费模型调用所需的完整接入环境。
