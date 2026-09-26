# -*- coding: utf-8 -*-
"""**真·逐阶段**：给 `collect_episode` **本尊**里的函数挂计时器 —— 取代手抄件。

★★★ 为什么必须有这个（2026-09-26，一次真实的"工具骗人"）：
  用户让「先看看能不能优化掉前向里面低效的 python 代码」。我用
  `rl/phase_bench.py`（**我手抄的**回路计时件）得出「前向占 48~56%、引擎 1~2%」，
  照着把前向优化了 1.10× —— 然后拿**整条采集回路**做交替 A/B 一验：
  **只有 0.996×**。⇒ 前向那 10% 在整条回路里根本看不见。
  ⇒ 把计时器**直接挂在真函数上**，真相是（同一台机、同一副工具）：

  | 配置 | 战斗 DP(`assess`) | 前向 | 引擎 |
  |---|---|---|---|
  | size12 / t_max 20 | 20.0% | 45.5% | 1.3% |
  | size12 / t_max 40 | 58.9% | 18.0% | 0.5% |
  | size8  / t_max 40 | **90.8%**（149 ms/步） | **2.9%** | 0.1% |

  ★ **占比不是常数，它随"仗打多大"剧烈变化** —— 因为 `combat_probs.assess`
    是对**参战单位组合**的精确 DP，状态空间随交战规模**组合爆炸**。
    小图 3 国挤在一起 ⇒ 大战 ⇒ DP 吃掉一切；现役配置 `--t-max 400` 是长局
    ⇒ **长局里前向可以低到 3%，而战斗 DP 才是大头**。
  ⇒ 手抄件的错在于**只量了一个配置的早期**，然后把那个比值当成了普遍规律。

★ 口径：不抄任何逻辑 —— `wrap()` 把表挂在 `combat_probs.frame_odds/assess/
  retreat_odds`、`encode.obs_of`、`train._score/collate`、`net.forward_state`、
  `sb.step/legal` 上，然后跑**真的** `collect_episode`。

用法：  python rl/real_phase.py [latent|none] [size] [t_max]
       例：python rl/real_phase.py latent 12 40
"""
"""**真·逐阶段**：直接给 `collect_episode` 本尊里的函数挂计时器。

★ 为什么不用 `rl/phase_bench.py`：那份是我**手抄**的回路，它报「前向 48~56%、
  观测 2 ms」。但整条回路的交替 A/B 量出 inference_mode **只有 0.996×**
  （前向那 10% 在整条回路里根本看不见）⇒ **手抄件漏了大头**。
  这里不抄 —— 给真函数挂表，跑真 `collect_episode`。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import combat_probs as CB                # noqa: E402
from rl import encode as E                       # noqa: E402
from rl import train as T                        # noqa: E402
from rl.model import build_model                 # noqa: E402
from rl.sandbox import Sandbox                   # noqa: E402

mem = sys.argv[1] if len(sys.argv) > 1 else "latent"
size = int(sys.argv[2]) if len(sys.argv) > 2 else 12
t_max = int(sys.argv[3]) if len(sys.argv) > 3 else 20
torch.set_num_threads(1)
mem_slots = 8 if mem == "latent" else 0

ACC: dict[str, float] = {}
CALLS: dict[str, int] = {}


def wrap(mod, name, label):
    real = getattr(mod, name)

    def timed(*a, **k):
        t = time.perf_counter()
        try:
            return real(*a, **k)
        finally:
            ACC[label] = ACC.get(label, 0.0) + (time.perf_counter() - t)
            CALLS[label] = CALLS.get(label, 0) + 1
    setattr(mod, name, timed)


wrap(CB, "frame_odds", "战斗明细 frame_odds")
wrap(CB, "assess", "  ├ assess(DP)")
wrap(CB, "retreat_odds", "  ├ retreat_odds")
wrap(CB, "assess_with_reinforcements", "  ├ assess_with_reinf")
wrap(E, "obs_of", "观测 obs_of(总)")
wrap(T, "_score", "打分 Φ (_score)")
wrap(T, "collate", "collate")
_real_fs = None


def run():
    sb = Sandbox(seed=11, size=size, t_max=t_max, n_nations=3, halls_known=True,
                 territory=True, alliances="random2v2").reset()
    torch.manual_seed(0)
    nets = {p: build_model(mem_slots=mem_slots) for p in sb.players}
    for n in nets.values():
        n.eval()
    # 前向也挂表
    for n in nets.values():
        real = n.forward_state

        def fs(batch, m=None, _r=real):
            t = time.perf_counter()
            try:
                return _r(batch, m)
            finally:
                ACC["前向 forward_state"] = ACC.get("前向 forward_state", 0.0) \
                    + (time.perf_counter() - t)
                CALLS["前向 forward_state"] = CALLS.get("前向 forward_state", 0) + 1
        n.forward_state = fs
    real_step = sb.step

    def step(a):
        t = time.perf_counter()
        try:
            return real_step(a)
        finally:
            ACC["引擎 sb.step"] = ACC.get("引擎 sb.step", 0.0) \
                + (time.perf_counter() - t)
            CALLS["引擎 sb.step"] = CALLS.get("引擎 sb.step", 0) + 1
    sb.step = step
    real_legal = sb.legal

    def legal():
        t = time.perf_counter()
        try:
            return real_legal()
        finally:
            ACC["legal"] = ACC.get("legal", 0.0) + (time.perf_counter() - t)
            CALLS["legal"] = CALLS.get("legal", 0) + 1
    sb.legal = legal

    t0 = time.perf_counter()
    with torch.inference_mode():
        steps, _info = T.collect_episode(nets, sb, rng=np.random.default_rng(0))
    return time.perf_counter() - t0, len(steps)


wall, n = run()
print(f"\nmemory={mem} size={size} t_max={t_max} ⇒ {n} 步，"
      f"墙钟 {wall:.2f} s = **{wall / n * 1000:.1f} ms/步**\n")
print(f"{'阶段':<26}{'ms/步':>9}{'占墙钟':>9}{'次数':>9}")
for k, v in sorted(ACC.items(), key=lambda kv: -kv[1]):
    print(f"{k:<26}{v / n * 1000:9.2f}{v / wall:9.1%}{CALLS[k]:9d}")
rest = wall - ACC.get("观测 obs_of(总)", 0.0) - ACC.get("前向 forward_state", 0.0) \
    - ACC.get("打分 Φ (_score)", 0.0) - ACC.get("引擎 sb.step", 0.0) \
    - ACC.get("legal", 0.0)
print(f"{'（其余/采样/遍历）':<26}{rest / n * 1000:9.2f}{rest / wall:9.1%}")
