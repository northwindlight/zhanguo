# -*- coding: utf-8 -*-
"""**军费占比上限 `MIL_SHARE` 的扫描** —— v11/v12 各扫一遍，找它的最优值。

## 这个旋钮是什么

`ruleai/v11/economy.py:30`：

    MIL_SHARE = 0.30   # 军费占收入的上限：出兵、涨兵**同一个条件**（用户 2026-09-11）

用在 `economy.py:166`：

    mil_ok = inc <= 0.5 or (upkeep / inc) < MIL_SHARE
    army_cap = max(army_n + 1, supply_cap) if mil_ok else army_n

⇒ **`upkeep/inc` 一旦顶到这条线，本回合就不再出兵、不再涨兵**（`army_cap = army_n`
= 一支都不加）。所以它不是"预算分配比例"，是**开关**：低于线就照常扩军
（`army_cap = max(army_n+1, supply_cap)`，只看补给上限），顶到线就整个停。

★用户 2026-09-15：「不同打法没有好坏之分」—— 但这里量的是**目标函数本身**
（终局总消费），不是打法偏好，所以它有好坏之分。

## 口径

- 三代同款：`HORIZON = TURNS + 20`（走 `rl.ruleai_bridge`，**别直接给模块赋值** ——
  v11/v12 是包，赋在 `entry` 上经济层读不到；2026-09-15 踩过）
- 每局开始 `clear_state`（v11+ 的编组是模块内存，不清会跨局漏，同日踩过）
- `jitter=0`（评估一律真值）、老师固定 rng `0xB4BE`、跑在 `deepcopy(world)` 上
- **逐图配对 + 对地图做 bootstrap**（用户口径：别只看均值，也别只报逐图）
- 真值表：`MIL_SHARE` 设完**回读自证**（`set_knob`），跑完**还原**

用法：
  python experiments/probe_mil_share.py                       # v12, 默认档位
  python experiments/probe_mil_share.py v11,v12 --turns 500
  python experiments/probe_mil_share.py --shares 0.15,0.30,0.60 --maps 4
"""
from __future__ import annotations

import copy
import random
import statistics as st
import sys

import rule_ai
from rl.env import ZhanguoEnv
from rl.ruleai_bridge import clear_state, horizon_of, set_horizon, set_knob

VERSIONS: list[str] = []
SHARES = [0.15, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60]
MAPS, TURNS, SEED0 = 8, 200, 900_000
i = 1
while i < len(sys.argv):
    a = sys.argv[i]
    if a == "--shares":
        i += 1; SHARES = [float(x) for x in sys.argv[i].split(",")]
    elif a == "--maps":
        i += 1; MAPS = int(sys.argv[i])
    elif a == "--turns":
        i += 1; TURNS = int(sys.argv[i])
    elif a == "--seed0":
        i += 1; SEED0 = int(sys.argv[i])
    else:
        VERSIONS += a.split(",")
    i += 1
if not VERSIONS:
    VERSIONS = ["v12"]


def bootstrap_ci(vals: list[float], n: int = 20000, seed: int = 12345):
    """对**地图**做 bootstrap（用户口径：扩大样本、别逐图下结论）。"""
    rng = random.Random(seed)
    k = len(vals)
    ms = []
    for _ in range(n):
        ms.append(sum(vals[rng.randrange(k)] for _ in range(k)) / k)
    ms.sort()
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


env = ZhanguoEnv(map_size=16, max_turns=TURNS)
print(f"军费占比 MIL_SHARE 扫描：{len(VERSIONS)} 代 × {len(SHARES)} 档 × "
      f"{MAPS} 图 × {TURNS} 回合（图集基点 {SEED0}，jitter=0）\n")

for v in VERSIONS:
    _name, fn = rule_ai.resolve(v)
    set_horizon(fn, TURNS + 20)
    base_share = None
    cur = [x for x in SHARES if abs(x - 0.30) < 1e-9]
    base_share = cur[0] if cur else SHARES[len(SHARES) // 2]
    per: dict[float, list] = {}
    tile: dict[float, list] = {}
    try:
        for s in SHARES:
            set_knob(fn, "MIL_SHARE", s)          # ★设完回读自证，跑完还原
            assert set_knob(fn, "MIL_SHARE", s) == s
            vals, tls = [], []
            for k in range(MAPS):
                clear_state(fn)
                env.reset(SEED0 + k)
                rng = random.Random(0xB4BE)
                w = copy.deepcopy(env.world)
                for t in range(TURNS):
                    fn(w, env.agent, rng, max_actions=10 ** 9,
                       on_action=lambda *a: None)
                    w.resolve_turn()
                    if t + 1 < TURNS:
                        w.begin_turn()
                vals.append(w.spend_total(env.agent))
                tls.append(len(w.own_tiles(env.agent)))
            per[s] = vals
            tile[s] = tls
            lo, hi = bootstrap_ci(vals)
            star = "  ←缺省" if s == base_share else ""
            print(f"  {v} MIL_SHARE={s:.2f}: 消费均值 {st.mean(vals):>9,.0f}  "
                  f"[{lo:>9,.0f}, {hi:>9,.0f}]  领地 {st.mean(tls):5.1f}{star}")
    finally:
        set_knob(fn, "MIL_SHARE", 0.30)           # ★还原（别把全局旋钮留给下一个探针）

    b = per[base_share]
    print(f"  --- 相对缺省 {base_share:.2f} 的逐图配对（比值 >1 = 比缺省好）---")
    for s in SHARES:
        if s == base_share:
            continue
        r = [y / x for x, y in zip(b, per[s])]     # ★比值方向：分子=本档
        lo, hi = bootstrap_ci(r)
        up = sum(1 for x in r if x > 1)
        sig = "★" if (lo > 1 or hi < 1) else " "
        print(f"     {s:.2f}: 均值比 {st.mean(r):5.3f}  CI[{lo:5.3f},{hi:5.3f}]{sig} "
              f" 逐图 {up}/{len(r)} 张更高")
    print(f"  （视野 HORIZON={horizon_of(fn)}，口径 = 每局 {TURNS} + 20）\n")
