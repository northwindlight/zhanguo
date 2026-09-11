# RL 下一阶段计划（2026-09-11 定）

> 这份是**跨会话存档点**：上下文压缩后、或新会话接手，先读这里。

## 已完成（P0）

- 目标函数 = **终局总消费**（`world.spend_total`）；`rl/` 里 env / model / ppo / bc 齐备。
- **老师 = `expand_rule_v9.py`**（= v8 + 视野门控，2026-09-11）。
  口径 = LLM 玩家面板的信息集（`World.visible_to`），堵掉四处越权偷看。
  ⚠️ v8 及以前的成绩（20 图 T500 2381k）是**带偷看**量的，不可与 v9 混用。
- **冻结词表 `rl/vocab.py`** + 两条一致性测试
  （`test_vocab_main_parity` / `test_branch_behavior_parity`）。
- **`rl/TOKEN_DESIGN.md`**：512-token 窗口布局（地图 patch + 实体 + 记忆 + 候选 cross-attn）。

## 约束（每一步都受它管，别忘）

| 事实 | 后果 |
|---|---|
| **训练机 = ECS**（阿里云，1 可用核 / 3.3 GiB / torch 2.14.0+cpu） | Win 主机**跑训练坏了**，已退出；见 `训练机器性能简报.md` |
| Pi 5：4 真核 / 8.8 GiB 可用 / **无 bf16** / 单核慢 5.9× | 只能做小规模或对照 |
| 现状一局 PPO 更新 ≈ **1 分钟 @ECS**；512-token/3M 方案 ≈ **42 分钟/局** | 一个月 ≈ 1000 局，可行但紧 |
| ECS 3.3 GiB | rollout 缓冲**必须存帧、不能存 token**（2.4 GB 装不下） |
| ECS 只有 1 可用核（SMT 对向量零收益） | "64 并行环境"在这台上不成立 |
| 简报推荐 bf16，但它自己的表里 bf16 比 fp32 慢 | **bf16 / 线程数都要在真实模型上量过再定** |

## 阶段

| | 内容 | 状态 |
|---|---|---|
| P0 | 冻结词表 + 一致性测试 + v9 老师 | ✅ |
| **P1** | **ECS 上跑通现有 BC（v9 老师）** | ← 现在 |
| P2 | `rl/tokenize.py`：只出 token 张量 + mask，配纯函数测试 | |
| P3 | tokenizer 接**现有的点积头**（不换网络），验证 BC 命中不掉 | |
| P4 | 换 Transformer 主干 + 候选 cross-attention + 记忆 | |
| P5 | PPO | |

### 为什么 P1 排在换架构之前

ECS 是**全新的训练机，一次都没训过**。先用**现有**管线跑一遍，拿两样东西：

1. **ECS 训练通路的验证 + 实测速度**（简报上的 tok/s 是 8.6 万参数玩具的，
   真实管线多快没人知道）；
2. **v9 老师的 BC 基线**（旧检查点是照**偷看的 v8** 训的，口径已经不对了）。

这两样不管架构怎么换都要用。反过来：**如果 ECS 跑不动，架构设计得再漂亮也没用。**

### P1 验收

- ECS 上 40 局跑完（20 纯 BC + 20 DAgger，`--turns 70`，`--teacher v9`）。
- 记录：**秒/局**、**真命中（扣 end_turn）训练/验证**、验证集是否在爬。
- 产物 `rl/runs/bc/v9_70.pt` 拉回 Pi。
- 判断：ECS 能不能承担后面的训练（拿秒/局 × 计划的局数对账）。

## 部署与运行

```bash
# 从 Pi 推代码（ECS 上 GitHub 不通，只能这么走）
rsync -a --no-perms --no-owner --no-group \
  --exclude '.venv/' --exclude 'rl/runs/' --exclude '__pycache__/' \
  --exclude 'mp_config*.json' --exclude 'mp_save*' --exclude 'mp_journal.md' \
  --exclude 'mp_map.txt' --exclude '结算报告.md' --exclude '.git/' \
  ~/projects/python/zhanguo/ northwind@ecs.northwind.site:~/zhanguo/

# 跑：**必须 tmux**（Windows 机上 ssh 会话一断进程就被带走，丢过一次）
ssh northwind@ecs.northwind.site \
  'cd ~/zhanguo && tmux new-session -d -s bc "~/.venv/bin/python -m rl.bc ... 2>&1 | tee rl/runs/bc/train.log"'
```

## 待定 / 未决

- **ps1 启动器指向一台不能跑训练的机器**（`train_bc.ps1` / `fix_bc.ps1`）——
  要不要改成 ECS 版（tmux/nohup）？还没定。
- 简报里的**主机名/公网 IP 被抹掉了**（仓库 public）——要不要写回去，未定。
- **记忆的实现方式**（GRU/SSM 隐状态 vs 固定窗口）——见 `TOKEN_DESIGN.md` 第 8 节。
- **仍然存在的观测层缺口**：`world.tiles` 是持久的，而 `rl/env._obs()` 用当前视野把它遮掉
  → 学生记不住走过的地方，老师记得。这是「记忆」最硬的理由，且**在观测层就能补**。
- **地图尺寸**：`train.py` / `compare.py` 都没接 `--map-sizes`（只有 `bc.py` 有），
  分布外测试要先把这条补上。
- **外交/战争**：本分支已删；标尺要对战就必须回到有战争的分支。
