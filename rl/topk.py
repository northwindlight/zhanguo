# -*- coding: utf-8 -*-
"""诊断 BC 权重：老师的动作落在模型前 1/5/10/25/50 名里的比例，按类别拆开。

为什么不能只看 top-1：候选有上百个（后期上千），模型可能已经把范围缩小到
「十几个候选之一」——loss 在降、策略确实在学，但第一名还没轮到正确答案。
top-k 能看到这个中间状态；top-1 会把「学了一半」误判成「什么都没学会」。

**注意天花板**：老师读的是全图（`world.armies` 找视野外的野人），而 RL 观测
按引擎视野遮迷雾——有一部分决策**原理上**猜不中，top-k 也追不平。

    python3 -m rl.topk --ckpt rl/runs/bc_probe/last.pt --turns 120
"""
from __future__ import annotations

import argparse
import random
from collections import Counter, defaultdict

import torch
import torch.nn as nn

from rl.bc import get_teacher, match, pack, to_action
from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet

KS = (1, 5, 10, 25, 50)


def collect_labels(env: ZhanguoEnv, turns: int, seed: int, which: str = "v6"):
    """跑一局，采 (观测, 老师动作在候选里的下标, 类别)。"""
    teacher = get_teacher(which)
    env.reset(seed)
    samples: list[tuple] = []
    pend: dict = {}

    def on_action(tool, args):
        pend["obs"], pend["spec"] = env._obs(), to_action(tool, args)

    def on_result(tool, args, ok):
        if ok and pend["spec"] is not None:
            i = match(pend["obs"].cand["actions"], pend["spec"])
            if i is not None:
                samples.append((pend["obs"], i, tool))

    rng = random.Random(seed)
    for t in range(turns):
        teacher(env.world, env.agent, rng, max_actions=10 ** 9,
                on_action=on_action, on_result=on_result)
        env.world.resolve_turn()
        if t + 1 < turns:
            env.world.begin_turn()
    return samples


def main() -> None:
    ap = argparse.ArgumentParser(description="BC 权重的 top-k 命中诊断")
    ap.add_argument("--ckpt", default="rl/runs/bc_probe/last.pt")
    ap.add_argument("--teacher", default="v6", choices=("v6", "v3", "old"))
    ap.add_argument("--turns", type=int, default=120)
    ap.add_argument("--seed", type=int, default=777)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--legacy-scoring", action="store_true",
                    help="把打分前的 LayerNorm 换成直通，用来装**加 LayerNorm 之前**"
                         "的旧检查点——查老失败的根因是不是同一个（模长捷径）")
    args = ap.parse_args()

    torch.set_num_threads(max(1, args.threads))
    env = ZhanguoEnv(map_size=args.map_size, max_turns=args.turns)
    env.reset(args.seed)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=args.map_size ** 2)
    if args.legacy_scoring:
        model.cand_ln, model.q_ln = nn.Identity(), nn.Identity()
    ck = torch.load(args.ckpt, map_location="cpu", weights_only=False)
    model.load_state_dict(ck["model"])
    model.eval()

    samples = collect_labels(env, args.turns, args.seed, args.teacher)
    print(f"权重 {args.ckpt}  采集 {len(samples)} 个老师动作（{args.turns} 回合）\n")

    tot: Counter = Counter()
    hits: dict = defaultdict(Counter)
    pred_kind: Counter = Counter()
    for s in range(0, len(samples), 256):
        chunk = samples[s:s + 256]
        (grid, glob, cand, mask), acts = pack([(o, i) for o, i, _ in chunk], model.n_tiles)
        with torch.no_grad():
            order = model(grid, glob, cand, mask)[0].argsort(dim=-1, descending=True)
        at = torch.as_tensor(acts).unsqueeze(1)
        ok = {k: (order[:, :k] == at).any(1).numpy() for k in KS}
        for b, (o, _i, tool) in enumerate(chunk):      # 计数只在**这一层**，
            tot[tool] += 1                             # 别放进 k 循环里（会重复计数）
            tot["全体"] += 1
            pred_kind[o.cand["actions"][int(order[b, 0])].kind] += 1
            for k in KS:
                if ok[k][b]:
                    hits[tool][k] += 1
                    hits["全体"][k] += 1

    print(f"{'类别':<10}{'样本':>7}" + "".join(f"{'top' + str(k):>9}" for k in KS))
    for tool in ["全体"] + sorted(t for t in tot if t != "全体"):
        n = tot[tool]
        if not n:
            continue
        print(f"{tool:<10}{n:>7}" + "".join(f"{hits[tool][k] / n:>9.1%}" for k in KS))

    # ---- argmax 落在哪一类：和老师的边际分布对照 ----
    # 这一栏才是「策略能不能干活」的直接证据。曾经模型 top10 里 82% 有 build，
    # 但 argmax 里 build 一次都没有——第一名叫一个与局面无关的常数偏置霸占，
    # 于是它从不建产能、从不移动。只看 top-k 看不出来，必须看 argmax 分布。
    print(f"\n{'类别':<10}{'老师占比':>10}{'模型argmax':>12}")
    for kind in KINDS:
        if tot.get(kind):
            print(f"{kind:<10}{tot[kind] / tot['全体']:>10.1%}"
                  f"{pred_kind[kind] / max(1, sum(pred_kind.values())):>12.1%}")


if __name__ == "__main__":
    main()
