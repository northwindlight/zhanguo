# 二十轮报告数据附件

对应[综合报告](../经济RL二十轮综合报告-20261111.md)。数据固定为连续 PPO 当前链第1—20轮；第3/5轮历史重复权重已从主表排除。所有比值CSV保存原始小数：1=教师100%，不是已经乘100的百分数。

## 表格

| 文件 | 行数 | 内容 |
| --- | ---: | --- |
| [rounds.csv](tables/rounds.csv) | 20 | 当前权重身份、loss/KL/熵/覆盖、动作构成、两环境成绩和最差席/尾段 |
| [training_worlds.csv](tables/training_worlds.csv) | 160 | 每世界配置、决策、成功/失败/结束、S、运行秒数 |
| [training_seats.csv](tables/training_seats.csv) | 640 | 每训练国家的消费、投资、动作、开局、触顶和成功项目描述比值 |
| [evaluation_seats.csv](tables/evaluation_seats.csv) | 160 | 固定两环境的八席位，全20轮；末段、开局、失败、短缺、土地等 |
| [evaluation_history.csv](tables/evaluation_history.csv) | 600 | 每10回合的四个模型席位累计S和土地，与教师配对 |
| [gradient_probes.csv](tables/gradient_probes.csv) | 420 | 60个首微批探针的7项加权梯度；不能当整批/整轮梯度 |
| [kl_windows.csv](tables/kl_windows.csv) | 1,036 | 窗口覆盖行数、KL和β变化；窗口内按行数加权 |
| [bc_epochs.csv](tables/bc_epochs.csv) | 20 | 14世界BC各epoch的验证损失、总体/分动作准确率 |
| [legacy_ablation.csv](tables/legacy_ablation.csv) | 19 | 旧暖身起点的受控更新次数/覆盖率；不是当前链消融 |
| [legacy_long_games.csv](tables/legacy_long_games.csv) | 6 | 旧消融的三组×两换位长局；分母是同一起点暖身模型，非教师 |
| [kaggle_jobs.csv](tables/kaggle_jobs.csv) | 20 | 实际作业开始/结束、软件环境、重复或失败与当前权重对应关系 |
| [sources.csv](tables/sources.csv) | 334 | 原实验相对路径、文件大小和SHA；20份权重只留引用摘要 |

完整ZIP另有8,237行 `tables/updates.csv`，保留每批标量指标；嵌套梯度详见探针CSV及冻结的原始jsonl。

`finish` 是决策减失败项目减成功建设/征兵得到的剩余数，并逐世界检查加总。失败率分母为全部决策，尝试项目成功率分母不含finish。`tail_S_per_turn` 为末50回合平均S增量；同窗口比值也等于末50回合累计增量比。

`sampled_S_per_successful_project` 等描述比值不表示项目因果收益；`training_worlds.seconds` 是单世界测量，不可直接相加当成并行作业墙钟。loss、KL、clip的轮次摘要沿用优化器原记录：平均的单位是更新批次，不冒充全行等权统计。

## 审计与资源

- [summary.json](summary.json)：冻结时间、主分析范围、总量、契约、排除的历史策略与检查项。
- [checkpoint-audit.json](checkpoint-audit.json)：20份权重冷加载后的实际策略摘要、配置、参数量及种子祖先。
- [bc-input-checksums.json](bc-input-checksums.json)：28个原始BC文件与打包清单一致。
- [retained-trajectory-checksums.json](retained-trajectory-checksums.json)：核查时仍保留的24份轨迹，含当时第21轮；不是全部历史轨迹。
- [resources.json](resources.json)：独立采集时刻的ECS服务/资源、迁移logp验证、配额与本地空间；第21轮状态只用于资源说明，不并入主统计。
- [verification.json](verification.json)：独立核验器通过记录；314份冻结源文件验证，20份未随包权重明确列为引用，不冒充重新加载。

## 图表与复现

`figures/` 每图提供PNG和SVG：performance、seats、optimization、long_game、bc。热图颜色在比值2处截断，但标注值和CSV未截断。

报告生成脚本在仓库 `scripts/econ_training_report.py`，要求原实验资料仍在本地；它不重跑游戏、不训练。`scripts/plot_econ_training_report.py` 只读表格，使用matplotlib，可以对证据ZIP解包后的表格重新出图。

原始权重、NPZ、pkl数组和凭据不随报告入Git；完整ZIP放本地忽略目录 `archive/previews/econ-training-report-20261111.zip`。ZIP含冻结报告、优化日志及必要代码，附 `SHA256SUMS` 和独立核验脚本。
