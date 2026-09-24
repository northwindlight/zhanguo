# -*- coding: utf-8 -*-
"""自对弈 + PPO（沙盒版，**一个文件**）。

    为什么合成一个（旧线是 `ppo.py` + `workers.py` + `train.py` 三件套）
    ─────────────────────────────────────────────────────────────────
    那三件套是为"**全动作空间 + 并行 worker 收集**"设计的（每步 K≈300 候选、
    一局 9k 步、要跨进程广播权重）。沙盒小得多：K≈40、一局几十步、单进程足够
    ⇒ 合起来一个文件反而看得清。

    三件事，各一段
    ──────────────
      ① `collect_episode` —— **两份独立网络**（`W_甲` vs `W_乙`）对打一局，
         落每步的 `(obs, cand, mask, action_idx, logprob, value, reward)`
      ② `gae` + `ppo_update` —— 标准 PPO clip（GAE-λ 优势）
      ③ `train` —— 主循环：collect → update → 评估

    ★★ 奖励 = **打分器的差分**（用户 2026-09-24：「有打分器的话就不用 bc 了，直接 ppo 对抗」）
    ──────────────────────────────────────────────────────────────────────
        reward_t = score(s_{t+1}) − score(s_t)          （非终局）
        终局     = ±1                                    （丢家直接输）
    这是 **potential-based shaping**（势函数差分）⇒ 理论保证**不改变最优策略**、
    不引入 reward hacking。它把"稀疏奖励 + 纯 PPO 学不动"那个死结从根上消掉：
    BC 与 MCTS **都不需要**。

    ★ 自对弈的**两份网络各练各的**：甲的经验只喂 `W_甲`，乙的只喂 `W_乙`。
      对手在动（非平稳），但能防"自己克自己"（用户 2026-09-24 的选择）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn

from . import encode, evaluate
from . import vocab as V
from .model import PolicyNet
from .sandbox import END, PLAYERS, Sandbox


# ================================================================ 一条样本
@dataclass
class Step:
    grid: np.ndarray
    glob: np.ndarray
    cand: np.ndarray
    mask: np.ndarray
    army: np.ndarray
    army_mask: np.ndarray
    cand_xy: np.ndarray    # 候选的目标格索引（网络拿它 gather 空间特征）
    aidx: int              # 采到的候选下标
    logp: float
    value: float
    reward: float
    done: bool
    player: str


def obs_of(sb, me: str) -> dict:
    """沙盒局面 → 网络要的一整套张量（**候选与 `legal()` 一一对应**）。"""
    cand = encode.candidate_features(sb)
    return {
        "grid": encode.encode_grid(sb, me),
        "glob": encode.encode_glob(sb, me),
        "cand": cand,
        "mask": np.ones(len(cand), dtype=bool),
        "army": encode.encode_armies(sb, me),
        "army_mask": None,          # 下面按实际条数补
        "cand_xy": encode.candidate_xy(sb),
    }


# ================================================================ 批次拼装
def collate(rows: list[dict]) -> dict:
    """把一串单样本拼成批次（候选数**变长** ⇒ 补零 + mask）。

    ★ 候选补齐用 0 行 + `mask=False`：`PolicyNet` 会把它们 `masked_fill(-1e9)`
      ⇒ 不参与 softmax，也不产生梯度。
    """
    b = len(rows)
    k = max(r["cand"].shape[0] for r in rows)
    n = max((r["army"].shape[0] for r in rows), default=1) or 1
    cw = rows[0]["cand"].shape[1]
    aw = rows[0]["army"].shape[1] if rows[0]["army"].size else V.A_WIDTH

    cand = np.zeros((b, k, cw), np.float32)
    mask = np.zeros((b, k), bool)
    army = np.zeros((b, n, aw), np.float32)
    army_mask = np.zeros((b, n), bool)
    cand_xy = np.zeros((b, k, 2), np.int64)
    for i, r in enumerate(rows):
        kk = r["cand"].shape[0]
        cand[i, :kk] = r["cand"]
        mask[i, :kk] = r["mask"]
        cand_xy[i, :kk] = r["cand_xy"]
        nn_ = r["army"].shape[0]
        if nn_:
            army[i, :nn_] = r["army"]
            army_mask[i, :nn_] = True
    # ★ 观测框**逐帧可变**（= 视野外接框，与地图大小无关）⇒ 批内**补零对齐**到最大尺寸。
    #   用户 2026-09-24：「你不能整网格大小，必须是**地图大小无关**的设计，和以前一样」。
    gs = [r["grid"] for r in rows]
    gh = max(g.shape[1] for g in gs)
    gw = max(g.shape[2] for g in gs)
    grid = np.zeros((b, gs[0].shape[0], gh, gw), np.float32)
    for i, g in enumerate(gs):
        grid[i, :, :g.shape[1], :g.shape[2]] = g
    return {
        "grid": torch.tensor(grid),
        "glob": torch.tensor(np.stack([r["glob"] for r in rows])),
        "cand": torch.tensor(cand),
        "mask": torch.tensor(mask),
        "army": torch.tensor(army),
        "army_mask": torch.tensor(army_mask),
        "cand_xy": torch.tensor(cand_xy),
    }


# ================================================================ ① 收集
@torch.no_grad()
def collect_episode(nets: dict[str, PolicyNet], sb: Sandbox, *,
                    temperature: float = 1.0, rng: np.random.Generator | None = None,
                    greedy: bool = False) -> tuple[list[Step], dict]:
    """两份网络对打一局。返回 `(步列表, 概要)`。步列表里每步记着**是哪一方的**。"""
    rng = rng or np.random.default_rng(0)
    steps: list[Step] = []
    while not sb.is_terminal():
        me = sb.current_player()
        if me is None:
            break
        obs = obs_of(sb, me)
        batch = collate([obs])
        logits, value = nets[me](batch["grid"], batch["glob"], batch["cand"],
                                 batch["mask"], batch["army"], batch["army_mask"],
                                 batch["cand_xy"])
        logits = logits[0]
        probs = torch.softmax(logits, -1).numpy()
        if greedy:
            aidx = int(np.argmax(probs))
        else:
            p = np.power(probs, 1.0 / max(1e-6, temperature))
            p = p / p.sum()
            aidx = int(rng.choice(len(p), p=p))
        logp = float(np.log(max(1e-12, probs[aidx])))

        actions = sb.legal()
        act = actions[aidx]
        prev_score = _score(sb, me)
        sb.step(act)
        done = sb.is_terminal()
        rew = _reward(sb, me, prev_score, done)
        steps.append(Step(obs["grid"], obs["glob"], obs["cand"], obs["mask"],
                          obs["army"], obs["army_mask"] if obs["army_mask"] is not None
                          else np.ones(max(1, obs["army"].shape[0]), bool),
                          obs["cand_xy"],
                          aidx, logp, float(value[0]), rew, done, me))
    info = {"turns": sb.turn, "winner": sb.winner(),
            "reward": {n: sb.reward(n) for n in PLAYERS}}
    return steps, info


def _score(sb: Sandbox, me: str) -> float:
    """★ 打分前先取**视野掩码** —— 「打分只对可见视野打分」（用户 2026-09-24）。

    不给 mask 的话打分器就是**上帝视角**（能点名视野外的敌军位置/数量/国土），
    那是作弊，模型会照着它学出"朝看不见的敌人去"的策略。
    """
    foe = next((n for n in PLAYERS if n != me), None)
    from ruleai.v11plus import pathfind
    mask = pathfind.vision_mask(sb.world, me)
    return evaluate.score(sb.world, me, foe, mask)


def _reward(sb: Sandbox, me: str, prev: float, done: bool) -> float:
    """★ **打分器差分**；终局换成 ±1（丢家直接输，别让 ±INF 进梯度）。"""
    foe = next((n for n in PLAYERS if n != me), None)
    t = evaluate.terminal(sb.world, me, foe)
    if t is not None:
        return 1.0 if t > 0 else (-1.0 if t < 0 else 0.0)
    if done:
        return 0.0
    return float(np.tanh((_score(sb, me) - prev) / 20.0))


# ================================================================ ② PPO
def gae(rewards: list[float], values: list[float], dones: list[bool],
        *, gamma: float = 0.99, lam: float = 0.95) -> tuple[np.ndarray, np.ndarray]:
    """GAE-λ。**每方的轨迹单独算**（两份网络各练各的）。"""
    n = len(rewards)
    adv = np.zeros(n, np.float32)
    last = 0.0
    for t in reversed(range(n)):
        next_v = 0.0 if (t == n - 1 or dones[t]) else values[t + 1]
        delta = rewards[t] + gamma * next_v - values[t]
        last = delta + gamma * lam * (0.0 if dones[t] else last)
        adv[t] = last
    return adv, adv + np.array(values, np.float32)


def ppo_update(net: PolicyNet, steps: list[Step], *, epochs: int = 4,
               clip: float = 0.2, vf_coef: float = 0.5, ent_coef: float = 0.01,
               lr: float = 3e-4) -> dict:
    """标准 PPO clip 更新（**只喂这一方的步**）。"""
    if not steps:
        return {}
    rows = [{"grid": s.grid, "glob": s.glob, "cand": s.cand, "mask": s.mask,
             "army": s.army, "army_mask": s.army_mask,
             "cand_xy": s.cand_xy} for s in steps]
    batch = collate(rows)
    aidx = torch.tensor([s.aidx for s in steps], dtype=torch.long)
    old_logp = torch.tensor([s.logp for s in steps], dtype=torch.float32)
    adv, ret = gae([s.reward for s in steps], [s.value for s in steps],
                   [s.done for s in steps])
    adv_t = torch.tensor(adv)
    adv_t = (adv_t - adv_t.mean()) / (adv_t.std() + 1e-8)
    ret_t = torch.tensor(ret)

    opt = torch.optim.Adam(net.parameters(), lr=lr)
    stats = {}
    for _ in range(epochs):
        logits, value = net(batch["grid"], batch["glob"], batch["cand"],
                            batch["mask"], batch["army"], batch["army_mask"],
                            batch["cand_xy"])
        logp_all = torch.log_softmax(logits, -1)
        logp = logp_all.gather(1, aidx.unsqueeze(1)).squeeze(1)
        ratio = torch.exp(logp - old_logp)
        surr = torch.min(ratio * adv_t,
                         torch.clamp(ratio, 1 - clip, 1 + clip) * adv_t)
        p = torch.softmax(logits, -1)
        ent = -(p * logp_all).sum(-1).mean()
        loss = -surr.mean() + vf_coef * nn.functional.mse_loss(value, ret_t) - ent_coef * ent
        opt.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(net.parameters(), 0.5)
        opt.step()
        stats = {"loss": float(loss), "pg": float(-surr.mean()),
                 "vf": float(nn.functional.mse_loss(value, ret_t)),
                 "ent": float(ent), "kl": float((old_logp - logp).mean())}
    return stats


# ================================================================ ③ 训练
def _streak(infos: list[dict], state: dict, who: str) -> int:
    """更新并返回"`who` 连续赢了几局"（`state` 跨 iter 存活）。"""
    for i in infos:
        state[who] = state.get(who, 0) + 1 if i["winner"] == who else 0
    return state.get(who, 0)


def train(*, iters: int = 100, episodes_per_iter: int = 8, seed: int = 0,
          lr: float = 3e-4, temperature: float = 1.0,
          first_streak_limit: int = 5, log=print) -> dict[str, PolicyNet]:
    """主循环：自对弈 collect → 两份网络各 update 一次。

    ★★ **自动闸门**：`first_streak_limit`（缺省 5）—— **先手连续赢这么多局就抛断言**
    （用户 2026-09-24：「如果先手连续赢 5 局，**断言抛出**」）。

    "先手连赢"是**训练没在学真对抗**的红旗：要么策略退化成"谁先手谁赢"，
    要么有结构性 bug（比如打分器只奖励进攻 ⇒ 双方都无脑冲、先动的赢）。
    它不该悄悄跑下去 —— 当场炸，然后去查。
    ★ 本闸门**已故意弄响过一次**（把 `first_streak_limit` 调成 1 跑一遍，见提交记录）。
    """
    rng = np.random.default_rng(seed)
    nets = {n: PolicyNet() for n in PLAYERS}
    state: dict = {}
    for it in range(1, iters + 1):
        buf = {n: [] for n in PLAYERS}
        infos = []
        for e in range(episodes_per_iter):
            sb = Sandbox(seed=int(rng.integers(1 << 30))).reset()
            steps, info = collect_episode(nets, sb, temperature=temperature,
                                          rng=rng)
            infos.append(info)
            for s in steps:
                buf[s.player].append(s)
        st = {}
        for n in PLAYERS:
            st[n] = ppo_update(nets[n], buf[n], lr=lr)
        wins = sum(1 for i in infos if i["winner"] == PLAYERS[0])
        turns = np.mean([i["turns"] for i in infos])
        log(f"[{it:4d}] 局数{len(infos)} 甲胜{wins} 平均回合{turns:.1f} "
            f"| W_甲 {_fmt(st['甲'])} | W_乙 {_fmt(st['乙'])}")
        # ---- ★ 自动闸门：先手连续赢 ⇒ 炸 ----
        first = PLAYERS[0]
        n_first = _streak(infos, state, first)
        if n_first >= first_streak_limit:
            raise AssertionError(
                f"★ 先手（{first}）已**连续赢 {n_first} 局** —— 这是"
                f"「训练没在学真对抗」的红旗：策略可能退化成'谁先手谁赢'，"
                f"或存在结构性 bug（打分器偏向进攻 / 开局距离不足 / 有越权偷看…）。"
                f"用户 2026-09-24 要求此处断言抛出，别让它悄悄跑下去。")
    return nets


def _fmt(d: dict) -> str:
    if not d:
        return "—"
    return " ".join(f"{k}={v:+.3f}" for k, v in d.items() if k in ("pg", "vf", "ent"))


if __name__ == "__main__":
    import sys
    it = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    train(iters=it, episodes_per_iter=4)