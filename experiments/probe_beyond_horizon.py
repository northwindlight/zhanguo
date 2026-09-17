# -*- coding: utf-8 -*-
"""**训 50 回合、考 200 回合**：过了训练视界之后学生还在不在干活？（用户 2026-09-17）

用户的话：「拿个最新样本测一下 200 回合，也许没有 ppo 训练，50 回合后就不知道干什么了，
不过可以先看看」。

判据不是总分，是**分段行为**：把 200 回合切成 `1-50`（训练过的窗口）/ `51-100` /
`101-150` / `151-200` 四段，逐段报——
  · 每段**动作种类计数**（build/recruit/move/attack/buy/sell/end_turn）⇒ 它还在决策吗？还是只会 end_turn？
  · 每段**消费增量**与**领地增量** ⇒ 还在花钱、还在扩张吗？

只看总分会被前 50 回合盖住：那 50 回合是它唯一见过的，真正的问题是**之后**。

用法：
    python experiments/probe_beyond_horizon.py [ckpt ...] [--turns 200] [--seed 900000]
    # 默认跑 rl/runs/ep24.pt；评估图用 **900000+**（留出图，不在训练池里）
    # ★对照：把参数写成 `teacher:v11plus` 就跑**规则老师**同一张图的同一张表 ——
    #   没有这个对照，"学生不动"分不清是"学生坏了"还是"这张图本来就慢"。
"""
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch  # noqa: E402

from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv  # noqa: E402
from rl.hw import set_threads  # noqa: E402
from rl.ppo import act  # noqa: E402
from rl.tokenize import GROUPS, tokenize  # noqa: E402
from rl.transformer import WindowTransformer  # noqa: E402

CKPTS = [a for a in sys.argv[1:] if not a.startswith("--") and not a.lstrip("-").isdigit()] or ["rl/runs/ep24.pt"]
TURNS = 200
SEED = 900000


def _opt(name, default):
    """`--name value` 取值（不给就用默认）。"""
    if name in sys.argv:
        return int(sys.argv[sys.argv.index(name) + 1])
    return default


TURNS = _opt("--turns", TURNS)
SEED = _opt("--seed", SEED)      # ★对照用：留出图 900000+ / 训练池里的图（如 18）
SEG = 50                      # 分段长度：1-50 是训练窗口，后面都是"没见过"
set_threads(4)

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=ACT_SAFETY)
env.reset(SEED)
_w = tokenize(env, env._obs())


def load(path):
    m = WindowTransformer({g: _w.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(path, map_location="cpu", weights_only=False)
    m.load_state_dict(ck["model"])
    m.eval()
    return m, ck


def run(model, deterministic: bool, teacher=None):
    """跑满 TURNS 回合，按段累计。返回 (总消费, 总领地, 分段表)。

    `teacher` 非空时不用模型，改跑**规则老师**（对照臂）—— 它每次只动一步、
    由外层循环推进回合（老师的入口是"整回合"，所以这里按回合调）。
    """
    import random

    env.reset(SEED)
    env.world.max_turns = TURNS
    obs = env._obs()
    segs = []          # [(段号, {kind: n}, 消费增量, 领地末)]
    cur = {k: 0 for k in KINDS}
    spend0 = 0.0
    seg_i = 0
    rng = random.Random(0xB4BE)
    n_turn = 0                 # ★老师的回合推进**照抄 `collect_episode`**：老师 → resolve → begin。
    while True:                #   `world.turn` 是在 `begin_turn` 里涨的，不是 `resolve_turn`
        if teacher is not None:   #   —— 拿它当过循环条件会一回合就退出（踩过）。
            teacher(env.world, env.agent, rng, max_actions=10 ** 9,
                    on_action=lambda tool, _a: cur.__setitem__(
                        tool, cur.get(tool, 0) + 1))
            env.world.resolve_turn()
            n_turn += 1
            t = n_turn
            done = n_turn >= TURNS
            if not done:
                env.world.begin_turn()
            obs = env._obs()
        else:
            w = tokenize(env, obs)
            i, _lp, _v = act(model, obs, deterministic=deterministic, win=w)
            a = obs.cand["actions"][i]
            cur[a.kind] = cur.get(a.kind, 0) + 1
            obs, _r, done, _info = env.step(a)
            t = env.world.turn
        if t >= (seg_i + 1) * SEG or done:
            sp = env.world.spend_total(env.agent)
            segs.append((seg_i + 1, dict(cur), sp - spend0,
                         len(env.world.own_tiles(env.agent))))
            cur = {k: 0 for k in KINDS}
            spend0 = sp
            seg_i += 1
        if done or t >= TURNS:
            break
    return env.world.spend_total(env.agent), len(env.world.own_tiles(env.agent)), segs


import rl.bc as _bc  # noqa: E402  （老师的取法：与训练同一个入口）

for path in CKPTS:
    if path.startswith("teacher:"):
        model, ck = None, {"iter": "-", "args": {}}
        who = f"规则老师 {path.split(':', 1)[1]}"
        teacher = _bc.get_teacher(path.split(":", 1)[1])
        arms = ((True, "老师"),)
    else:
        model, ck = load(path)
        who = f"{path}（iter {ck.get('iter')}，训练时 turns={ck['args'].get('turns')}）"
        teacher = None
        arms = ((True, "贪心"), (False, "采样"))
    print(f"\n{'='*78}\n{who}  评估图 seed {SEED}，跑到 {TURNS} 回合", flush=True)
    for det, tag in arms:
        t0 = time.time()
        sp, tiles, segs = run(model, det, teacher=teacher)
        print(f"\n  ── {tag}：终局消费 {sp:>9,.0f}  领地 {tiles:>4}  ({time.time()-t0:.0f}s)", flush=True)
        print(f"     {'回合段':<10}{'build':>6}{'recruit':>8}{'move':>6}{'attack':>7}"
              f"{'buy':>5}{'sell':>5}{'end':>5}   {'消费增量':>10}{'领地末':>7}", flush=True)
        for idx, kinds, dsp, tl in segs:
            lo = (idx - 1) * SEG + 1
            hi = min(idx * SEG, TURNS)
            print(f"     {f'{lo}-{hi}':<10}{kinds['build']:>6}{kinds['recruit']:>8}"
                  f"{kinds['move']:>6}{kinds['attack']:>7}{kinds['buy']:>5}"
                  f"{kinds['sell']:>5}{kinds['end_turn']:>5}   {dsp:>10,.0f}{tl:>7}", flush=True)
        # 一眼判据：训练视界**之后**那几段，还在建/征/打吗？还是一路 end_turn？
        after = segs[1:]
        if after:
            act_after = st.mean([sum(v for k, v in s[1].items() if k != "end_turn") for s in after])
            end_after = st.mean([s[1]["end_turn"] for s in after])
            print(f"     ⇒ 视界之后每段（均值）：非 end_turn 动作 {act_after:,.0f} 个，"
                  f"end_turn {end_after:,.0f} 个", flush=True)
