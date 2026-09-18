# -*- coding: utf-8 -*-
"""**撞墙率探针** —— 用户 2026-09-18：「模型一回合就撞 30 次墙……可以测的是，
新模型在撞墙上有没有改善，我觉得可能没有，**熵占主导**」。

## 为什么这一条比成绩那把尺更硬

`--invalid-penalty` 是 reward 里**唯一**那个"密集 + 与动作直接挂钩 + 方向一致"的项
（每被引擎拒一次扣 `invalid_penalty` 消费）。它**正是** `pg` 缺的那种信号。
所以："连它都教不会模型少撞墙" ⇒ 对"熵主导、策略梯度不转向"的**行为层铁证**。

而它还有个好处：**便宜**。撞墙率是**逐步密集统计**，一局 200 回合就有上千个动作样本，
不像成绩那把尺要靠 5 张图的中位数。**一两个种子就够**。

## 口径

采样臂（用户口径：判据只看采样），`torch.manual_seed` 内部固定 ⇒ 可复现。
同时报出 reward 的**分解**（消费项 / 撞墙惩罚项 / 占地项），回答
「一回合扣 60 的惩罚 vs 占地给 10」到底谁在主导信号。

用法：
    python experiments/probe_invalid_rate.py <ckpt> [<ckpt> ...] [--seed 900000] [--reps 2]
    ZHANGUO_THREADS=1 ...   # ECS 是 1 物理核
"""
from __future__ import annotations

import os
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch  # noqa: E402

from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv  # noqa: E402
from rl.hw import set_threads  # noqa: E402
from rl.ppo import act, policy_logits  # noqa: E402
from rl.tokenize import GROUPS, tokenize  # noqa: E402
from rl.transformer import WindowTransformer  # noqa: E402


def _opt(name, default):
    return type(default)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


CKPTS = [a for a in sys.argv[1:] if not a.startswith("--") and not a.lstrip("-").isdigit()]
SEED = _opt("--seed", 900000)
DET = "--det" in sys.argv            # 贪心臂：分开"模仿没学会"与"采样抽出来的"
WANT_ENT = "--ent" in sys.argv       # 顺带量**同一批状态**上的动作分布熵
REPS = _opt("--reps", 2)
TURNS = _opt("--turns", 200)
PEN = _opt("--invalid-penalty", 2.0)      # 与炉子同口径
LAND = _opt("--land-bonus", 0.0)
set_threads(int(os.environ.get("ZHANGUO_THREADS", "4")))

env = ZhanguoEnv(map_size=16, max_turns=TURNS, max_actions_per_turn=ACT_SAFETY,
                 invalid_penalty=PEN, land_bonus=LAND)
env.reset(0)
_w = tokenize(env, env._obs())
RS = env.reward_scale


def load(p: str):
    m = WindowTransformer({g: _w.feats[g].shape[1] for g in GROUPS},
                          d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    ck = torch.load(p, map_location="cpu", weights_only=False)
    miss, _ = m.load_state_dict(ck["model"], strict=False)
    m.eval()
    print(f"  载入 {p}（缺失 {len(miss)} 项）", flush=True)
    return m


def run(model, seed: int, rep: int) -> dict:
    """跑一局，数步数/撞墙/回合，并把 reward 拆成三块。"""
    # ★采样臂**必须播种**（`act` 走 `torch.multinomial`）—— 不播种的话同一 ckpt
    #   两次跑出完全不同的结果，任何结论都是假的（`PLAN.md` §六 #4 那条）。
    torch.manual_seed(0x5EED + rep)
    obs = env.reset(seed)
    steps = rej = turn_ends = 0
    pen_sum = 0.0
    ent_sum = 0.0
    by_kind: dict[str, int] = {}
    while True:
        w = tokenize(env, obs)
        if WANT_ENT:
            # ★与"撞墙率"**同一批状态**上量熵 —— 拿训练日志里的 `ent` 跨图比是错的
            #   （实测 115 的 ent 更低却撞得更多，因为非法候选占比还取决于走到什么状态）。
            with torch.no_grad():
                _lg, _v0, _ = policy_logits(model, obs, win=w)
                _p = torch.softmax(_lg, dim=-1)
                ent_sum += float(-(_p * torch.log(_p + 1e-12)).sum(-1).mean())
        i, _lp, _v = act(model, obs, deterministic=DET, win=w)
        a = obs.cand["actions"][i]
        obs, _r, done, info = env.step(a)
        steps += 1
        if not info["ok"]:
            rej += 1
            pen_sum += PEN * RS
            # ★分动作类型数：只给一个总数说不清是"寻路选不到合法格"还是"挑错了目标"，
            #   而这两者的处方完全不同（前者是表征/候选集问题，后者是策略问题）。
            by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        if a.kind == "end_turn":
            turn_ends += 1
        if done:
            break
    s = env.summary()
    # ★**不截断**（2026-09-18 用户抓的）：「只打前 4」会让人把"没显示"读成"没有" ——
    #   我自己就据此说过"sell/recruit 已经降下来了"，而实际它们还在（170：sell 283）。
    #   尺子不许自己吃掉信息，全打。
    top = " ".join(f"{k}:{v}" for k, v in sorted(by_kind.items(), key=lambda kv: -kv[1]))
    return {"steps": steps, "rej": rej, "turns": turn_ends,
            "spend": s["spend_total"], "tiles": s["tiles"],
            "con_rew": s["spend_total"] * RS, "pen_rew": pen_sum,
            "ent": ent_sum / max(steps, 1), "kinds": top}


if not CKPTS:
    print("（没给 ckpt —— 只跑不了；至少给一个）")


t0 = time.time()
print(f"种子 {SEED}，各 {TURNS} 回合（{'**贪心臂**' if DET else '采样臂'}，"
      f"torch 种子内部固定）；撞墙惩罚 {PEN} 消费/次\n", flush=True)
print(f"{'ckpt':<22}{'步/回合':>9}{'撞墙/回合':>11}{'消费':>10}{'地':>6}"
      f"{'消费reward':>12}{'惩罚reward':>12}{'惩罚占比':>10}{'动作熵':>9}   被拒构成（前 4）", flush=True)

for p in CKPTS:
    m = load(p)
    rs = [run(m, SEED, r) for r in range(REPS)]
    steps = st.mean(x["steps"] / max(x["turns"], 1) for x in rs)
    rej = st.mean(x["rej"] / max(x["turns"], 1) for x in rs)
    spend = st.median(x["spend"] for x in rs)
    tiles = st.median(x["tiles"] for x in rs)
    con = st.median(x["con_rew"] for x in rs)
    pen = st.median(x["pen_rew"] for x in rs)
    share = pen / (con + pen) if (con + pen) else 0.0
    kinds = max(rs, key=lambda x: x["rej"])["kinds"]
    print(f"{Path(p).stem:<22}{steps:>9.1f}{rej:>11.1f}{spend:>10,.0f}{tiles:>6.0f}"
          f"{con:>12.1f}{pen:>12.1f}{share:>10.0%}{st.median(x['ent'] for x in rs):>9.2f}   {kinds}", flush=True)

print(f"\n总耗时 {time.time() - t0:.0f}s", flush=True)
