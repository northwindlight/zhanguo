# -*- coding: utf-8 -*-
"""按**标签类别**拆开看 BC 拟合命中率 —— 判断"稀疏但决定性的动作学会没有"。

    ~/.venv/bin/python -m experiments.bc_class_hit rl/runs/bc/ep12.pt [--turns 70] [--seed 500000]

为什么需要它（2026-09-12 实测）：总命中率是个**会骗人的平均数**。
一局 482 个样本里，`sell` 占 259、`end_turn` 70，而 `build` 只有 19、`recruit` 只有 3。
总命中率 30% 看着"还行"，拆开却是：

    end_turn 76% / sell:石油 100% / sell:木头 0% / move 3% / attack 0% / build:林场 29%

→ **它只学会了高频且不需要分辨的类**；`move`/`attack`（扩张动作）命中 ≈ 0 ——
  这才是"零扩张"的真正原因。同一张表也能证伪"是标签噪声"：
  实测 `move` 39/39、`attack` 24/24 **全部精确匹配**，所以 0% 是**没学会**。

配套：`--labels` 只查标签质量（老师的动作在候选集里有精确对应吗），不加载模型。
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from rl.bc import collect_episode, get_teacher, match  # noqa: E402
from rl.env import KINDS, ZhanguoEnv  # noqa: E402
from rl.tokenize import GROUPS  # noqa: E402


def label_quality(env, teacher, turns: int, seed: int) -> None:
    """老师每个动作在候选集里是**精确**命中，还是被 `match()` 兜底降级？"""
    import rl.bc as bc
    stats = collections.Counter()
    ex = collections.defaultdict(list)
    orig = bc.match

    def spy(actions, spec):
        i = orig(actions, spec)
        if spec is None:
            stats["—|spec_none"] += 1
            return i
        kind = spec[0]
        if i is None:
            stats[f"{kind}|none"] += 1
            return i
        a = actions[i]
        exact = (a.kind == spec[0] and (not spec[1] or a.sub == spec[1])
                 and (spec[2] is None or a.tile == spec[2])
                 and (not spec[3] or a.army == spec[3]) and a.amount == spec[4])
        stats[f"{kind}|{'exact' if exact else 'fallback'}"] += 1
        if not exact and len(ex[kind]) < 3:
            ex[kind].append((spec, (a.kind, a.sub, a.tile, a.army, a.amount)))
        return i

    bc.match = spy
    try:
        demos, _spend, _miss = collect_episode(env, turns, seed=seed, teacher_fn=teacher)
    finally:
        bc.match = orig
    print(f"\n标签质量（{len(demos)} 个样本）")
    print(f"{'类别':>10}{'精确':>7}{'兜底':>7}{'无候选':>8}")
    for k in sorted({x.split('|')[0] for x in stats}):
        print(f"{k:>10}{stats[f'{k}|exact']:>7}{stats[f'{k}|fallback']:>7}"
              f"{stats[f'{k}|none']:>8}")
    for k, items in ex.items():
        for spec, got in items:
            print(f"    {k}: 想要 {spec} → 给成 {got}")


def class_hits(ckpt: str, env, teacher, turns: int, seed: int) -> None:
    from rl.model import PolicyNet
    from rl.transformer import WindowTransformer
    from rl.tokenize import tokenize

    demos, spend, miss = collect_episode(env, turns, seed=seed, teacher_fn=teacher,
                                         with_window=True)
    obs0 = demos[0][0]
    ck = torch.load(ckpt, map_location="cpu", weights_only=False)
    net = (ck.get("args") or {}).get("net", "mlp")
    sub_sizes = [len(env.sub_tables[k]) for k in KINDS]
    if net == "tf":
        w0 = demos[0][3]
        model = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                                  d_model=192, n_layer=4, n_head=4)
        model.set_sub_sizes(sub_sizes)
        use_win = True
    else:
        model = PolicyNet(len(env.obs_channels()), env.glob_size(), sub_sizes,
                          obs0.grid.shape[1] * obs0.grid.shape[2])
        use_win = False
    model.load_state_dict(ck["model"])
    model.eval()

    hit, tot = collections.Counter(), collections.Counter()
    with torch.no_grad():
        for s in demos:
            o, lab = s[0], s[1]
            w = s[3] if use_win else None
            if use_win:
                from rl.ppo import act as _act
                i, _lp, _v = _act(model, o, deterministic=True, win=w)
            else:
                import numpy as np
                from rl.ppo import collate
                grid, glob, cand, mask = collate([{"grid": o.grid, "glob": o.glob,
                                                   "cand": o.cand}], need_grid=True)
                logits, _v = model(grid, glob, cand, mask)
                i = int(logits[0].argmax())
            a = o.cand["actions"][lab]
            key = a.kind + (f":{a.sub}" if a.sub else "")
            tot[key] += 1
            hit[key] += int(i == lab)
    print(f"\n按标签类别的命中率（{len(demos)} 个样本，老师消费 {spend:,.0f}，"
          f"未匹配 {miss}）")
    print(f"{'类别':>16}{'样本':>7}{'命中':>8}")
    for k, n in tot.most_common(20):
        print(f"{k:>16}{n:>7}{hit[k] / n:>8.0%}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("ckpt", nargs="?", default="", help="留空则只查标签质量（--labels）")
    ap.add_argument("--labels", action="store_true", help="只查标签质量，不加载模型")
    ap.add_argument("--turns", type=int, default=70)
    ap.add_argument("--seed", type=int, default=500000)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--teacher", default="v10")
    a = ap.parse_args()

    env = ZhanguoEnv(map_size=a.map_size, max_turns=a.turns)
    teacher = get_teacher(a.teacher, a.turns)
    if a.labels or not a.ckpt:
        label_quality(env, teacher, a.turns, a.seed)
    if a.ckpt:
        class_hits(a.ckpt, env, teacher, a.turns, a.seed)


if __name__ == "__main__":
    main()
