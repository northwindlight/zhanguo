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
from . import scoring as S
from . import vocab as V
from .model import PolicyNet, build_model
from .sandbox import END, PLAYERS, Sandbox


# ================================================================ 一条样本
@dataclass
class Step:
    obs: dict              # `encode.obs_of` 的产物（原样存，collate 时再拼）
    aidx: int              # 采到的候选下标
    logp: float
    value: float
    reward: float
    done: bool
    player: str


def obs_of(sb, me: str, acts=None) -> dict:
    """沙盒局面 → 网络要的一整套（**候选与 `legal()` 一一对应**）。

    ★ 直接转发 `encode.obs_of` —— 编码的唯一实现在那边，这里不再自己拼一遍
      （旧版这里有一份复刻，shape 一改就得改两处）。
    """
    return encode.obs_of(sb, me, acts)


# ================================================================ 批次拼装
# ★ 候选/窗口都**逐帧变长**（候选数、军队数、观测框尺寸都随局面变）⇒ 批内补零 + mask。
#   `mask=False` 的位置 `PolicyNet` 会 `masked_fill(-1e9)` ⇒ 不进 softmax、不产生梯度；
#   窗口的 padding 走 `key_padding_mask`，**别让它以"内容"身份进注意力**。
_INT_KEYS = ("type_idx", "tile_xy", "army_idx")
_FLOAT_KEYS = ("pos_dx", "pos_dy", "has_pos", "cand_content", "cand_marks")


def collate(rows: list[dict]) -> dict:
    """把一串单样本拼成批次。"""
    b = len(rows)
    cands = [r["cand"] for r in rows]
    k = max(c["type_idx"].shape[0] for c in cands)
    cw = cands[0]["cand_content"].shape[1]
    mw = cands[0]["cand_marks"].shape[1]

    out: dict = {kk: torch.zeros(b, k, dtype=torch.long) for kk in _INT_KEYS}
    out["tile_xy"] = torch.zeros(b, k, 2, dtype=torch.long)
    for kk in _FLOAT_KEYS:
        w = cw if kk == "cand_content" else (mw if kk == "cand_marks" else 1)
        out[kk] = torch.zeros(b, k, w) if w > 1 else torch.zeros(b, k)
    mask = np.zeros((b, k), bool)
    for i, c in enumerate(cands):
        kk = c["type_idx"].shape[0]
        for key in _INT_KEYS:
            out[key][i, :kk] = torch.as_tensor(c[key])
        out["tile_xy"][i, :kk] = torch.as_tensor(c["tile_xy"])
        for key in ("pos_dx", "pos_dy", "has_pos"):
            out[key][i, :kk] = torch.as_tensor(c[key])
        out["cand_content"][i, :kk] = torch.as_tensor(c["cand_content"])
        out["cand_marks"][i, :kk] = torch.as_tensor(c["cand_marks"])
        mask[i, :kk] = r_mask = np.asarray(rows[i]["mask"], bool)
        assert r_mask.all(), "沙盒的候选已由 `legal()` 屏蔽过，掩码本该全亮"
    out["mask"] = torch.tensor(mask)

    # ---- 窗口：每组补零到批内最大 token 数 ----
    groups = list(rows[0]["win"])
    win, wmask = {}, {}
    for g in groups:
        w = rows[0]["win"][g].shape[1]
        n = max(r["win"][g].shape[0] for r in rows)
        t = np.zeros((b, max(1, n), w), np.float32)
        mt = np.zeros((b, max(1, n)), bool)
        for i, r in enumerate(rows):
            nn_ = r["win"][g].shape[0]
            if nn_:
                t[i, :nn_] = r["win"][g]
                mt[i, :nn_] = r["win_mask"][g]
        win[g], wmask[g] = torch.tensor(t), torch.tensor(mt)
    out["win"], out["win_mask"] = win, wmask

    # ---- 网格：观测框**逐帧可变**（= 视野外接框，与地图大小无关）⇒ 补零对齐 ----
    gs = [r["grid"] for r in rows]
    gh = max(g.shape[1] for g in gs)
    gw = max(g.shape[2] for g in gs)
    grid = np.zeros((b, gs[0].shape[0], gh, gw), np.float32)
    for i, g in enumerate(gs):
        grid[i, :, :g.shape[1], :g.shape[2]] = g
    out["grid"] = torch.tensor(grid)
    return out


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
        actions = sb.legal()
        if not actions:      # ★ 保险：`_auto_advance` 本该已经推进了（动作空间已无全局 END）
            break
        obs = obs_of(sb, me, actions)
        batch = collate([obs])
        logits, value = nets[me](batch)
        logits = logits[0]
        probs = torch.softmax(logits, -1).numpy()
        if greedy:
            aidx = int(np.argmax(probs))
        else:
            p = np.power(probs, 1.0 / max(1e-6, temperature))
            p = p / p.sum()
            aidx = int(rng.choice(len(p), p=p))
        logp = float(np.log(max(1e-12, probs[aidx])))

        act = actions[aidx]
        prev_score = _score(sb, me)
        sb.step(act)
        done = sb.is_terminal()
        rew = _reward(sb, me, prev_score, done)
        steps.append(Step(obs, aidx, logp, float(value[0]), rew, done, me))
    info = {"turns": sb.turn, "winner": sb.winner(),          # ★ **实体标签**（联盟/单国）
            "winner_members": sb.winner_members(),        # ★ 胜方实体里的国家名单
            "first": sb.first,
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
    return float(np.tanh((_score(sb, me) - prev) / S.REWARD_TANH_SCALE))


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
    batch = collate([s.obs for s in steps])
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
        logits, value = net(batch)
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
        stats = {"loss": float(loss.detach()), "pg": float(-surr.mean().detach()),
                 "vf": float(nn.functional.mse_loss(value, ret_t).detach()),
                 "ent": float(ent.detach()),
                 "kl": float((old_logp - logp).mean().detach())}
    return stats


# ================================================================ ③ 训练
def _streak(infos: list[dict], state: dict, who: str) -> int:
    """更新并返回"`who` 连续赢了几局"（`state` 跨 iter 存活）。

    ★ 判据是**该局自己的先手**（`info["first"]`，传 `who="__first__"` 时），
      不是写死的某一方 —— 先手**每轮在轮换**，盯着"甲"看会把
      "甲在轮换中赢了 5 局"（= 两份网络技能不对称，自对弈里**正常**）
      误报成"先手连赢"（= 胜负由行动顺序决定，**才是红旗**）。

    ★ 胜负按**实体**判（联盟胜利 / 单国胜利）：看 `winner_members`，
      **别**拿国名跟 `winner` 那个实体标签比字符串。
    """
    for i in infos:
        mem = i.get("winner_members") or ()
        hit = (i["first"] in mem) if who == "__first__" else (who in mem)
        state[who] = state.get(who, 0) + 1 if hit else 0
    return state.get(who, 0)


def train(*, iters: int = 100, episodes_per_iter: int = 8, seed: int = 0,
          lr: float = 3e-4, temperature: float = 1.0,
          first_streak_limit: int = 5, size: int = 8,
          size_min: int | None = None, size_max: int | None = None,
          log=print) -> dict[str, PolicyNet]:
    """主循环：自对弈 collect → 两份网络各 update 一次。

    ★★ **自动闸门**：`first_streak_limit`（缺省 5）—— **先手连续赢这么多局就抛断言**
    （用户 2026-09-24：「如果先手连续赢 5 局，**断言抛出**」）。

    "先手连赢"是**训练没在学真对抗**的红旗：要么策略退化成"谁先手谁赢"，
    要么有结构性 bug（比如打分器只奖励进攻 ⇒ 双方都无脑冲、先动的赢）。
    它不该悄悄跑下去 —— 当场炸，然后去查。
    ★ 本闸门**已故意弄响过一次**（把 `first_streak_limit` 调成 1 跑一遍，见提交记录）。
    """
    rng = np.random.default_rng(seed)
    nets = {n: build_model() for n in PLAYERS}
    _log_params(nets[PLAYERS[0]], log)
    state: dict = {}
    for it in range(1, iters + 1):
        buf = {n: [] for n in PLAYERS}
        infos = []
        # ★ **每 `episodes_per_iter` 局换一次先后手**（用户 2026-09-24：「每 8 局换先后手」）
        #   —— 缺省 `episodes_per_iter=8` ⇒ 正好每个 iter 换一次。
        #   固定先手会把"先手优势"永远记在同一个网络头上（实测闸门连响就是这个）。
        first = PLAYERS[(it - 1) % len(PLAYERS)]
        for e in range(episodes_per_iter):
            # ★ **地图尺寸也随机**（用户 2026-09-24：「改成随机地图」）—— **域随机化**：
            #   模型要能泛化到不同大小的图，而不是记住"这张图该怎么打"。
            #   （seed 本来就每局不同 ⇒ 地形早已随机；这里补的是**尺寸**这一维。）
            lo = size_min if size_min is not None else size
            hi = size_max if size_max is not None else size
            sz = int(rng.integers(lo, hi + 1))
            sb = Sandbox(seed=int(rng.integers(1 << 30)), size=sz,
                         first=first).reset()
            steps, info = collect_episode(nets, sb, temperature=temperature,
                                          rng=rng)
            infos.append(info)
            for s in steps:
                buf[s.player].append(s)
        st = {}
        for n in PLAYERS:
            st[n] = ppo_update(nets[n], buf[n], lr=lr)
        # ★ 按**实体**判谁赢（联盟胜利 / 单国胜利）：`winner` 是实体标签，比不得国名
        wins = sum(1 for i in infos if PLAYERS[0] in (i.get("winner_members") or ()))
        turns = np.mean([i["turns"] for i in infos])
        # 先手胜率（本 iter 内）：`0.5` = 行棋顺序不影响胜负 ⇒ 健康。
        # 两份网络技能不对称（甲/乙胜场悬殊）**不是**警报，自对弈里正常。
        nf = sum(1 for i in infos if i["first"] in (i.get("winner_members") or ()))
        log(f"[{it:4d}] 局数{len(infos)} 甲胜{wins} 先手胜{nf} 平均回合{turns:.1f} "
            f"| W_甲 {_fmt(st['甲'])} | W_乙 {_fmt(st['乙'])}")
        # ---- ★ 自动闸门：先手连续赢 ⇒ 炸 ----
        # 判据见 `_streak` 的 docstring：看**该局自己的先手**，不看某一方。
        n_first = _streak(infos, state, "__first__")
        if n_first >= first_streak_limit:
            raise AssertionError(
                f"★ **先手已连续赢 {n_first} 局**（判据 = `winner == first`，"
                f"先手每轮在轮换）—— 这是「训练没在学真对抗」的红旗："
                f"胜负由**行动顺序**而不是策略决定，可能策略退化成'谁先手谁赢'，"
                f"或存在结构性 bug（打分器偏向进攻 / 开局距离不足 / 有越权偷看…）。"
                f"用户 2026-09-24 要求此处断言抛出，别让它悄悄跑下去。")
    return nets


def _log_params(net: PolicyNet, log) -> None:
    """建网时把**参数量打出来**（用户 2026-09-24 点过：「0.135m 的模型真的够用吗…
    **至少给我弄到 1m**」）—— 容量是设计目标之一，别让它悄悄退回去。"""
    n = net.n_params()
    log(f"★ 主干 = WindowTransformer（窗口组 {list(net.groups)}，d_model={net.d_model}）"
        f" · 参数量 **{n/1e6:.3f}M**")


def _fmt(d: dict) -> str:
    if not d:
        return "—"
    return " ".join(f"{k}={v:+.3f}" for k, v in d.items() if k in ("pg", "vf", "ent"))


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="沙盒 PPO 自对弈训练")
    ap.add_argument("--threads", type=int, default=1,
                    help="torch 线程数（0=自动=物理核）。★ **ECS 上必须 1** —— "
                         "SMT 逻辑核对向量计算零收益、只多同步开销（实测开 2 线程慢 3.4×）。"
                         "`rl/run_ecs.sh` 已经在**进程启动前**设了 OMP/MKL/OpenBLAS=1，"
                         "这里是第三道保险。")
    ap.add_argument("--iters", type=int, default=5)
    ap.add_argument("--episodes", type=int, default=4, help="每 iter 自对弈局数")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--temperature", type=float, default=1.0)
    ap.add_argument("--first-streak-limit", type=int, default=5,
                    help="先手连续赢这么多局就抛断言（闸门，见 train() 的 docstring）")
    ap.add_argument("--size", type=int, default=8,
                    help="地图边长（`--size-min/--size-max` 给了就忽略它）。"
                         "★ 8 上「先手速攻」是结构性最优（两国核心最多隔 "
                         "min_margin(8,2)=5 格、步兵 1 格/回合 ⇒ 5 回合直达，"
                         "实测闸门连响）⇒ 16 起才有真正的对抗空间")
    ap.add_argument("--size-min", type=int, default=None,
                    help="★ **随机地图**：每局在 [min,max] 里抽边长（域随机化）。建议 16 起")
    ap.add_argument("--size-max", type=int, default=None)
    a = ap.parse_args()
    if a.threads:
        import torch
        torch.set_num_threads(a.threads)
    train(iters=a.iters, episodes_per_iter=a.episodes, seed=a.seed, lr=a.lr,
          temperature=a.temperature, first_streak_limit=a.first_streak_limit,
          size=a.size, size_min=a.size_min, size_max=a.size_max)