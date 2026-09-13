# -*- coding: utf-8 -*-
"""可执行性 AUC 的分层版：头/策略的排序能力是否超出「动作种类×数量」的构成先验？

`probe_exec_head.py`（量具，不动）的两个口径缺口：
  · 状态 = 一局开头**连续的若干步**（`turn` 数的是步），1~2 局、开局、高度相关
  · AUC 不分动作种类：点不动的候选集中在市场（卖超存量/买超现金）与买不起的 build，
    「头 0.709 / 策略 0.217」可能只是种类构成

本探针：
  · 评估图 900000+e，整局 TURNS 回合，每 EVERY 步取一个状态（每局最多 MAXS 个）
  · 每个状态暴力 deepcopy 枚举全部候选的真实可执行性（与原探针同法）
  · 基线 prior_cell = (kind, sub, amount) 在**其他局**里的成功率（按局交叉拟合，无泄漏）
  · 报：全局 AUC / 状态内 AUC / **状态内且同种类** AUC（按正负对数加权），并按回合 <70 / ≥70 拆开

判据（事先写死）：
  · 头的「状态内同种类」AUC ≤ 0.55，或不高于 prior_cell ⇒ 头只学到了构成，没有局面层面的可执行性
  · 策略的「状态内同种类」AUC 显著 < 0.5 ⇒「偏好点不动的候选」在种类内部也成立；≈0.5 ⇒ 原结论是构成效应

用法：python experiments/probe_exec_head_strat.py <ckpt> [局=4] [回合=200] [每几步=97] [每局最多状态=20]
"""
import collections
import copy
import sys
import time

import numpy as np
import torch

from rl.env import KINDS, ZhanguoEnv
from rl.ppo import _one_step, forward_batch, policy_logits
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

CKPT = sys.argv[1]
EPS = int(sys.argv[2]) if len(sys.argv) > 2 else 4
TURNS = int(sys.argv[3]) if len(sys.argv) > 3 else 200
EVERY = int(sys.argv[4]) if len(sys.argv) > 4 else 97
MAXS = int(sys.argv[5]) if len(sys.argv) > 5 else 20
SPLIT_TURN = 70

t0 = time.time()
env = ZhanguoEnv(map_size=16, max_turns=TURNS)
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS}, d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
ck = torch.load(CKPT, map_location="cpu", weights_only=False)
_miss, _ = m.load_state_dict(ck["model"], strict=False)
m.eval()
HEAD = not any(k.startswith("exec_head.") for k in _miss)
print(f"权重 {CKPT}（第 {ck.get('iter', '?')} 块）  exec_head {'已训' if HEAD else '随机 ⇒ 只报策略与先验'}")
print(f"{EPS} 局 × {TURNS} 回合，每 {EVERY} 步取状态、每局最多 {MAXS} 个；图 900000+\n", flush=True)

# 每候选一行：(状态id, 局, 游戏回合, kind, sub, amount, p_exec, logit, 真值)
rows = []
sid = 0
for e in range(EPS):
    torch.manual_seed(3000 + e)
    obs = env.reset(900000 + e)
    step = taken = 0
    while True:
        win = tokenize(env, obs)
        lg, _v, cm = policy_logits(m, obs, win=win, use_exec=False)
        if step % EVERY == EVERY // 2 and taken < MAXS:
            with torch.no_grad():
                pe = (forward_batch(m, [_one_step(obs)], [win], return_exec=True)[3]
                      if HEAD else None)
            cands = obs.cand["actions"]
            idxs = [i for i, v in enumerate(cm[0].tolist()) if v]
            n_ok = 0
            for i in idxs:
                e2 = copy.deepcopy(env)
                try:
                    ok = bool(e2.step(cands[i])[3]["ok"])
                except Exception:  # noqa: BLE001
                    ok = False
                n_ok += ok
                a = cands[i]
                rows.append((sid, e, env.world.turn, a.kind, a.sub, a.amount,
                             float(pe[0, i]) if HEAD else float("nan"), float(lg[0, i]), ok))
            print(f"  局{e} 步{step} 回合{env.world.turn}：候选 {len(idxs)}，可执行 {n_ok / len(idxs):.1%}"
                  f"  （{time.time() - t0:.0f}s）", flush=True)
            sid += 1
            taken += 1
        i = int(torch.multinomial(torch.softmax(lg, -1), 1).item())
        obs, _r, done, _info = env.step(obs.cand["actions"][i])
        step += 1
        if done:
            break

N = len(rows)
st = np.array([r[0] for r in rows])
ep = np.array([r[1] for r in rows])
turn = np.array([r[2] for r in rows])
kind = np.array([r[3] for r in rows])
cell = [(r[3], r[4], r[5]) for r in rows]
pexec = np.array([r[6] for r in rows])
logit = np.array([r[7] for r in rows])
y = np.array([r[8] for r in rows], bool)


def prior_scores():
    """(kind, sub, amount) 在其他局的成功率；该格其他局没出现 → 退到 kind，再退到全局。"""
    out = np.empty(N)
    for e in range(EPS):
        tr = ep != e
        cc, ck_ = collections.defaultdict(lambda: [0, 0]), collections.defaultdict(lambda: [0, 0])
        for j in np.nonzero(tr)[0]:
            cc[cell[j]][0] += y[j]
            cc[cell[j]][1] += 1
            ck_[kind[j]][0] += y[j]
            ck_[kind[j]][1] += 1
        g = y[tr].mean() if tr.any() else 0.5
        for j in np.nonzero(~tr)[0]:
            s, n = cc.get(cell[j], (0, 0))
            if n:
                out[j] = s / n
            else:
                s, n = ck_.get(kind[j], (0, 0))
                out[j] = s / n if n else g
    return out


prior = prior_scores()


def auc(scores, labels):
    """Mann-Whitney AUC（平均秩处理并列）。返回 (auc, 正负对数)。"""
    s = np.asarray(scores, float)
    lab = np.asarray(labels, bool)
    npos, nneg = int(lab.sum()), int((~lab).sum())
    if npos == 0 or nneg == 0:
        return float("nan"), 0
    order = np.argsort(s, kind="mergesort")
    ss = s[order]
    r = np.empty(len(s))
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and ss[j + 1] == ss[i]:
            j += 1
        r[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    ranks = np.empty(len(s))
    ranks[order] = r
    return (ranks[lab].sum() - npos * (npos + 1) / 2) / (npos * nneg), npos * nneg


def weighted_group_auc(scores, mask, keys):
    """按 keys 分组各算 AUC，按正负对数加权平均（没有正负两类的组跳过）。"""
    groups = collections.defaultdict(list)
    for j in np.nonzero(mask)[0]:
        groups[keys[j]].append(j)
    num = den = 0.0
    for js in groups.values():
        a, p = auc(scores[js], y[js])
        if p:
            num += a * p
            den += p
    return num / den if den else float("nan")


def report(tag, mask):
    if not mask.any():
        return
    print(f"\n=== {tag}：{int(mask.sum())} 个候选 / {len(set(st[mask]))} 个状态，基准可执行率 {y[mask].mean():.1%} ===")
    by_state = list(st)
    by_state_kind = [(st[j], kind[j]) for j in range(N)]
    print(f"  {'':<22}{'全局':>8}{'状态内':>10}{'状态内同种类':>14}")
    for name, sc in (("exec_head p_exec", pexec), ("策略 logits", logit), ("先验 kind×sub×amount", prior)):
        if name.startswith("exec") and not HEAD:
            continue
        g = auc(sc[mask], y[mask])[0]
        ws = weighted_group_auc(sc, mask, by_state)
        wk = weighted_group_auc(sc, mask, by_state_kind)
        print(f"  {name:<22}{g:>8.3f}{ws:>10.3f}{wk:>14.3f}")
    print("  各种类：候选占比 / 可执行率")
    for k in KINDS:
        km = mask & (kind == k)
        if km.any():
            print(f"    {k:<9}{km.sum() / mask.sum():>7.1%}{y[km].mean():>9.1%}")


ALL = np.ones(N, bool)
report("全部", ALL)
report(f"回合 < {SPLIT_TURN}", turn < SPLIT_TURN)
report(f"回合 ≥ {SPLIT_TURN}", turn >= SPLIT_TURN)
print("\n判据：头「状态内同种类」≤0.55 或 ≤ 先验 ⇒ 头只学到构成；策略「状态内同种类」显著 <0.5 ⇒ 偏好点不动的候选在种类内也成立")
print(f"（总耗时 {time.time() - t0:.0f}s）")
