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

    ★★ `halls_known`（缺省 **True**）—— 用户 2026-09-24：「**先炼一个基于已知的得基座**」
    ─────────────────────────────────────────────────────────────────────────
    即**他国市政厅的位置已知**（"已派间谍"模式）。这是**基座**：先把"拿到已知目标"
    这件事学会，再上"自己找厅"那一版（`halls_known=False`，`--no-halls-known`）。

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
from .league import League
from .model import PolicyNet, build_model
from .sandbox import Sandbox, n_nations_for


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
def collect_episode(nets: dict, sb: Sandbox, *,
                    temperature: float = 1.0, rng: np.random.Generator | None = None,
                    greedy: bool = False,
                    max_steps: int | None = None) -> tuple[list[Step], dict]:
    """一局自对弈。返回 `(步列表, 概要)`。步列表里每步记着**是哪一方的**。

    ★★ `nets` 是**按槽位编号**的网络表 `{槽位: PolicyNet}`（用户 2026-09-25：
      多玩家 3 人起步）—— 谁是哪个槽位由 `train` 每局**轮转**决定（见那里）。

    ★★ `max_steps`（**本局步数上限**，`None` = 不限）—— **内存闸**。
      实测（见 `rl/PLAN.md` §12.10）：12-30 的图上**一步观测上百 KB**，
      而缓冲区是 `步数 × 单帧观测` 全量held在内存里 ⇒ 大图上**一局就能顶到 GB 级**
      （实测：12-30 的炉子第 1 个 iter 跑了 31 分钟、RSS 涨到 **1742MB 还在涨**，
      可用内存只剩 1697MB ⇒ 那是要 OOM 的）。
      ⇒ 截断在**训练**里是标准做法（truncated rollout）。
      ★★ 截断时**必须把最后一步标成 `done`**：否则 GAE 会**跨局串味**
        （`gae` 靠 `dones[t]` 重置；不标的话这一局的尾巴会去借下一局的价值）。
        代价是最后一步的值自举为 0（少算了 `γV(s_T)`）—— 标准近似，量级很小。
    """
    rng = rng or np.random.default_rng(0)
    if max_steps is not None and max_steps <= 0:
        max_steps = None          # ★ `0` 是"不限"，不是"走一步就停"
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
        if max_steps is not None and len(steps) >= max_steps:
            break
    truncated = not sb.is_terminal()
    if truncated and steps:
        # ★★ 截断 ⇒ 最后一步**当成边界**（`done=True`）—— 见上面 docstring 里那条。
        steps[-1] = Step(steps[-1].obs, steps[-1].aidx, steps[-1].logp,
                         steps[-1].value, steps[-1].reward, True, steps[-1].player)
    info = {"turns": sb.turn, "winner": sb.winner(),          # ★ **实体标签**（联盟/单国）
            "winner_members": sb.winner_members(),        # ★ 胜方实体里的国家名单
            "truncated": truncated,                       # ★ 没打完（步数预算截断）
            "first": sb.first,
            "players": tuple(sb.players),                 # ★ 这一局有几个国家（统计用）
            "reward": {n: sb.reward(n) for n in sb.players}}
    return steps, info


def _score(sb: Sandbox, me: str) -> float:
    """★ 打分前先取**视野掩码** —— 「打分只对可见视野打分」（用户 2026-09-24）。

    不给 mask 的话打分器就是**上帝视角**（能点名视野外的敌军位置/数量/国土），
    那是作弊，模型会照着它学出"朝看不见的敌人去"的策略。
    """
    from ruleai.v11plus import pathfind
    mask = pathfind.vision_mask(sb.world, me)
    # ★ 已知的厅 = **视野 ∪ 永久记忆**（`known_halls` 顺手把本帧看见的记下来）。
    #   用户 2026-09-24：「**发现厅了就应该永久标记，因为厅是拆不掉也不能移动的**」
    #   ⇒ 不能再拿"当前视野"回答"厅在哪"：敌厅一离开视野，逼近/守家那几项**当帧塌 0**，
    #     势函数差分变噪声（本该是"我看见过它，它一直在那儿"）。见 `rl/hall_memory.py`。
    #   ★ 两种可见模式（间谍 / 自己找厅）的差别**只在沙盒构造时**（记忆的初值），
    #     这里一条路走到底。
    # ★★ 不传 `enemy` ⇒ 打分器自动用**全部对手**（`rival_nations`）。
    #   多玩家下"某一个敌人"是错的：漏掉的那个对手，它的军队/国土/厅**一概不进分数**
    #   而**不报错**（用户 2026-09-25：3 人起步）。见 `evaluate.score` 的 docstring。
    return evaluate.score(sb.world, me, mask=mask,
                          known=sb.known_halls(me, mask),
                          **dict(zip(("kills", "dmg"), sb.kills.snapshot())))
    # ★★ `kills`/`dmg` = **累计战果账本**的 (击杀表, 血量表)（单调、不进迷雾）——
    #   替换掉原来「看得见的敌国军队数 / 敌方血量」那两项（它们有**迷雾悖论**）。


def _reward(sb: Sandbox, me: str, prev: float, done: bool) -> float:
    """★ **打分器差分**；终局换成 ±1（丢家直接输，别让 ±INF 进梯度）。"""
    t = evaluate.terminal(sb.world, me)      # ★ 终局判据是**实体**，与"某个敌人"无关
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
               lr: float = 3e-4, minibatch: int | None = None) -> dict:
    """标准 PPO clip 更新（**只喂这一方的步**）。

    ★★ **必须分 minibatch**（`scoring.PPO_MINIBATCH`，缺省 128）—— 这是踩出来的：
      `cross2` 的开销是 **O(K²)**，而"整个 buffer 一次性前向+反传"在一条 iter
      （~1800 步、K 可达 200）下会把注意力中间量顶到 **14 GB RSS** ⇒ 在 16 GB 的 Pi 上
      被 **OOM 杀**（实测 `exit=137`，日志只写到表头）。标准 PPO 本来就分 minibatch。
    """
    if not steps:
        return {}
    minibatch = S.PPO_MINIBATCH if minibatch is None else minibatch
    n = len(steps)
    aidx_all = np.array([s.aidx for s in steps], dtype=np.int64)
    old_logp_all = np.array([s.logp for s in steps], dtype=np.float32)
    adv, ret = gae([s.reward for s in steps], [s.value for s in steps],
                   [s.done for s in steps])
    adv_all = (adv - adv.mean()) / (adv.std() + 1e-8)
    ret_all = np.asarray(ret, dtype=np.float32)

    opt = torch.optim.Adam(net.parameters(), lr=lr)
    stats: dict = {}
    order = np.arange(n)
    for _ in range(epochs):
        np.random.shuffle(order)
        for lo in range(0, n, minibatch):
            sel = order[lo:lo + minibatch]
            # ★ **按 minibatch collate**（不是先 collate 全部再切）—— 大 batch 的补零
            #   张量本身就是一笔大分配，切完再切就白付了
            obs = [steps[i].obs for i in sel]
            batch = collate(obs)
            aidx = torch.as_tensor(aidx_all[sel])
            old_logp = torch.as_tensor(old_logp_all[sel])
            adv_t = torch.as_tensor(adv_all[sel])
            ret_t = torch.as_tensor(ret_all[sel])
            logits, value = net(batch)
            logp_all = torch.log_softmax(logits, -1)
            logp = logp_all.gather(1, aidx.unsqueeze(1)).squeeze(1)
            ratio = torch.exp(logp - old_logp)
            surr = torch.min(ratio * adv_t,
                             torch.clamp(ratio, 1 - clip, 1 + clip) * adv_t)
            p = torch.softmax(logits, -1)
            ent = -(p * logp_all).sum(-1).mean()
            loss = (-surr.mean() + vf_coef * nn.functional.mse_loss(value, ret_t)
                    - ent_coef * ent)
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
          halls_known: bool = True, nations: int | None = None,
          t_max: int = 200, pool: int = 5, out: str | None = None,
          ckpt_every: int = 5, resume: str | None = None, max_steps: int = 0,
          league_db: str | None = None, league_mains: int = 2,
          league_snapshot_every: int = 50, league_from: str | None = None,
          league_min_games: int = 10, league_retire_rate: float = 0.20,
          log=print) -> dict[int, PolicyNet]:
    """主循环：自对弈 collect → 每个网络各 update 一次。

    ★★ **网络池按"槽位"编号**（用户 2026-09-25：多玩家 3 人起步）。
      槽位数 = `n_nations_for(最大边长)`（缺省 3）；每局有 `k` 个国家就用 `k` 个网络。
      ★ **"国家 → 网络"每局轮转**（见 `net_of`）：否则小图国家少时，
      高编号的网络那一局**一点数据都拿不到**（饿死），而它在大图上还要上场。

    ★★ **自动闸门**：`first_streak_limit`（缺省 5）—— **先手连续赢这么多局就抛断言**
    （用户 2026-09-24：「如果先手连续赢 5 局，**断言抛出**」）。

    "先手连赢"是**训练没在学真对抗**的红旗：要么策略退化成"谁先手谁赢"，
    要么有结构性 bug（比如打分器只奖励进攻 ⇒ 双方都无脑冲、先动的赢）。
    它不该悄悄跑下去 —— 当场炸，然后去查。
    ★ 本闸门**已故意弄响过一次**（把 `first_streak_limit` 调成 1 跑一遍，见提交记录）。
    """
    rng = np.random.default_rng(seed)
    lo = size_min if size_min is not None else size
    hi = size_max if size_max is not None else size
    # ★ `nations` 显式给了就**钉死**国家数（受控实验用；缺省按图大小算）
    k_of = (lambda _sz: int(nations)) if nations else n_nations_for
    n_slots = max(int(pool), k_of(hi) if not nations else int(nations))
    nets = {i: build_model() for i in range(n_slots)}
    _log_params(nets[0], log)
    # ★★ **联赛池**（用户 2026-09-25 定的口径，逐条见 `rl/league.py` 的 docstring）
    #   摘要：从最新快照开始分化 / 只增不删 / 随机抽 pt / 胜率永久化到 json /
    #        可有 2 个固定主 pt / 打 10 局以上胜率 <20% 的不再启用。
    #   ★ `league_db=None` ⇒ 池子照跑但**不落盘**（战绩不过夜）—— 真起炉必须给。
    lg = League(league_db, mains=league_mains,
                retire_min_games=league_min_games, retire_rate=league_retire_rate,
                max_k=k_of(hi), min_learners=1,
                fingerprint=_shape_fingerprint(), log=log)
    had = lg.load()
    mids = [f"L{i}" for i in range(n_slots)]
    log(f"★ 联赛池 **{n_slots} 份在训**（主 pt {league_mains}）"
        f"{'＋库已读回' if had else '（新库）'}"
        f" —— 每局按 `n_nations_for(边长)` **随机抽 k 份不重复**上场"
        + (f"；库 → {league_db}（SQLite/WAL）" if league_db else "；★ **库不落盘**"))
    state: dict = {}
    it0 = _load_ckpt(resume, nets, log=log) if resume else 0
    # ★★「联赛池从**最新快照**开始分化」（用户原话）：所有在训成员**从同一份起跑**，
    #   ⇒ 起点相同、每局抽到的对手组合不同 ⇒ **风格自己漂开**。
    #   （原来是各随机初始化 —— 大部分生下来就是废的，谈不上"分化"。）
    #   ★ 只在**建池时**生效（`--resume` 时权重来自主档，两者同时给会互相覆盖，
    #     所以这里显式让 resume 赢，并说明白）。
    if league_from and resume:
        log(f"★ 同时给了 `--league-from` 和 `--resume` —— **resume 赢**"
            f"（延续训练优先，别把练过的权重盖回起点）")
    elif league_from:
        seed = _load_seed(league_from)
        for i in range(n_slots):
            # ★ **按槽位**：第 i 份继承起点里的第 i 份（缺了才退回第 0 份）
            nets[i].load_state_dict(seed.get(i, seed[min(seed)]))
        log(f"★ 联赛池**从原来 {len(seed)} 个分出去**：{n_slots} 份在训成员"
            f"**各继承自己那一条血脉**（{league_from}）⇒ 此后各抽各的对手、"
            f"**继续分化**（★ 不是「都从同一份起跑」—— 那等于把几条血脉掐成一条）")
    for i, mid in enumerate(mids):
        lg.bind_live(mid, nets[i], born=it0)
    if out:
        # ★ 起炉前先落一份"第 0 代"存档：跑挂了也还有东西可续，且形状元数据在册
        _save_ckpt(out, nets, it0,
                   meta=_ckpt_meta(lo, hi, halls_known, nations, n_slots, t_max))
        log(f"★ 起始存档 → {out}（第 {it0} iter）")
    for it in range(it0 + 1, it0 + iters + 1):
        buf: dict[str, list] = {mid: [] for mid in mids}
        infos = []
        budget = int(max_steps) if max_steps else 0     # ★ 本 iter 的**步数预算**（内存闸）
        n_cut = 0
        for e in range(episodes_per_iter):
            if max_steps and budget <= 0:
                break                                   # ★ 预算用完 ⇒ 本 iter 就收这些
            # ★ **地图尺寸也随机**（用户 2026-09-24：「改成随机地图」）—— **域随机化**：
            #   模型要能泛化到不同大小的图，而不是记住"这张图该怎么打"。
            #   （seed 本来就每局不同 ⇒ 地形早已随机；这里补的是**尺寸**这一维。）
            sz = int(rng.integers(lo, hi + 1))
            k = k_of(sz)
            players = V.PLAYER_NAMES[:k]
            # ★ **≥3 轮流手**：先手在**每局**之间轮换（不只是每 iter）
            #   ⇒ 一个 iter 内 k 个国家都当过一次先手，"先手胜率"这个统计才有意义
            #   （健康值 = 1/k，不再是 0.5）。
            first = players[(it + e) % k]
            sb = Sandbox(seed=int(rng.integers(1 << 30)), size=sz, t_max=t_max,
                         first=first, halls_known=halls_known).reset()
            # ★★ **从池子里随机抽 k 份不重复的 pt** 上场（用户：「随机抽 pt」）。
            #   `rng.permutation` 取前 k ⇒ 无重复、且每局独立。沙盒是**对称**的
            #   （各国开局一样、先手另算）⇒ 网络扮哪个国家不带偏差，
            #   "谁的数据"只是被摊平。
            #   ★ 抽出来的可能是**池里的冻结快照**（旧代的自己）—— 那正是"分化"的
            #     来源：对手不只一个打法。快照**只当对手、不进梯度**（下面按 mid 分派）。
            draw = lg.draw(k, rng)
            net_of = {p: lg.net_of(draw[i]) for i, p in enumerate(players)}
            mid_of = {p: draw[i] for i, p in enumerate(players)}
            steps, info = collect_episode(net_of, sb, temperature=temperature,
                                          rng=rng,
                                          max_steps=(budget if max_steps else None))
            if max_steps:
                budget -= len(steps)
            if info.get("truncated"):
                # ★ 没打完的局**没有胜方** ⇒ 不进 `infos`（先手连赢闸门看的就是它）、
                #   也**不记战绩**（谁都没赢，记了就是把噪声当胜率）。
                #   ★ 但要**数出来**并进日志：大图早期局局截断会变成常态，
                #     不报的话"池子一直没有战绩"这件事会**静默**。
                n_cut += 1
            else:
                infos.append(info)
                # ★ 战绩记到**每个上场的成员**头上（用户：「标记每个 pt 的胜率」）——
                #   包括冻结快照：它们也要有胜率，否则"打不动的停用"无从判起。
                # ★★ **平局不计**（打满 `t_max`、没有胜方）：胜率的分母是「**有胜负的局**」。
                #   不排除的话，"甲没赢"会被记成"甲输了"—— 打满上限的局**每方各记一负**，
                #   于是一池子平局会把**所有人**的胜率压到 0 ⇒ 淘汰规则把池子清空，
                #   而日志上看只是"大家都在输"。★ 这是"静默"那一类，专门钉了守卫。
                won = set(info.get("winner_members") or ())
                if won:
                    for p in players:
                        lg.record(mid_of[p], p in won, it=it)
            for s in steps:
                mi = mid_of[s.player]
                if mi in buf:                 # ★ 只有在训成员进梯度；快照只当对手
                    buf[mi].append(s)
        st = {mids[i]: ppo_update(nets[i], buf[mids[i]], lr=lr)
              for i in range(n_slots)}
        # ★ 按**实体**判谁赢（联盟胜利 / 单国胜利）：`winner` 是实体标签，比不得国名
        win_by = {p: sum(1 for i in infos
                         if p in (i.get("winner_members") or ()))
                  for p in V.PLAYER_NAMES}
        # ★ 全部截断时 `infos` 是空的 —— `np.mean([])` 会出 nan + 一条 RuntimeWarning
        #   （**看起来像 bug 但其实是"本 iter 一局都没打完"**）⇒ 这里显式兜住。
        turns = float(np.mean([i["turns"] for i in infos])) if infos else float("nan")
        # 先手胜率（本 iter 内）：健康值 = **1/k**（先手在 k 国之间轮换）
        #   —— 明显偏高才是"胜负由行动顺序决定"的嫌疑。
        nf = sum(1 for i in infos if i["first"] in (i.get("winner_members") or ()))
        live = {m: _fmt(st[m]) for m in st if buf[m]}
        # ★ 截断局数**必须报**：大图上早期局局截断会成常态，不报的话
        #   "池子一直没战绩 / 没终局奖励"会**静默**（这正是这条日志存在的理由）。
        cut = f" 截断{n_cut}" if n_cut else ""
        tstr = f"{turns:.1f}" if infos else "—"
        log(f"[{it:4d}] 局数{len(infos)}{cut} 胜场{win_by} 先手胜{nf} 平均回合{tstr} "
            f"| 网络 {live}")
        # ---- ★ 联赛池：淘汰 / 冻快照 / 账本落盘 ----
        #  ★ 这三件事**每 iter 都做**，不是"每 N iter 做一次" —— 淘汰规则靠的是
        #    战绩累积，漏做一次不会有症状，但账本会慢慢和现实对不上。
        killed = lg.retire()
        if killed:
            log(f"  ★ 停用（打满{lg.retire_min_games}局且胜率<{lg.retire_rate:.0%}）"
                f"→ {killed}（**只标停用、不删除**，仍留在账本里）")
        if league_snapshot_every and it % league_snapshot_every == 0:
            pool_i = [i for i, mi in enumerate(mids) if lg.members[mi].active]
            if pool_i:                      # ★ 冻一份当前权重进池（只增不删）
                i = pool_i[(it // league_snapshot_every) % len(pool_i)]
                lg.add_snapshot(nets[i], it, mid=f"S{it:05d}L{i}")
        lg.updated_iter = it
        lg.save()
        log(f"  {lg.report()}")
        # ---- ★ 自动闸门：先手连续赢 ⇒ 炸 ----
        # 判据见 `_streak` 的 docstring：看**该局自己的先手**，不看某一方。
        n_first = _streak(infos, state, "__first__")
        if n_first >= first_streak_limit:
            raise AssertionError(
                f"★ **先手已连续赢 {n_first} 局**（判据 = `winner == first`，"
                f"先手每轮在轮换）—— 这是「训练没在学真对抗」的红旗："
                f"胜负由**行动顺序**而不是策略决定，可能策略退化成「谁先手谁赢」，"
                f"或存在结构性 bug（打分器偏向进攻 / 开局距离不足 / 有越权偷看…）。"
                f"用户 2026-09-24 要求此处断言抛出，别让它悄悄跑下去。")
        # ---- ★ 存档（起炉长跑必须：原来一行保存都没有，崩了全没）----
        if out and it % max(1, ckpt_every) == 0:
            _save_ckpt(out, nets, it, meta=_ckpt_meta(lo, hi, halls_known,
                                                      nations, n_slots, t_max))
            log(f"  ★ 存档 → {out}（第 {it} iter）")
    if out:
        _save_ckpt(out, nets, it0 + iters, meta=_ckpt_meta(lo, hi, halls_known,
                                                     nations, n_slots, t_max))
    return nets


def _ckpt_meta(lo: int, hi: int, halls_known: bool, nations: int | None,
               n_slots: int, t_max: int = 200) -> dict:
    """★★ 存档的**形状元数据** —— 只为了一件事：将来加载时能**判定它过期了**。

    用户定过的铁律：**「ckpt 会被新代码加载就必须重炼」**（旧线 `feat/rl` 的教训）。
    而"形状对不对"这件事**光看权重张量是查不出来的**（能 `load_state_dict` 成功、
    却喂错口径的通道）。⇒ 把**决定输入宽度的那些常量**一并存进去，
    加载方拿 `_shape_fingerprint()` 比一下就知道该不该拒。
    """
    return {"size": [lo, hi], "halls_known": bool(halls_known),
            "nations": nations, "pool": n_slots, "t_max": int(t_max),
            "fingerprint": _shape_fingerprint()}


def _shape_fingerprint() -> dict:
    """决定**观测形状**的那几个常量（变了 ⇒ 旧 ckpt 一律作废）。"""
    from . import features as F
    return {
        "grid_channels": int(V.GRID_CHANNELS),
        "glob_size": int(V.GLOB_SIZE),
        "grid_cb_bins": int(V.CB_ROUND_BINS),
        "cand_marks": int(V.CAND_MARKS),
        "cand_content": int(F.F_CAND),
        "army_width": int(V.A_WIDTH_RAW + F.F_U + V.A_EXTRA),
        "glob_content": int(F.F_GLOB),
    }


def _load_seed(path: str) -> dict:
    """★ 读一份**分化起点**（`--league-from`）的**全部**权重（按槽位）。

    ★★ **按槽位灌**（`L0←nets[0]`、`L1←nets[1]`…）—— 用户 2026-09-25：
      「**分化指从原来 5 个来分化，而不是一个**」。
      我第一版把 5 份**都从 `nets[0]`** 起跑，那等于**把 5 条血脉掐成 1 条**，
      跟"分化"正好相反（5 份会先收敛成同一个东西，再一起漂）。
    ★★ 一样要校**形状指纹** —— 铁律：「**ckpt 会被新代码加载就必须重炼**」。
      权重张量能 `load_state_dict` 成功、却喂错口径的通道是**查不出来**的，
      而"起点"这条路是**唯一会静默毒害整个池子**的地方（池子里每一份都从它来）。
    """
    blob = torch.load(path, map_location="cpu", weights_only=False)
    fp = (blob.get("meta") or {}).get("fingerprint") or {}
    now = _shape_fingerprint()
    bad = {k: (fp.get(k), v) for k, v in now.items() if k in fp and fp[k] != v}
    if bad:
        raise SystemExit(
            f"★ 分化起点 {path} 的形状指纹对不上：{bad}（存的是旧值，现在的是新值）\n"
            f"  ⇒ 它是**旧代码**训的，不能当起点（整池都会被它带歪）")
    src = blob.get("nets") or {}
    if isinstance(src, dict) and src:
        return {int(k): v for k, v in src.items()}
    if blob.get("weights"):
        return {0: blob["weights"]}              # 单份（如池子成员那种格式）
    raise SystemExit(f"★ {path} 里没找到可用权重（既没有 `nets` 也没有 `weights`）")


def _load_ckpt(path: str, nets: dict, *, log=print) -> int:
    """★ **续跑读档**：把权重灌回 `nets`，返回**已经跑过的 iter 数**。

    ★★ 先校**形状指纹**：对不上就**直接拒**（不是警告）——
      用户定的铁律：「**ckpt 会被新代码加载就必须重炼**」。权重张量能 `load_state_dict`
      成功、却喂错口径的通道，是查不出来的 ⇒ 只能用指纹挡。
    """
    blob = torch.load(path, map_location="cpu", weights_only=False)
    meta = blob.get("meta") or {}
    fp = meta.get("fingerprint") or {}
    now = _shape_fingerprint()
    bad = {k: (fp.get(k), v) for k, v in now.items() if k in fp and fp[k] != v}
    if bad:
        raise SystemExit(
            f"★ {path} 的形状指纹对不上：{bad}（存的是旧值，现在的是新值）\n"
            f"  ⇒ 这个存档是**旧代码**训的，必须重炼（别硬加载）。")
    got = blob["nets"]
    hit = 0
    for i, net in nets.items():
        if i in got:
            net.load_state_dict(got[i]); hit += 1
    it0 = int(meta.get("iters", 0))
    log(f"★ 从 {path} 续跑：灌回 {hit}/{len(nets)} 份权重，已完成 **{it0}** 个 iter，"
        f"t_max={meta.get('t_max')} size={meta.get('size')}")
    return it0


def _save_ckpt(path: str, nets: dict, iters: int, *, meta: dict) -> None:
    """把池子里**每一份**权重连同形状元数据写盘（原子写：先临时文件再 rename）。"""
    import os
    from pathlib import Path
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    meta = dict(meta or {})
    meta["iters"] = int(iters)
    tmp = p.with_suffix(p.suffix + ".tmp")
    torch.save({"nets": {int(i): n.state_dict() for i, n in nets.items()},
                "meta": meta}, tmp)
    os.replace(tmp, p)                       # ★ 原子：别让"写了一半"被 pull 走


def _log_params(net: PolicyNet, log) -> None:
    """建网时把**参数量打出来**（用户 2026-09-24 点过：「0.135m 的模型真的够用吗…
    **至少给我弄到 1m**」）—— 容量是设计目标之一，别让它悄悄退回去。"""
    n = net.n_params()
    log(f"★ 主干 = WindowTransformer（窗口组 {list(net.groups)}，d_model={net.d_model}）"
        f" · 参数量 **{n/1e6:.3f}M**")
    log(f"★ 打分先验 {S.describe()}")


def _fmt(d: dict) -> str:
    if not d:
        return "—"
    return " ".join(f"{k}={v:+.3f}" for k, v in d.items() if k in ("pg", "vf", "ent"))


if __name__ == "__main__":
    import argparse
    import os
    import sys

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
                         "★ 国家数按 `n_nations_for(边长)` 自动定"
                         "（12⇒3、16⇒4、20⇒5；8 也兜到 3）；"
                         "★ 8 上「先手速攻」曾是结构性最优（**两国**核心最多隔 "
                         "min_margin(8,2)=5 格、步兵 1 格/回合 ⇒ 5 回合直达，"
                         "实测闸门连响）—— 多玩家正是为**解这个僵局**而做；"
                         "★ 2026-09-25 起炉范围改成 **12-20**（治换家流 + 让一局装得下"
                         "步数预算），`TILES_PER_NATION` 同时调到 60 以保住 3-5 国")
    ap.add_argument("--max-steps", dest="max_steps", type=int, default=0,
                    help="★★ **每 iter 的步数预算**（内存闸；0 = 不限）。"
                         "缓冲区 = `步数 × 单帧观测`全量held在内存里，而 12-30 的图上"
                         "单帧观测上百 KB ⇒ 大图无上限**一局就能顶到 GB 级**"
                         "（实测：12-30 的炉子第 1 个 iter 跑 31 分钟、RSS 1742MB 还在涨）。"
                         "用完了本 iter 就收摊，该局**截断**（没打完 ⇒ 不记战绩、"
                         "不进先手闸门，但**会计数进日志**）。")
    ap.add_argument("--t-max", dest="t_max", type=int, default=200,
                    help="★ 对局回合上限（兜底防僵局；到了判平）。"
                         "用户 2026-09-25 定：起炉用 **500**")
    ap.add_argument("--pool", type=int, default=5,
                    help="★ 联赛池份数（用户：「开局 **5** 个空权重，随机抽 pt 参与」）")
    ap.add_argument("--resume", type=str, default=None,
                    help="★ 从存档**续跑**（先校形状指纹，对不上直接拒）")
    # ---- ★ 联赛池（用户 2026-09-25：「现在做联赛池」）----
    ap.add_argument("--league-db", dest="league_db", type=str,
                    default="rl/runs/league.db",
                    help="★ 联赛库（**SQLite**，胜率永久化在这里）。"
                         "★ 用库不用 json 是**为了以后并行**（用户：「池子够大并行抽，"
                         "起多个独立进程筛」）—— 写入走原子自增 ⇒ 多进程记账不互相覆盖。"
                         "给 `none` ⇒ 池子照跑但**不落盘**（战绩不过夜 ⇒ 10 局门槛"
                         "永远够不到 ⇒ 淘汰规则变死代码，**还不报错**）")
    ap.add_argument("--league-mains", dest="league_mains", type=int, default=2,
                    help="★ **固定主 pt** 的份数（用户：「可以有两个固定主 pt，"
                         "也可以没有」）。主 pt = **不受淘汰规则约束**的基座")
    ap.add_argument("--league-snapshot-every", dest="league_snapshot_every",
                    type=int, default=50,
                    help="★ 每几个 iter 冻一份当前权重进池（**只增不删**）。"
                         "0 = 不冻（池子就只有在训的那几份）")
    ap.add_argument("--league-from", dest="league_from", type=str, default=None,
                    help="★ **分化的起点**（用户：「分化指从**原来 5 个**来分化，"
                         "**而不是一个**」）⇒ **按槽位灌**：第 i 份继承起点里的第 i 份，"
                         "各自延续自己的血脉。★ 与 `--resume` 同时给时 **resume 赢**")
    ap.add_argument("--league-min-games", dest="league_min_games", type=int,
                    default=10,
                    help="★ 淘汰门槛之一：**打满几局**才谈胜率（用户：10）")
    ap.add_argument("--league-retire-rate", dest="league_retire_rate", type=float,
                    default=0.20,
                    help="★ 淘汰门槛之二：胜率低于它 ⇒ `active=false`（用户：0.20）。"
                         "⚠ K 国局里随机胜率是 1/K —— 3 国 33%、**5 国 20%**"
                         "⇒ 这个阈值在 5 国局上等于「和随机持平」（偏严）")
    ap.add_argument("--restart-after", dest="restart_after", type=int, default=0,
                    help="★ 每跑这么多 iter 就**重启进程**（存档后续跑）—— 抗内存增长，"
                         "用户 2026-09-25：「不如定时重启」")
    ap.add_argument("--out", type=str, default=None,
                    help="★ 存档路径（长跑必须给！原子写 .tmp→rename）")
    ap.add_argument("--ckpt-every", dest="ckpt_every", type=int, default=5,
                    help="每几个 iter 存一次档")
    ap.add_argument("--nations", type=int, default=None,
                    help="★ **钉死国家数**（受控实验用；缺省按图大小算）。"
                         "§多玩家的最小验证：`--nations 3 --size 8`")
    ap.add_argument("--size-min", type=int, default=None,
                    help="★ **随机地图**：每局在 [min,max] 里抽边长（域随机化）。建议 16 起")
    ap.add_argument("--size-max", type=int, default=None)
    ap.add_argument("--halls-known", dest="halls_known", action="store_true",
                    default=True,
                    help="★ 他国市政厅位置**已知**（缺省；'已派间谍'模式）—— "
                         "用户：「先炼一个基于已知的**基座**」")
    ap.add_argument("--no-halls-known", dest="halls_known", action="store_false",
                    help="★ 反过来：'自己找厅'（模型得先侦察）")
    a = ap.parse_args()
    if a.threads:
        import torch
        torch.set_num_threads(a.threads)
    seg = min(a.iters, a.restart_after) if a.restart_after else a.iters
    train(iters=seg, episodes_per_iter=a.episodes, seed=a.seed, lr=a.lr,
          temperature=a.temperature, first_streak_limit=a.first_streak_limit,
          size=a.size, size_min=a.size_min, size_max=a.size_max,
          halls_known=a.halls_known, nations=a.nations, t_max=a.t_max, max_steps=a.max_steps,
          pool=a.pool, out=a.out, ckpt_every=a.ckpt_every, resume=a.resume,
          league_db=(None if (a.league_db or "").lower() in ("none", "") else a.league_db),
          league_mains=a.league_mains,
          league_snapshot_every=a.league_snapshot_every,
          league_from=a.league_from, league_min_games=a.league_min_games,
          league_retire_rate=a.league_retire_rate)
    if a.restart_after and a.iters > seg:
        # ★★ **定时重启**（用户 2026-09-25：「不如定时重启」）：跑完这一段就 `exec` 自己，
        #   **新进程 ⇒ RSS 归零**，并从刚存的档续跑。
        #   ★ 为什么用 `execv` 而不是外面套 shell 循环：`run_ecs.sh` 只认 `rl.<模块>`，
        #     而 exec 保持同一套 stdout/`tee`/日志管道不变（日志会一路接下去）。
        remain = a.iters - seg
        argv = [arg for arg in sys.argv[1:]]
        # ★ `--league-from` 是**一次性**的起点：重启时权重来自 `--resume` 的档，
        #   再带上它只会每 5 个 iter 刷一条"resume 赢"的提示（噪声）。
        for flag in ("--league-from",):
            if flag in argv:
                i = argv.index(flag)
                del argv[i:i + 2]
        for i, arg in enumerate(argv):                 # 把 `--iters` 改成剩余量
            if arg == "--iters":
                argv[i + 1] = str(remain)
        if "--resume" in argv:                         # 续跑点换成刚写的档
            argv[argv.index("--resume") + 1] = a.out
        else:
            argv += ["--resume", a.out]
        print(f"★ **定时重启进程**（RSS 归零）—— 剩余 {remain} 个 iter，"
              f"从 {a.out} 续跑", flush=True)
        os.execv(sys.executable, [sys.executable, "-m", "rl.train"] + argv)