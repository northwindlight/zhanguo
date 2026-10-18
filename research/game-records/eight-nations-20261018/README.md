# 八国局 20261018（50 回合快照）

真实对局，不是合成测试。2026-10-18 02:57:32 开局，10:19:15 第 50 回合结算后中断；存档顶层 `turn=50`、`period=51`，第 51 回合尚未开始。

**⚠️ 本局不是模型对照实验。** Gemini 三国 `reasoning_effort=high` / `max_tokens=32768`，DeepSeek/Qwen 五国 `reasoning_effort=low` / `max_tokens=16384`；effort 与组完全共线，跨组差异不可归因于模型。详见 `report.md` 第 1 节与第 8 节。

## 文件

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| `report.md` | 100,365 B | `d355979d73e7733a5a9c421ae2cbca729619badf7be4fc6a1a7301b2f40f3e75` |
| `journal.md` | 12,421,349 B | `74337c59091eafe8b0479f329e884ef887feb2126685383924ef8ca77ce1cae4` |
| `state.json.gz` | 14,183,770 B | `605346f32a67ba1ecafac18fa1c23220d4f5eabfda807290de56ea1165be8dbc` |
| `map.txt` | 865 B | `043481a015ff57be9a9c2e0473e5d15c9a0a3ee7c7715e0f59e018992038a3a8` |

原始存档 `session.json` 为 107,275,643 B，SHA-256 `c56b3659c4a8d5e46c67af7787d3f3d55f5583d8fbf6bc29d6e478c93c41da42`，超过 GitHub 单文件 100 MiB 限制，因此以 gzip 收录。解压后校验：

```bash
gunzip -c state.json.gz > session.json
sha256sum session.json
# 期望 c56b3659c4a8d5e46c67af7787d3f3d55f5583d8fbf6bc29d6e478c93c41da42
```

`journal.md` 与 `map.txt` 均为逐字节复制，未裁剪、未重写。`report.md` 是本局的分析报告（约 5.2 万字符，其中中文约 2 万），包含数据修正、经济/军事/行为分析与复现说明。

## 对局参数

| 国 | 模型 | 路由 | effort | max_tokens | ctx |
|---|---|---|---|---|---:|
| 秦、赵、楚 | `gemini-3.8-flash-tiered` | Anthropic `/v1/messages` @ 8045 | high | 32768 | 700k |
| 魏、燕、周 | `deepseek-v4.1-flash-free` | OpenAI @ 13338 | low | 16384 | 700k |
| 韩、齐 | `qwen3.8-flash` | OpenAI @ 5050 | low | 16384 | 262144 |
| 议长 | `qwen3.8-flash` | 5050 | high | 32768 | 262144 |

地图 24×24，seed `20261018`，`max_turns=300`，experimental 发行版 0.8.0，代码 `main` @ `ec5946c`（0.4.0），存档格式 `zhanguo.session/4`。完整参数见 `report.md` 第 1 节。

## 没有收录什么

- 没有 `config.json`：它含 9 个 `api_key`（其中 3 个 `sk-` 真实密钥）。本记录不包含任何凭据；复现需自备网关。
- 没有修改日志/存档内容；没有补造未记录的行动或思考。

## 引用

请写“八国局 seed 20261018，50 回合，effort 不对称（Gemini high / 其余 low）”，并附本目录链接；不要把它当作模型能力排行榜。许可见仓库根目录（MPL-2.0）。
