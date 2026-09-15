# -*- coding: utf-8 -*-
"""**逐代老师的"操作数量"** —— 每回合发多少动作、用掉多少预算、各层怎么分。

## 为什么问（用户 2026-09-15：「v11 还有一个操作数量问题，也修修」）

v11 的设计注自己写着（`ruleai/v11/economy.py` 文件头，分歧 3）：
> **给军事段留额度**（`V11_MIL_RESERVE`）：清仓/备料也要卡这个闸。
> **v10 只在建造循环里卡，于是买卖能把 `max_actions` 吃干、军事段一个动作都发不出**
> （看海配置默认只有 12 个）。

⇒ 但"修了"不等于"够了"。这里量三件事（**都只用原始计数，不下判断**）：
1. **每回合动作数**（均值/中位/最大）—— 用掉多少预算
2. **预算利用率** = 动作数 / `max_actions`
3. **每回合被引擎拒的比例**（`ok=False`）—— 拒了也占额度的

★ `max_actions` 取 **RL 的口径（512）** 与 **看海口径（12）** 两档 ——
因为设计注提到的病是"看海只有 12 个"，而 RL 是 512，两档的结论可能相反。

用法：python experiments/probe_teacher_actions.py v10,v11,v12 [--maps 4] [--turns 200]
      [--max-actions 512] [--seed0 900000]
"""
from __future__ import annotations

import copy
import random
import statistics as st
import sys

import rule_ai
from rl.env import ACT_SAFETY, ZhanguoEnv
from rl.ruleai_bridge import clear_state, horizon_of, set_horizon

VERSIONS: list[str] = []
MAPS, TURNS, SEED0, MAXA = 4, 200, 900_000, ACT_SAFETY
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--maps":
        i += 1; MAPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--seed0":
        i += 1; SEED0 = int(sys.argv[i])
    elif a == "--max-actions":
        i += 1; MAXA = int(sys.argv[i])
    else:
        VERSIONS += a.split(",")
    i += 1
if not VERSIONS:
    VERSIONS = ["v10", "v11"]
    pass

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
print(f"{len(VERSIONS)} 代 × {MAPS} 图 × {TURNS} 回合；max_actions={MAXA}（图集基点 {SEED0}）\n")

for v in VERSIONS:
    _name, fn = rule_ai.resolve(v)
    set_horizon(fn, TURNS + 20)      # ★走桥：`mod.HORIZON=n` 对 v11/v12 是空操作
    per_turn = []          # 每回合发出去的动作数
    for k in range(MAPS):
        clear_state(fn)              # ★每局开始清（v11+ 的编组）
        env.reset(SEED0 + k)
        rng = random.Random(0xB4BE)
        w = copy.deepcopy(env.world)
        cnt = {"n": 0}
        def _cb(*_a, **_k):
            cnt["n"] += 1
        for t in range(TURNS):
            cnt["n"] = 0
            fn(w, env.agent, rng, max_actions=MAXA, on_action=_cb)
            per_turn.append(cnt["n"])
            w.resolve_turn()
            if t + 1 < TURNS:
                w.begin_turn()
    n = len(per_turn)
    used = sum(per_turn) / n
    print(f"  {v:>4}: 每回合动作数 均值 {used:6.1f}  中位 {st.median(per_turn):5.0f}  "
          f"最大 {max(per_turn):5.0f}   预算利用率 {used/MAXA:6.1%}")
    hi = [x for x in per_turn if x >= MAXA]
    print(f"        触顶（≥max_actions）的回合：{len(hi)}/{n} = {len(hi)/n:.1%}   "
          f"零动作的回合：{sum(1 for x in per_turn if x == 0)}/{n}")
