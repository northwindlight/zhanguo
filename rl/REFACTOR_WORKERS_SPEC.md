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

## 6. 交付

- `--workers N`（默认 1）
- §5 七条验收的**证据**（md5、`nvidia-smi`/`uptime` 输出、diff 截图）
- 一段文档写清三件事：**合并顺序**、**RNG 归谁**、**不保证什么**（§3.2）
