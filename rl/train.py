# -*- coding: utf-8 -*-
"""战国 RL 训练入口：PPO 训一个"只认总消费"的君主。

目标函数 = **终局总消费**（world.spend_total：建造 + 征兵 + 军费，按当时市价折金）。
奖励 = 每步总消费增量（Σ 增量 ≡ 终局总消费，密集但不改目标）。

一局跑满 500 回合 ≈ 9k 步——经济要滚复利，短局看不出名堂。长局按
`--rollout-steps` 分块做 PPO（块边界用 value 自举），只是把长局切成可训练的小段，
**不改目标函数**。

    python3 -m rl.train --map-size 16 --turns 500 --iterations 100
    python3 -m rl.train --eval-only --ckpt rl/runs/single16/last.pt --episodes 3

产物：rl/runs/<run>/ 下 model.pt / last.pt / log.csv / config.json
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
from rl.ppo import PPO, Rollout, act, value_of


def build_env(args) -> ZhanguoEnv:
    return ZhanguoEnv(map_size=args.map_size, seed=args.seed, agent=args.agent,
                      max_turns=args.turns, max_actions_per_turn=args.max_actions,
                      reward_scale=args.reward_scale)


def build_model(env: ZhanguoEnv) -> PolicyNet:
    return PolicyNet(n_grid_ch=len(env.obs_channels()), n_glob=env.glob_size(),
                     sub_sizes=[len(env.sub_tables[k]) for k in KINDS],
                     n_tiles=env.map_size ** 2)


def play_episode(env: ZhanguoEnv, model: PolicyNet, seed: int, deterministic: bool = False) -> dict:
    """跑完整一局（不训练），用于评估。"""
    obs = env.reset(seed)
    total = 0.0
    while True:
        idx, _lp, _v = act(model, obs, deterministic=deterministic)
        obs, r, done, _info = env.step(obs.cand["actions"][idx])
        total += r
        if done:
            break
    s = env.summary()
    s["ep_return"] = total
    s["seed"] = seed
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description="战国 RL 训练（PPO，目标=总消费）")
    ap.add_argument("--map-size", type=int, default=16)
    ap.add_argument("--turns", type=int, default=500, help="每局回合上限（经济滚复利，短局没意义）")
    ap.add_argument("--agent", default="秦")
    ap.add_argument("--max-actions", type=int, default=24, help="每回合动作上限")
    ap.add_argument("--reward-scale", type=float, default=0.01)
    ap.add_argument("--iterations", type=int, default=100, help="PPO 更新块数（每块 rollout-steps 步）")
    ap.add_argument("--rollout-steps", type=int, default=2048, help="每次更新前采多少步")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--minibatch", type=int, default=512)
    ap.add_argument("--ent-coef", type=float, default=0.01)
    ap.add_argument("--lam", type=float, default=0.99,
                    help="GAE λ：长局信用传播要拉长（目标本身不折扣，λ 只影响方差/偏差）")
    ap.add_argument("--threads", type=int, default=4, help="torch CPU 线程数")
    ap.add_argument("--out", default="rl/runs/single16")
    ap.add_argument("--resume", default="", help="从 checkpoint 续训")
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--eval-every", type=int, default=10)
    ap.add_argument("--eval-episodes", type=int, default=2)
    args = ap.parse_args()

    torch.set_num_threads(max(1, args.threads))
    # 采样是 batch=1 的逐步前向：多线程的同步开销远大于收益（实测 4 线程 34.8ms/步
    # vs 单线程 7.7ms/步）。所以采样期间切单线程，PPO 更新（大 batch）再切回来。
    def set_collect_threads():
        torch.set_num_threads(1)

    def set_train_threads():
        torch.set_num_threads(max(1, args.threads))
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    env = build_env(args)
    model = build_model(env)
    ppo = PPO(model, lr=args.lr, epochs=args.epochs, minibatch=args.minibatch,
              ent_coef=args.ent_coef)
    start_iter = 0
    ck = None
    if args.resume:
        ck = torch.load(args.resume, map_location="cpu", weights_only=False)
        model.load_state_dict(ck["model"])
        start_iter = int(ck.get("iter", 0))
        # 优化器状态必须一起恢复：否则每次重启都是全新 Adam，
        # 第一步更新幅度异常大，会把策略踹坏（服务管理器自动重启时尤其致命）。
        if ck.get("opt"):
            try:
                ppo.opt.load_state_dict(ck["opt"])
                print("（含优化器状态）")
            except Exception as e:
                print(f"（优化器状态载入失败，忽略：{type(e).__name__}: {e}）")
        print(f"续训自 {args.resume}（第 {start_iter} 块）")

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "config.json").write_text(json.dumps(vars(args), ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    # 评估必须用**独立 env**：play_episode 会把环境跑到 done，
    # 借用训练 env 的话下一步采样就会撞 "env 未 reset 或已结束"。
    eval_env = build_env(args)

    def evaluate(n: int) -> dict:
        sp = [play_episode(eval_env, model, seed=900_000 + i, deterministic=True)
              for i in range(n)]
        return {"eval_spend": float(np.mean([s["spend_total"] for s in sp])),
                "eval_tiles": float(np.mean([s["tiles"] for s in sp])),
                "eval_armies": float(np.mean([s["armies"] for s in sp]))}

    if args.eval_only:
        print("评估：", evaluate(args.eval_episodes))
        return

    writer = None
    csv_fh = None
    t0 = time.time()
    seed = args.seed
    obs = env.reset(seed)
    rollout = Rollout(lam=args.lam)
    ep_ret, ep_steps, ep_done = 0.0, 0, False
    eps: list[dict] = []          # 已完成的局

    for it in range(start_iter + 1, start_iter + args.iterations + 1):
        # ---- 采样：填满一块（可能跨局）
        set_collect_threads()
        while len(rollout) < args.rollout_steps:
            if ep_done:                     # 上一局结束，开新局
                s = env.summary()
                s["ep_return"] = ep_ret
                s["ep_steps"] = ep_steps
                eps.append(s)
                seed += 1
                obs = env.reset(seed)
                ep_ret, ep_steps, ep_done = 0.0, 0, False
            idx, logp, val = act(model, obs)
            keep = obs
            obs, r, done, info = env.step(obs.cand["actions"][idx])
            rollout.add(keep, idx, logp, val, r, done)
            ep_ret += r
            ep_steps += 1
            ep_done = done

        # ---- 更新（块边界自举；局末则 0）
        set_train_threads()
        last_v = 0.0 if ep_done else value_of(model, obs)
        stats = ppo.update(rollout, last_value=last_v)
        rollout.clear()

        recent = eps[-3:]
        # 注意：row 的键必须在**每一块**都齐全——csv.DictWriter 的列头取自第一块，
        # 评估列若只在评估块才出现，writerow 会抛 "dict contains fields not in fieldnames"
        # （曾因此每 50 块崩一次、被服务管理器反复重启）。
        row = {
            "eval_spend": float("nan"), "eval_tiles": float("nan"),
            "eval_armies": float("nan"),
            "iter": it, "env_steps": (it * args.rollout_steps), "secs": round(time.time() - t0, 1),
            "episodes": len(eps),
            "last_spend": float(recent[-1]["spend_total"]) if recent else float("nan"),
            "mean_spend": float(np.mean([s["spend_total"] for s in recent])) if recent else float("nan"),
            "last_tiles": float(recent[-1]["tiles"]) if recent else float("nan"),
            "last_armies": float(recent[-1]["armies"]) if recent else float("nan"),
            **{k: round(v, 4) for k, v in stats.items()},
        }
        if args.eval_every and it % args.eval_every == 0:
            row.update(evaluate(args.eval_episodes))
        print(" | ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                         for k, v in row.items()), flush=True)
        if writer is None:
            csv_fh = open(out / "log.csv", "w", newline="", encoding="utf-8")
            writer = csv.DictWriter(csv_fh, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)
        csv_fh.flush()                      # 每块落盘：别让监控去猜缓冲区
        # 状态文件：不依赖 stdout（服务/nssm 下 stdout 未必接到文件）
        (out / "status.txt").write_text(
            f"iter={it} steps={it * args.rollout_steps} secs={row['secs']} "
            f"episodes={len(eps)} last_spend={row['last_spend']:.0f} "
            f"mean_spend={row['mean_spend']:.0f} tiles={row['last_tiles']:.0f} "
            f"armies={row['last_armies']:.0f} ent={row['ent']:.3f} "
            + (f"eval_spend={row['eval_spend']:.0f} eval_tiles={row['eval_tiles']:.0f}"
               if row["eval_spend"] == row["eval_spend"] else "eval_spend=-"),
            encoding="utf-8")
        blob = {"model": model.state_dict(), "opt": ppo.opt.state_dict(),
                "iter": it, "args": vars(args)}
        torch.save(blob, out / "last.pt")
        torch.save(blob, out / "model.pt")
    print(f"训练结束，用时 {time.time() - t0:.0f}s，产物在 {out}/")


if __name__ == "__main__":
    main()
