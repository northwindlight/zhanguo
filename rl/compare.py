# -*- coding: utf-8 -*-
"""正式对比：同一批地图上，跑 [训练好的模型·贪心] / [模型·采样] / [规则 AI]，给可信数字。

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

from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import act


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


def run_rule(env, seed: int, turns: int, max_actions: int = 10 ** 9,
             which: str = "v9") -> tuple[float, int]:
    """规则 AI 自己驱动世界（它直接调引擎，不走 RL 动作集）。

    which: "v9"=expand_rule_v9.py（**当前基线** = v8 + 视野门控）
           "v6"=expand_rule_v6.py（旧基线）/ "v3"=expand_rule_ai.py（第一版）。
    旧的 rule_ai 已退休，不再当基线。

    ★v9 的 `HORIZON` 是 **ROI 回收期窗口**（`left = HORIZON - turn`，回本超 `left`
    的楼不入选），按其口径设成 **每局回合 + 20**。不设的话短局里它会挑一堆局末
    才回本的楼，评估出来的就不是它真实的水平。
    """
    if which == "v3":
        from expand_rule_ai import expand_rule_turn as fn
    elif which == "v9":
        import expand_rule_v9 as m
        m.HORIZON = turns + 20
        fn = m.expand_rule_turn_v9
    else:
        from expand_rule_v6 import expand_rule_turn_v6 as fn
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
    ap.add_argument("--threads", type=int, default=0, help="torch CPU 线程数；**0 = 自动 = 物理核数**（ECS 1 / Pi 5 4）。SMT 的第二个逻辑核对向量计算收益为零，写死 4 在 ECS 上等于打开超订（实测慢 3.4×）")
    # 模型这一侧的上限只是安全网（回合该不该结束由 end_turn 决定），
    # 规则 AI 那一侧**不限额**——老师该按满血评估，不该被我们定的人为上限削。
    # 观测里那一维按固定 ACT_REF=64 归一化，所以这两个值不再互相牵制。
    ap.add_argument("--max-actions", type=int, default=ACT_SAFETY,
                    help="模型每回合动作上限（对齐 train.py）")
    ap.add_argument("--rule-actions", type=int, default=10 ** 9,
                    help="规则 AI 每回合动作上限（默认不限额）")
    args = ap.parse_args()

    import torch as _t
    from rl.hw import set_threads
    set_threads(args.threads)

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

    g, s, r_, e_, h_, et, ee, eh = [], [], [], [], [], [], [], []
    print(f"{'seed':>10}{'模型·贪心':>13}{'模型·采样':>13}{'v3':>13}{'v6':>13}"
          f"{'v9(基线)':>13}{'v9地':>7}")
    for i in range(args.episodes):
        seed = args.seed_base + i
        a = run_model(env, model, seed, deterministic=True)
        b = run_model(env, model, seed, deterministic=False)
        c = run_rule(env, seed, args.turns, max_actions=args.rule_actions, which="v3")
        d = run_rule(env, seed, args.turns, max_actions=args.rule_actions, which="v6")
        h = run_rule(env, seed, args.turns, max_actions=args.rule_actions, which="v9")
        g.append(a[0]); s.append(b[0]); r_.append(c[0]); e_.append(d[0]); h_.append(h[0])
        et.append(a[1]); ee.append(d[1]); eh.append(h[1])
        print(f"{seed:>10}{a[0]:>13,.0f}{b[0]:>13,.0f}{c[0]:>13,.0f}{d[0]:>13,.0f}"
              f"{h[0]:>13,.0f}{h[1]:>7}")

    def line(name, xs):
        print(f"{name:<12}均值 {st.mean(xs):>10,.0f}   中位 {st.median(xs):>10,.0f}   "
              f"最好 {max(xs):>10,.0f}  最差 {min(xs):>10,.0f}")
    print("\n" + "=" * 62)
    line("模型·贪心", g)
    line("模型·采样", s)
    line("v3(第一版)", r_)
    line("v6(旧基线)", e_)
    line("v9(当前基线)", h_)
    print(f"\n地数均值：模型贪心 {st.mean(et):.1f}   v6 {st.mean(ee):.1f}   "
          f"v9 {st.mean(eh):.1f}")
    # ★主打分口径 = **相对当前基线 v9**（模型是照它克隆的，就该跟它比）
    print(f"相对 v9：贪心 ×{st.mean(g)/max(1,st.mean(h_)):.2f}   "
          f"采样 ×{st.mean(s)/max(1,st.mean(h_)):.2f}   "
          f"（地数 ×{st.mean(et)/max(1,st.mean(eh)):.2f}）")


if __name__ == "__main__":
    main()
