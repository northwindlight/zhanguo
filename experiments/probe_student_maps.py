# -*- coding: utf-8 -*-
"""让学生在**指定的种子**上跑，逐图报数 —— 用于和老师逐图配对。

## 为什么需要它（2026-09-14）

现成的 `probe_eval_caliber.py` **写死了**种子（贪心 `900_000+i`、采样 `800_000+i`），
所以只能量「评估用的那 8 张」。而用户提的问题需要**按老师的表现挑图**：

> 「我担心 **BC 会把这些差局带给模型**，不过看起来没有。」

⇒ 要验它，必须让学生在**老师最差的那几张图**上跑。老师是**确定性**的
（纯 Python，无浮点内核，跨机逐位可复现）⇒ 它在某图上的分数是**精确值**，
所以"最差 N 张"是**真·最差**，**没有回归均值问题**（挑选是无偏的）。

## 判据（事先写死）

对老师**最差**的一组图与**最好**的一组图，分别比 学生/老师：

| 若…… | 会看到 |
|---|---|
| **学生继承了老师的坏图** | 最差组上 学生/老师 **也低**（BC 把坏局学进去了） |
| **学生是"对冲式模仿者"** | 最差组上 **学生赢**、最好组上 **学生输**，且**学生方差更小** |

后者的机制：BC 学的是「状态 → 动作」的**分布平均**，老师是一条**确定的**轨迹；
在老师走崩的图上 BC 大概率不复现那个**特定**坏分支，反而躲开灾难，代价是吃不到好图。

## 用法

    python experiments/probe_student_maps.py <ckpt> 900062,900079,900112 [--turns 200] [--exec 0]

★ `--exec` 默认 0 = 与当前训练/评估口径一致（`SAMPLING_USE_EXEC=False`）。
★ **贪心档才是与老师可比的档**（老师确定性）—— 采样档只作参考，一并报。
"""
from __future__ import annotations

import sys

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer
from rl.train import play_episode

CK = None
SEEDS: list[int] = []
TURNS = 200
USE_EXEC = False
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--exec":
        i += 1; USE_EXEC = bool(int(sys.argv[i]))
    elif CK is None:
        CK = a
    else:
        SEEDS += [int(x) for x in a.split(",") if x.strip()]
    i += 1
if CK is None or not SEEDS:
    print(__doc__); sys.exit(2)

env = ZhanguoEnv(map_size=16, max_turns=TURNS)      # 评估一律真值（jitter 不传）
env.reset(0)
w0 = tokenize(env, env._obs())

m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CK, map_location="cpu", weights_only=False)
_miss, _ = m.load_state_dict(ck["model"], strict=False)
m.eval()

print(f"{CK}（第 {ck.get('iter','?')} 块）· {len(SEEDS)} 张图 × {TURNS} 回合 · "
      f"use_exec={USE_EXEC}" + ("  ★exec 头未训（旧权重）" if any(
          k.startswith("exec_head.") for k in _miss) else ""))
print()
print(f"{'seed':>8}  {'贪心':>10}  {'采样':>10}")
g_all, s_all = [], []
for sd in SEEDS:
    # 两档各自播种，避免互相消耗 RNG 流
    torch.manual_seed(2000 + sd % 1000)
    g = play_episode(env, m, seed=sd, deterministic=True, use_win=True, use_exec=USE_EXEC)
    torch.manual_seed(3000 + sd % 1000)
    s = play_episode(env, m, seed=sd, deterministic=False, use_win=True, use_exec=USE_EXEC)
    g_all.append(g["spend_total"]); s_all.append(s["spend_total"])
    print(f"{sd:>8}  {g['spend_total']:>10,.0f}  {s['spend_total']:>10,.0f}", flush=True)

n = len(SEEDS)
print(f"\n均值       {sum(g_all)/n:>10,.0f}  {sum(s_all)/n:>10,.0f}")
print(f"最低       {min(g_all):>10,.0f}  {min(s_all):>10,.0f}")
print(f"最高       {max(g_all):>10,.0f}  {max(s_all):>10,.0f}")
if n > 1:
    import statistics as st
    print(f"std        {st.pstdev(g_all):>10,.0f}  {st.pstdev(s_all):>10,.0f}")
print("\n★ 逐图配对：把上面的数与老师在同一批 seed 上的数并排（老师用 probe_teacher_ceiling.py，"
      "或其逐图归档）。判据见文件头。")
