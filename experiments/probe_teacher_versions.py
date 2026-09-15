# -*- coding: utf-8 -*-
"""**逐代老师对比** —— 同一批图、同一回合数，比各代规则 AI 的消费。

## 为什么要自己写（不用 `probe_teacher_ceiling.py`）

因为那个经 `rl.bc.get_teacher`，而 **`rl/bc.py` 顶部 `import torch`**
⇒ 在没有 torch 的机器（Pi）上跑不了。**老师本身是纯 Python**，
`rl/env.py` 也不需要 torch ⇒ 这里自己接那段（10 行，与 `rl/train.teacher_baseline` 同款）。

## 为什么要比

用户 2026-09-15：**「你先测试 v11，v12 按 v11 迭代」** ——
main 那批把各代收进 `ruleai/` 并新加了 **v11（经济层与军事层分离）**，
但 `rule_ai.py` 说它"**可选**，缺省仍是 v10" ⇒ **它到底比 v10 好还是差，没人量过。**

## 口径（与 `probe_teacher_ceiling` 逐项对齐，便于与历史数比）

- 图集基点 `SEED0`（默认 900000，= 贪心档同图）
- `TURNS` 回合（默认 200）、`jitter=0`（评估一律真值）
- 老师基准：`rng = random.Random(0xB4BE)`（`rl/train.teacher_baseline` 同款固定流）、
  跑在 `copy.deepcopy(env.world)` 上、每回合 `resolve_turn()` + `begin_turn()`
- `HORIZON = turns + 20`（各代同口径，漏了它在短局里会按 200 回合规划）

用法：python experiments/probe_teacher_versions.py [v10,v11,...] [--maps 8] [--turns 200] [--seed0 900000]
"""
from __future__ import annotations

import copy
import random
import statistics as st
import sys

import rule_ai
from rl.env import ZhanguoEnv
from rl.ruleai_bridge import clear_state, horizon_of, set_horizon

VERSIONS: list[str] = []
MAPS, TURNS, SEED0 = 8, 200, 900_000
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--maps":
        i += 1; MAPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--seed0":
        i += 1; SEED0 = int(sys.argv[i])
    else:
        VERSIONS += a.split(",")
    i += 1
if not VERSIONS:
    VERSIONS = ["v10", "v11"]

env = ZhanguoEnv(map_size=16, max_turns=TURNS)
print(f"{len(VERSIONS)} 代 × {MAPS} 图 × {TURNS} 回合（图集基点 {SEED0}，jitter=0）\n")

per: dict[str, list] = {}
for v in VERSIONS:
    _name, fn = rule_ai.resolve(v)
    # ★ 与 get_teacher 同口径。**必须走桥**：`mod.HORIZON = n` 对包版本（v11/v12）
    #   是空操作 —— 2026-09-15 踩过（500 回合那次 v10 真设成 520、v11/v12 仍是
    #   缺省 200，口径差 2.6 倍，一度误读成"v11/v12 长局塌到 25%"）。
    set_horizon(fn, TURNS + 20)
    vals = []
    # ★刻度要覆盖长局：早先只到 200，500 回合那轮**看不到"从哪一回合开始塌"**
    #   （只有终值）⇒ 补到 500（用户 2026-09-15「还要看 500 回合的结果」）。
    marks = [m for m in (20, 50, 70, 100, 150, 200, 250, 300, 350, 400, 450, 500)
             if m <= TURNS]
    tile_at = {m: [] for m in marks}
    for k in range(MAPS):
        clear_state(fn)               # ★每局开始清（v11+ 的编组，见 ruleai_bridge）
        env.reset(SEED0 + k)
        rng = random.Random(0xB4BE)
        w = copy.deepcopy(env.world)
        for t in range(TURNS):
            fn(w, env.agent, rng, max_actions=10 ** 9, on_action=lambda *a: None)
            w.resolve_turn()
            if t + 1 < TURNS:
                w.begin_turn()
            if (t + 1) in tile_at:
                tile_at[t + 1].append((len(w.own_tiles(env.agent)),
                                       w.spend_total(env.agent)))
        vals.append(w.spend_total(env.agent))
    per[v] = vals
    print(f"  {v:>4}: 视野(HORIZON)={horizon_of(fn)}  均值 {st.mean(vals):>9,.0f}  "
          f"中位 {st.median(vals):>9,.0f}  "
          f"最低 {min(vals):>9,.0f}  最高 {max(vals):>9,.0f}")
    print(f"        逐图 " + " ".join(f"{x:,.0f}" for x in vals))
    print("        ★逐期（领土/累计消费）："
          + "  ".join(f"T{m}={st.mean([x[0] for x in v]):.0f}/{st.mean([x[1] for x in v]):,.0f}"
                      for m, v in sorted(tile_at.items())))

if len(per) > 1:
    base = VERSIONS[0]
    print(f"\n=== 相对 {base} 的逐图比 ===")
    for v in VERSIONS[1:]:
        r = [b / a for a, b in zip(per[base], per[v])]
        up = sum(1 for x in r if x > 1)
        print(f"  {v} / {base}: 均值比 {st.mean(r):.3f}   逐图 {up}/{len(r)} 张更高   "
              + " ".join(f"{x:.2f}" for x in r))
