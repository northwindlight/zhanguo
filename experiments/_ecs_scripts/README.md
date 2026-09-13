# ECS 侧 AI 留下的探针（2026-09-14 存档）

ECS 上那个 Claude 会话（`44ef2f51`）2026-09-14 因账号封禁中断。
它的探针与日志全部拉回此处存档 —— **它们是今晚一半结论的来源**。

| 脚本 | 干什么 |
|---|---|
| `probe_grad_noise.py` | ★McCandlish 梯度噪声尺度 `B_ep`（回答"要几局"） |
| `probe_exec_head_strat.py` | ★分层 AUC（全局/状态内/状态内同种类/**同候选跨状态**）+ 查表先验 |
| `probe_trunk_linear_exec.py` | ★在给定主干上**现拟合线性探针**，回答"辅助损失有没有塑造主干" |
| `probe_ratio_drift.py` | S0/S1：ratio 口径偏移 + 漂移方向对齐哪一项梯度 |
| `bootstrap_strat_auc.py` | 对 `strat_rows_*.npz` 做 bootstrap 95% CI |
| `verify_spend_rules_mismatch.py` | `spend_rules.py` 与引擎的三处口径不一致（可复现） |

日志在 `rl/runs/probe_ecs/`。
