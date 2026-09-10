# -*- coding: utf-8 -*-
"""行为克隆冷启动：先跟 rule_ai 学会「模式」，再交给 PPO 微调。

为什么需要它：从零 RL 时，所有配置都卡在同一处——**探索不出「复利链」**
（建产能 → 出兵 → 占地 → 再建产能）。这条链要几十回合才回本，而策略在学会它
之前就已经塌进「少做事」的局部最优（ent → 0.1，局末消费掉到 1 千）。

BC 把最难的那一步用现成的规则 AI 直接灌进去：rule_ai 虽然不会扩张（固定 5 块地），
但它会建产能、会卖余量换现金、会征兵——**正是策略缺的那个"会做事"的先验**。

做法：拿 rule_ai 跑局，在它**每次动手前**抓一帧观测（那一刻的状态就是该动作的输入），
把它的动作映射成 RL 候选清单里的下标，监督训练（交叉熵）。

    python3 -m rl.bc --episodes 30 --out rl/runs/bc/last.pt
    # 之后照常 PPO，从这份权重起步：
    python3 -m rl.train ... --resume rl/runs/bc/last.pt
"""
from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import collate
from rule_ai import rule_turn


def to_action(tool: str, args: dict):
    """rule_ai 的 (tool, args) → (kind, sub, tile, army, amount)。坐标转 0-based。"""
    try:
        if tool == "build":
            x, y = map(int, str(args["tile"]).split())
            return ("build", str(args["building"]), (x - 1, y - 1), 0, 1)
        if tool == "recruit":
            x, y = map(int, str(args["tile"]).split())
            return ("recruit", str(args.get("kind", "步")), (x - 1, y - 1), 0, int(args.get("n", 1)))
        if tool == "move":
            return ("move", "", (int(args["x"]) - 1, int(args["y"]) - 1),
                    int(args["army_id"]), 1)
        if tool == "attack":
            ids = args.get("army_ids") or [args.get("army_id")]
            return ("attack", "", (int(args["x"]) - 1, int(args["y"]) - 1), int(ids[0]), 1)
        if tool in ("buy", "sell"):
            return (tool, str(args["good"]), None, 0, int(args.get("qty", 1)))
    except (KeyError, TypeError, ValueError):
        return None
    return None


def match(actions, spec):
    """把规则 AI 的动作对到候选清单的下标；数量档对不上就退而求其次（同类别）。"""
    if spec is None:
        return None
    kind, sub, tile, army, amount = spec
    fallback = None
    for i, a in enumerate(actions):
        if a.kind != kind:
            continue
        if sub and a.sub != sub:
            continue
        if tile is not None and a.tile != tile:
            continue
        if army and a.army != army:
            continue
        if a.amount == amount:
            return i
        if fallback is None:
            fallback = i
    return fallback


def collect_episode(env: ZhanguoEnv, turns: int, seed: int):
    """跑一局 rule_ai，逐步抓 (观测, 候选下标)。返回 (样本, 该局消费, 未匹配数)。"""
    env.reset(seed)
    demos: list[tuple] = []
    miss = 0
    pending: dict = {}

    def on_action(tool, args):
        # 只抓状态，先不入库——规则 AI 会尝试注定失败的动作（资源不够的建造），
        # 那些动作没有对应的合法候选，混进数据集只会教坏策略。
        pending["obs"] = env._obs()           # 动作执行**之前**的状态
        pending["spec"] = to_action(tool, args)

    def on_result(tool, args, ok):
        nonlocal miss
        if not ok:
            return
        i = match(pending["obs"].cand["actions"], pending["spec"])
        if i is None:
            miss += 1
        else:
            demos.append((pending["obs"], i))

    rng = random.Random(seed)
    for t in range(turns):
        rule_turn(env.world, env.agent, rng, max_actions=64,
                  on_action=on_action, on_result=on_result)
        env.world.resolve_turn()
        if t + 1 < turns:
            env.world.begin_turn()
    return demos, env.world.spend_total(env.agent), miss


def bc_step_batches(demos, minibatch, n_tiles):
    for s in range(0, len(demos), minibatch):
        chunk = demos[s:s + minibatch]
        steps = [{"grid": o.grid, "glob": o.glob, "cand": o.cand, "act": i,
                  "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False} for o, i in chunk]
        yield collate(steps, n_tiles), np.array([i for _o, i in chunk])


def main() -> None:
    ap = argparse.ArgumentParser(description="行为克隆冷启动（学 rule_ai 的模式）")
    ap.add_argument("--episodes", type=int, default=30, help="跑多少局规则 AI 采样本")
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--max-actions", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=4, help="每局样本训几遍")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--minibatch", type=int, default=512)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--out", default="rl/runs/bc/last.pt")
    args = ap.parse_args()

    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)

    env = ZhanguoEnv(map_size=args.map_size, max_turns=args.turns,
                     max_actions_per_turn=args.max_actions)
    # 先 reset 一次拿到 obs 维度
    env.reset(0)
    model = PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                      sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                      n_tiles=args.map_size ** 2)
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    total_steps = 0
    for ep in range(args.episodes):
        demos, spend, miss = collect_episode(env, args.turns, seed=args.seed + ep)
        if not demos:
            print(f"第 {ep} 局没采到样本，跳过")
            continue
        total_steps += len(demos)
        losses = []
        for _ in range(args.epochs):
            for (grid, glob, cand, mask), acts in bc_step_batches(demos, args.minibatch,
                                                                  model.n_tiles):
                logits, _v = model(grid, glob, cand, mask)
                logp = F.log_softmax(logits, dim=-1)
                loss = -logp.gather(1, torch.as_tensor(acts).unsqueeze(1)).mean()
                opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
                opt.step()
                losses.append(float(loss))
        # 训练集上的命中率（前 200 个样本）
        with torch.no_grad():
            (grid, glob, cand, mask), acts = next(bc_step_batches(demos[:200], 200,
                                                                  model.n_tiles))
            hit = (model(grid, glob, cand, mask)[0].argmax(-1).numpy() == acts).mean()
        print(f"局 {ep + 1}/{args.episodes}  样本 {len(demos)}  规则AI消费 {spend:,.0f}  "
              f"未匹配 {miss}  loss {np.mean(losses):.3f}  命中率 {hit:.1%}  "
              f"累计 {time.time() - t0:.0f}s", flush=True)

    torch.save({"model": model.state_dict(), "iter": 0,
                "args": {"source": "behavior_clone", "episodes": args.episodes,
                         "turns": args.turns, "map_size": args.map_size,
                         "max_actions": args.max_actions}}, out)
    print(f"\nBC 完成：{total_steps} 个样本 → {out}")


if __name__ == "__main__":
    main()
