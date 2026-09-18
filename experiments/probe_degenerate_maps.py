# -*- coding: utf-8 -*-
"""**退化图**上的三方对拍：老师 / BC 起点 / PPO（用户 2026-09-18：「先量退化图，bc和ppo都量」）。

问的是：**老师失灵的地方，学生会不会跟着失灵** —— 这是 1 号模型那条验收线
（「证明这套机器能超过老师」）最干净的试法。

## 两张退化图（都是实测挑出来的，形态不同）

| seed | 形态 | 地 | 总消费 | 建造/征兵/军费 |
|---|---|---|---|---|
| **142542** | **养兵不用**：征兵占 72%、一格没打下来 | 5 | 6,922 | 25/**72**/3 |
| **451383** | **建造推不动**：地不少但消费只有健康的 1/3 | 63 | 6,083 | **70**/4/26 |
| 900000 | 健康对照（同一批留出图里的第一张） | 210 | 24,871 | 58/6/36 |

★这两张正是**只按领地判退化会漏掉**的那一类（451383 地 63）—— 见 `rl/bc.py`
`episode_is_degenerate` 的②消费判据。

## 口径

`rl.compare` 的 `run_model` / `run_rule`（**torch 种子内部固定** ⇒ 可复现），各 200 回合。
贪心与采样**都报**：熵高时 argmax 会掉进"建最贵的楼→资源耗光→躺平"的近视陷阱（`train.py` 的
`evaluate` docstring 明写），所以不能只看一档。

用法：
    python experiments/probe_degenerate_maps.py [bc_ckpt] [ppo_ckpt]
"""
from __future__ import annotations

import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch  # noqa: E402

from rl.compare import run_model, run_rule  # noqa: E402
from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv  # noqa: E402
from rl.hw import set_threads  # noqa: E402
from rl.tokenize import GROUPS, tokenize  # noqa: E402
from rl.transformer import WindowTransformer  # noqa: E402

TURNS = 200
MAPS = [
    (142542, "★养兵不用型"),
    (451383, "★建造推不动型"),
    (900000, "健康对照"),
]
BC = sys.argv[1] if len(sys.argv) > 1 else "rl/runs/gpu_pull/bc_candx100_gpu/ep400.pt"
PPO = sys.argv[2] if len(sys.argv) > 2 else "rl/runs/gpu_pull/ppo_candx/last.pt"

set_threads(4)
env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=ACT_SAFETY)
env.reset(0)
_w = tokenize(env, env._obs())


def load(p: str):
    m = WindowTransformer({g: _w.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(p, map_location="cpu", weights_only=False)
    miss, _ = m.load_state_dict(ck["model"], strict=False)
    m.eval()
    print(f"  载入 {p}（缺失 {len(miss)} 项）", flush=True)
    return m


bc, ppo = load(BC), load(PPO)
print(f"\n各 {TURNS} 回合；★ = 该档**超过老师**\n", flush=True)

for seed, tag in MAPS:
    t0 = time.time()
    ts, tt = run_rule(env, seed, TURNS, max_actions=10 ** 9, which="v11plus")
    print(f"── seed {seed}（{tag}）  老师：消费 {ts:>9,.0f}  地 {tt:>4}", flush=True)
    for name, m in (("BC  ep400", bc), ("PPO ckpt_95", ppo)):
        for det, dtag in ((True, "贪心"), (False, "采样")):
            sp, tl = run_model(env, m, seed, deterministic=det, use_win=True)
            flag = "  ★超过老师" if sp > ts else ""
            print(f"     {name}·{dtag:<4} 消费 {sp:>9,.0f}（{sp / max(ts, 1):>5.0%}）"
                  f"  地 {tl:>4}{flag}", flush=True)
    print(f"     （{time.time() - t0:.0f}s）\n", flush=True)
