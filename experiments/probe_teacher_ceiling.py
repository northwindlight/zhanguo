# -*- coding: utf-8 -*-
"""规则 AI 老师自己在那 8 张评估图上能拿多少？—— 量"DAgger 锚定的天花板"。

## 为什么要问（用户 2026-09-13 的提议）

用户提议「**每张图先 1 局 DAgger（100 回合）+ 2 局 PPO（200 回合）**，
频繁换图难以固化策略」。

**换图那一半我有信心**（跨图难度差 5.4×，每局换图 ⇒ 每个梯度步被"这张图运气
好不好"主导）。**DAgger 那一半我担心**：注入老师标签 = 把策略往老师行为拉，
**如果老师的天花板就 12,000 上下，而 PPO 已经爬到 14,497，那 DAgger 会把它拽回来**。

**关键数**：BC 学生（不完美模仿）= 12,029；PPO 峰值 = **14,497**。
**老师本人**是多少？**没量过。** 本探针就是量它。

## 判据

- 老师 **> 14,497** ⇒ DAgger 锚定**安全**（还有上升空间），用户的提议可以上
- 老师 **≈ 12,000** ⇒ DAgger 会**和 PPO 打架**，得改设计（比如只在开局锚、或改成
  "老师只在学生明显退步时才介入"）

★老师跑法与评估**同口径**：`evaluate()` 用固定种子 `900_000+i`（贪心）/`800_000+i`（采样），
这里只跑 **900_000+i**（与贪心评估同一批图），200 回合，`jitter=0`（评估恒真值）。

用法：python experiments/probe_teacher_ceiling.py [局数] [回合]
"""
import sys
import copy

from rl.env import ZhanguoEnv
from rl.bc import get_teacher
from rl.train import teacher_baseline

EPS = int(sys.argv[1]) if len(sys.argv) > 1 else 8
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 200

teacher = get_teacher("v10", turns=TURNS)
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
print(f"规则 AI 老师 v10，在**贪心评估同一批图**（种子 900000+）上跑 {EPS} 局 × {TURNS} 回合\n")

vals = []
for i in range(EPS):
    seed = 900_000 + i
    env.reset(seed)
    # 与 `teacher_baseline` 同款：跑在**世界副本**上，不碰 env.world
    v = teacher_baseline(copy.deepcopy(env.world), env.agent, TURNS, teacher)
    vals.append(v)
    print(f"  图{i}（seed {seed}）：老师消费 {v:>9,.0f}")

vals.sort()
n = len(vals)
print(f"\n=== 老师在这 {n} 张图上 ===")
print(f"  均值   {sum(vals) / n:>9,.0f}")
print(f"  中位   {vals[n // 2]:>9,.0f}")
print(f"  最低   {vals[0]:>9,.0f}   最高 {vals[-1]:>9,.0f}")
print(f"\n对照：BC 学生（不完美模仿）12,029 · PPO 峰值（ckpt_5）14,497")
print("判据：老师 > 14,497 ⇒ DAgger 锚定安全；≈ 12,000 ⇒ DAgger 会和 PPO 打架。")
