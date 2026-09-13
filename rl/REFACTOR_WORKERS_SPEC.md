# 规格：rollout 并行化（`--workers N`）

> 下发给重构方。**只讲要做出什么功能、不许碰什么**。
> 机器不同，所以**不设"比现在快 X 倍"的指标** —— 只列可观测的功能事实。

## 0. 一句话

把**训练时的 rollout 收集**从「单进程逐步」改成「N 个 worker 进程并行」。

**只动收集。其余一律不动。**

## 1. 范围

### 要改

- `rl/train.py` 的采样循环（`collect_episode` 的调用点及其周边）

### 不许改（改了算越界）

| # | 不许动 | 为什么 |
|---|---|---|
| 1 | `rl/env.py` | 观测/规则/动作语义，一个字都不许动 |
| 2 | 仓库根目录的引擎（`mp.py` 等） | `TestEngineIsMainByteForByte` 盯着，必须与 main **逐字相同** |
| 3 | `rl/ppo.py` 的 `policy_logits` / `act` / `PPO.update` / `Rollout` **语义** | 训练数学不能变 |
| 4 | `experiments/probe_validity.py`、`experiments/probe_exec_head.py`、`evaluate()`、`--eval-only` | **它们是量具**（见 §3.4） |
| 5 | 现有 ckpt 的可加载性（`strict=False` 的既有约定） | 续训链不能断 |

## 2. 目标（**功能**，不是性能倍数）

1. **新增 `--workers N`，默认 1。**
2. **N=1 时逐位不变**（验收见 §4）。
3. **N>1 时必须真的并行** —— 可观测判据（不是倍数）：
   - 收集阶段 **CPU 占用 > 1 个核**（现状 `load average ≈ 1.0`，10 核机器闲着 9 个）
   - **GPU 利用率显著高于现状的 10%**
4. **`PPO.update` 与 `Rollout` 的接口不变** —— `update()` 不该知道 worker 的存在。
5. **吞吐可观测**：日志里能读到每块的 `env_steps/sec`（现在只有累计 `secs`）。

## 3. 正确性契约（最关键的一节）

### 3.1 ★N=1 必须逐位相同

**做法**：`if workers == 1:` 走**原来那段代码，一个字不改**。
不是"重写一版行为等价的" —— 是**物理上同一段代码**。这是全项目的纪律：
新开关不得改变既有路径。

### 3.2 ★为什么不能指望 N>1 与 N=1 一致（先读，别当 bug 修）

现在的确定性是**逐位**的（实测：同机同权重跑两遍输出逐位相同）。
它建立在两条 RNG 事实上：

- **训练期**：`torch.manual_seed(args.seed)` 只在开头调**一次**（`train.py:268`），
  之后每局只 `env.reset(_s)`、**不重置 torch RNG**
  ⇒ 动作采样吃的是一条**跨局连续的全局流**。
- **探针期**：`probe_validity.py` **逐局** `torch.manual_seed(2000+ep)`
  ⇒ 这才是探针能逐位复现的原因。

开 N 个进程 = N 条流，交错顺序必然变 ⇒ **N>1 与 N=1 轨迹不可比，这是预期不是缺陷**。
⇒ 推论：**N>1 跑出的 ckpt 不能与任何历史 ckpt 比数值**。

**这条要写进文档，别让人以为是回归。**

### 3.3 N>1 必须满足的四条

1. **采样（`torch.multinomial`）在 worker 本地做，用 worker 自己的 torch RNG**；
   中央进程**只做前向**（前向不消耗 RNG）。
2. **每个 worker 有自己的 `ZhanguoEnv` 实例**；`env.reset(map_seed)` 的
   **episode → map seed 映射必须与 N=1 一致**（地图池 `_take_seed()` 那条链不能乱）。
3. **合并顺序必须确定，并写进注释**。建议按 `(worker_id, 该 worker 内的 episode 序号)` 排。
   ★**这条不是形式主义**：`Rollout._scale()`（`rl/ppo.py`）是 **running RMS，
   按 `add()` 的调用顺序更新** —— 顺序变了，奖励归一化的历史就变了，
   **训练信号就不同**。（GAE 那边倒是安全的：`gae()` 在 `done` 处 `nonterm=0` 断开，
   局与局独立，所以局的先后不影响优势。）
4. **`pool_i` 照旧存进 ckpt**（`rl/train.py` 的 blob），续训不能重复练前几张图。

### 3.4 ★量具一律不动

`probe_validity.py` / `probe_exec_head.py` / `evaluate()` / `--eval-only`
**必须继续走原来的单 env 顺序路径**。

理由（2026-09-13 刚踩过）：同一份 ckpt 在 `torch 2.14/py3.13` 与 `torch 2.4.1/py3.10`
上测出的撞墙率是 33.6% vs 39.6%，**连逐局步数都不同** —— 跨软件栈就不可比了。
换量具比换软件栈更狠：**等于把之前所有基线一笔勾销**。

## 4. 实施建议（哪种形状）

**先做简单的那个：N 个进程，每个进程一份模型副本在 GPU 上，各跑各的 env 循环。**

- 不用 IPC、不用中央推理服务。GPU 现在只用 10%、模型只有 2.11M 参数（fp32 ≈ 8.4MB），
  12GB 显存放 N 份绰绰有余（N 到 10 都没问题）。
- 每个 worker 的 batch=1 前向还是 5.89ms，但**N 个 worker 的前向可以在 GPU 上重叠**
  （GPU 有 90% 空闲），于是 wall-clock 是并行的。
- **PPO 更新完之后要把新权重广播给所有 worker**（这是唯一的同步点）。

**先别做的**：中央批量推理服务（把 N 个 worker 的 obs 攒成 batch=32）。
理论上前向能省 8.7×（实测 batch=1 = 5.89ms，batch=32 = 21.64ms = 0.68ms/样本），
但要处理 IPC 和 token 窗口的序列化，复杂度高一个量级。
**等简单版跑通、量到瓶颈还在前向上，再考虑。**

**已经排除的**：多线程。`ZhanguoEnv` 是纯 Python，GIL 让线程跑 CPU-bound Python 根本不并行。
torch 自己的线程也已经证明没用（`train.py` 采样期强制 `torch.set_num_threads(1)`，
注释记着：4 线程 34.8ms/步 vs 单线程 7.7ms/步）。

## 5. 验收清单

| # | 验收项 | 判据 |
|---|---|---|
| 1 | **N=1 逐位不变** | 重构**前后**、**同一台机器**、同参跑小任务，产出 ckpt **md5 相同** |
| 2 | 默认值 | 不传 `--workers` ⇒ 1，且走的是**原来那段代码** |
| 3 | N=4 能跑通 | `--turns 20 --iterations 2 --rollout-episodes 2` 跑完不崩，`Rollout` 结构正确 |
| 4 | 真并行 | 收集阶段 CPU > 1 核；GPU 利用率 > 10%（贴 `nvidia-smi` 与 `uptime` 证据） |
| 5 | 量具未动 | `git diff` 里 `experiments/probe_validity.py`、`experiments/probe_exec_head.py` **零改动** |
| 6 | 引擎未动 | `TestEngineIsMainByteForByte` 仍绿 |
| 7 | ckpt 兼容 | 旧 ckpt 仍能 `--resume` 载入 |

**验收 1 的命令**（`--device cpu` 是为了避开 GPU 非确定性；
两台机器跑不出同一个 md5 没关系，**同一台机器重构前后**必须相同）：

```
python -m rl.train --net tf --turns 20 --iterations 2 --rollout-episodes 2 \
  --seed 12345 --device cpu --threads 1 --exec-head 0.1 --rules-jitter 0.1 \
  --resume rl/runs/bc_cont/ep100.pt --out <dir>
```

## 5b. ★并行下的 `--teacher-baseline`：**禁用**（报错退出）

**结论：`--workers > 1` 且 `--teacher-baseline` ⇒ 启动时报错退出，要基准就 `--workers 1`。**

```python
if args.workers > 1 and args.teacher_baseline:
    print("[错误] --teacher-baseline 不支持并行模式：它是被 --map-pool 淘汰的旧方案"
          "（PLAN §R），且 +8~16 s/局与并行目标直接冲突。要基准请用 --workers 1。",
          file=sys.stderr)
    sys.exit(2)
```

**★必须报错退出，不能静默忽略** —— 静默忽略会让人以为基准生效了而实际没有，
那是最难查的一类错。

### 为什么是禁用，而不是"同步跑"或"worker 内开线程"

**1. 它是被淘汰的方案，不是"暂时没用"。**
PLAN §R 那张表：地图难度方差有两条路 ——

| 方案 | 成本 | 结论 |
|---|---|---|
| **事前筛图（`--map-pool`）** | **一次性 45 秒** | ★**选它**：难度根本不出现，训练时也不用跑老师 |
| 每局跑老师基准（`--teacher-baseline`） | 每局 **+8~16 s（永久）** | 事后校正；还跟学生抢 GIL。**代码已就位，未启用** |

**两条路解决同一个问题，我们选了便宜的那条**，`rl/maps/medium_pool.json`（251 张）
已进仓库。⇒ 不是"还没轮到用"，是**已经用另一个方案解决了**。

**2. 证据：从来没启用过。** 本地 3 个 + 机房 5 个 run 的 `config.json` 全是
`teacher_baseline: false`；PLAN 两处明写「代码已就位，**未启用**」。

**3. 代价与并行化的目标直接冲突。** +8~16 s/局，而我们实测 **~30 s/局**
（200 回合）⇒ 开着它等于 **+27~53% 墙钟**。开并行的全部意义就是省墙钟。

**4. 它是"量具"性质。** 算的是"该图的标准答案"，用于**事后校正/对照**
⇒ 归 §3.4「量具一律不动、走串行」。

### 两个常见误解（澄清，免得再走一遍）

**「开局同步跑」与串行语义**不等价**。** 串行时基准跑在**后台线程**、
藏在学生 30~48 s 的采样里（抢 GIL，但部分能藏）；改成开局同步等就是
**实打实 +8 s/局**。而且 N 个 worker 同时开局会一起卡，形成**同步屏障**。

**「worker 内后台线程」的复杂度取决于架构 —— 可能根本不存在。**
若 worker 跑的是**完整的一局循环**（各持模型副本，即 §4 推荐的简单版），
那么 `_spawn_baseline`/`_settle_baseline`/`_bl` 那套**原样就是对的**：
它是**进程内**状态，主进程根本不需要知道它的存在。
只有选了"worker 送 obs、中央批量前向"那条路才需要拆。
即便如此**仍推荐禁用** —— 上面的理由 1、3、4 与架构无关。

**旁证（可核实）**：`teacher_baseline` 用的是 `random.Random(0xB4BE)`，
**固定本地 RNG、不碰 torch 全局 RNG** ⇒ 不影响采样流。
所以"RNG 会乱"不是理由；**墙钟才是**。

### 附带建议（**不在本次范围**）

`--teacher-baseline` / `--baseline-turns` / `_spawn_baseline` / `_settle_baseline` /
`teacher_baseline()` 已被 §R 淘汰，**建议删掉**。
但**别混进并行化这个 commit** —— 并行化要保持「只动收集」的边界，
混进去就没法干净回滚了。

## 6. 交付

- `--workers N`（默认 1）
- §5 七条验收的**证据**（md5、`nvidia-smi`/`uptime` 输出、diff 截图）
- 一段文档写清三件事：**合并顺序**、**RNG 归谁**、**不保证什么**（§3.2）
