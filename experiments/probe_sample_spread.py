# -*- coding: utf-8 -*-
"""**采样分布** —— 回答「超越 BC 有没有空间」（用户 2026-09-19）。

## 问的是什么

用户原话：「**有很多建筑是需要的，但是 bc 不建，模型现在还行是因为 bc 有基础，
但是超越 bc 呢？**」

BC 同时给了地板和天花板：它只教老师做过的 ⇒ 超出 BC 需要「探索 + 选择」，
而选择那一半（`pg`）实测是噪声（批间一致性 0.06~0.09）。所以正路是
**把"选择"从 pg 换成"高分轨迹照着学"**（filtered BC / ReST / 回报加权回归）——
探索已经有了（采样臂 `ent≈3.5`），选择用**真实目标**（终局消费）来打分。

**这条路能不能爬，取决于头部分布：** 若"自己采的最好那批"只比中位数高一点点，
filtered BC 就爬不动；若高出很多，就有得爬。

## 口径（三处必须说清）

1. **采样臂**（用户口径：判据只看采样）。`torch.manual_seed` 逐条变 ——
   ★`rl.compare.run_model` **内部固定 `torch.manual_seed(0)`**，同一 (模型, 图)
   重复调用会给**同一个数**，拿它量分布是错的（20 次全一样）。这里每条轨迹
   换一个种子。
2. **多图**（跨图 σ≈45%，单图分不出"能力"和"开局抽签"）：默认跑 5 张留出图。
3. 报的是**分位数**不是均值 —— filtered BC 关心的是"最好的那一批有多好"。

用法：
    python experiments/probe_sample_spread.py <ckpt> [--reps 4] [--jobs 24]
"""
from __future__ import annotations

import os
import statistics as st
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _opt(name, default):
    a = sys.argv
    return type(default)(a[a.index(name) + 1]) if name in sys.argv else default


CKPTS = [a for a in sys.argv[1:]
         if not a.startswith("--") and not a.lstrip("-").isdigit() and "," not in a]
REPS = _opt("--reps", 4)
JOBS = _opt("--jobs", 24)
TURNS = _opt("--turns", 200)
SEEDS = [900000 + i for i in range(5)]

_G: dict = {}


def _init(ckpt: str, turns: int) -> None:
    """每个进程只装一次模型（48 核 ⇒ 48 进程，模型 2.3M 参数，装起来很便宜）。"""
    import torch
    torch.set_num_threads(1)
    from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv
    from rl.tokenize import GROUPS, tokenize
    from rl.transformer import WindowTransformer
    env = ZhanguoEnv(map_size=16, max_turns=turns, max_actions_per_turn=ACT_SAFETY)
    env.reset(0)
    w0 = tokenize(env, env._obs())
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(ckpt, map_location="cpu", weights_only=False)
    m.load_state_dict(ck["model"], strict=False)
    m.eval()
    _G.update(env=env, model=m, tokenize=tokenize)


def _one(task: tuple[int, int]) -> tuple[int, int, float, int]:
    """跑一条轨迹。**每条换一个 torch 种子** —— 否则同一 (模型, 图) 全是同一个数。"""
    import torch
    from rl.ppo import act
    seed, rep = task
    torch.manual_seed(0x5EED + 7919 * rep + seed)
    env, m, tok = _G["env"], _G["model"], _G["tokenize"]
    obs = env.reset(seed)
    while True:
        w = tok(env, obs)
        i, _lp, _v = act(m, obs, deterministic=False, win=w)
        obs, _r, done, _info = env.step(obs.cand["actions"][i])
        if done:
            break
    s = env.summary()
    return seed, rep, float(s["spend_total"]), int(s["tiles"])


def pct(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    return ys[min(len(ys) - 1, int(q * len(ys)))]


t0 = time.time()
for ckpt in CKPTS:
    tasks = [(s, r) for s in SEEDS for r in range(REPS)]
    print(f"\n=== {Path(ckpt).stem} · {len(tasks)} 条采样轨迹 · {TURNS} 回合 ===",
          flush=True)
    with ProcessPoolExecutor(max_workers=min(JOBS, len(tasks)),
                             initializer=_init, initargs=(ckpt, TURNS)) as ex:
        rows = list(ex.map(_one, tasks, chunksize=1))
    print(f"{'图':<10}{'n':>4}{'最低':>9}{'p50':>9}{'p80':>9}{'p95':>9}{'最高':>9}",
          flush=True)
    allsp = []
    for sd in SEEDS:
        xs = [sp for s, _r, sp, _t in rows if s == sd]
        allsp += xs
        print(f"{sd:<10}{len(xs):>4}{min(xs):>9,.0f}{pct(xs, .5):>9,.0f}"
              f"{pct(xs, .8):>9,.0f}{pct(xs, .95):>9,.0f}{max(xs):>9,.0f}", flush=True)
    print(f"{'合计':<10}{len(allsp):>4}{min(allsp):>9,.0f}{pct(allsp, .5):>9,.0f}"
          f"{pct(allsp, .8):>9,.0f}{pct(allsp, .95):>9,.0f}{max(allsp):>9,.0f}", flush=True)
    med = pct(allsp, .5)
    top20 = sorted(allsp)[int(0.8 * len(allsp)):]
    print(f"\n★头部空间：p50 {med:,.0f} → 前 20% 均值 {st.mean(top20):,.0f}"
          f"（**{st.mean(top20) / med - 1:+.1%}**）", flush=True)
    print(f"  跨图中位数：{st.median([pct([sp for s,_r,sp,_t in rows if s==sd], .5) for sd in SEEDS]):,.0f}"
          f"  ← 判据口径（5 图取中位）", flush=True)

print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)
