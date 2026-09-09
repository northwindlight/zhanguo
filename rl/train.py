# -*- coding: utf-8 -*-
"""战国 RL 训练入口：PPO 训一个"只认总消费"的君主。

目标函数 = **终局总消费**（world.spend_total：建造 + 征兵 + 军费，按当时市价折金）。
奖励 = 每步总消费增量（Σ 增量 ≡ 终局总消费，密集但不改目标）。

    python3 -m rl.train --map-size 16 --turns 30 --iterations 50
    python3 -m rl.train --eval-only --ckpt rl/runs/default/last.pt --episodes 10

产物：rl/runs/<run>/ 下 model.pt（每轮覆盖）、last.pt、log.csv、config.json
"""
from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import torch

from rl.env import KINDS, ZhanguoEnv
from rl.model import PolicyNet
from rl.ppo import PPO, Rollout, act


def build_env(args) -> ZhanguoEnv:
    return ZhanguoEnv(map_size=args.map_size, seed=args.seed, agent=args.agent,
                      max_turns=args.turns, max_actions_per_turn=args.max_actions,
                      reward_scale=args.reward_scale)


def build_model(env: ZhanguoEnv) -> PolicyNet:
    return PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                     sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                     n_tiles=env.map_size ** 2)


def run_episode(env: ZhanguoEnv, model: PolicyNet, seed: int, rollout: Rollout | None,
                deterministic: bool = False) -> dict:
    obs = env.reset(seed)
    ep_ret = 0.0
    steps = 0
    while True:
        idx, logp, val = act(model, obs, deterministic=deterministic)
        action = obs.cand["actions"][idx]
        if rollout is not None:
            keep = obs
        obs, r, done, info = env.step(action)
        if rollout is not None:
            rollout.add(keep, idx, logp, val, r, done)
        ep_ret += r
        steps += 1
        if done:
            break
    s = env.summary()
    s.update({"ep_return": ep_ret, "steps": steps, "seed": seed})
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="战国 RL 训练（PPO，目标=总消费）")
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--turns", type=int, default=30, help="每局回合上限")
    ap.add_argument("--agent", default="秦")
    ap.add_argument("--max-actions", type=int, default=24, help="我方每回合动作上限")
    ap.add_argument("--reward-scale", type=float, default=0.01)
    ap.add_argument("--iterations", type=int, default=50)
    ap.add_argument("--episodes", type=int, default=1, help="每轮采样几局")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--minibatch", type=int, default=256)
    ap.add_argument("--ent-coef", type=float, default=0.01)
    ap.add_argument("--threads", type=int, default=4, help="torch CPU 线程数")
    ap.add_argument("--out", default="rl/runs/default")
    ap.add_argument("--resume", default="", help="从 checkpoint 续训")
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--eval-episodes", type=int, default=5)
    ap.add_argument("--log-every", type=int, default=1)
    args = ap.parse_args()

    torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    env = build_env(args)
    model = build_model(env)
    if args.resume:
        ck = torch.load(args.resume, map_location="cpu", weights_only=False)
        model.load_state_dict(ck["model"])
        print(f"续训自 {args.resume}（第 {ck.get('iter', '?')} 轮）")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(vars(args), ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def evaluate(episodes: int) -> dict:
        rs, sp = [], []
        for i in range(episodes):
            s = run_episode(env, model, seed=10_000 + i, rollout=None, deterministic=True)
            rs.append(s["spend_total"])
            sp.append(s["tiles"])
        return {"eval_spend": float(np.mean(rs)), "eval_tiles": float(np.mean(sp))}

    if args.eval_only:
        print("评估：", evaluate(args.eval_episodes))
        return

    ppo = PPO(model, lr=args.lr, epochs=args.epochs, minibatch=args.minibatch,
              ent_coef=args.ent_coef)
    csv_path = out / "log.csv"
    writer = None
    t0 = time.time()
    for it in range(1, args.iterations + 1):
        rollout = Rollout()
        eps = []
        for e in range(args.episodes):
            eps.append(run_episode(env, model, seed=args.seed + it * 100 + e, rollout=rollout))
        stats = ppo.update(rollout)
        row = {
            "iter": it, "steps": len(rollout), "secs": round(time.time() - t0, 1),
            "ep_return": float(np.mean([s["ep_return"] for s in eps])),
            "spend_total": float(np.mean([s["spend_total"] for s in eps])),
            "spend_build": float(np.mean([s["spend_build"] for s in eps])),
            "spend_recruit": float(np.mean([s["spend_recruit"] for s in eps])),
            "spend_supply": float(np.mean([s["spend_supply"] for s in eps])),
            "tiles": float(np.mean([s["tiles"] for s in eps])),
            "armies": float(np.mean([s["armies"] for s in eps])),
            "alive": float(np.mean([1.0 if s["alive"] else 0.0 for s in eps])),
            **{k: round(v, 4) for k, v in stats.items()},
        }
        if args.eval_every and it % args.eval_every == 0:
            row.update(evaluate(args.eval_episodes))
        if it % args.log_every == 0 or it == 1:
            print(" | ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                             for k, v in row.items()), flush=True)
        if writer is None:
            writer = csv.DictWriter(open(csv_path, "w", newline="", encoding="utf-8"),
                                    fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)
        torch.save({"model": model.state_dict(), "iter": it, "args": vars(args)}, out / "last.pt")
        torch.save({"model": model.state_dict(), "iter": it, "args": vars(args)}, out / "model.pt")
    print(f"训练结束，用时 {time.time() - t0:.0f}s，产物在 {out}/")


if __name__ == "__main__":
    main()
