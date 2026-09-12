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
            reward: float, done: bool, win=None):
        """`win`：token 窗口（P4 主干要）。**必须存**，不能更新时重算 ——
        PPO 的重要性比 `exp(logp_new − logp_old)` 要求两次前向看到**同一个输入**；
        重算（哪怕只在浮点上差一点）会直接污染 ratio。

        ★存 **fp32**，不学 `grid` 那样压 fp16：`grid` 通道大多是 0/1，压了无所谓；
        窗口里有连续量（块均值、相对坐标），而 ratio 对 logp 的**差值**敏感 ——
        两次前向输入不一致的代价远大于那点内存。"""
        self.steps.append({
            "grid": obs.grid.astype(np.float16),     # 存半精度省内存
            "glob": obs.glob,
            "cand": obs.cand,
            "win": win,
            "act": int(action_idx), "logp": float(logprob), "val": float(value),
            "rew": float(self._scale(reward, done)), "done": bool(done),
        })

    def state(self) -> dict:
        """归一化器状态——**必须存进 checkpoint**。

        否则重启续训时 _mean/_var/_count 从零开始，奖励缩放尺度突变，
        critic 学到的价值全部错配到新尺度上，每次重启都要经历一段破坏期。
        """
        return {"mean": self._mean, "var": self._var, "count": self._count, "ret": self._ret}

    def load_state(self, d: dict) -> None:
        self._mean = float(d.get("mean", 0.0))
        self._var = float(d.get("var", 1.0))
        self._count = float(d.get("count", 1e-4))
        self._ret = float(d.get("ret", 0.0))

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
def collate(steps: list[dict], n_tiles: int = 0, *, need_grid: bool = True):
    """把若干 step 拼成一个 batch（网格补到批内最大；候选补 K_max、军队补 A_max）。

    ★**网格尺寸逐帧可变**（观测 = 可见区外接框，随帝国增长；地图尺寸也逐局可变），
    所以这里必须补齐到批内最大，并且**用补齐后的行宽重算落点下标** ——
    环境给的是外接框内的相对坐标 `(tile_dx, tile_dy)`，不是扁平下标，
    正是因为不同帧的行宽不同，扁平下标拼批后必然错位。
    参数 `n_tiles` 已废弃（空位下标 = 补齐后的 H*W，由本函数算出来），保留只为兼容调用点。
    """
    b = len(steps)
    k = max(len(s["cand"]["actions"]) for s in steps)
    a = max(s["cand"]["n_armies"] for s in steps)
    ch = steps[0]["grid"].shape[0]
    hmax = max(s["grid"].shape[1] for s in steps)
    wmax = max(s["grid"].shape[2] for s in steps)
    null_tile = hmax * wmax

    # 网格补齐：右下补 0（= 「框外」，与本帧的雾一致）。
    # ★`need_grid=False`：P4 的主干**没有 CNN**，地图信息全在窗口的 M 组里，
    #   拼这个网格纯属白干 —— 而它是这里最贵的一段（b×C×hmax×wmax 的 numpy 拷贝）。
    #   `hmax/wmax` 仍然要算（`tile_idx` 的扁平下标按它重算），但那只是读 shape。
    if need_grid:
        gpad = np.zeros((b, ch, hmax, wmax), np.float32)
        for i, s in enumerate(steps):
            g = s["grid"]
            gpad[i, :, :g.shape[1], :g.shape[2]] = g
        grid = torch.as_tensor(gpad)
    else:
        grid = None
    glob = torch.as_tensor(np.stack([s["glob"] for s in steps]), dtype=torch.float32)
    type_idx = torch.zeros(b, k, dtype=torch.long)
    sub_idx = torch.zeros(b, k, dtype=torch.long)
    tile_idx = torch.full((b, k), null_tile, dtype=torch.long)
    army_idx = torch.full((b, k), a, dtype=torch.long)
    amount_idx = torch.zeros(b, k, dtype=torch.long)
    # 相对落点（-1 = 无落点）。补出来的位置**保持 -1**，别补 0 —— 0 是合法的相对坐标。
    tile_dx = torch.full((b, k), -1, dtype=torch.long)
    tile_dy = torch.full((b, k), -1, dtype=torch.long)
    tile_hx = torch.full((b, k), -1, dtype=torch.long)   # ★相对家（模型特征用）
    tile_hy = torch.full((b, k), -1, dtype=torch.long)
    mask = torch.zeros(b, k, dtype=torch.bool)
    afeats = torch.zeros(b, a, steps[0]["cand"]["army_feats"].shape[1], dtype=torch.float32)
    for i, s in enumerate(steps):
        c = s["cand"]
        m = len(c["actions"])
        type_idx[i, :m] = torch.as_tensor(c["type_idx"][:m])
        sub_idx[i, :m] = torch.as_tensor(c["sub_idx"][:m])
        # 落点：相对坐标 → 补齐后的扁平下标（`fmap.flatten(2)` 是 `x*W + y`）
        dx = torch.as_tensor(c["tile_dx"][:m])
        dy = torch.as_tensor(c["tile_dy"][:m])
        ti = torch.where(dx >= 0, dx * wmax + dy, torch.full_like(dx, null_tile))
        tile_idx[i, :m] = ti
        tile_dx[i, :m] = dx
        tile_dy[i, :m] = dy
        tile_hx[i, :m] = torch.as_tensor(c["tile_hx"][:m])
        tile_hy[i, :m] = torch.as_tensor(c["tile_hy"][:m])
        amt_idx = torch.as_tensor(c["army_idx"][:m])
        amt_idx = torch.where(amt_idx >= c["n_armies"], torch.full_like(amt_idx, a), amt_idx)
        army_idx[i, :m] = amt_idx
        amount_idx[i, :m] = torch.as_tensor(c["amount_idx"][:m])
        mask[i, :m] = True
        if c["n_armies"]:
            afeats[i, :c["n_armies"]] = torch.as_tensor(c["army_feats"])
    cand = {"type_idx": type_idx, "sub_idx": sub_idx, "tile_idx": tile_idx,
            "army_idx": army_idx, "amount_idx": amount_idx, "army_feats": afeats,
            "null_tile": null_tile,
            # ★规则表内容（§10.2 载体 B）：每个 kind 一张 `[B, n_sub, F_kind]`。
            #   逐帧现算（域随机化会每局换表），所以拼批时按帧 stack，不做去重。
            "content": {k: torch.as_tensor(np.stack([s["cand"]["content"][k]
                                                     for s in steps]))
                        for k in steps[0]["cand"].get("content", {})},
            # ★`tile_dx/tile_dy` 原样带出去（-1 = 无落点）。P4 的主干**没有网格**，
            #   候选只带自己的相对落点去 attend 窗口，所以它要的是这两个，
            #   不是 `tile_idx`（那是给 CNN 用的扁平下标，绑死网格形状）。
            #   补出来的位置保持 -1，别补 0 —— 0 是个合法的相对坐标。
            "tile_dx": tile_dx, "tile_dy": tile_dy,
            # ★相对家的那一对（与 token 组同原点）
            "tile_hx": tile_hx, "tile_hy": tile_hy}
    return grid, glob, cand, mask


def collate_cand(steps: list[dict], n_tiles: int = 0):
    """只拼候选与掩码（不建网格）—— P4 的主干入口。"""
    _grid, _glob, cand, mask = collate(steps, n_tiles, need_grid=False)
    return cand, mask


def collate_window(wins: list) -> dict:
    """token 窗口列表 → 批。`{"feats": {组: [B,n,F]}, "mask": {组: [B,n]}}`。

    比 `collate` 简单得多，因为 **`tokenize` 已经保证每组的特征宽度 F 是常量**
    （模块级常量，不随局面变；`tests/test_tokenize.py::test_各组宽度是常量` 盯着）。
    所以这里只补 **token 数**那一维，不补特征维——补特征维正是 `collate` 里
    候选要补到批内最大 K 的原因，那笔 padding 账在候选那边付，别在窗口这边再付一次。
    """
    from rl.tokenize import GROUPS

    if not wins:
        raise ValueError("空窗口列表")
    out_f: dict[str, torch.Tensor] = {}
    out_m: dict[str, torch.Tensor] = {}
    for g in GROUPS:
        f0 = wins[0].feats[g]
        for w in wins:                      # F 必须逐帧一致，不一致就是 tokenize 违约
            assert w.feats[g].shape[1] == f0.shape[1], (
                f"{g} 组特征宽度不一致：{w.feats[g].shape[1]} != {f0.shape[1]}"
                " —— 宽度随局面变会让整批错位，见 rl/tokenize.py 的偏离说明 1")
        nmax = max(w.feats[g].shape[0] for w in wins)
        f = np.zeros((len(wins), nmax, f0.shape[1]), np.float32)
        m = np.zeros((len(wins), nmax), bool)
        for i, w in enumerate(wins):
            n = w.feats[g].shape[0]
            f[i, :n] = w.feats[g]
            m[i, :n] = w.mask[g]
        out_f[g] = torch.as_tensor(f)
        out_m[g] = torch.as_tensor(m)
    return {"feats": out_f, "mask": out_m}


# ---------------------------------------------------------------- 采样
def is_transformer(model) -> bool:
    """P4 主干与点积头**签名不同**（前者吃窗口+候选，后者吃网格+全局+候选）。
    用一个能力探测分派，比到处传 `--net` 字符串稳（模型自己知道自己是哪种）。"""
    return hasattr(model, "encode_window")


def _one_step(obs):
    return {"grid": obs.grid, "glob": obs.glob, "cand": obs.cand,
            "act": 0, "logp": 0.0, "val": 0.0, "rew": 0.0, "done": False}


def forward_batch(model, steps, wins=None):
    """统一入口：`(logits, value, cand_mask)`。`wins` 只在需要窗口的模型上用。

    **第三个返回值是候选掩码**，不是摆设：PPO 算熵时要 `masked_fill(~mask, 0)`
    把补齐出来的候选排除掉，少了它整块会 `NameError`（改这个签名时就踩了）。
    点积头那边掩码在 `collate` 内部生成、不随 `grid/glob` 一起返回，所以这里统一补上。

    `n_tiles` 从模型上取（点积头有、P4 主干没有）—— 别让调用方去猜自己是哪种模型，
    那正是 `is_transformer` 要消掉的东西。
    """
    if is_transformer(model):
        cand, cmask = collate_cand(steps)
        logits, value = model(collate_window(wins), cand, cmask)
        return logits, value, cmask
    grid, glob, cand, mask = collate(steps, getattr(model, "n_tiles", 0))
    wb = collate_window(wins) if wins is not None else None
    logits, value = model(grid, glob, cand, mask, win=wb)
    return logits, value, mask


@torch.no_grad()
def value_of(model, obs, win=None) -> float:
    """只算价值（长局分块更新时，块边界用它自举）。"""
    wins = None if (win is None and not is_transformer(model)) else [win]
    return float(forward_batch(model, [_one_step(obs)], wins)[1][0].item())


@torch.no_grad()
def act(model, obs, deterministic: bool = False, win=None):
    """按当前策略选一个候选动作。返回 (下标, logprob, value)。

    `win`：token 窗口（P3 起）。给了就走窗口编码的 query。**采样期间 batch=1**，
    所以 `collate_window` 的补 token 维是空操作。
    """
    wins = None if (win is None and not is_transformer(model)) else [win]
    logits, value, _m = forward_batch(model, [_one_step(obs)], wins)
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
                # 统一入口：点积头与 P4 主干签名不同，`forward_batch` 自己分派。
                # 窗口只对 P4 主干有意义，别给点积头传（它的 forward 不收 win）。
                wins = ([s["win"] for s in mb] if is_transformer(self.model) else None)
                logits, value, mask = forward_batch(self.model, mb, wins)
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
