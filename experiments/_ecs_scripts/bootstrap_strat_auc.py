# -*- coding: utf-8 -*-
"""给 `probe_exec_head_strat.py` 落盘的 npz 做 bootstrap 置信区间（纯 numpy，不跑环境）。

统计量：「同候选跨状态」AUC（(kind, sub, amount) 相同的候选跨状态排序，按正负对数加权），
头 p_exec、策略 logits，以及二者之差（头 − 策略，同一次重采样内配对）。

两种重采样都报，互相校验：
  · 按状态：粒度细，但同局状态相关 ⇒ 区间偏窄
  · 按局：局是独立单位，但局数少 ⇒ 区间偏宽、偏粗
判据（事先写死）：头 − 策略 的 95% 区间下界 > 0（两种重采样都成立）⇒「头的局面级排序优于策略」成立

用法：python experiments/bootstrap_strat_auc.py <npz> [重采样次数=400] [种子=0] [分组含局号0/1]

第 4 个参数（**默认 0 = 原行为，输出逐字不变**）：`1` 把分组键从 `kind|sub|amount`
换成 `kind|sub|amount|ep`，即**只在同一局内配对**。为什么需要它：
`probe_trunk_linear_exec.py` 的线性探针是**按局交叉拟合**的，留出局之间的分数有偏移，
跨局配对会把这份偏移当成信号 —— 那个探针打印「同候选跨状态」时用的键就是 `cell|ep`，
这里加同一个开关才能与它同口径。`probe_exec_head_strat.py` 的 npz（头分数，非交叉拟合）
**不要用这个开关**，仍走默认。
"""
import collections
import sys

import numpy as np

PATH = sys.argv[1]
B = int(sys.argv[2]) if len(sys.argv) > 2 else 400
SEED = int(sys.argv[3]) if len(sys.argv) > 3 else 0
WITHIN_EP = bool(int(sys.argv[4])) if len(sys.argv) > 4 else False
SPLIT_TURN = 70

d = np.load(PATH, allow_pickle=False)
state, ep, turn, y = d["state"], d["ep"], d["turn"], d["y"].astype(bool)
pexec, logit = d["pexec"], d["logit"]
cell_keys = np.array([f"{k}|{s}|{a}" for k, s, a in zip(d["kind"], d["sub"], d["amount"])])
if WITHIN_EP:
    cell_keys = np.array([f"{c}|{e}" for c, e in zip(cell_keys, ep)])
_, cell = np.unique(cell_keys, return_inverse=True)
HEAD = bool(np.isfinite(pexec).all())


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


def cell_auc(idx):
    """idx：行下标（可重复）。返回 (头, 策略)。"""
    groups = collections.defaultdict(list)
    for j in idx:
        groups[cell[j]].append(j)
    out = []
    for sc in ((pexec if HEAD else None), logit):
        if sc is None:
            out.append(float("nan"))
            continue
        num = den = 0.0
        for js in groups.values():
            js = np.asarray(js)
            a, p = auc(sc[js], y[js])
            if p:
                num += a * p
                den += p
        out.append(num / den if den else float("nan"))
    return out


def ci(vals):
    v = np.asarray([x for x in vals if np.isfinite(x)])
    if len(v) < 10:
        return float("nan"), float("nan"), len(v)
    return float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)), len(v)


def run(tag, mask):
    rows = np.nonzero(mask)[0]
    if len(rows) == 0:
        return
    rng = np.random.default_rng(SEED)
    by_state = collections.defaultdict(list)
    by_ep = collections.defaultdict(list)
    for j in rows:
        by_state[state[j]].append(j)
        by_ep[ep[j]].append(j)
    states = list(by_state)
    eps = list(by_ep)
    h0, p0 = cell_auc(rows)
    print(f"\n=== {tag}：{len(rows)} 个候选 / {len(states)} 个状态 / {len(eps)} 局 ===")
    print(f"  点估计：头 {h0:.3f}  策略 {p0:.3f}  差 {h0 - p0:+.3f}")
    for unit, groups, keys in (("按状态", by_state, states), ("按局", by_ep, eps)):
        hs, ps, ds = [], [], []
        for _ in range(B):
            pick = rng.choice(len(keys), size=len(keys), replace=True)
            idx = np.concatenate([groups[keys[k]] for k in pick])
            h, p = cell_auc(idx)
            hs.append(h)
            ps.append(p)
            ds.append(h - p)
        (hl, hu, _), (pl, pu, _), (dl, du, n) = ci(hs), ci(ps), ci(ds)
        verdict = "下界>0 ✓" if np.isfinite(dl) and dl > 0 else "下界≤0 ✗"
        print(f"  {unit:<4} 95% CI（{n}/{B} 次有效）：头 [{hl:.3f}, {hu:.3f}]  策略 [{pl:.3f}, {pu:.3f}]"
              f"  差 [{dl:+.3f}, {du:+.3f}]  {verdict}")
    if len(eps) < 8:
        print(f"  ⚠ 只有 {len(eps)} 局：按局的区间很粗（重采样取值组合有限）")


print(f"{PATH}  重采样 {B} 次  头{'已训' if HEAD else '缺失（只报策略）'}"
      + ("  分组键含局号（同局内配对）" if WITHIN_EP else ""))
run("全部", np.ones(len(y), bool))
run(f"回合 < {SPLIT_TURN}", turn < SPLIT_TURN)
run(f"回合 ≥ {SPLIT_TURN}", turn >= SPLIT_TURN)
print("\n判据：头−策略 的 95% 区间下界 > 0（按状态与按局都成立）⇒ 头的局面级排序优于策略")