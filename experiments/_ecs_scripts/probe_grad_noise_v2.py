# -*- coding: utf-8 -*-
"""`probe_grad_noise.py` 的 **v2 重写版**（McCandlish 梯度噪声尺度，单位=局）。

## ★门槛判决（2026-09-14 13:05，实测后写死）：**五种播种全不中 ⇒ 走退路**
试跑 0 目标指纹 1959 步 / 11768.292333333335；实测：
s(池种子)=1787、e(序号)=1882、s3000=2168、2000=2004、none(不补种)=1921 —— 最近差 35 步。
v1 的播种法已随源文件一起丢了，**不再猜**（判据事先写了"五种都不中就停"）。
⇒ **F 三组（BC 参照对 / bc1 / entann）全部用本 v2、seedmode=s、同一天同一把尺子测**：
   **组间对比（锚降不降 B_ep）干净成立**；**与归档 ≥30/≥33 的数字不同尺，不得直接对照**（只可作量级参考并标注）。

## 为什么是"重写"而不是"恢复"
v1 源文件被 2026-09-14 12:26 的 push-all 覆盖成 0 字节，且两处都没有 .pyc。
本 v2 依据 ① v1 文件头 docstring 的公式（12:35 前读过全文头 40 行）
② 归档日志 `probe_ecs/gradnoise_{v10_ckpt5,bc_ep100}_12x200.log` 的输出格式与数值反推实现。

**验收门槛（F 正式三组之前先过）**：
  `..._v2.py rl/runs/ppo_v10/ckpt_5.pt rl/runs/ppo_v10/ckpt_5.pt 12 200 100`
  逐位复现归档的 14 局步数/消费、c/s/L̄、三组 ‖μ‖²/trΣ/B_ep。
  复现 ⇒ v2 ≡ v1；不复现 ⇒ 三组（参照/bc1/entann）全部由 v2 同一把尺子测，
  **组间互比仍干净，但不能与归档参照比** —— 会照实标注。

公式（v1 docstring 逐字）：
  g_i = 第 i 局的 pg 梯度（θ 固定、零更新 ⇒ ratio≡1，clip 不起作用）
      = −Σ_{t∈i} ((A_t−c)/s)·∇log π(a_t) / L̄    （c、s、L̄ 由试跑局定死）
  ‖μ‖² = mean_{i≠j} g_i·g_j   trΣ = mean_i ‖g_i‖² − ‖μ‖²   B_ep = trΣ/‖μ‖²
  每块 N 局时 cos²(ḡ_N, μ) ≈ 1/(1+B_ep/N)；每局按奇/偶步拆两半：两半协方差 = 局内共享（结局）噪声
试跑局顺带算 ent_coef·∇ent，与 ‖μ‖ 比大小。
采样口径 = rl.train.SAMPLING_USE_EXEC；env/奖励/惩罚/抖动/norm 状态取自 recipe ckpt。

用法：python experiments/_ecs_scripts/probe_grad_noise_v2.py <权重ckpt> <recipe ckpt> [局=12] [回合=200] [池偏移=100] [--seedmode s|e|s3000] [--trialgate]
"""
import argparse
import json
from pathlib import Path
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

# ★本文件住在 experiments/_ecs_scripts/ 下，得自己把仓库根挂上（同目录那几个探针都有这行）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from rl.ppo import Rollout, act, forward_batch, _one_step  # noqa: E402
from rl.tokenize import tokenize
from rl.train import SAMPLING_USE_EXEC, build_env, build_model

ap = argparse.ArgumentParser()
ap.add_argument("w_ck"); ap.add_argument("r_ck")
ap.add_argument("episodes", nargs="?", type=int, default=12)
ap.add_argument("--arch", default="candx", choices=("candx", "t111"),
                help="权重 ckpt 是哪代架构：candx=现在 / t111=2026-09-13 之前（111 键）")
ap.add_argument("turns", nargs="?", type=int, default=200)
ap.add_argument("off", nargs="?", type=int, default=100)
ap.add_argument("--seedmode", default="s", choices=("s", "e", "s3000", "2000", "none"))
ap.add_argument("--trialgate", action="store_true")
A = ap.parse_args()

t0 = time.time()


class NS:
    def __init__(self, d):
        for k, v in d.items():
            setattr(self, k, v)


# ★`--arch t111`：用 **111 张量那代架构**加载 2026-09-13 之前的 ckpt。
#   不能直接 strict=False 塞进新架构 —— candx 加的 cross2/ln_cand 会是随机初值，
#   而 candx 就在读出段，直接改 h ⇒ 量出来的 ‖μ‖² 不是那个模型的。
#   `rl/train.py:build_model` 是在函数**内部** import 的，所以猴补丁能生效。
if getattr(A, "arch", "candx") == "t111":
    import importlib.util as _ilu
    import rl.transformer as _rt
    _sp = _ilu.spec_from_file_location(
        "_t111", Path(__file__).resolve().parent.parent / "_transformer_111.py")
    _tm = _ilu.module_from_spec(_sp); _sp.loader.exec_module(_tm)
    _rt.WindowTransformer = _tm.WindowTransformer
    print("★用 111 张量那代架构（candx 之前）")

RCK = torch.load(A.r_ck, map_location="cpu", weights_only=False)
WCK = torch.load(A.w_ck, map_location="cpu", weights_only=False)
ra = NS(RCK["args"])

env = build_env(ra)
model = build_model(env, ra)
missing, unexpected = model.load_state_dict(WCK["model"], strict=False)
model.eval()
print(f"权重 {A.w_ck}（第 {WCK.get('iter','?')} 块）  recipe {A.r_ck}")
print(f"缺失 {list(missing)}  多余 {list(unexpected)}")

pool = json.load(open(ra.map_pool))["pool"]
seeds = pool[A.off:A.off + 2 + A.episodes]
norm_src = "权重" if WCK.get("norm") else "recipe"
ro = Rollout(gamma=1.0, lam=1.0, normalize=bool(ra.norm_reward))
ro.load_state(WCK.get("norm") or RCK.get("norm") or {})
print(f"2 试跑 + {A.episodes} 局 × {A.turns} 回合  池 {ra.map_pool} 偏移 {A.off}  "
      f"SAMPLING_USE_EXEC={SAMPLING_USE_EXEC}")
print(f"lam=1.0 ent_coef={ra.ent_coef} norm_reward={bool(ra.norm_reward)} (norm 状态来自 {norm_src}) "
      f"invalid_penalty={ra.invalid_penalty} jitter={ra.rules_jitter}", flush=True)

PARAMS = [(n, p) for n, p in model.named_parameters()]
NAMES = [n for n, _ in PARAMS]
OFFS, acc = [], 0
for _, p in PARAMS:
    OFFS.append((acc, acc + p.numel())); acc += p.numel()
NPAR = acc
IDX_TRUNK = np.array([i for i, n in enumerate(NAMES)
                      if not (n.startswith("score.") or n.startswith("value.") or n.startswith("exec_head."))])
IDX_HEAD = np.array([i for i, n in enumerate(NAMES)
                     if n.startswith("score.") or n.startswith("value.") or n.startswith("exec_head.")])
IDX_ALL = np.arange(len(PARAMS))
# v1 的 [score] 组：主干以外 = score + value + exec_head？还是只 score？——门槛二选一都试得出来
IDX_SCORE = np.array([i for i, n in enumerate(NAMES) if n.startswith("score.") or n.startswith("value.")])
_T = torch.as_tensor(IDX_TRUNK); _S = torch.as_tensor(IDX_SCORE); _A = torch.as_tensor(IDX_ALL)


def set_seed(seed, idx):
    if A.seedmode == "s":
        torch.manual_seed(seed)
    elif A.seedmode == "e":
        torch.manual_seed(idx)
    elif A.seedmode == "2000":
        torch.manual_seed(2000 + idx)
    elif A.seedmode == "none":
        pass
    else:
        torch.manual_seed(3000 + idx)


def rollout_game(seed, idx):
    set_seed(seed, idx)
    obs = env.reset(seed)
    buf = []
    while True:
        win = tokenize(env, obs)
        o_pre = obs
        with torch.no_grad():
            a_i, _lp, v = act(model, o_pre, win=win, use_exec=SAMPLING_USE_EXEC)
        obs, r, done, info = env.step(o_pre.cand["actions"][a_i])
        buf.append({"obs": o_pre, "act": a_i, "val": v, "rew": ro._scale(r, done),
                    "done": done, "win": win})
        if done:
            break
    n = len(buf)
    adv = np.zeros(n, np.float32)
    last = 0.0
    for t in reversed(range(n)):
        s = buf[t]
        nv = 0.0 if t == n - 1 else buf[t + 1]["val"]
        nonterm = 0.0 if s["done"] else 1.0
        delta = s["rew"] + nv * nonterm - s["val"]
        last = delta + nonterm * last
        adv[t] = last
    return buf, adv, float(info["spend_total"]), n


def step_grad(st, coef):
    """单步 pg 梯度（含 coef·(−(A−c)/s)/L̄ 已乘进 coef），flat float64。"""
    lg, _v, _mk = forward_batch(model, [_one_step(st["obs"])], [st["win"]])
    lp = F.log_softmax(lg, dim=-1)[0, st["act"]]
    gr = torch.autograd.grad(coef * lp, [p for _, p in PARAMS], allow_unused=True)
    out = torch.zeros(NPAR, dtype=torch.float64)
    for (lo, hi), x in zip(OFFS, gr):
        if x is not None:                       # value./exec_head. 不在 logp 图里 ⇒ None→0
            out[lo:hi] = x.detach().double().reshape(-1)
    return out


def step_ent_grad(st):
    lg, _v, mk = forward_batch(model, [_one_step(st["obs"])], [st["win"]])
    logp = F.log_softmax(lg, dim=-1)
    p = logp.exp()
    ent = -(p * logp).masked_fill(~mk.bool(), 0.0).sum(-1)[0]
    gr = torch.autograd.grad(ent, [x for _, x in PARAMS], allow_unused=True)
    out = torch.zeros(NPAR, dtype=torch.float64)
    for (lo, hi), x in zip(OFFS, gr):
        if x is not None:
            out[lo:hi] = x.detach().double().reshape(-1)
    return out


if A.trialgate:
    buf, adv, spend, n = rollout_game(seeds[0], 0)
    print(f"  试跑0 seed {seeds[0]}: {n} 步  消费 {spend!r}  （{time.time()-t0:.0f}s）")
    print("门槛（归档 v10_ckpt5 那次）：1959 步 / 11768.292333333335")
    sys.exit(0)

tbufs = []
for i in (0, 1):
    buf, adv, spend, n = rollout_game(seeds[i], i)
    tbufs.append((buf, adv))
    print(f"  试跑{i} seed {seeds[i]}: {n} 步  消费 {spend!r}  （{time.time()-t0:.0f}s）", flush=True)
alladv = np.concatenate([a for _, a in tbufs])
c = float(alladv.mean()); s = float(alladv.std())
Lbar = float(np.mean([len(b) for b, _ in tbufs]))
print(f"  优势中心 c={c:.4f} 尺度 s={s:.4f}  平均局长 L̄={Lbar:.0f}  （{time.time()-t0:.0f}s）", flush=True)

g_ent = torch.zeros(NPAR, dtype=torch.float64)
ntrial = 0
for buf, _ in tbufs:
    for st in buf:
        g_ent += step_ent_grad(st)
        ntrial += 1
g_ent *= (ra.ent_coef / ntrial)

GS, spends, lens = [], [], []
SHARED = {"trunk": [], "score": [], "all": []}   # F4 起：奇偶列只存每组标量 2·⟨o,e⟩（等价、省内存）
for i in range(A.episodes):
    g = torch.zeros(NPAR, dtype=torch.float64)
    go = torch.zeros_like(g); ge = torch.zeros_like(g)
    buf, adv, spend, n = rollout_game(seeds[2 + i], 2 + i)
    for t, st in enumerate(buf):
        coef = -float((adv[t] - c) / s) / Lbar
        gg = step_grad(st, coef)
        g += gg
        if t % 2 == 0:
            go += gg
        else:
            ge += gg
    GS.append(g.numpy())
    for key, cidx in (("trunk", _T), ("score", _S), ("all", _A)):
        SHARED[key].append(2.0 * float(go[cidx].dot(ge[cidx])))
    spends.append(spend); lens.append(n)
    print(f"  局{i} seed {seeds[2+i]}: {n} 步  消费 {spend!r}  梯度 {time.time()-t0:.0f}s（累计）", flush=True)

G = np.stack(GS)
E = G.shape[0]
print(f"\n=== 结果（{E} 局）===")


def mu2_of(M):
    m = M.mean(axis=0)
    return float(m @ m - (M ** 2).sum(axis=1).mean() / E) if False else \
        sum(float(np.dot(M[i], M[j])) for i in range(E) for j in range(i + 1, E)) * 2 / (E * (E - 1))


def _key_of(cols):
    return "trunk" if len(cols) == len(IDX_TRUNK) and int(cols[0]) == int(IDX_TRUNK[0]) else (
        "score" if len(cols) == len(IDX_SCORE) else "all")


def report(label, cols):
    M = G[:, cols]
    mu2 = mu2_of(M)
    trs = (M ** 2).sum(axis=1).mean() - mu2
    jk_mu = []
    for i in range(E):
        M2 = np.delete(M, i, axis=0)
        e2 = E - 1
        jk_mu.append(sum(float(np.dot(M2[a], M2[b])) for a in range(e2) for b in range(a + 1, e2)) * 2 / (e2 * (e2 - 1)))
    jk_mu = np.array(jk_mu)
    semu = float(np.sqrt((E - 1) / E * ((jk_mu - jk_mu.mean()) ** 2).sum()))
    jk_tr = (np.delete(M, 0, axis=0) ** 2).sum(axis=1).mean()
    jk_trs = np.array([float((np.delete(M, i, axis=0) ** 2).sum(axis=1).mean() - jk_mu[i]) for i in range(E)])
    setr = float(np.sqrt((E - 1) / E * ((jk_trs - jk_trs.mean()) ** 2).sum()))
    print(f"\n[{label}]")
    print(f"  ‖μ‖² = {mu2:.4e} ± {semu:.2e}（jackknife SE，z={mu2/semu:+.2f}）")
    print(f"  trΣ（单局噪声）= {trs:.4e} ± {setr:.2e}   单局 SNR ‖μ‖²/trΣ = {mu2/trs:.4f}")
    if mu2 > 0:
        b = trs / mu2
        print(f"  ★B_ep = {b:.1f} ± {b*setr/trs:.1f} 局")
        cs = [float(np.sqrt(1.0 / (1.0 + b / N))) for N in (1, 4, 8, 16, 32, 64)]
        print(f"  每块 N 局时 cos(ḡ_N, μ)：N=1: {cs[0]:.2f}  N=4: {cs[1]:.2f}  N=8: {cs[2]:.2f}"
              f"  N=16: {cs[3]:.2f}  N=32: {cs[4]:.2f}  N=64: {cs[5]:.2f}")
    else:
        print("  ★‖μ‖² ≤ 0：12 局内分辨不出真梯度（B_ep 至少是 12 的量级以上）")
    shared = float(np.mean(SHARED[_key_of(cols)]))
    indep = trs - shared
    print(f"  噪声拆分：半局独立 {indep:.3e}（{indep/trs:.0%}）  局内共享（结局）{shared:.3e}（{shared/trs:.0%}）")
    gen = g_ent.numpy()[cols]
    mubar = G.mean(axis=0)[cols]
    ng = float(np.linalg.norm(gen)); nm = float(np.linalg.norm(mubar))
    cosv = float(gen @ mubar / (ng * nm)) if ng > 0 and nm > 0 else float("nan")
    print(f"  ‖ent_coef·∇ent‖ = {ng:.3e}   ‖μ‖ = {nm:.3e}   比值 = {ng/nm if nm else float('nan'):.2f}"
          f"   cos(∇ent 项, ḡ_{E}) = {cosv:+.3f}")


report("主干", IDX_TRUNK)
report("score", IDX_SCORE)
report("全部", IDX_ALL)
print(f"\n局消费：均值 {np.mean(spends):,.0f}  最低 {min(spends):,.0f}  最高 {max(spends):,.0f}   "
      f"局长均值 {np.mean(lens):.0f}")
print(f"（总耗时 {time.time()-t0:.0f}s）")
