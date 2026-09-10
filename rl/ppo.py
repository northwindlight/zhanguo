# -*- coding: utf-8 -*-
"""PPO（含 GAE）——候选动作清单版。

- **gamma = 1.0、lam = 1.0**：奖励是总消费增量，Σ 增量 ≡ 终局总消费。目标函数本身就是
  不折扣的，所以不打折；λ=1 时 GAE 退化为蒙特卡洛优势 `A_t = 整局剩余消费 − V(s_t)`，
  与目标同构——这样「前期挥霍、几百步后资源耗光」能被完整追责（λ=0.99 只看 100 步，追不到）。
- 每步候选集大小不同：minibatch 内按最大 K 补齐 + mask，补齐项不参与 softmax/熵。
"""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F


# ---------------------------------------------------------------- 轨迹缓冲
class Rollout:
    """轨迹缓冲 + **回报归一化**（running RMS）。

    为什么需要：奖励是每步消费增量，量级横跨好几个数量级（早期几十、后期几万），
    critic 直接在这种尺度上回归会不稳（我们实测 vf 到过 10³）。
    做法是 CleanRL 那套：维护折扣回报的均值/方差，用 1/std 缩放奖励，
    于是 critic 永远在 O(1) 尺度上学习，目标函数不变（正数缩放不影响最优策略）。
    """

    def __init__(self, gamma: float = 1.0, lam: float = 0.95, normalize: bool = True):
        self.gamma = float(gamma)
        self.lam = float(lam)
        self.normalize = bool(normalize)
        self.steps: list[dict] = []
        self._ret = 0.0            # 当前折扣回报（局末清零）
        self._mean = 0.0
        self._var = 1.0
        self._count = 1e-4

    def _scale(self, reward: float, done: bool) -> float:
        self._ret = self._ret * self.gamma + reward
        self._count += 1
        delta = self._ret - self._mean
        self._mean += delta / self._count
        self._var += delta * (self._ret - self._mean)
        if done:
            self._ret = 0.0
        if not self.normalize:
            return reward
        sd = max(self._var / self._count, 1e-8) ** 0.5
        return reward / sd

    def add(self, obs, action_idx: int, logprob: float, value: float,
            reward: float, done: bool):
        self.steps.append({
            "grid": obs.grid.astype(np.float16),     # 存半精度省内存
            "glob": obs.glob,
            "cand": obs.cand,
            "act": int(action_idx), "logp": float(logprob), "val": float(value),
            "rew": float(self._scale(reward, done)), "done": bool(done),
        })

    def __len__(self) -> int:
        return len(self.steps)

    def clear(self):
        self.steps.clear()

    def gae(self, last_value: float = 0.0) -> None:
        """就地算优势/回报（存回每个 step）。"""
        n = len(self.steps)
        adv = np.zeros(n, np.float32)
        last = 0.0
        for t in reversed(range(n)):
            s = self.steps[t]
            nv = last_value if t == n - 1 else self.steps[t + 1]["val"]
            nonterm = 0.0 if s["done"] else 1.0
            delta = s["rew"] + self.gamma * nv * nonterm - s["val"]
            last = delta + self.gamma * self.lam * nonterm * last
            adv[t] = last
        for t, s in enumerate(self.steps):
            s["adv"] = float(adv[t])
            s["ret"] = float(adv[t] + s["val"])
        return adv.mean(), adv.std()


# ---------------------------------------------------------------- 拼批
def collate(steps: list[dict], n_tiles: int):
    """把若干 step 拼成一个 batch（候选补齐到 K_max、军队补齐到 A_max）。"""
    b = len(steps)
    k = max(len(s["cand"]["actions"]) for s in steps)
    a = max(s["cand"]["n_armies"] for s in steps)

    grid = torch.as_tensor(np.stack([s["grid"] for s in steps]).astype(np.float32))
    glob = torch.as_tensor(np.stack([s["glob"] for s in steps]), dtype=torch.float32)
    type_idx = torch.zeros(b, k, dtype=torch.long)
    sub_idx = torch.zeros(b, k, dtype=torch.long)
    tile_idx = torch.full((b, k), n_tiles, dtype=torch.long)
    army_idx = torch.full((b, k), a, dtype=torch.long)
    amount_idx = torch.zeros(b, k, dtype=torch.long)
    mask = torch.zeros(b, k, dtype=torch.bool)
    afeats = torch.zeros(b, a, steps[0]["cand"]["army_feats"].shape[1], dtype=torch.float32)
    for i, s in enumerate(steps):
        c = s["cand"]
        m = len(c["actions"])
        type_idx[i, :m] = torch.as_tensor(c["type_idx"][:m])
        sub_idx[i, :m] = torch.as_tensor(c["sub_idx"][:m])
        tile_idx[i, :m] = torch.as_tensor(c["tile_idx"][:m])
        amt_idx = torch.as_tensor(c["army_idx"][:m])
        amt_idx = torch.where(amt_idx >= c["n_armies"], torch.full_like(amt_idx, a), amt_idx)
        army_idx[i, :m] = amt_idx
        amount_idx[i, :m] = torch.as_tensor(c["amount_idx"][:m])
        mask[i, :m] = True
        if c["n_armies"]:
            afeats[i, :c["n_armies"]] = torch.as_tensor(c["army_feats"])
    cand = {"type_idx": type_idx, "sub_idx": sub_idx, "tile_idx": tile_idx,
            "army_idx": army_idx, "amount_idx": amount_idx, "army_feats": afeats}
    return grid, glob, cand, mask


# ---------------------------------------------------------------- 采样
@torch.no_grad()
def value_of(model, obs) -> float:
    """只算价值（长局分块更新时，块边界用它自举）。"""
    grid, glob, cand, mask = collate([{"grid": obs.grid, "glob": obs.glob, "cand": obs.cand,
                                       "act": 0, "logp": 0.0, "val": 0.0,
                                       "rew": 0.0, "done": False}], model.n_tiles)
    return float(model(grid, glob, cand, mask)[1][0].item())


@torch.no_grad()
def act(model, obs, deterministic: bool = False):
    """按当前策略选一个候选动作。返回 (下标, logprob, value)。"""
    grid, glob, cand, mask = collate([{"grid": obs.grid, "glob": obs.glob,
                                       "cand": obs.cand, "act": 0, "logp": 0.0,
                                       "val": 0.0, "rew": 0.0, "done": False}],
                                     model.n_tiles)
    logits, value = model(grid, glob, cand, mask)
    logp = F.log_softmax(logits, dim=-1)
    if deterministic:
        idx = int(logp.argmax(-1).item())
    else:
        idx = int(torch.multinomial(logp.exp(), 1).item())
    return idx, float(logp[0, idx].item()), float(value[0].item())


# ---------------------------------------------------------------- PPO
class PPO:
    def __init__(self, model, *, lr: float = 3e-4, clip: float = 0.2, epochs: int = 4,
                 minibatch: int = 256, vf_coef: float = 0.5, ent_coef: float = 0.01,
                 max_grad_norm: float = 0.5, adv_norm: str = "minibatch"):
        self.model = model
        self.adv_norm = adv_norm          # "minibatch"（CleanRL 默认）/"global"（整块一次）
        self.opt = torch.optim.Adam(model.parameters(), lr=lr)
        self.clip = clip
        self.epochs = epochs
        self.minibatch = minibatch
        self.vf_coef = vf_coef
        self.ent_coef = ent_coef
        self.max_grad_norm = max_grad_norm

    def update(self, rollout: Rollout, last_value: float = 0.0) -> dict:
        adv_mean, adv_std = rollout.gae(last_value=last_value)
        steps = rollout.steps
        if self.adv_norm == "global":
            # 整块一次性归一化：**保留「这一局整体是好是坏」的信息**。
            # 每 minibatch 各归各的，会把「全是陷阱局的批」和「全是好局的批」都拉成
            # 零均值同方差——两局差 40 倍的信息就被抹掉了，策略于是骑墙。
            a = np.array([s["adv"] for s in steps], np.float32)
            a = (a - a.mean()) / (a.std() + 1e-8)
            for s, v in zip(steps, a):
                s["adv"] = float(v)
        n = len(steps)
        idxs = np.arange(n)
        stats = {"pg": 0.0, "vf": 0.0, "ent": 0.0, "kl": 0.0, "clipfrac": 0.0, "n": 0}
        for _ in range(self.epochs):
            np.random.shuffle(idxs)
            for start in range(0, n, self.minibatch):
                mb = [steps[i] for i in idxs[start:start + self.minibatch]]
                grid, glob, cand, mask = collate(mb, self.model.n_tiles)
                logits, value = self.model(grid, glob, cand, mask)
                logp_all = F.log_softmax(logits, dim=-1)
                act_t = torch.as_tensor([s["act"] for s in mb], dtype=torch.long)
                logp = logp_all.gather(1, act_t.unsqueeze(1)).squeeze(1)
                old_logp = torch.as_tensor([s["logp"] for s in mb], dtype=torch.float32)
                adv = torch.as_tensor([s["adv"] for s in mb], dtype=torch.float32)
                if self.adv_norm == "minibatch":
                    adv = (adv - adv.mean()) / (adv.std() + 1e-8)
                ret = torch.as_tensor([s["ret"] for s in mb], dtype=torch.float32)

                ratio = (logp - old_logp).exp()
                pg1 = -adv * ratio
                pg2 = -adv * ratio.clamp(1 - self.clip, 1 + self.clip)
                pg = torch.max(pg1, pg2).mean()
                # 价值裁剪：单块训练里回报可能突然很大（比如一局收尾把消费打上去），
                # 不裁剪的话价值网会被一个离群目标拽飞、连带把策略也带崩。
                old_v = torch.as_tensor([s["val"] for s in mb], dtype=torch.float32)
                v_clip = old_v + (value - old_v).clamp(-self.clip, self.clip)
                vf = torch.max((value - ret) ** 2, (v_clip - ret) ** 2).mean()
                p = logp_all.exp()
                ent = -(p * logp_all.masked_fill(~mask, 0.0)).sum(-1).mean()
                loss = pg + self.vf_coef * vf - self.ent_coef * ent

                self.opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), self.max_grad_norm)
                self.opt.step()

                with torch.no_grad():
                    stats["pg"] += float(pg)
                    stats["vf"] += float(vf)
                    stats["ent"] += float(ent)
                    stats["kl"] += float((old_logp - logp).mean())
                    stats["clipfrac"] += float(((ratio - 1).abs() > self.clip).float().mean())
                    stats["n"] += 1
        for k in ("pg", "vf", "ent", "kl", "clipfrac"):
            stats[k] /= max(1, stats["n"])
        stats["adv_mean"] = float(adv_mean)
        stats["adv_std"] = float(adv_std)
        return stats
