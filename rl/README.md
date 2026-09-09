# 战国 · RL 训练版

本分支（`feat/rl`）在主线基础上做了两件事：

1. **移除国家间外交**——信件、馈赠、换图、间谍、共同防御、保障、联盟与投票、宣战与议和
   连同「外交中心」建筑一并删除。各国**永久中立**：不能互攻、不能结盟，
   扩张只能靠打野人（无主地块），地图上的野人是唯一敌人。
2. **加了 RL 训练层**（`rl/`）——用 PyTorch + PPO 训一个"只认总消费"的君主。

目标函数 = **终局总消费**（`world.spend_total`）= 累计建造 + 征兵 + 军费，按当时市价折金。
口径与主线计分板一致：只算真正被消耗掉的资源，市场买卖与囤货不计。

## 跑起来

```bash
.venv/bin/python -m pip install numpy torch        # aarch64 CPU 轮子，清华源即可
.venv/bin/python -m rl.train --map-size 16 --turns 30 --iterations 200
.venv/bin/python -m rl.train --eval-only --ckpt rl/runs/default/last.pt --episodes 10
```

产物在 `rl/runs/<run>/`：`log.csv`（逐轮指标）、`last.pt` / `model.pt`（checkpoint）、`config.json`。
`rl/runs/` 已在 `.gitignore` 中。

常用参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--map-size` | 16 | 地图边长（16 → 16×16） |
| `--turns` | 30 | 每局回合上限 |
| `--rivals` | `楚` | 脚本对手（逗号分隔，`dummy_turn` 代打） |
| `--max-actions` | 24 | 我方每回合动作上限 |
| `--iterations` / `--episodes` | 50 / 1 | 训练轮数 / 每轮采样局数 |
| `--lr` / `--epochs` / `--minibatch` | 3e-4 / 4 / 256 | PPO 超参 |
| `--reward-scale` | 0.01 | 奖励缩放（只影响数值尺度，不改最优策略） |
| `--threads` | 4 | torch CPU 线程数 |

## 环境（`rl/env.py`）

- **单智能体**：`agent`（默认秦）由策略控制，其余国家由 `mp_ai.dummy_turn` 脚本代打。
- **回合**：`begin_turn` → 我方逐步行动 → 脚本对手行动 → `resolve_turn` → 下一回合。
  动作数达上限或选 `end_turn` 即结束本回合。
- **观测**：`grid [C,H,W]` 地块通道（地形 one-hot、资源、归属 one-hot、各建筑数量、
  己方/边境/军队血量/在建等）+ `glob [G]` 全局向量（资源、市价与均衡价、总消费、
  建筑统计、军队统计、对手概况、回合进度）。通道名见 `env.obs_channels()`。
- **动作 = 每步枚举的合法动作清单**（candidate set）：`build / recruit / move / attack /
  retreat / buy / sell / end_turn`，每个候选带子项（建筑/兵种/物资）、地块、军队、数量档。
  清单由 env 按引擎规则枚举，**天然可行**，策略只在清单上做 softmax，
  不会出现"输出非法动作再修"的麻烦。数量离散为 `(1,2,3,4,5,6,8,10,12,16)`。
- **奖励 = 总消费增量**：`spend_total` 全期只增不减，Σ 每步增量 ≡ 终局总消费，
  所以这是**密集但不改目标**的奖励（不是塑形）；折扣率取 `gamma=1.0`，因为目标本身不折扣。

## 网络与算法（`rl/model.py` / `rl/ppo.py`）

- 网格用两层 3×3 卷积编码成逐格特征；全局向量过 MLP；军队过小 MLP。
- 候选动作按 `type ⊕ sub ⊕ 地块特征 ⊕ 军队特征 ⊕ 数量` 编码后与全局特征拼接打分，
  在候选集上 softmax —— 动作空间随局面变长，网络维度不依赖局面大小。
- PPO：裁剪目标 + GAE + 优势归一化 + 梯度裁剪 + 熵正则；每步候选集大小不同，
  minibatch 内按最大 K 补齐并 mask。

## 基线

`dummy_turn`（内置规则 AI：补能源 → 屯田 → 兵营 → 征兵 → 打最近野人）是天然基线。
训练日志里的 `spend_total` 可直接与脚本对手对照（`rivals` 用的就是它）。
