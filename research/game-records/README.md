# 真实游戏日志与完整存档

这里收录对局提供者实际游玩战国时留下的完整存档和原始日志，公开供研究与参考。它们来自旧版单体游戏，并非合成测试或 experimental 的验收局。

| 对局 | 存档回合 | 模型 | 状态 | 存档 | 日志 |
| --- | --- | --- | --- | --- | --- |
| 八国终局，种子 20260921 | 258 | `qwen3.8flash`、DeepSeek | 已终局，燕为最后存续国家 | [state.json](eight-nations-258/state.json) | [journal.md](eight-nations-258/journal.md) |
| 八国，种子 20261009 | 158 | `qwen3.8flash` | 当前公开快照，日志含之后的未完成回合及调用失败 | [state.json](eight-nations-158/state.json) | [journal.md](eight-nations-158/journal.md) |

两局均为 24×24 地图，初始国家为秦、魏、韩、赵、燕、齐、楚、周。模型名称由对局提供者说明。258 局确实使用过 DeepSeek，原始存档中部分 `reasoning_signature.model` 为 `deepseek-flash[1m]`；这些字段原样保留。没有整理或推断各国、各回合的完整模型切换表，不能把该局当作单一模型的受控实验。

## 文件与完整性

每局包含完整 JSON 存档 `state.json` 和整份原始文本日志 `journal.md`。文件只改了存放目录与文件名，内容逐字节保留，没有裁剪、重写、补齐回合或修改世界状态。

[manifest.json](manifest.json) 记录原始文件名、保存回合、地图参数、模型来源、文件大小和 SHA-256。[SHA256SUMS](SHA256SUMS) 可直接校验：

```bash
cd research/game-records
sha256sum -c SHA256SUMS
```

日志可能包含重启、重复回合、模型错误或尚未结算的行动。保存回合以 JSON 顶层 `turn` 为准；日志末尾的文字提示不一定与该字段一致。原始日志中未记录的内容不另行补造，存档中的模型记忆也不等于开局以来的全部推理对话。

## 版本与研究用途

这些存档的 `version` 为 4，来自现已保存在 [`legacy` 分支](https://github.com/northwindlight/zhanguo/tree/legacy) 的旧版代码体系。当前 `main` 是模块化版本，采用独立发行版、统一玩家 API 与 `zhanguo.session/2` 整局格式。旧存档需对照生成它的历史版本研究或续局，不能直接交给当前 main 或 experimental 加载。

可用这些资料观察长期经济经营、战争、外交、国策修改、工具错误与记忆变化。公开资料保留了真实过程，包含失败与人工干预的可能影响；它们是研究参考材料，不提供标准化排行榜结论。

本目录在 main 和 legacy 中各保留一份相同资料。没有收录含接入凭据的私人配置；公开资料不包含复现付费模型调用所需的完整接入环境。
