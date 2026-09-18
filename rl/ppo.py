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

from rl.device import model_device, move_to


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
            reward: float, done: bool, win=None, ok: bool = True, turn: int = 0):
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
            # ★可执行性辅助头的**标签**（专家 2026-09-13）：引擎这一步接受了没有。
            #   默认 True ⇒ 不传时行为与开关存在前相同。
            "ok": bool(ok),
            # ★这一步在第几回合（BC 锚只锚前 N 回合用）。默认 0 ⇒ 不传时行为不变。
            "turn": int(turn),
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


def forward_batch(model, steps, wins=None, *, return_exec: bool = False):
    """统一入口：`(logits, value, cand_mask)`。`wins` 只在需要窗口的模型上用。

    **第三个返回值是候选掩码**，不是摆设：PPO 算熵时要 `masked_fill(~mask, 0)`
    把补齐出来的候选排除掉，少了它整块会 `NameError`（改这个签名时就踩了）。
    点积头那边掩码在 `collate` 内部生成、不随 `grid/glob` 一起返回，所以这里统一补上。

    `n_tiles` 从模型上取（点积头有、P4 主干没有）—— 别让调用方去猜自己是哪种模型，
    那正是 `is_transformer` 要消掉的东西。
    """
    # ★搬运点之一（见 `rl/device.py`）：collate 出来的一律是 CPU 张量，模型可能在 GPU 上。
    #   放在这里而不是各调用点 —— 训练、评估、DAgger 采样**全部**走这个入口。
    dev = model_device(model)
    # ★返回的掩码**也要搬**：调用方会拿它跟 logits 一起算（`logp_all.masked_fill
    #   (~mask, …)`），留在 CPU 就炸「expected self and mask to be on the same
    #   device」。搬过的张量已经在上面的 `move_to(...)` 里造出来了，这里只是
    #   把**同一个**搬后版本返回 —— 别再 `collate` 一次，那会白算一遍。
    if is_transformer(model):
        cand, cmask = collate_cand(steps)
        _mv = move_to((collate_window(wins), cand, cmask), dev)
        if return_exec:
            logits, value, pexec = model(*_mv, return_exec=True)
            return logits, value, _mv[2], pexec
        logits, value = model(*_mv)
        return logits, value, _mv[2]
    grid, glob, cand, mask = collate(steps, getattr(model, "n_tiles", 0))
    wb = collate_window(wins) if wins is not None else None
    _mv = move_to((grid, glob, cand, mask), dev)
    logits, value = model(*_mv, win=move_to(wb, dev))
    return logits, value, _mv[3]


@torch.no_grad()
def value_of(model, obs, win=None) -> float:
    """只算价值（长局分块更新时，块边界用它自举）。"""
    wins = None if (win is None and not is_transformer(model)) else [win]
    return float(forward_batch(model, [_one_step(obs)], wins)[1][0].item())


EXEC_EPS = 1e-3            # σ(p_exec) 的下限：p→0 时 log 不发散
EXEC_BETA = 0.5            # 软加权强度（专家 2026-09-13 给的原值）


def exec_bias(logits, pexec, beta: float = EXEC_BETA, eps: float = EXEC_EPS):
    """可执行性软加权的**唯一一份公式**：`logit' = logit + β · log(σ(p_exec) + ε)`。

    ★★**采样侧与更新侧必须都走这一处**（2026-09-18 用户选定的第三条路的立命之本）：
    上一版把加权**只加在采样侧** ⇒ `act()` 存的 `old_logp` 来自加权分布，而
    `PPO.update` 重算的 `logp_all` 来自未加权分布 ⇒ 参数一动没动时
    `ratio = π_raw/π_w ≠ 1`，clip 作用在**错位**的比值上（探针实测：训过的头
    1.9% 候选出信任域）。**修法不是"别加权"，是"两侧一致"** —— 于是 β 变成一个
    可以调、可以退火的正常超参。

    为什么值得再试（那一版还测出"软加权对行为是空操作"，33.2% vs 33.8%）：
    那条测量的前提是**头没训过**（p_exec≈0.5 ⇒ 压制≈0）。而冻结表征里
    `ok` 是线性可分的（`probe_exec_head_learn.py` 冒烟：留出 AUC 0.457 → **0.880**）
    ⇒ 头训得出来，压制才有力。
    """
    return logits + beta * torch.log(torch.sigmoid(pexec) + eps)


@torch.no_grad()
def policy_logits(model, obs, win=None, use_exec: bool = False,
                  exec_beta: float = EXEC_BETA):
    """候选 logits，**`act()` 与诊断探针共用这一处**。返回 `(logits, value, mask)`。

    `use_exec=True` 时套上可执行性软加权（`exec_bias`）：**软加权、不硬 mask**，
    保住「让模型自己学会哪些点不动」的口径。

    ★**公式只此一份**：探针要判断「主干自己学会了没有」还是「靠推理时加权兜住」，
      必须跑同一策略的开关两档。公式要是抄成第二份，改一处忘一处，
      ——「测的」和「跑的」就不是同一个策略了，而那正是这个探针要防的事。
    """
    wins = None if (win is None and not is_transformer(model)) else [win]
    if use_exec:
        logits, value, mask, pexec = forward_batch(model, [_one_step(obs)], wins,
                                                   return_exec=True)
        # 软加权：概率高的候选 logit 上去，低的下来，但**谁都没被删掉**。
        return exec_bias(logits, pexec, exec_beta), value, mask
    logits, value, mask = forward_batch(model, [_one_step(obs)], wins)
    return logits, value, mask


def act(model, obs, deterministic: bool = False, win=None, use_exec: bool = False,
        exec_beta: float = EXEC_BETA):
    """按当前策略选一个候选动作。返回 (下标, logprob, value)。

    `win`：token 窗口（P3 起）。给了就走窗口编码的 query。**采样期间 batch=1**，
    所以 `collate_window` 的补 token 维是空操作。

    `use_exec` / `exec_beta`：见 `policy_logits` 与 `exec_bias`。
    ⚠ **默认关**：`bc.py` 也调这个函数，而它的 ckpt 里 `exec_head` 是随机初始化的
    （旧 ckpt 用 `strict=False` 加载），打开等于拿噪声去加权。
    ⚠ **开了就一定把 `PPO(exec_beta=…)` 设成同一个值**，否则两侧不一致。
    """
    logits, value, _m = policy_logits(model, obs, win=win, use_exec=use_exec,
                                      exec_beta=exec_beta)
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
                 exec_coef: float = 0.0, exec_beta: float = 0.0,
                 bc_model=None, bc_coef: float = 0.0, bc_turns: int = 70,
                 max_grad_norm: float = 0.5, adv_norm: str = "minibatch",
                 grad_diag: bool = False):
        self.model = model
        # ★梯度分项诊断（2026-09-18，用户：「方差主导了？比价值头更高？」）。
        #   **默认关 ⇒ 行为与开关存在前逐位相同**（关时一行诊断代码都不进）。
        #   打开后每步额外算 `pg/vf/ent/bc` 四项各自的梯度，累计批间一致性与分散度。
        #   为什么量「一致性」而不是梯度大小：**Adam 每参数步长≈lr**（`m̂/√v̂`），
        #   所以幅度大的零均值噪声会被压掉，**只有批间一致的分量推得动权重**。
        self.grad_diag = bool(grad_diag)
        self._diag: dict | None = {} if self.grad_diag else None

        self.adv_norm = adv_norm          # "minibatch"（CleanRL 默认）/"global"（整块一次）
        self.opt = torch.optim.Adam(model.parameters(), lr=lr)
        self.clip = clip
        self.epochs = epochs
        self.minibatch = minibatch
        self.vf_coef = vf_coef
        self.ent_coef = ent_coef
        # ★可执行性辅助头的权重（0 = 关，行为与开关存在前逐位相同）。
        #   专家给的起点是 0.1；标签只有"当步选中的候选"有（部分标签）。
        self.exec_coef = exec_coef
        # ★第三条路（用户 2026-09-18）：把可执行性软加权**同时**加在采样侧与更新侧。
        #   `0.0` = 关 ⇒ 行为与开关存在前逐位相同。⚠ 采样侧必须用同一个值
        #   （`act(use_exec=…, exec_beta=…)`），否则又回到那个 `ratio ≠ 1` 的错位 bug。
        self.exec_beta = float(exec_beta)
        # ★BC 锚（用户 2026-09-14 拍板）：冻一份 BC 策略，对**回合 ≤ bc_turns** 的状态
        #   加 `bc_coef · KL(π_θ ‖ π_BC)`。理由：实测「学了忘」发生在**开局**
        #   （`mkt` 切法 A：开局前 20 回合 7/8 局已在掉，而那里所有策略构成相同），
        #   而 BC 教的正是开局那 70 回合 ⇒ 灾难性遗忘。
        #   **只锚 ≤bc_turns**：后期（战争/外交）没有老师示范，不锚，留给 PPO 自己学。
        self.bc_model = bc_model
        self.bc_coef = bc_coef
        self.bc_turns = bc_turns
        self.max_grad_norm = max_grad_norm

    # ---- 梯度分项诊断（只有 `grad_diag=True` 才会被调到） ----
    def _diag_step(self, terms: dict) -> None:
        """累计**本 minibatch** 各项的梯度（向量和 + 平方和 + 范数和）。

        为什么留这三个量：Adam 的 `m̂/√v̂` 就是**逐参数信噪比** ——
        批间一致的项 `m` 涨、`√v` 也涨但比值趋于 1（拿满步长）；零均值噪声项 `m→0`
        而 `√v` 照涨 ⇒ 步长被压掉。**所以判据是 `‖m/(σ+ε)‖`，不是 `‖g‖`。**
        """
        params = [p for p in self.model.parameters() if p.requires_grad]
        for name, t in terms.items():
            if t is None:
                continue
            gs = torch.autograd.grad(t, params, retain_graph=True, allow_unused=True)
            g = torch.cat([(torch.zeros_like(p) if x is None else x).detach().reshape(-1)
                           for p, x in zip(params, gs)])
            e = self._diag.setdefault(name, {"sum": torch.zeros_like(g),
                                             "sq": torch.zeros_like(g),
                                             "norm": 0.0, "n": 0})
            e["sum"] += g
            e["sq"] += g * g
            e["norm"] += float(g.norm())
            e["n"] += 1

    def report_grad_diag(self, reset: bool = True) -> dict:
        """把累计的分项梯度算成可读数：批间一致性 `coh` 与 Adam 信噪比 `snr`。

        `coh = ‖mean_b g‖/mean_b‖g‖`：1 = 每批都指同一个方向；→0 = 纯零均值噪声。
        `snr = ‖mean_b g/(std_b g + ε)‖`：**这一项真正推得动多少权重**。
        `cos:a:b`：两项的一致分量是互相帮忙还是互相拆台。
        """
        out: dict = {}
        means: dict = {}
        for name, e in (self._diag or {}).items():
            n = max(1, e["n"])
            m = e["sum"] / n
            sd = (e["sq"] / n - m * m).clamp(min=0.0).sqrt()
            means[name] = m
            out[name] = {
                "n_minibatch": e["n"],
                "norm_mean": float(m.norm()),               # 一致分量的大小
                "norm_avg": e["norm"] / n,                  # 逐批平均大小
                "coh": float(m.norm()) / max(e["norm"] / n, 1e-30),
                "snr": float((m / (sd + 1e-8)).norm()),
            }
        names = list(means)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                den = float(means[a].norm() * means[b].norm())
                out[f"cos:{a}:{b}"] = (float((means[a] * means[b]).sum()) / den) if den else 0.0
        if reset and self._diag is not None:
            self._diag = {}
        return out

    def update(self, rollout: Rollout, last_value: float = 0.0,
               warmup: bool = False) -> dict:
        """`warmup=True`：**只训辅助头**，策略/价值参数全部冻结。

        ★为什么需要（2026-09-13 实测教训）：从旧 ckpt 续训时 `exec_head` 是
        **随机初始化**的（`strict=False` 加载），而软加权**每一步都在用它**去改
        logits。不做 warmup 直接开 `--exec-head 0.1`，等于让一个随机线性层在头
        1200 步里乱压 logits —— 实测两块就把策略打回开局（tiles 5、
        消费 2374，而 BC 是 4747）。

        `requires_grad=False` 的参数在 Adam 里没有梯度 ⇒ `step()` 不动它们，
        所以不需要单独的优化器。
        """
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
        # ★warmup：**冻结除辅助头之外的一切**。`requires_grad=False` 的参数在
        #   Adam 里拿不到梯度 ⇒ `step()` 不动它们，所以不需要第二个优化器。
        _frozen = []
        if warmup:
            for _n, _p in self.model.named_parameters():
                if _p.requires_grad and not _n.startswith("exec_head."):
                    _p.requires_grad = False
                    _frozen.append(_p)
        try:
            return self._update_iters(rollout, steps, n, idxs, warmup,
                                      adv_mean, adv_std)
        finally:
            for _p in _frozen:                      # 无论如何都要解冻
                _p.requires_grad = True

    def _update_iters(self, rollout, steps, n, idxs, warmup, adv_mean, adv_std) -> dict:
        stats = {"pg": 0.0, "vf": 0.0, "ent": 0.0, "kl": 0.0, "clipfrac": 0.0, "n": 0,
                 "bc_kl": 0.0}
        for _ in range(self.epochs):
            np.random.shuffle(idxs)
            for start in range(0, n, self.minibatch):
                mb = [steps[i] for i in idxs[start:start + self.minibatch]]
                # 统一入口：点积头与 P4 主干签名不同，`forward_batch` 自己分派。
                # 窗口只对 P4 主干有意义，别给点积头传（它的 forward 不收 win）。
                wins = ([s["win"] for s in mb] if is_transformer(self.model) else None)
                if self.exec_coef or self.exec_beta:
                    logits, value, mask, pexec = forward_batch(
                        self.model, mb, wins, return_exec=True)
                else:
                    logits, value, mask, pexec = *forward_batch(self.model, mb, wins), None
                if self.exec_beta:
                    # ★**与采样侧同一个公式、同一个 β**（`exec_bias` 只此一份）。
                    #   放在这里 ⇒ `logp_all` / `ent` / `ratio` 全都基于**加权后**的
                    #   分布，与 `act()` 存下来的 `old_logp` 一致 ⇒ `ratio` 在参数未动时 = 1。
                    logits = exec_bias(logits, pexec, self.exec_beta)
                # ★`as_tensor` 不给 `device=` 就落在 **CPU**，而 `forward_batch` 已经把
                #   logits/value 搬到了模型所在设备 ⇒ `gather` 会炸
                #   「Expected all tensors to be on the same device」。
                #   **这是第三处搬运点** —— `rl/device.py` 开头只点了两处（forward_batch
                #   与 bc 的训练步），漏了这个；`bc.py` **不走 `PPO.update`**（它有自己的
                #   训练步）所以从没暴露，只在 PPO 这条路上炸。
                _d = logits.device
                logp_all = F.log_softmax(logits, dim=-1)
                act_t = torch.as_tensor([s["act"] for s in mb], dtype=torch.long, device=_d)
                logp = logp_all.gather(1, act_t.unsqueeze(1)).squeeze(1)
                old_logp = torch.as_tensor([s["logp"] for s in mb], dtype=torch.float32,
                                           device=_d)
                adv = torch.as_tensor([s["adv"] for s in mb], dtype=torch.float32,
                                      device=_d)
                if self.adv_norm == "minibatch":
                    # ★★退化批守卫（2026-09-13 实测，杀过一整炉）——
                    #   `torch.std()` 默认**无偏**（ddof=1），**n==1 时是 nan**。
                    #   而切批是 `range(0, n, minibatch)`：只要 `n % minibatch == 1`，
                    #   最后一个 minibatch 就只有 1 个样本 ⇒ `adv` 全 nan ⇒ loss nan
                    #   ⇒ **权重 nan** ⇒ 下一块 `act()` 采样时崩：
                    #   `probability tensor contains either inf, nan or element < 0`。
                    #   每块约 1/32 概率踩中，125 块期望踩 ~4 次 —— 表现就是
                    #   **"随机时刻崩"**，且崩前一块的 `pg` 印成 nan（`vf` 仍是有限值，
                    #   因为 vf 不含 adv，这正是判据）。
                    #   实测现场：块 16，8161 = 255×32 + 1。
                    #   修法：退化批**只中心化、不缩放**（n==1 时中心化后恒为 0）。
                    adv = adv - adv.mean()
                    if adv.numel() > 1:
                        _sd = adv.std()
                        if torch.isfinite(_sd) and _sd > 1e-8:
                            adv = adv / _sd
                ret = torch.as_tensor([s["ret"] for s in mb], dtype=torch.float32,
                                      device=_d)

                ratio = (logp - old_logp).exp()
                pg1 = -adv * ratio
                pg2 = -adv * ratio.clamp(1 - self.clip, 1 + self.clip)
                pg = torch.max(pg1, pg2).mean()
                # 价值裁剪：单块训练里回报可能突然很大（比如一局收尾把消费打上去），
                # 不裁剪的话价值网会被一个离群目标拽飞、连带把策略也带崩。
                old_v = torch.as_tensor([s["val"] for s in mb], dtype=torch.float32,
                                        device=_d)
                v_clip = old_v + (value - old_v).clamp(-self.clip, self.clip)
                vf = torch.max((value - ret) ** 2, (v_clip - ret) ** 2).mean()
                p = logp_all.exp()
                ent = -(p * logp_all.masked_fill(~mask, 0.0)).sum(-1).mean()
                # ★可执行性辅助 loss（专家 2026-09-13）：标签**只有"当步选中的那个
                #   候选"有**（`env.step` 的 ok），其余候选无标签 —— 所以是
                #   部分标签下的 BCE，只算选中位置。软加权在 `act` 里做。
                exec_loss = None
                if self.exec_coef:
                    ok_t = torch.as_tensor([s["ok"] for s in mb], dtype=torch.float32,
                                           device=_d)
                    pe = pexec.gather(1, act_t.unsqueeze(1)).squeeze(1)
                    exec_loss = F.binary_cross_entropy_with_logits(pe, ok_t)
                # ★BC 锚：只对 turn ≤ bc_turns 的步，KL(π_θ ‖ π_BC)，π_BC 冻住不反传
                bc_kl = None
                if self.bc_model is not None and self.bc_coef:
                    _sel = [i for i, s in enumerate(mb) if s.get("turn", 0) <= self.bc_turns]
                    if _sel:
                        # ★BC 前向必须跑**整个 minibatch**、再按 `_i` 索引 ——
                        #   不能对子集 `_mb` 单独 collate：候选维 K 是**批内最大值**，
                        #   子集的 K 通常更小 ⇒ `_p`(K=378) 与 `_q`(K=196) 形状不等
                        #   （2026-09-14 实测崩在这里）。同一批输入 ⇒ K 一致、`mask` 也一致。
                        _w = ([s["win"] for s in mb]
                              if is_transformer(self.model) else None)
                        with torch.no_grad():
                            _bl, _bv, _bm = forward_batch(self.bc_model, mb, _w)
                            _q = F.log_softmax(_bl, dim=-1)
                        _i = torch.as_tensor(_sel, dtype=torch.long, device=_d)
                        _p = logp_all[_i]
                        _q = _q[_i]
                        _mk = mask[_i]
                        bc_kl = (_p.exp() * (_p - _q)).masked_fill(~_mk, 0.0).sum(-1).mean()

                if warmup:
                    # ★warmup 期间**只训头**：`pg/vf/ent` 的图照建但不出现在 loss 里，
                    #   而且策略参数已被冻结（`requires_grad=False`）⇒ Adam 不动它们。
                    loss = self.exec_coef * exec_loss if exec_loss is not None else pg * 0.0
                else:
                    loss = pg + self.vf_coef * vf - self.ent_coef * ent
                    if exec_loss is not None:
                        loss = loss + self.exec_coef * exec_loss
                    if bc_kl is not None:
                        loss = loss + self.bc_coef * bc_kl

                if self.grad_diag and not warmup:
                    # 四项**按它们在 loss 里的系数**取（符号也照 loss）⇒
                    # `Σ 四项梯度 ≡ ∇loss`，分解是恒等式不是近似。
                    self._diag_step({
                        "pg": pg,
                        "vf": self.vf_coef * vf,
                        "ent": -self.ent_coef * ent,
                        "bc": (self.bc_coef * bc_kl) if bc_kl is not None else None,
                    })
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
                    if bc_kl is not None:
                        stats["bc_kl"] += float(bc_kl)
                    stats["n"] += 1
        # ★`bc_kl` 必须一起归一（2026-09-14 修）：它上面是 `+=` 累加的，
        #   漏在这里就会报成"**所有 minibatch 的和**"——而 minibatch 数随
        #   `--rollout-episodes`（4 局 ~215 / 16 局 ~850）和**每局长度**（5~200 回合）变
        #   ⇒ 同一个逐批 KL，日志上能差 4~5 倍，**跨 run、跨块都不可比**。
        #   踩过：A16（16 局）块101 报 bc_kl=60.9，看着像锚项爆炸，实际逐批 0.071
        #   与 bc1 的 0.066 一致。**只改日志口径，训练数学与 ckpt 一字未动 ⇒ 不必重炼。**
        for k in ("pg", "vf", "ent", "kl", "clipfrac", "bc_kl"):
            stats[k] /= max(1, stats["n"])
        stats["adv_mean"] = float(adv_mean)
        stats["adv_std"] = float(adv_std)
        return stats
