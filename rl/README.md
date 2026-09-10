# 战国 · RL 训练版

本分支（`feat/rl`）在主线基础上做了两件事：

1. **移除国家间外交**——信件、馈赠、换图、间谍、共同防御、保障、联盟与投票、宣战与议和
   连同「外交中心」建筑一并删除。各国**永久中立**：不能互攻、不能结盟，
   扩张只能靠打野人（无主地块），地图上的野人是唯一敌人。
2. **加了 RL 训练层**（`rl/`）——用 PyTorch + PPO 训一个"只认总消费"的君主。

目标函数 = **终局总消费**（`world.spend_total`）= 累计建造 + 征兵 + 军费，按当时市价折金。
口径与主线计分板一致：只算真正被消耗掉的资源，市场买卖与囤货不计。

## 设计：RL 只走游戏层

| 层 | 文件 | RL 用不用 |
|----|------|-----------|
| 游戏层 | `game.py`（数值表）+ `mp.py`（引擎 `World`）+ `rule_ai.py`（规则 AI） | **只用这层** |
| LLM 层 | `mp_ai.py`（toolcall schema + 文本面板 + 国策 plan）、`ctx.py`、`mp_run.py` | 不用 |
| RL 层 | `rl/`（env + 网络 + PPO） | — |

RL 环境不 import `mp_ai`：没有工具 schema、没有文本面板、没有国策（`plan` 是 LLM 层
`end_turn` 的前置条件，RL 的回合由 env 自己控制，不需要它）。动作直接调 `World` 的方法。

**单国独局**：地图上只有 `agent` 一国（默认秦），没有对手、没有脚本 AI——
先把单人学明白，再谈多国。

## 跑起来

```bash
.venv/bin/python -m pip install "torch==2.14.0+cpu" \
    --index-url https://download.pytorch.org/whl/cpu \
    --extra-index-url https://pypi.tuna.tsinghua.edu.cn/simple
.venv/bin/python -m rl.train --map-size 16 --turns 500 --iterations 200
.venv/bin/python -m rl.train --eval-only --ckpt rl/runs/single16/last.pt --episodes 3
```

> PyPI 上 aarch64 的 torch wheel 会把 CUDA 运行库写进依赖（2.14 起不再按 `platform_machine`
> 门控），在 Pi 上会拉几个 GB 的 cu13 包——所以走官方 **CPU 专用频道** `whl/cpu`，
> 那里有 `torch-2.14.0+cpu-…-aarch64.whl`，零 CUDA 依赖。

产物在 `rl/runs/<run>/`：`log.csv`（逐块指标）、`last.pt` / `model.pt`（checkpoint）、`config.json`。
`rl/runs/` 已在 `.gitignore` 中。

常用参数：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--map-size` | 16 | 地图边长（16 → 16×16） |
| `--turns` | 500 | **每局回合上限**——经济要滚复利，短局看不出名堂 |
| `--max-actions` | 24 | 每回合动作上限 |
| `--iterations` | 100 | PPO 更新块数（每块 `--rollout-steps` 步） |
| `--rollout-steps` | 2048 | 每次更新前采多少步（长局切成块训练） |
| `--minibatch` / `--epochs` | 512 / 3 | PPO 超参 |
| `--lam` | 0.99 | GAE λ（长局信用传播要拉长；目标本身不折扣） |
| `--eval-every` | 10 | 每多少块跑一次确定性评估（0 = 不评） |
| `--threads` | 4 | torch CPU 线程数 |

## 环境（`rl/env.py`）

- **回合**：`begin_turn` → 我方逐步行动 → `resolve_turn` → 下一回合。
  动作数达上限或选 `end_turn` 即结束本回合；一局 500 回合 ≈ 9k 步。
- **观测**：`grid [C,H,W]` 地块通道（地形 one-hot、资源、归属、各建筑数量、
  己方/边境/军队血量/在建/可见等）+ `glob [G]` 全局向量（资源、市价与均衡价、总消费、
  建筑统计、军队统计、回合进度）。通道名见 `env.obs_channels()`。
  **视野按引擎规则走**（`_vision_mask()` 复刻 `World.visible_to`：自家格 + 八邻 +
  瞭望塔半径 4 圆）：视野外的**地形/资源/归属/建筑/敌军一律置 0**，和 LLM 玩家看到的一样多
  （`mp_ai` 也用 `visible_to` 过滤军队）；自己的军队与地块始终可见。
  未占领格的资源本就未知（引擎在占地时才掷），故只在视野内显示已探明地块。
- **动作 = 每步枚举的合法动作清单**（candidate set）：`build / recruit / move / attack /
  retreat / buy / sell / end_turn`，每个候选带子项（建筑/兵种/物资）、地块、军队、数量档。
  清单由 env 按引擎规则枚举，**天然可行**，策略只在清单上做 softmax，
  不会出现"输出非法动作再修"的麻烦。数量离散为 `(1,2,3,4,5,6,8,10,12,16)`。
  **按类别限额**（build 64 / move 64 / attack 64 / recruit 32 / …）：帝国越大候选越多，
  不设上界会让打分开销随国力线性膨胀；限额 + **按回合轮转的起点**保证被裁掉的选项
  后续回合仍会被看到。
- **奖励 = 总消费增量**：`spend_total` 全期只增不减，Σ 每步增量 ≡ 终局总消费，
  所以这是**密集但不改目标**的奖励（不是塑形）；折扣率取 `gamma=1.0`，因为目标本身不折扣。

## 网络与算法（`rl/model.py` / `rl/ppo.py`）

- 网格用 1×1 瓶颈 + n 层 3×3 卷积编码成逐格特征；全局向量过 MLP；军队过小 MLP。
- 候选动作编码成 key，全局状态出一个 query，**点积打分**后 softmax
  —— 动作空间随局面变长，网络维度不依赖局面大小。
- PPO：裁剪目标 + GAE + 优势归一化 + 梯度裁剪 + 熵正则；每步候选集大小不同，
  minibatch 内按最大 K 补齐并 mask。

### 规模怎么定的（`rl/bench.py` 实测，别拍脑袋）

第一版打分头是 `[全局 ⊕ 候选] → MLP`，实测每步 K≈300 时它吃掉全部时间，
卷积规模几乎不影响耗时（tiny 3.0s vs wide 3.8s）。改成点积打分后：

| 配置 | 参数量 | ms/次 | 秒/轮（12 次前后向） |
|------|--------|-------|---------------------|
| tiny (1×1→24 + 3×3×1, 48) | 0.08M | 637 | 7.6 |
| **base (1×1→32 + 3×3×2, 64)** | **0.13M** | **1112** | **13.3** |
| wide (1×1→48 + 3×3×2, 96) | 0.21M | 1537 | 18.4 |
| deep (1×1→32 + 3×3×3, 64) | 0.17M | 1391 | 16.7 |
| plain (3×3×2, 64) | 0.15M | 1241 | 14.9 |

（`python3 -m rl.bench --map-size 16 --batch 256 --k 300`，Pi 5 4 线程。）
取 **base**：卷积规模此时才开始真正影响耗时，而 0.13M 参数的模型对这张 16×16 的地图够用。

## 基线参考

同一局（seed 0、200 回合、单国）：规则 AI（`rule_ai.py`）花掉 **6261**，
随机策略约 1500——会打与乱打差 4 倍，且规则 AI 的支出仍在线性增长（资源没花完，
说明还有空间）。这是训练要超过的下限。
