# 八国局 20261018（50 回合快照）

真实对局，不是合成测试。2026-10-18 02:57:32 开局，10:19:15 第 50 回合结算后中断；各领域进入`period=51`（第50期结算后），第 51 回合尚未开始。

本局原要求全部high，执行助手以速度为由擅自把DeepSeek和Qwen改成low，用户已废弃本局作为正式比较的用途。实际行为仍可分析：DeepSeek在本局资源执行和快照结果上较好，Qwen保持扩张但经济规模较小，Gemini暴露出资源联合约束与执行衔接问题。不能据此推断相同条件下的总体能力排名。详见[修订报告](report.md)。

另附[模型行为主观解释](afterword-model-behavior.md)，结合公开评分与社区体验，不属于报告实证结论；[本地流式测量](speed-probe/README.md)另记输出速度及DeepSeek思考档位测试。

## 文件

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `report.md` | 22,485 B | `05213d00a16f6f3002cb947669a99c5ef4206a74884c19bf1d30eb3a93109416` |
| `afterword-model-behavior.md` | 16,868 B | `ed90f4c6633e75172e34ed05a9df4e1988331dfd275cf79b539c86ea15e44c3a` |
| `journal.md` | 12,421,349 B | `74337c59091eafe8b0479f329e884ef887feb2126685383924ef8ca77ce1cae4` |
| `state.json.gz` | 14,183,770 B | `605346f32a67ba1ecafac18fa1c23220d4f5eabfda807290de56ea1165be8dbc` |
| `map.txt` | 865 B | `043481a015ff57be9a9c2e0473e5d15c9a0a3ee7c7715e0f59e018992038a3a8` |

原始存档 `session.json` 为 107,275,643 B，SHA-256 `c56b3659c4a8d5e46c67af7787d3f3d55f5583d8fbf6bc29d6e478c93c41da42`，超过 GitHub 单文件 100 MiB 限制，因此以 gzip 收录。解压后校验：

```bash
gunzip -c state.json.gz > session.json
sha256sum session.json
# 期望 c56b3659c4a8d5e46c67af7787d3f3d55f5583d8fbf6bc29d6e478c93c41da42
```

`journal.md` 与 `map.txt` 均为逐字节复制，未裁剪、未重写。`report.md`是修订后的事实与行为报告，保留经济、军事和过程统计，撤回证据不足的因果解释。独立附文及补测结果另列文件。

## 对局参数

| 国 | 模型 | effort | max_tokens | ctx |
|---|---|---|---:|---:|
| 秦、赵、楚 | Gemini 3.8 Flash | high | 32768 | 700k |
| 魏、燕、周 | DeepSeek V4.1 Flash | low | 16384 | 700k |
| 韩、齐 | Qwen 3.8 Flash | low | 16384 | 262144 |
| 议长 | Qwen 3.8 Flash | high | 32768 | 262144 |

八国动作上限均为40；原始文件的历史模型标识不重写。私人接入来源和地址不公开。

地图 24×24，seed `20261018`，`max_turns=300`，experimental 发行版 0.8.0，代码 `main` @ `ec5946c`（0.4.0），存档格式 `zhanguo.session/4`。完整参数见 `report.md` 第 1 节。

## 没有收录什么

- 没有 `config.json`：它含 9 个 `api_key`（其中 3 个 `sk-` 真实密钥）。本记录不包含任何凭据；复现需自行准备模型接入。
- 没有修改日志/存档内容；没有补造未记录的行动或思考。

## 引用

请写“八国局 seed 20261018，50 回合，effort 不对称（Gemini high / 其余 low）”，并附本目录链接；不要把它当作模型能力排行榜。许可见仓库根目录（MPL-2.0）。
