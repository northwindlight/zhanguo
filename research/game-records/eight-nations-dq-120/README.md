# 八国旧局：四国 Qwen、四国 DeepSeek

找回的 2026-10-18 八国长局。**完整结算到第 119 回合，存档停在第 120 回合开局边界**。第 120 回合已有部分行动，但没有完成结算；它是中断快照，尚未终局。

地图 24×24，种子 `20261018`。这是独立于同目录下 258 回合旧版终局、158 回合 Qwen 局和 50 回合三模型混合局的另一场游戏。

## 阵容与局面

最后一次续局的君主配置如下；议会审核模型为 `qwen3.8-flash`，八国君主与审核模型均为 `high`。DeepSeek 君主窗口为 200,000 token、填充水位 70%，Qwen 君主窗口为 262,144 token。具体参数见 [run-settings.json](run-settings.json)；本局经过配置调整，不能将最后配置视作全程配置。

| 国家 | 君主模型 | 第 120 回合边界国土 | 军队 | 黄金 |
| --- | --- | ---: | ---: | ---: |
| 秦 | DeepSeek V4.1 Flash Free | 22 | 21 | 45 |
| 魏 | DeepSeek V4.1 Flash Free | 26 | 14 | 74 |
| 韩 | Qwen 3.8 Flash | 24 | 12 | 41 |
| 赵 | Qwen 3.8 Flash | 35 | 15 | 52 |
| 燕 | DeepSeek V4.1 Flash Free | 72 | 14 | 105 |
| 齐 | Qwen 3.8 Flash | 79 | 31 | 167 |
| 楚 | Qwen 3.8 Flash | 77 | 12 | 94 |
| 周 | DeepSeek V4.1 Flash Free | 已亡国 | — | — |

数值由历史版本正式读档后提取，原始输出见 [snapshot-overview.txt](snapshot-overview.txt)，精简数据见 [snapshot-summary.json](snapshot-summary.json)。土地、军队与黄金是这一边界的状态，不代表最终成绩。

## 材料

- [session.json.gz](session.json.gz)：原始完整存档的压缩副本，包括模块状态、君主记忆、议会服务和审计索引。
- [session.json.audit.tar.gz](session.json.audit.tar.gz)：存档同级的全部审计文件，保留 `session.json.audit/` 相对路径。
- [journal.md.gz](journal.md.gz)：从第 20 回合重建回退后继续运行的原始日志，覆盖其后的正式支线、重启和失败尝试，直到第 120 回合中断。
- [journal-before-rollback.md.gz](journal-before-rollback.md.gz)：此前支线的完整原始日志，行动记录为第 0—22 回合。第 20—22 回合后来回退，不能再与后续正式支线合计。
- [run-settings.json](run-settings.json)：最后一次续局的游戏与模型参数，不含接入地址、密钥及本机存档路径。
- [audit-model-markers.json](audit-model-markers.json)：原始审计中的各国模型标记计数，与配置阵容交叉核对；条数不是独立行动数。
- [load-check.json](load-check.json)：历史版本正式加载与完整保存状态对比结果。
- [manifest.json](manifest.json)：原始文件、压缩文件及每个审计文件的大小、SHA-256 和归档说明。
- [publication-check.json](publication-check.json)、[SHA256SUMS](SHA256SUMS)：发布内容检查与本目录文件校验清单。

存档、两份日志和审计内容逐字节保留，没有重写游戏状态或补造缺失回合。日志互有重叠，保留原文件分别说明，不拼成一条伪造的连续轨迹。压缩是为了控制仓库体积；原始存档约 304 MB、后段日志约 111 MB。

校验及解压：

```bash
sha256sum -c SHA256SUMS
gzip -dc session.json.gz > session.json
tar -xzf session.json.audit.tar.gz
gzip -dc journal.md.gz > journal.md
gzip -dc journal-before-rollback.md.gz > journal-before-rollback.md
```

`session.json` 与 `session.json.audit/` 应放在同级。解压后文件哈希还可与 `manifest.json` 中的原始摘要逐项核对。

## 运行沿革与停止原因

1. 2026-10-18 开始四国 DeepSeek、四国 Qwen 的八国局，秦、魏、燕、周由 DeepSeek 控制，韩、赵、齐、楚由 Qwen 控制；周走争胜、诸侯可臣属保全的剧本规则。
2. 第 18 回合升级发行版。后来观察者处理了周国记忆污染，并从第 18 回合存档按原审计离线重建第 20 回合边界；原支线第 20—22 回合被放弃。这是人工回退，不能当作模型自主反思。
3. 第 23 回合遇到 DeepSeek 上下文超限，将其四国的窗口从 700,000 调整为 200,000，重做压缩后续跑；Qwen 窗口保持 262,144。
4. 完整边界 118 时升级议会，保留世界和真实外交义务，将旧国策转待重审，重建客户端短上下文并保留旧材料检索。这是人工升级，不能视为全程固定版本实验。
5. 之后完整结算至第 119 回合。当地归档说明记载：第 120 回合楚国议会连续第三次驳回，触发罢免，历史交接按全史 61 段生成，耗时过长，由观察者停止。日志末尾确有楚国第 120 回合历史交接请求及中断标记；完整存档仍在边界 120。

## 版本

最终存档格式为 `zhanguo.session/4`，发行版为 `experimental 0.15.0`，君主客户端状态为 `parliament/3`。已用历史提交 [`280296cdd89fda314072ff4d80440ff5b9e3897e`](https://github.com/northwindlight/zhanguo/commit/280296cdd89fda314072ff4d80440ff5b9e3897e) 正式加载，保存状态与原始 JSON 完全一致；本次没有推进回合或调用模型。

当前主线的存档契约已变化，研究或续跑应使用上述历史版本。私人 API 配置和接入环境不在本归档中。
