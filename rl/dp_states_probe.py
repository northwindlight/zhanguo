# -*- coding: utf-8 -*-
"""**战斗 DP 到底跑多大** —— `assess` 的状态数、截断量、**以及耗时的分布**。

    python rl/dp_states_probe.py [局数] [size_min] [size_max] [t_max]

★ 背景（`rl/turn_phase.py` 量到的）：1v1 / t_max 100 时单个决策点的开销从
  第 1-10 回合的 11.9 ms 涨到 91-100 回合的 64.3 ms，**涨幅全在战斗 DP**
  （assess 0.73 → 43.60 ms/点），而前向几乎是平的（7.83 → 11.18）。

★★★ 两轮实测的结论（2026-09-27，别重复走）：

  **① 截断：没有。** `truncated` 最大 **6.66e-16**，>1e-6 的占 **0.0%**
     ⇒ DP 是**精确**的，"精度在偷偷降"这个担心**不成立**，撤回。
     （第一版探针报「1-10 段 50% 截断」是**我判据坏**：`1.0 - sum(概率)` 留
     1e-16 浮点渣，我拿 `> 0` 卡它 ⇒ 全是假阳性。判据必须卡**量级**。）

  **② 状态上限：从没撞到。** `n_states` 最大 **3602**，上限 60000，
     顶到上限 **0%** ⇒ `ASSESS_MAX_STATES` **不是**提速旋钮。

  **③ 调用次数爆炸。** 后 60 个回合占掉全部 assess 调用的 **94%**
     （19677/20868）—— 早期每 10 回合只有 ~100 次，41 回合往后每 10 回合 2000~3900 次。
     `frame_odds` 是对**每个交战格**调一次 ⇒ 战线铺开 = 调用数爆炸。

  **④ 单次耗时是重尾，中位数骗人。** 每 10 回合的 `ms/次` **中位**全程平在
     0.84~1.81 ms —— 但 `turn_phase` 的**均值**是 ~19.5 ms/次。**差 12 倍**，
     全在尾部：少数几千个状态的调用吃掉大部分时间。
     ⇒ 优化"每次调用的固定开销"没用；要动的是**大仗的状态数**。

★ 判据：看 `按 n_states 分档` 那张表 —— 每档的**总耗时占比**。

★★★ 2026-09-27 实测（4 局 size 13/14，全打满 100 回合，**全部平局**）：

  **战斗 DP 占总墙钟 58.8%**（375.9 s / 639 s）—— 与 `turn_phase.py` 独立量出的
  56% 吻合 ⇒ **两个探针互相印证**（这条就是单位 bug 的验证：修好后 4 局的
  assess 占比 74.6/48.8/48.3/44.9% 直接对得上）。

  耗时**不在调用次数上，在状态数上**：

  | n_states 档 | 调用数 | 占调用 | ms/次均值 | 总秒 | **占总 DP 时间** |
  |---|---|---|---|---|---|
  | 1 | 8099 | 38.8% | 0.67 | 5.4 | **1.4%** |
  | 2-10 | 990 | 4.7% | 1.18 | 1.2 | **0.3%** |
  | 11-100 | 5803 | 27.8% | 8.70 | 50.5 | **13.4%** |
  | 101-1000 | 3069 | 14.7% | 66.41 | 203.8 | **54.2%** |
  | >1000 | 176 | 0.8% | 653.32 | 115.0 | **30.6%** |

  ⇒ **>100 状态的调用只占 15.5% 的次数，却吃掉 85% 的 DP 时间**
    （= 全部墙钟的 **50%**）。而 **43.5% 的调用（≤10 状态）只值 1.7%**。
  ⇒ **优化"每次调用的固定开销"是白干。**
  ★ 尾部极重：`ms/次` 中位 1.70 / p90 31.27 / p99 216 / **均值 18.02** / 最大 **3590**
    （**单次 3.6 秒**）；最贵 1%（208 次）吃 33%，最贵 10% 吃 **80%**。
    **只看中位数就会得出"单次很便宜"的错误结论**（我第一版就是这么错的）。
  ★ 时间 ≈ `0.65 ms + 0.33 ms × n_states`（1 状态的调用 0.67 ms、>1000 档
    653 ms/次）⇒ **固定开销可忽略，状态数就是全部**。
"""
import sys
import time

import numpy as np
import torch

sys.path.insert(0, ".")
from rl import combat_probs as CB                # noqa: E402
from rl import train as T                        # noqa: E402
from rl.model import build_model                  # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

NG = int(sys.argv[1]) if len(sys.argv) > 1 else 4
SMIN = int(sys.argv[2]) if len(sys.argv) > 2 else 13
SMAX = int(sys.argv[3]) if len(sys.argv) > 3 else 14
TMAX = int(sys.argv[4]) if len(sys.argv) > 4 else 100
BAND = 10
torch.set_num_threads(1)      # ★ Pi 是用户干活的机器，只占一个核
CAP = 60000

NS: dict[int, list] = {}       # 回合 -> [n_states, ...]
TR: dict[int, list] = {}       # 回合 -> [truncated, ...]
MS: dict[int, list] = {}       # 回合 -> [ms, ...]
FLAT: list[tuple[int, int, float]] = []   # (n_states, 回合, ms) 全局，做分档/分位用
games = []

for gi in range(NG):
    size = SMIN + (gi % (SMAX - SMIN + 1)) if SMAX > SMIN else SMIN
    sb = Sandbox(seed=9500 + gi, size=size, t_max=TMAX, n_nations=2,
                 halls_known=True, territory=True,
                 alliances="random2v2").reset()
    torch.manual_seed(gi)
    nets = {p: build_model(mem_slots=8) for p in sb.players}
    for n in nets.values():
        n.eval()
    real_assess = CB.assess
    acc = {"s": 0.0, "n": 0}

    def assess(*a, **k):
        t = time.perf_counter()
        try:
            o = real_assess(*a, **k)
        finally:
            dt = (time.perf_counter() - t) * 1000      # ms
            acc["s"] += dt / 1000.0                    # ★ 秒。第一版累的是 ms
            acc["n"] += 1                              #   却按秒印 ⇒ 报出 "74564%"
        NS.setdefault(sb.turn, []).append(o.n_states)
        TR.setdefault(sb.turn, []).append(o.truncated)
        MS.setdefault(sb.turn, []).append(dt)
        FLAT.append((o.n_states, sb.turn, dt))
        return o
    CB.assess = assess
    t0 = time.perf_counter()
    with torch.inference_mode():
        _steps, info = T.collect_episode(nets, sb, rng=np.random.default_rng(gi))
    wall = time.perf_counter() - t0
    CB.assess = real_assess
    games.append((size, info["turns"], wall, acc["s"], acc["n"]))
    print(f"  局 {gi + 1}/{NG} size={size} 回合 {info['turns']} "
          f"墙钟 {wall:.1f}s（其中 assess {acc['s']:.1f}s = {acc['s'] / wall:.0%}）"
          f" 胜方 {info.get('winner') or '平'}", flush=True)

alln = [n for n, _t, _m in FLAT]
allm = [m for _n, _t, m in FLAT]
allt = [x for v in TR.values() for x in v]
tw = sum(g[2] for g in games)
ts = sum(g[3] for g in games)
print(f"\nassess 共 **{len(alln)} 次**，总 {ts:.0f}s / 全部墙钟 {tw:.0f}s = **{ts / tw:.0%}**")
print(f"n_states：中位 {np.median(alln):.0f} / p90 {np.percentile(alln, 90):.0f} / "
      f"p99 {np.percentile(alln, 99):.0f} / 最大 {max(alln)}（上限 {CAP}，"
      f"顶到上限 {np.mean([n >= CAP for n in alln]):.0%}）")
print(f"truncated：中位 {np.median(allt):.2e} / 最大 {max(allt):.2e}"
      f"（>1e-6 占 {np.mean([x > 1e-6 for x in allt]):.2%}）")
print(f"ms/次：中位 {np.median(allm):.2f} / p90 {np.percentile(allm, 90):.2f} / "
      f"p99 {np.percentile(allm, 99):.2f} / **均值 {np.mean(allm):.2f}** / 最大 {max(allm):.0f}")

# ★ 集中度：最贵的 1% 调用吃掉多少时间
o = np.sort(allm)[::-1]
k = max(1, len(o) // 100)
print(f"★ 最贵 1%（{k} 次）吃掉 **{o[:k].sum() / o.sum():.0%}** 的 assess 时间；"
      f"最贵 10% 吃掉 {o[:len(o) // 10].sum() / o.sum():.0%}")

print(f"\n{'回合':>7}{'调用':>7}{'ms/次中位':>11}{'ms/次均值':>11}"
      f"{'n_states中位':>13}{'n_states最高':>13}{'该段占总时间':>13}")
for lo in range(1, TMAX + 1, BAND):
    hi = lo + BAND - 1
    ms = [x for k2 in range(lo, hi + 1) for x in MS.get(k2, [])]
    if not ms:
        continue
    ns = [n for k2 in range(lo, hi + 1) for n in NS.get(k2, [])]
    print(f"{f'{lo}-{hi}':>7}{len(ms):>7}{np.median(ms):>11.2f}{np.mean(ms):>11.2f}"
          f"{np.median(ns):>13.0f}{max(ns):>13}{sum(ms) / 1000 / ts:>12.1%}", flush=True)

# ★★ 钱表：耗时 vs 状态数
print(f"\n{'n_states 档':>14}{'调用数':>9}{'占比':>8}{'ms/次均值':>12}{'总秒':>9}{'总时间占比':>12}")
EDGES = [(1, 1), (2, 10), (11, 100), (101, 1000), (1001, 10 ** 9)]
for lo, hi in EDGES:
    sel = [m for n, _t, m in FLAT if lo <= n <= hi]
    if not sel:
        continue
    label = f"{lo}" if lo == hi else (f"{lo}-{hi}" if hi < 10 ** 9 else f">{lo - 1}")
    print(f"{label:>14}{len(sel):>9}{len(sel) / len(allm):>7.1%}"
          f"{np.mean(sel):>12.2f}{sum(sel) / 1000:>9.1f}{sum(sel) / 1000 / ts:>11.1%}")

print("\n★ 读法：若「>1000 状态」那档的总时间占比很高 ⇒ 时间在**大仗的搜索空间**上，"
      "中位数和小仗怎么优化都没用。反之若均匀 ⇒ 每次调用的固定开销才是目标。", flush=True)
