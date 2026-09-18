# -*- coding: utf-8 -*-
"""量：**价值头有没有把「远期收益」装进去**（用户 2026-09-18：「可以测」）。

## 问的是什么

用户把"学生不爱扩张"定成了一个**奖励/远见**问题，不是视界问题：

> 「后期收益显著高于前期，但是**模型不知道**……跑 500 只会练成滚雪球模型，
>   兑现点没意义，**他不知道未来会兑现**」

⇒ 代码里唯一能承载"未来会兑现"的地方是**价值头**：`γ=1` 时 `V(s)` 的定义就是
「从这个状态起、到局末的总消费」。若 `V` 在"刚投了资"的状态上是高的，优势函数就会把
那笔远期收益算进当下这一步 —— **不需要它亲自走过**（老师走过，BC 数据里有那些轨迹）。

## 判据（决定性）

在**老师走过的轨迹**上，逐步取 `(状态, 该状态的剩余真实消费)`，与模型给的 `V(s)` 比：

| 结果 | 含义 |
|---|---|
| `V` 与「剩余真实消费」**同向且量级对得上** | 远见**在**，问题在别处（策略头没用上它 ⇒ `score`/candx 那条线） |
| `V` **压平**（在"投了资"和"没投资"的状态上给的差不多），而真实剩余差 3~5 倍 | **价值头没学会远期** ⇒ 那才是要治的 |

**为什么要用老师轨迹**：只有老师的轨迹里**真的发生了扩张**，那里的"剩余真实消费"才会高。
学生自己的轨迹里没有那个未来，量不出这条依赖（那正是它学不到的原因）。

用法：
    python experiments/probe_value_foresight.py <ckpt> [<ckpt> ...] [--seed 900000]
    # `--arch t111` 同 `probe_beyond_horizon`（老 ckpt 要用当时代架构）
"""
from __future__ import annotations

import random
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch  # noqa: E402

import rule_ai  # noqa: E402
from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv  # noqa: E402
from rl.hw import set_threads  # noqa: E402
from rl.ppo import value_of  # noqa: E402
from rl.tokenize import GROUPS, tokenize  # noqa: E402
from rl.transformer import WindowTransformer  # noqa: E402

CKPTS = [a for a in sys.argv[1:] if not a.startswith("--") and not a.lstrip("-").isdigit()]
SEED = int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 900000
TURNS = 200
set_threads(int(__import__("os").environ.get("ZHANGUO_THREADS", "4")))

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=ACT_SAFETY)
env.reset(0)
_w = tokenize(env, env._obs())
teacher = rule_ai.resolve("v11plus")[1]


def teacher_trajectory():
    """老师走一局，逐**回合开头**留下 `(obs, 已花, 领地, 军队)`。"""
    env.reset(SEED, map_seed=SEED)
    env.world.max_turns = TURNS
    rng = random.Random(0xB4BE)
    snap = []
    for k in range(TURNS):
        obs = env._obs()
        snap.append({"turn": k + 1, "obs": obs,
                     "spent": env.world.spend_total(env.agent),
                     "tiles": len(env.world.own_tiles(env.agent)),
                     "armies": len(env.world.nation_armies(env.agent))})
        teacher(env.world, env.agent, rng, max_actions=10 ** 9, on_action=lambda *a: None)
        env.world.resolve_turn()
        if k + 1 < TURNS:
            env.world.begin_turn()
    final = env.world.spend_total(env.agent)
    for s in snap:
        # V 学的就是这个：`(局末 − 此刻) × reward_scale`
        s["true_future"] = (final - s["spent"]) * env.reward_scale
    return snap, final


def load(p: str):
    m = WindowTransformer({g: _w.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(p, map_location="cpu", weights_only=False)
    miss, _ = m.load_state_dict(ck["model"], strict=False)
    m.eval()
    print(f"  载入 {p}（缺失 {len(miss)} 项）", flush=True)
    return m


snap, final = teacher_trajectory()
print(f"老师 seed {SEED}：终局消费 {final:,.0f}  终局领地 {snap[-1]['tiles']}\n", flush=True)
print(f"{'回合':>5}{'已花':>9}{'真实剩余':>10}{'领地':>6}{'军队':>6}", end="", flush=True)
for p in CKPTS:
    print(f"{'V@' + Path(p).stem:>14}", end="")
print(flush=True)

vals = {p: [] for p in CKPTS}
models = {p: load(p) for p in CKPTS}
for s in snap:
    if s["turn"] % 25 and s["turn"] != 1:
        continue
    w = tokenize(env, s["obs"])
    row = f"{s['turn']:>5}{s['spent']:>9,.0f}{s['true_future']:>10.2f}" \
          f"{s['tiles']:>6}{s['armies']:>6}"
    for p in CKPTS:
        v = value_of(models[p], s["obs"], win=w)
        vals[p].append((s["true_future"], v))
        row += f"{v:>14.2f}"
    print(row, flush=True)

print("\n★整条轨迹上的相关性（老师走过的状态，含真扩张）：", flush=True)
for p in CKPTS:
    xs = [a for a, _ in vals[p]]
    ys = [b for _, b in vals[p]]
    mx, my = st.mean(xs), st.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = (sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys)) ** 0.5
    r = num / den if den else float("nan")
    spread = max(ys) - min(ys)
    print(f"  {Path(p).name:<28} corr(V, 真实剩余) = {r:+.3f}   "
          f"V 跨度 {spread:.2f}（真实剩余跨度 {max(xs) - min(xs):.2f}）", flush=True)
