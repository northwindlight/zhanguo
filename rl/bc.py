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

from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv
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
            kind = args.get("kind") or args.get("unit") or "步"   # v6 用 unit，旧版用 kind
            return ("recruit", str(kind), (x - 1, y - 1), 0, int(args.get("n", 1)))
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


def get_teacher(which: str):
    """老师：v6=用户第二版扩张流（最强，推荐）/ v3=第一版 / old=稳经济不扩张。"""
    if which == "v6":
        from expand_rule_v6 import expand_rule_turn_v6 as fn
    elif which == "v3":
        from expand_rule_ai import expand_rule_turn as fn
    else:
        from rule_ai import rule_turn as fn
    return fn


def collect_episode(env: ZhanguoEnv, turns: int, seed: int, teacher_fn=None,
                    student=None, dagger_every: int = 2):
    """跑一局，采 (观测, 候选下标)。返回 (样本, 该局消费, 未匹配数)。

    `student=None`：**老师自己走**（纯 BC，只覆盖老师的轨迹）。
    `student=模型`：**学生走、老师打标签**（DAgger，覆盖学生实际会走到的状态）。
    """
    teacher_fn = teacher_fn or get_teacher("v6")
    env.reset(seed)

    # ---------------- DAgger：学生走、老师打标签 ----------------
    # 只在**回合开头**问一次老师（在世界的副本上问，不污染真实局面）。
    # 每步都问的话要几千次 deepcopy + 老师规划，太贵；而回合边界恰好是学生
    # 偏航最明显的地方（比如它整局没建东西，回合 100 的局面和老师见过的完全
    # 不同），把那些状态补上标签就治住了主要漂移。
    #
    # dagger_every：**不是每回合都问**。Pi5 上一次 deepcopy+老师规划约 1.9s，
    # 500 回合全问 = 每局 15 分钟，12 局跑三小时。隔几回合问一次，
    # 标签少一些但覆盖的偏航状态足够，时间可控。
    if student is not None:
        import copy
        from rl.ppo import act as _act
        demos_d: list[tuple] = []
        miss_d = 0
        obs = env.reset(seed)
        rng = random.Random(seed)
        last_turn = -1
        while True:
            t = env.world.turn
            if t != last_turn and t % max(1, dagger_every) == 0:
                last_turn = t
                w2 = copy.deepcopy(env.world)
                got: list[tuple] = []
                teacher_fn(w2, env.agent, rng, max_actions=1,
                           on_action=lambda tool, args: got.append((tool, args)))
                if got:
                    i = match(obs.cand["actions"], to_action(*got[0]))
                    if i is None:
                        miss_d += 1
                    else:
                        demos_d.append((obs, i))
            idx, _lp, _v = _act(student, obs)
            obs, _r, done, _info = env.step(obs.cand["actions"][idx])
            if done:
                break
        return demos_d, env.world.spend_total(env.agent), miss_d

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
        # 老师**不限额**：实测它每回合最多 15 个动作（中位 8），所以 64 从来没卡住过，
        # 但那是"碰巧没卡住"。规则 AI 想动多少动多少，限额不该由我们来定。
        teacher_fn(env.world, env.agent, rng, max_actions=10 ** 9,
                   on_action=on_action, on_result=on_result)
        # **「何时停手」也要教**：规则 AI 从不发 end_turn 动作（它在 v6/rule_ai 里
        # 一次都没出现），干完活就直接返回。所以只采它做过的动作的话，数据集里
        # 根本没有 end_turn 这个示范——模型永远学不会停手，每回合一路磨到
        # MAX_ACTIONS 安全上限（实测 512 步/回合），评估和训练都被拖死。
        # 老师停手处的局面，正确答案就是 end_turn，补一条标签。
        o_end = env._obs()
        j = next((k for k, a in enumerate(o_end.cand["actions"])
                  if a.kind == "end_turn"), None)
        if j is not None:
            demos.append((o_end, j))
        env.world.resolve_turn()
        if t + 1 < turns:
            env.world.begin_turn()
    return demos, env.world.spend_total(env.agent), miss


def pack(chunk, n_tiles):
    """(obs, 下标) 列表 → (模型输入, 标签)。"""
    steps = [{"grid": o.grid, "glob": o.glob, "cand": o.cand, "act": i,
              "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False} for o, i in chunk]
    return collate(steps, n_tiles), np.array([i for _o, i in chunk])


def hit_rate(model, samples, n_tiles) -> float:
    """在给定样本上量命中率（老师动作是否被选中）。分批，别一次塞爆内存。"""
    if not samples:
        return float("nan")
    hit = tot = 0
    with torch.no_grad():
        for s in range(0, len(samples), 256):
            chunk = samples[s:s + 256]
            (grid, glob, cand, mask), acts = pack(chunk, n_tiles)
            pred = model(grid, glob, cand, mask)[0].argmax(-1).numpy()
            hit += int((pred == acts).sum())
            tot += len(acts)
    return hit / max(1, tot)


def main() -> None:
    ap = argparse.ArgumentParser(description="行为克隆冷启动（学 rule_ai 的模式）")
    ap.add_argument("--teacher", default="v6", choices=("v6", "v3", "old"),
                    help="老师：v6=用户第二版扩张流（五图均 141k，推荐）/ v3=第一版（82k）"
                         "/ old=稳经济不扩张（7k）")
    ap.add_argument("--episodes", type=int, default=30, help="跑多少局老师 AI 采样本")
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--map-size", type=int, default=16)
    # 每回合动作数的安全上界（不是游戏规则）。观测里那一维按固定 ACT_REF=64
    # 归一化，所以这个值改大改小**不再影响观测**，三个脚本之间也不用对齐。
    ap.add_argument("--max-actions", type=int, default=ACT_SAFETY)
    # 关键：训的是**整个回放缓冲**，不是本局。只训本局有两个死穴——
    # ① 灾难性遗忘：每局换了地图就把上一局学的冲掉；② 梯度步数被样本数绑死，
    # 24 局 × 4 epoch × 7 批 = 672 步，克隆一个规则 AI 差了两个数量级。
    ap.add_argument("--steps", type=int, default=250, help="每局采完后做多少梯度步")
    ap.add_argument("--dagger-every", type=int, default=1,
                    help="DAgger 每隔几回合问一次老师（1=每回合，不节流；"
                         "慢机器上可调大，代价是标签变稀）")
    ap.add_argument("--buffer", type=int, default=40000, help="回放缓冲上限（滚动窗口）")
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--minibatch", type=int, default=256)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--ckpt-every", type=int, default=4,
                    help="每几局存一个 ep<N>.pt（0=不存）——跑几小时的东西，"
                         "得能中途量分，不然只能干等")
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

    teacher_fn = get_teacher(args.teacher)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def save(path: Path) -> None:
        torch.save({"model": model.state_dict(), "iter": 0,
                    "args": {"source": "behavior_clone", "episodes": args.episodes,
                             "turns": args.turns, "map_size": args.map_size,
                             "max_actions": args.max_actions, "steps": args.steps,
                             "grad_steps": grad_steps}}, path)
    print(f"老师 = {args.teacher}（{teacher_fn.__module__}）"
          f"  每局 {args.steps} 梯度步  缓冲 {args.buffer}")
    rng = random.Random(args.seed ^ 0xBEEF)
    buffer: list = []
    val: list = []                 # 验证集：只用来量命中率，**永不参与训练**
    t0 = time.time()
    total_steps = 0
    grad_steps = 0
    for ep in range(args.episodes):
        # 后半程用 **DAgger**：让学生自己跑，再让老师在**学生走到的状态**上打标签。
        # 这是治「分布漂移」的标准药——只学老师的轨迹，学生一旦偏离就没标签了。
        use_student = (ep >= args.episodes // 2) and len(buffer) > 0
        demos, spend, miss = collect_episode(
            env, args.turns, seed=args.seed + ep, teacher_fn=teacher_fn,
            student=model if use_student else None, dagger_every=args.dagger_every)
        if not demos:
            print(f"第 {ep} 局没采到样本，跳过")
            continue
        total_steps += len(demos)
        # 每局留 10% 作验证集（滚动），训练碰不到
        cut = max(1, len(demos) // 10)
        val.extend(demos[:cut])
        buffer.extend(demos[cut:])
        if len(buffer) > args.buffer:
            buffer = buffer[-args.buffer:]
        if len(val) > 2000:
            val = val[-2000:]

        losses = []
        for _ in range(args.steps):
            chunk = [buffer[rng.randrange(len(buffer))] for _ in range(args.minibatch)]
            (grid, glob, cand, mask), acts = pack(chunk, model.n_tiles)
            logits, _v = model(grid, glob, cand, mask)
            logp = F.log_softmax(logits, dim=-1)
            loss = -logp.gather(1, torch.as_tensor(acts).unsqueeze(1)).mean()
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 0.5)
            opt.step()
            losses.append(float(loss.detach()))
            grad_steps += 1

        hit = hit_rate(model, val, model.n_tiles)
        # 这个消费数**两种模式含义不同**：纯 BC 局是老师的水平（~15 万），
        # DAgger 局是**学生自己走**打出来的（可能接近 0）——标错会误判成"老师崩了"。
        who = "学生" if use_student else "老师"
        print(f"局 {ep + 1}/{args.episodes}  样本 {len(demos)}(缓冲 {len(buffer)})  "
              f"{who}消费 {spend:,.0f}  未匹配 {miss}  loss {np.mean(losses):.3f}  "
              f"验证命中 {hit:.1%}  梯度步 {grad_steps}  累计 {time.time() - t0:.0f}s",
              flush=True)
        # 中途存点：只在跑完才存的话，想提前量一次分就得干等几小时。
        if args.ckpt_every and (ep + 1) % args.ckpt_every == 0:
            save(out.parent / f"ep{ep + 1}.pt")

    save(out)
    print(f"\nBC 完成：{total_steps} 个样本 · {grad_steps} 梯度步 → {out}")


if __name__ == "__main__":
    main()
