# -*- coding: utf-8 -*-
"""**战斗 DP 的内容签名重复率** —— 量出「按内容签名 memo」（PLAN §12.2 第 6 条之外的
另一条、也是**真正的大头**）值多少钱。**待用户拍**（任务 #15）。

★★★ 2026-09-26 为什么会有这个工具：用户让「先看看能不能优化掉前向里面低效的
   python 代码」。前向量完（`inference_mode` 1.10×），拿整条采集回路做交替 A/B
   一验 —— **只有 0.996×**。⇒ 前向那 10% 在整条回路里根本看不见
   ⇒ **手抄的那份逐阶段计时（`rl/phase_bench.py`）漏了大头**。
   把计时器**直接挂在真函数上**（`rl/real_phase.py` 那套做法）之后真相是：

   | 配置 | 战斗 DP(`assess`) 占比 | 前向占比 |
   |---|---|---|
   | size12 / t_max 20 | 20.0% | 45.5% |
   | size12 / t_max 40 | 58.9% | 18.0% |
   | size8  / t_max 40 | **90.8%**（149 ms/步） | **2.9%** |

   ⇒ **占比随"仗打多大"剧烈变化，而现役配置是 `--t-max 400` 的长局**
     ⇒ 战斗 DP 才是大头，前向在长局里可以低到 3%。

★ 本工具量的是**这条 memo 的上限**（不是实现）：
    `assess(b, retreat_units=...)` 是 `(b.order, b.init, retreat_units)` 的**纯函数**
    （`b.init` 含 hp），⇒ 同签名答案**逐位相同**，memo 是**精确**的不是近似。
    ★ 与 `snapshot` docstring 那条「**绝不跨回合缓存**」**不冲突**：那条禁的是
      「**按格子**缓存」（hp 变了答案就错）；这里 key 是**内容**（含 hp）。
      用户 2026-09-25 的原话就是「按内容签名 memo」。

实测（免费 GPU 机、cpu、1 线程、记忆臂）：

    size12 / t_max40: 1283 次调用 / **100 个唯一签名** ⇒ 重复 **92.2%**
    size8  / t_max40:  698 次调用 /  **84 个唯一签名** ⇒ 重复 **88.0%**

⇒ 一局里战斗 DP 被调 **~700~1300 次，而输入只有 ~100 种**。

用法：  python rl/dp_memo_probe.py [latent|none] [size] [t_max]
       例：python rl/dp_memo_probe.py latent 12 40
"""
"""**战斗 DP 的内容签名重复率** —— 直接量出「按内容签名 memo」值多少钱。

`assess(b, retreat_units=...)` 是 `(b.order, b.init, retreat_units)` 的**纯函数**
（`b.init` = 每方的 (兵种, hp, 撤退标记, 数量) 元组，**含 hp**）。
⇒ 同签名的两次调用**答案逐位相同**，memo 是**精确**的，不是近似。

★★ 注意：这与 `snapshot` docstring 里那条「**绝不跨回合缓存**」**不冲突** ——
  那条禁的是"**按格子**缓存"（hp 变了答案就错）。这里按**内容**（含 hp）取 key，
  内容一样答案必然一样。用户 2026-09-25 的原话正是「按内容签名 memo」。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import combat_probs as CB                # noqa: E402
from rl import train as T                        # noqa: E402
from rl.model import build_model                 # noqa: E402
from rl.sandbox import Sandbox                   # noqa: E402

mem = sys.argv[1] if len(sys.argv) > 1 else "latent"
size = int(sys.argv[2]) if len(sys.argv) > 2 else 12
t_max = int(sys.argv[3]) if len(sys.argv) > 3 else 40
torch.set_num_threads(1)
mem_slots = 8 if mem == "latent" else 0

seen: set = set()
n_call = 0
n_hit = 0
dp_time = 0.0
real = CB.assess


def spy(b, **kw):
    global n_call, n_hit, dp_time
    ru = kw.get("retreat_units")
    # ★ 镜像 `assess` 自己那行 `state0 = tuple(b.init[F] for F in b.order)`
    key = (tuple(b.order), tuple(b.init[F] for F in b.order),
           None if ru is None else frozenset(ru))
    n_call += 1
    if key in seen:
        n_hit += 1
        return            # ★ 命中就**不真算**（下面报的耗时是"省下多少"）
    seen.add(key)
    t = time.perf_counter()
    r = real(b, **kw)
    dp_time += time.perf_counter() - t
    return r


CB.assess = spy

sb = Sandbox(seed=11, size=size, t_max=t_max, n_nations=3, halls_known=True,
             territory=True, alliances="random2v2").reset()
torch.manual_seed(0)
nets = {p: build_model(mem_slots=mem_slots) for p in sb.players}
for n in nets.values():
    n.eval()
t0 = time.perf_counter()
with torch.inference_mode():
    steps, _info = T.collect_episode(nets, sb, rng=np.random.default_rng(0))
wall = time.perf_counter() - t0

print(f"\nmemory={mem} size={size} t_max={t_max} ⇒ {len(steps)} 步，"
      f"墙钟 {wall:.2f} s = {wall / len(steps) * 1000:.1f} ms/步\n")
print(f"assess 调用   {n_call}（{n_call / len(steps):.2f}/步）")
print(f"唯一签名      {len(seen)}")
print(f"**重复（memo 命中）** {n_hit} = **{n_hit / max(1, n_call):.1%}**")
print(f"真算掉的 DP 时间 {dp_time:.2f} s = 墙钟的 {dp_time / wall:.1%}")
# ★★ 算术要说清楚：上面那个 wall 是**已经跳过命中**量出来的
#   ⇒ 「没有 memo 的墙钟」= wall + dp_time（那 88~92% 本来要真算）
#   ⇒ 提速 = (wall + dp_time) / wall。★ 我第一版写成 wall/(wall-dp_time)，
#     那是**重复扣了一次**，会把提速报大（1.98× vs 真实的 1.50×）。
no_memo = wall + dp_time
print(f"⇒ 无 memo 的墙钟 ≈ {wall:.2f} + {dp_time:.2f} = **{no_memo:.2f} s**")
print(f"⇒ **memo 的提速 ≈ {no_memo / wall:.2f}×**"
      f"（DP 占无-memo 墙钟 {dp_time / no_memo:.1%}）")
