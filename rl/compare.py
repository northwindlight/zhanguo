# -*- coding: utf-8 -*-
"""正式对比：同一批地图上，跑 [训练好的模型·贪心] / [模型·采样] / [rule_ai]，给可信数字。

为什么需要：训练时的评估只用固定 3 张图（900000+ / 800000+），可比但样本太小；
规则 AI 的 13,856 更是单张图（seed 0）量的。这里统一到同一批随机图上，三方同图对比。

    python3 -m rl.compare --ckpt rl/runs/srvD/last.pt --episodes 20
"""
from __future__ import annotations

import argparse
import random
import statistics as st
from pathlib import Path

import torch

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import act
from rule_ai import rule_turn


def run_model(env, model, seed: int, deterministic: bool) -> tuple[float, int]:
    torch.manual_seed(0)
    obs = env.reset(seed)
    while True:
        i, _lp, _v = act(model, obs, deterministic=deterministic)
        obs, _r, done, _info = env.step(obs.cand["actions"][i])
        if done:
            break
    s = env.summary()
    return s["spend_total"], s["tiles"]


def run_rule(env, seed: int, turns: int, max_actions: int = 24,
             which: str = "old") -> tuple[float, int]:
    """规则 AI 自己驱动世界（它直接调引擎，不走 RL 动作集）。

    which: "old"=rule_ai.py（稳经济不扩张）/ "v3"=expand_rule_ai.py（扩张流·用户第一版）
           / "v6"=expand_rule_v6.py（扩张流·用户第二版，最强基线）。
    """
    if which == "v6":
        from expand_rule_v6 import expand_rule_turn_v6 as fn
    elif which == "v3":
        from expand_rule_ai import expand_rule_turn as fn
    else:
        fn = rule_turn
    env.reset(seed)
    rng = random.Random(seed)
    for t in range(turns):
        fn(env.world, env.agent, rng, max_actions=max_actions)
        env.world.resolve_turn()
        if t + 1 < turns:
            env.world.begin_turn()
    return env.world.spend_total(env.agent), len(env.world.own_tiles(env.agent))


def main() -> None:
    ap = argparse.ArgumentParser(description="模型 vs 规则 AI（同图对比）")
    ap.add_argument("--ckpt", default="rl/runs/srvD/last.pt")
    ap.add_argument("--episodes", type=int, default=20, help="几张图（每张三方各跑一次）")
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--seed-base", type=int, default=500_000)
    ap.add_argument("--threads", type=int, default=4)
    # 回合内动作上限**必须和训练时一致**——它进观测（turn_actions 归一化那一维），
    # 训练 64 而这里写死 16 会让同一维的取值放大 4 倍，等于拿漂移的输入评估。
    ap.add_argument("--max-actions", type=int, default=64,
                    help="模型每回合动作上限（对齐 train.py）")
    ap.add_argument("--rule-actions", type=int, default=64,
                    help="规则 AI 每回合动作上限（老师也该按满血评估）")
    args = ap.parse_args()

    import torch as _t
    _t.set_num_threads(max(1, args.threads))

    env = ZhanguoEnv(map_size=args.map_size, max_turns=args.turns,
                     max_actions_per_turn=args.max_actions)
    env.reset(0)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=args.map_size ** 2)
    ck = _t.load(args.ckpt, map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()
    print(f"模型：{args.ckpt}（iter {ck.get('iter')}）  图 {args.episodes} 张  回合 {args.turns}")

    g, s, r_, e_, et, ee = [], [], [], [], [], []
    print(f"{'seed':>10}{'模型·贪心':>13}{'模型·采样':>13}{'v3':>13}{'v6(新基线)':>13}"
          f"{'v3地':>6}")
    for i in range(args.episodes):
        seed = args.seed_base + i
        a = run_model(env, model, seed, deterministic=True)
        b = run_model(env, model, seed, deterministic=False)
        c = run_rule(env, seed, args.turns, max_actions=args.rule_actions, which="v3")
        d = run_rule(env, seed, args.turns, max_actions=args.rule_actions, which="v6")
        g.append(a[0]); s.append(b[0]); r_.append(c[0]); e_.append(d[0])
        et.append(a[1]); ee.append(d[1])
        print(f"{seed:>10}{a[0]:>13,.0f}{b[0]:>13,.0f}{c[0]:>13,.0f}{d[0]:>13,.0f}{d[1]:>6}")

    def line(name, xs):
        print(f"{name:<12}均值 {st.mean(xs):>10,.0f}   中位 {st.median(xs):>10,.0f}   "
              f"最好 {max(xs):>10,.0f}  最差 {min(xs):>10,.0f}")
    print("\n" + "=" * 62)
    line("模型·贪心", g)
    line("模型·采样", s)
    line("v3(第一版)", r_)
    line("v6(第二版)", e_)
    print(f"\n模型贪心地数均值 {st.mean(et):.1f}   v6 地数均值 {st.mean(ee):.1f}")
    print(f"相对 v6：贪心 ×{st.mean(g)/max(1,st.mean(e_)):.2f}   "
          f"采样 ×{st.mean(s)/max(1,st.mean(e_)):.2f}")


if __name__ == "__main__":
    main()
