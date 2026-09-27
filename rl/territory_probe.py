# -*- coding: utf-8 -*-
"""**1v1：国土规模 → 先手优势**（2026-09-27 用户提议的国土加码，用来看它值不值）。

    python rl/territory_probe.py [每格局数]

用户三条口径：
  · 「地图 **10-14** 格，**小地图后手吃亏更严重**」
  · 「随机国土给了**更大纵深**，换家敏感性更高」
  · 「你在 1v1 地图上**增加国土规模**就行」⇒ 落地成 `TERRITORY_FRAC_2P`（只对 n<=2）

★ 判据：**先手赢的比例**（两人局若无先手优势应约 **50%**）。
★ 每次**交替先手**，免得把"先手"和"某个座位"混起来。
★ 只改 `TERRITORY_FRAC_LO/HI/2P`（模块常量），别的都不动。

★★★ **2026-09-27 首轮（每格 4 局）—— 欠功率，别当结论**：

    | 边长 | 国土比例   | 范围    | 有胜负 | 先手赢 | 先手占比 | 平均回合 |
    |---|---|---|---|---|---|---|
    | 10 | 0.15~0.35 | [8,18]  | 2 | 0 |   0% | 76.8 |
    | 10 | 0.35~0.60 | [18,30] | 3 | 0 |   0% | 50.0 |
    | 10 | 0.50~0.80 | [25,40] | 3 | 2 |  67% | 64.5 |
    | 12 | 0.15~0.35 | [11,25] | 1 | 0 |   0% | 84.0 |

  每格只有 **2~3 局分出胜负** ⇒ 0% 和 67% 都可能是噪声。**要能判，每格得 30 局以上。**
  ★ 一条**苗头**（也别信）：size 10 前两格里**先手赢 0 次** —— 与"先手 5 回合直达"
    的担心**方向相反**，更像"先手必须先亮牌、后手能针对"。
"""
import sys

import numpy as np
import torch

torch.set_num_threads(1)      # ★ Pi 是用户干活的机器，只占一个核
sys.path.insert(0, ".")
from rl import sandbox as SB                      # noqa: E402
from rl import train as T                         # noqa: E402
from rl.model import build_model                  # noqa: E402
from rl.sandbox import Sandbox                    # noqa: E402

N = int(sys.argv[1]) if len(sys.argv) > 1 else 4
T_MAX = 100
SIZES = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else [10, 12, 14]
FRACS = [(0.15, 0.35), (0.35, 0.60), (0.50, 0.80)]
print(f"1v1  t_max={T_MAX}  每格 {N} 局（未训练网）\n", flush=True)
print(f"{'边长':>5}{'国土比例':>12}{'国土范围':>12}{'有胜负':>7}"
      f"{'先手赢':>7}{'先手占比':>9}{'平均回合':>9}", flush=True)

for size in SIZES:
    for flo, fhi in FRACS:
        SB.TERRITORY_FRAC_LO, SB.TERRITORY_FRAC_HI = flo, fhi
        lo, hi = SB.territory_range(size, 2)
        dec = first = 0
        turns = []
        for i in range(N):
            first_p = "甲" if i % 2 == 0 else "乙"
            sb = Sandbox(seed=7000 + i, size=size, t_max=T_MAX, n_nations=2,
                         first=first_p, halls_known=True, territory=True,
                         alliances="random2v2").reset()
            torch.manual_seed(i)
            nets = {p: build_model(mem_slots=0) for p in sb.players}
            for n in nets.values():
                n.eval()
            with torch.inference_mode():
                st, info = T.collect_episode(nets, sb, rng=np.random.default_rng(i),
                                             max_steps=20000)
            turns.append(info["turns"])
            w = info.get("winner")
            if not w or info.get("truncated"):
                continue
            dec += 1
            if sb.first in (info.get("winner_members") or ()):
                first += 1
        print(f"{size:>5}{f'{flo:.2f}~{fhi:.2f}':>12}{f'[{lo},{hi}]':>12}{dec:>7}"
              f"{first:>7}{first / max(1, dec):>8.0%}{np.mean(turns):>9.1f}",
              flush=True)
SB.TERRITORY_FRAC_LO, SB.TERRITORY_FRAC_HI = 0.15, 0.35
SB.TERRITORY_FRAC_2P = (0.35, 0.60)
print("\n★ 健康：先手占比 ≈ **50%**。接近 100% ⇒ 由行动顺序决定，学不到技能。", flush=True)
