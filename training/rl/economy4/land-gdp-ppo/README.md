# 土地折算 GDP 的 2000 局 PPO

2026-11-23 用户确认：新增土地在持有期间，每回合产生训练专用 GDP；丢地则降低。游戏实际 GDP、价格、观测和规则不变。

设开局土地为 L0，结算后土地为 L，n=max(0,L-L0)。当 n 为 0、1、2、3、4、5 时，额外奖励分别为 0、10、15、18、20、21；之后每块再加 1。每回合训练奖励为「真实名义 GDP + 土地折算 GDP」，统一除以 3000 后进入 PPO。奖励比较当前持有量与开局量，反复丢失和收复同一块地不会提高奖励档位。

从工业军事 BC 第四轮开始，checkpoint SHA256 为 `e972957a0a2689b840da0c1f831cc8abb6eee75055544be52047bad3e00842bf`。四席共享同一策略快照、自主决策和市场，每次 PPO 更新汇总四席轨迹；没有脚本军事接管。

| 参数 | 本轮设置 |
| --- | --- |
| 训练规模 | 恰好 2000 个四国世界，共 8000 条国家轨迹 |
| 地图、时长 | 24×24，100 回合 |
| 学习率、数值精度 | 5e-4，FP32 |
| 折扣、GAE λ | 1.0、0.99，按游戏回合计算 |
| 每批采样 | 最多 8 个世界；双 T4 分别采不同地图 |
| 每批更新 | minibatch 32，随机抽取 25% 决策，最多 384 次更新 |
| 其他奖励和约束 | BC/KL 锚、执行辅助、记忆辅助、无效动作惩罚和熵奖励均为 0 |
| 每段 | 200 个世界后退出，下载校验后继续下一段 |
| 最终回测 | 新的 24 张同种子地图，分别运行初始 BC 和最终 PPO，各 100 回合 |

价值头随 PPO 一起更新。双卡用于并行采样；当前更新器在单卡上训练同一个策略。原始大轨迹写入临时目录，以 mmap 读取，更新成功后删除；每批保留统计、采样日志、更新日志和新检查点。

## 自动运行与恢复

私有 Kaggle 任务：`northwindlight/zhanguo-land-gdp-ppo`。本机用户服务 `zhanguo-land-gdp-ppo-watch.service` 执行项目根目录的 `watch_land_gdp_ppo.py`，负责十段提交、进度跟踪、下载及 SHA256 校验、状态数据集更新。每个远端任务前台完成有限工作后退出，释放 GPU。

```bash
systemctl --user status zhanguo-land-gdp-ppo-watch.service --no-pager
cat land-gdp-ppo-results/live.json
tail -n 8 land-gdp-ppo-results/run-001-live.log
```

`live.json` 的 `last_completed_batch.games` 是远端已完成更新的局数；`segments/NNN/verified.json` 是已下载并核实的局数。刚启动时可能只有远端状态，尚无完整批次。日志跟随器重连会重播旧日志，不表示训练重跑。

每批检查点使用独立文件名，先校验有限数值并发布状态，再清理上一批权重。每段的新检查点和 native 模型下载核实后，才提交下一段。下一段同时核对期望局数与输入 SHA，防止 Kaggle 误挂旧数据集版本。数值或训练错误会记录 `failure.json` 并停止自动续跑，避免盲目更改训练条件。

本机服务中断后，可用同一 Python 命令恢复；提交标记避免重复提交已在运行的分段。按需恢复服务：

```bash
systemd-run --user --unit=zhanguo-land-gdp-ppo-watch --collect \
  --property=Restart=on-failure --property=RestartSec=45 \
  --property=StandardOutput=append:/home/northwind/zhanguo-training-20261121/economy4/land-gdp-ppo-results/watch.log \
  --property=StandardError=inherit \
  /usr/bin/python3 -u /home/northwind/zhanguo-training-20261121/economy4/watch_land_gdp_ppo.py
```

运行环境和配置存于 `launch.json`、`run-config.json`。完成后本机生成 `land-gdp-ppo-results/verified.json`、`done.json`、最终对照 `segments/010/work/evaluation.json` 和 ZIP 交付包。只有这些标记通过校验，才算训练及交付完成。

## 已做检查

本机验证了奖励阶梯、丢地回退、开局土地零奖励、真实 GDP 不变、完整四席轨迹对齐、mmap 与原读取结果一致、非零土地奖励进入 PPO、FP32 更新有限数值、灭国后 GDP 归零。记录在 `land-gdp-ppo-results/verification.json`；小规模测试通过不代表模型已经学会扩张，最终表现以独立回测为准。

## 11 月 24 日恢复记录

首批 8 局的 384 个 PPO minibatch 已完成，随后进度日志的重复 games 字段触发异常。已修复字段合并并增加连续两批、部分分段恢复和最终回测停训检查。从首批检查点及完整优化器状态恢复，SHA256 为 `f9aa47b00564d2b4bb52921f3c2d5cabf6d48f2c55f3d443b3ed27342e0a78c5`；没有重跑或丢弃这 8 局。首段补至 200 局，随后按 200 局边界续跑。用户将本轮目标提高到 2000 局，其余参数保持原值。旧失败任务及恢复证据保存在 `land-gdp-ppo-results/failed-001/`。

`military-health.json` 将最近 64 局与之前 64 局比较，报告征兵席位比例、进攻席位比例、拿地席位比例、实际 GDP、土地奖励、裁军和饿死。满窗口后，未拿地或征兵比例降到前一窗口一半以下会标记；这用于审查，不会自行修改奖励或停训。小样本不据此下退化结论。日志重放按批次去重，累计局数与单批局数分开统计。
