# -*- coding: utf-8 -*-
"""把 `train.collect` 的**每一步**拆成阶段逐个计时 —— 回答「步进大头是什么」。

★★★★★ **2026-09-26 重要更正：下面那张表**误导了我自己一整天**，看之前先读这段。**
  它是**我手抄**的一份回路（`legal`/`obs_of`/`forward_state`/… 一句句重写），
  **抄漏了 `collect_episode` 里的东西**，而且**只量了一个配置的早期**。
  实测反例：照它把前向优化了 1.10×，再拿**整条真回路**做交替 A/B 一验 ——
  **只有 0.996×**（那 10% 在整条回路里根本看不见）。
  ⇒ 把表挂在**真函数**上（`rl/real_phase.py`）之后，同一个 `collect_episode`：

  | 配置 | 战斗 DP(`assess`) 占比 | 前向占比 |
  |---|---|---|
  | size12 / t_max 20 | 20.0% | 45.5% |
  | size12 / t_max 40 | 58.9% | 18.0% |
  | size8  / t_max 40 | **90.8%** | **2.9%** |

  **占比不是常数**：`combat_probs.assess` 是对参战单位组合的精确 DP，随交战规模
  **组合爆炸**；现役配置是 `--t-max 400` 的长局 ⇒ 长局里前向可以低到 3%，
  **真正的大头是战斗 DP**（`rl/dp_memo_probe.py` 量到一局 ~700~1300 次调用
  只有 ~100 个唯一签名 ⇒ 88~92% 是重复）。
  ⇒ **要判"大头是谁"，用 `rl/real_phase.py`（挂真函数），别用本文件。**

★ 本文件留着只为一件事：**同一配置下比较"某个改动前后"的相对变化**
  （它至少是自洽的）。**别再把它的比值当普遍规律。**

★★ 2026-09-26 实测（Tesla P4 那台免费机，cpu/1 线程，记忆臂 mem_slots=8）：

| 阶段 | size16 / 800 步 | size20 / 500 步 |
|---|---|---|
| **前向** | 5.27 ms（**56%**） | 6.66 ms（**48%**） |
| **观测编码** | 2.01 ms（21%） | 4.04 ms（29%） |
| legal（候选集） | 1.14 ms（12%） | 1.40 ms（10%） |
| 打分 Φ（`_score`） | 0.33 ms | 0.70 ms |
| 奖励（**又一次** Φ） | 0.32 ms | 0.68 ms |
| **引擎 `sb.step`** | **0.15 ms（1.6%）** | **0.17 ms（1.2%）** |
| 采样 / 视野 | 各 0.10 ms | 各 0.2 ms |
| 合计 | 9.41 ms/步 | 13.98 ms/步 |

⇒ **引擎本身只占 1~2%，前向才是大头**（问题是它是 **B=1 的小算子**：
   batch 恒为 1、每步一次同步 ⇒ 启动开销吃掉算力）。
★ 构成在各尺寸/各局深处**基本稳定**（开局 15 回合与第 29 回合一个样），
  所以别指望"局越长引擎越贵" —— 我原来就是这么猜的，量出来是错的。
★ `legal` 会随局推进变贵（0.69 → 1.14 ms，候选集大了）。
★ **同尺寸的 cuda 前向反而更慢**（6.86 vs 5.27 / 7.36 vs 6.66）——
  同一副工具、同一段代码、只换设备。★ 这条推翻了记忆里「前向占 84% 且上卡是对的」
  那个结论：那个 84% 的分解**漏了采样/视野/打分三个阶段**，而它的 cpu 前向 8.90ms
  是**在 8 个 worker 满载下**量的 ⇒ 拿它跟空载的 cuda 4.89ms 比，**基准不干净**。
★★ 教训：**"哪一步占大头"必须用同一副工具、同一时刻、逐阶段量**；
   端到端比 A/B 会被共享宿主机的噪声盖住（实测同一份 cpu 配置两轮跑出 46s vs 117s）。

用法：  python phase_bench.py [mem_slots] [size] [steps] [device]
        例：python phase_bench.py 8 20 500 cpu
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl.sandbox import Sandbox
from rl.encode import obs_of
from rl.train import collate, to_dev
from rl.model import build_model
from rl.train import _score, _reward

mem_slots = int(sys.argv[1]) if len(sys.argv) > 1 else 8
device = sys.argv[4] if len(sys.argv) > 4 else "cpu"
size = int(sys.argv[2]) if len(sys.argv) > 2 else 16
n_steps = int(sys.argv[3]) if len(sys.argv) > 3 else 300
t_max = 150

torch.manual_seed(0)
sb = Sandbox(seed=3, size=size, t_max=t_max, n_nations=3, halls_known=True,
             territory=True, alliances="random2v2").reset()
net = build_model(mem_slots=mem_slots).to(device).eval()
rng = np.random.default_rng(0)
print(f"局：size={size} t_max={t_max} 国=3 · 网 mem_slots={mem_slots} · 设备 {device} · 前 {n_steps} 步")

T = dict(legal=0.0, 观测obs=0.0, 视野=0.0, 前向fwd=0.0, 采样=0.0,
         打分_pre=0.0, 引擎step=0.0, 奖励_reward=0.0)
mem = None
n = 0
with torch.no_grad():
    while n < n_steps and not sb.is_terminal():
        me = sb.current_player()
        if me is None:
            break
        t = time.perf_counter(); actions = sb.legal(); T["legal"] += time.perf_counter() - t
        if not actions:
            break
        t = time.perf_counter()
        obs = obs_of(sb, me, actions)
        batch = collate([obs])
        T["观测obs"] += time.perf_counter() - t

        t = time.perf_counter(); sb.visible_enemies(me); T["视野"] += time.perf_counter() - t

        t = time.perf_counter()
        if mem is None:
            mem = net.mem_init(1, device=device)
        batch = to_dev(batch, device)
        # ★★ 必须与 `collect_episode` **逐字同口径**（2026-09-26：那边换成了
        #   `inference_mode`，实测 1.10×）—— 工具跟不上生产，读数就是假的。
        #   ★ `.clone()` 也照抄：它是"把槽带出 inference_mode"的那一步。
        with torch.inference_mode():
            logits, value, mem_out = net.forward_state(batch, mem)
        mem = mem_out.detach().clone()
        T["前向fwd"] += time.perf_counter() - t

        t = time.perf_counter()
        logits = logits[0]
        probs = torch.softmax(logits, -1).detach().cpu().numpy()
        aidx = int(rng.choice(len(probs), p=probs / probs.sum()))
        T["采样"] += time.perf_counter() - t

        t = time.perf_counter(); prev = _score(sb, me); T["打分_pre"] += time.perf_counter() - t
        t = time.perf_counter(); sb.step(actions[aidx]); T["引擎step"] += time.perf_counter() - t
        t = time.perf_counter(); _reward(sb, me, prev, sb.is_terminal()); T["奖励_reward"] += time.perf_counter() - t
        n += 1

tot = sum(T.values())
print(f"\n{'阶段':<14}{'ms/步':>9}{'占比':>8}")
for k, v in sorted(T.items(), key=lambda kv: -kv[1]):
    print(f"{k:<14}{v * 1000 / n:9.3f}{v / tot:8.1%}")
print(f"{'合计':<14}{tot * 1000 / n:9.3f}   （{n} 步，回合到 {sb.turn}）")
big = T["前向fwd"]
print(f"\n⇒ 非前向部分合计 {(tot - big) * 1000 / n:.3f} ms/步（{(tot - big) / tot:.1%}）")
print(f"⇒ 引擎+打分（引擎step + 打分_pre + 奖励_reward）= "
      f"{(T['引擎step'] + T['打分_pre'] + T['奖励_reward']) * 1000 / n:.3f} ms/步"
      f"（{(T['引擎step'] + T['打分_pre'] + T['奖励_reward']) / tot:.1%}）")
