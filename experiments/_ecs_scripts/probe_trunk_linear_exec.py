# -*- coding: utf-8 -*-
"""辅助损失有没有塑造主干？—— 在**同一批状态**上给多个主干各现拟合一个线性可执行性探针。

背景：分层 v2 显示 ckpt_5 的 exec 头「同候选跨状态」AUC ~0.62、策略 ~0.50。头与 `score`
吃同一份 `[h, q0]`，头只是一层线性读出 ⇒ 局面级可执行性**线性地编码在主干里**。
但这不能说明是辅助损失教出来的：BC 主干（从没训过辅助损失）可能本来就有，
甚至**随机初始化**的主干（随机投影保留输入信息）也可能读得出来。

做法：
  · 驱动策略（默认 ppo_v10/ckpt_5，采样不加权）在评估图 900000+e 上跑，每 EVERY 步取一个状态
  · 每个状态暴力 deepcopy 枚举全部候选的真实可执行性（与 probe_exec_head_strat 同法）
  · 对每个被测主干（含 RANDOM = 同架构随机初始化），在**同一批状态**上前向，
    用 forward hook 截 `exec_head` 的输入 = `[h, q0]`（不重写前向）
  · 按局交叉拟合 L2 逻辑回归（训练用其他局、预测留出局）⇒ 线性探针分数
  · AUC 列：状态内同种类 / **同候选跨状态（仅同局内配对）** —— 交叉拟合的分数跨局有偏移，
    只在同一局内比才干净（先验列踩过的坑）

判据（事先写死）：
  · RANDOM 的探针 = 「随机特征就能读出多少」的基线
  · BC 探针 − RANDOM ≤ +0.03 ⇒ BC 主干没有超出随机特征的可执行性信息
  · ckpt_5 探针 − BC 探针 ≥ +0.05 ⇒ 训练（辅助损失 + 5 块 PPO，二者此处分不开）把主干推向了可执行性
  · 某 ckpt 自带头 ≈ 其新拟合探针（±0.03）⇒ 头已接近最优线性读出

用法：python experiments/probe_trunk_linear_exec.py [局=4] [回合=200] [每几步=97] [每局最多状态=20] [驱动ckpt] [被测ckpt…|RANDOM]

★可选：环境变量 `TRUNK_DUMP_NPZ=<前缀>` ⇒ 给**每个被测主干**各存一份逐行分数
`<前缀>__<主干名>.npz`，字段与 `bootstrap_strat_auc.py` 对齐
（`pexec` ← 交叉拟合的线性探针分、`logit` ← 策略 logits、`head` ← 自带头原始分），
于是「线性探针 − 策略 logits」（= 那道缝）可以直接 bootstrap 出 95% CI。
**不设这个变量时行为与输出逐字不变**（本文件其余部分未改动）。
⚠ 用它跑出来的 npz 做 bootstrap 时**必须传第 4 个参数 1**（分组键含局号）——
线性探针是按局交叉拟合的，跨局有偏移，只有同局内配对才与上面打印的那列同口径。
"""
import collections
import copy
import os
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

from rl.env import KINDS, ZhanguoEnv
from rl.ppo import _one_step, forward_batch
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

EPS = int(sys.argv[1]) if len(sys.argv) > 1 else 4
TURNS = int(sys.argv[2]) if len(sys.argv) > 2 else 200
EVERY = int(sys.argv[3]) if len(sys.argv) > 3 else 97
MAXS = int(sys.argv[4]) if len(sys.argv) > 4 else 20
DRIVER = sys.argv[5] if len(sys.argv) > 5 else "rl/runs/ppo_v10/ckpt_5.pt"
TARGETS = sys.argv[6:] or ["RANDOM", "rl/runs/bc_cont/ep100.pt",
                           "rl/runs/ppo_v10/ckpt_5.pt", "rl/runs/ppo_v10/ckpt_35.pt"]
SPLIT_TURN = 70
L2 = 1e-3

t0 = time.time()
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())


def make(path):
    if path == "RANDOM":
        torch.manual_seed(12345)
    m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
    m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
    trained = False
    if path != "RANDOM":
        ck = torch.load(path, map_location="cpu", weights_only=False)
        miss, _ = m.load_state_dict(ck["model"], strict=False)
        trained = not any(k.startswith("exec_head.") for k in miss)
    m.eval()
    return m, trained


cap = {}


def hook_for(name):
    def hook(_mod, inp, _out):
        cap[name] = inp[0].detach()
    return hook


models = {}
for p in TARGETS:
    m, trained = make(p)
    m.exec_head.register_forward_hook(hook_for(p))
    models[p] = (m, trained)
driver, _ = make(DRIVER)
print(f"驱动 {DRIVER}；被测 {TARGETS}")
print(f"{EPS} 局 × {TURNS} 回合，每 {EVERY} 步取状态、每局最多 {MAXS} 个；图 900000+\n", flush=True)

meta = []                                   # (状态, 局, 游戏回合, kind, cell, 真值)
feats = {p: [] for p in TARGETS}
heads = {p: [] for p in TARGETS}
logits = {p: [] for p in TARGETS}
sid = 0
for e in range(EPS):
    torch.manual_seed(3000 + e)
    obs = env.reset(900000 + e)
    step = taken = 0
    while True:
        win = tokenize(env, obs)
        with torch.no_grad():
            lg_d, _v, _m = forward_batch(driver, [_one_step(obs)], [win])
        if step % EVERY == EVERY // 2 and taken < MAXS:
            cands = obs.cand["actions"]
            truths = []
            for a in cands:
                e2 = copy.deepcopy(env)
                try:
                    truths.append(bool(e2.step(a)[3]["ok"]))
                except Exception:  # noqa: BLE001
                    truths.append(False)
            for p, (m, _tr) in models.items():
                with torch.no_grad():
                    lg, _v, _mk, pe = forward_batch(m, [_one_step(obs)], [win], return_exec=True)
                feats[p].append(cap[p][0].numpy().astype(np.float32))
                heads[p].append(pe[0].numpy())
                logits[p].append(lg[0].numpy())
            for a, ok in zip(cands, truths):
                meta.append((sid, e, env.world.turn, a.kind, f"{a.kind}|{a.sub}|{a.amount}", ok))
            print(f"  局{e} 步{step} 回合{env.world.turn}：候选 {len(cands)}，可执行 {np.mean(truths):.1%}"
                  f"  （{time.time() - t0:.0f}s）", flush=True)
            sid += 1
            taken += 1
        i = int(torch.multinomial(torch.softmax(lg_d, -1)[0], 1).item())
        obs, _r, done, _info = env.step(obs.cand["actions"][i])
        step += 1
        if done:
            break

st = np.array([r[0] for r in meta])
ep = np.array([r[1] for r in meta])
turn = np.array([r[2] for r in meta])
kind = np.array([r[3] for r in meta])
cell = np.array([r[4] for r in meta])
y = np.array([r[5] for r in meta], bool)
N = len(y)
print(f"\n共 {N} 个候选 / {sid} 个状态，基准可执行率 {y.mean():.1%}  （{time.time() - t0:.0f}s）", flush=True)


def fit_predict(X):
    """按局交叉拟合的 L2 逻辑回归，返回留出局上的 logit。"""
    out = np.empty(N)
    for e in np.unique(ep):
        tr = ep != e
        if tr.sum() == 0 or len(np.unique(y[tr])) < 2:
            out[~tr] = 0.0
            continue
        Xt = torch.tensor(X[tr])
        mu, sd = Xt.mean(0), Xt.std(0) + 1e-6
        Xn = (Xt - mu) / sd
        yt = torch.tensor(y[tr], dtype=torch.float32)
        w = torch.zeros(X.shape[1], requires_grad=True)
        b = torch.zeros(1, requires_grad=True)
        opt = torch.optim.LBFGS([w, b], max_iter=300, line_search_fn="strong_wolfe")

        def closure():
            opt.zero_grad()
            loss = F.binary_cross_entropy_with_logits(Xn @ w + b, yt) + L2 * (w * w).sum()
            loss.backward()
            return loss

        opt.step(closure)
        with torch.no_grad():
            out[~tr] = (((torch.tensor(X[~tr]) - mu) / sd) @ w + b).numpy()
    return out


def auc(scores, labels):
    npos, nneg = int(labels.sum()), int((~labels).sum())
    if npos == 0 or nneg == 0:
        return float("nan"), 0
    order = np.argsort(scores, kind="mergesort")
    ss = scores[order]
    r = np.empty(len(ss))
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        r[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    ranks = np.empty(len(ss))
    ranks[order] = r
    return (ranks[labels].sum() - npos * (npos + 1) / 2) / (npos * nneg), npos * nneg


def group_auc(scores, mask, keys):
    groups = collections.defaultdict(list)
    for j in np.nonzero(mask)[0]:
        groups[keys[j]].append(j)
    num = den = 0.0
    for js in groups.values():
        js = np.asarray(js)
        a, p = auc(scores[js], y[js])
        if p:
            num += a * p
            den += p
    return num / den if den else float("nan")


key_state_kind = np.array([f"{s}|{k}" for s, k in zip(st, kind)])
key_cell_ep = np.array([f"{c}|{e}" for c, e in zip(cell, ep)])

scores = {}
for p in TARGETS:
    X = np.concatenate(feats[p], axis=0)
    assert X.shape[0] == N, (X.shape, N)
    scores[(p, "线性探针")] = fit_predict(X)
    if models[p][1]:
        scores[(p, "自带头")] = np.concatenate(heads[p])
    scores[(p, "策略 logits")] = np.concatenate(logits[p])
    print(f"  拟合完 {p}  （{time.time() - t0:.0f}s）", flush=True)

for tag, mask in (("全部", np.ones(N, bool)), (f"回合 < {SPLIT_TURN}", turn < SPLIT_TURN),
                  (f"回合 ≥ {SPLIT_TURN}", turn >= SPLIT_TURN)):
    if not mask.any():
        continue
    print(f"\n=== {tag}：{int(mask.sum())} 个候选 / {len(set(st[mask]))} 个状态 ===")
    print(f"  {'主干':<30}{'分数':<12}{'状态内同种类':>14}{'同候选跨状态(同局)':>20}")
    for (p, kindname), sc in scores.items():
        print(f"  {p:<30}{kindname:<12}{group_auc(sc, mask, key_state_kind):>14.3f}"
              f"{group_auc(sc, mask, key_cell_ep):>20.3f}")

print("\n判据：BC 探针−RANDOM ≤+0.03 ⇒ BC 主干无超出随机特征的信息；ckpt_5 探针−BC 探针 ≥+0.05 ⇒ 训练把主干推向可执行性；"
      "自带头 ≈ 新拟合探针(±0.03) ⇒ 头已接近最优线性读出")

DUMP = os.environ.get("TRUNK_DUMP_NPZ", "")
if DUMP:
    # 逐行分数落盘（可选路径）。cell = f"{kind}|{sub}|{amount}"，sub 里不含 '|'（已核 npz 取值）
    sub = np.array([c.split("|")[1] for c in cell])
    amt = np.array([int(c.split("|")[2]) for c in cell])
    assert len(sub) == N == len(amt), (len(sub), N, len(amt))
    for p in TARGETS:
        tag = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in p).strip("_") or "target"
        out = f"{DUMP}__{tag}.npz"
        np.savez(out, state=st, ep=ep, turn=turn, kind=kind, sub=sub, amount=amt,
                 pexec=scores[(p, "线性探针")], logit=scores[(p, "策略 logits")],
                 head=np.concatenate(heads[p]), y=y, trained=np.array([models[p][1]]))
        print(f"逐行分数已存：{out}"
              f"（pexec=线性探针分、logit=策略 logits、head=自带头；trained={models[p][1]}）")
    print("★bootstrap 这份 npz 必须传第 4 个参数 1（分组键含局号）："
          "python experiments/bootstrap_strat_auc.py <npz> 400 0 1")

print(f"（总耗时 {time.time() - t0:.0f}s）")