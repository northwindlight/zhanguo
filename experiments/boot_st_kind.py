# -*- coding: utf-8 -*-
"""「状态内同种类」这一列的 bootstrap CI（偏移免疫、决策相关的那一列）。

为什么单独写：仓库里的 `bootstrap_strat_auc.py` 只 bootstrap「同候选跨状态」那一列，
而现在最该看的列是「状态内同种类」（探针 vs 策略）。这是**额外分析**，不改任何量具。

重采样单位：**局**（cluster bootstrap；局是独立单位）。同时报「按状态」作为对照（偏窄）。
统计量：按 (状态, 种类) 分组算 AUC，按正负对数加权平均 —— 与两把尺子打印的那一列同定义。

用法：
  python /tmp/boot_st_kind.py --check <npz>            # 快实现 vs 慢实现自校验（必须逐位相同）
  python /tmp/boot_st_kind.py <npz>... [B=400] [seed=0]
"""
import collections
import sys

import numpy as np


# ---------------------------------------------------------------- 慢（参照实现，纯 Python 循环）
def auc_slow(s, lab):
    s = np.asarray(s, float)
    lab = np.asarray(lab, bool)
    npos, nneg = int(lab.sum()), int((~lab).sum())
    if npos == 0 or nneg == 0:
        return float("nan"), 0
    o = np.argsort(s, kind="mergesort")
    ss = s[o]
    r = np.empty(len(ss))
    i = 0
    while i < len(ss):
        j = i
        while j + 1 < len(ss) and ss[j + 1] == ss[i]:
            j += 1
        r[i:j + 1] = (i + j) / 2 + 1
        i = j + 1
    rk = np.empty(len(ss))
    rk[o] = r
    return (rk[lab].sum() - npos * (npos + 1) / 2) / (npos * nneg), npos * nneg


def wgroup_slow(scores, gid, y):
    g = collections.defaultdict(list)
    for j in range(len(y)):
        g[gid[j]].append(j)
    num = den = 0.0
    for js in g.values():
        js = np.asarray(js)
        a, p = auc_slow(scores[js], y[js])
        if p:
            num += a * p
            den += p
    return num / den if den else float("nan")


# ---------------------------------------------------------------- 快（向量化，用于 bootstrap）
def wgroup_fast(scores, gid, y):
    idx = np.lexsort((scores, gid))
    g = gid[idx]
    s = scores[idx]
    lab = y[idx]
    n = len(g)
    if n == 0:
        return float("nan")
    bnd = np.nonzero(np.diff(g))[0] + 1
    starts = np.concatenate(([0], bnd))
    ends = np.concatenate((bnd, [n]))
    seglen = ends - starts
    seg_of = np.repeat(np.arange(len(starts)), seglen)
    r = np.arange(n) - np.repeat(starts, seglen) + 1.0
    same = np.concatenate(([False], (np.diff(s) == 0) & (np.diff(g) == 0)))
    run = np.cumsum(~same)
    cnt = np.bincount(run)
    sums = np.bincount(run, weights=r)
    r = sums[run] / cnt[run]
    ypos = lab.astype(np.int64)
    npos = np.add.reduceat(ypos, starts)
    rsum = np.add.reduceat(np.where(lab, r, 0.0), starts)
    pairs = npos * (seglen - npos)
    ok = pairs > 0
    if not ok.any():
        return float("nan")
    aucs = (rsum[ok] - npos[ok] * (npos[ok] + 1) / 2.0) / pairs[ok]
    return float((aucs * pairs[ok]).sum() / pairs[ok].sum())


def groups_of(keys):
    _, gid = np.unique(keys, return_inverse=True)
    return gid


def boot(path, B, seed):
    d = np.load(path, allow_pickle=False)
    y = d["y"].astype(bool)
    st, ep, turn = d["state"], d["ep"], d["turn"]
    gid = groups_of(np.array([f"{s}|{k}" for s, k in zip(st, d["kind"])]))
    scores = [("探针", d["pexec"].astype(float)), ("策略", d["logit"].astype(float))]
    if "head" in d.files and bool(d["trained"][0]):
        scores.append(("自带头", d["head"].astype(float)))
    print(f"\n##### {path}\n  {len(y)} 行 / {len(np.unique(st))} 状态 / {len(np.unique(ep))} 局 / 基准 {y.mean():.3f}")
    rng = np.random.default_rng(seed)
    eps = np.unique(ep)
    ep_rows = {e: np.nonzero(ep == e)[0] for e in eps}
    sts = np.unique(st)
    st_rows = {s: np.nonzero(st == s)[0] for s in sts}
    for tag, mask in (("全部", np.ones(len(y), bool)), ("回合<70", turn < 70), ("回合≥70", turn >= 70)):
        if mask.sum() < 500:
            continue
        pt = {n: wgroup_fast(s[mask], gid[mask], y[mask]) for n, s in scores}
        line = f"  [{tag}] 点估计 " + "  ".join(f"{n} {v:.3f}" for n, v in pt.items())
        if len(pt) >= 2:
            line += f"   缝(探针−策略) {pt['探针'] - pt['策略']:+.3f}"
        print(line)
        for unit, keys, rowmap in (("按局", eps, ep_rows), ("按状态", sts, st_rows)):
            draws = {n: [] for n, _ in scores}
            for _ in range(B):
                pick = rng.choice(len(keys), size=len(keys), replace=True)
                sel = np.concatenate([rowmap[keys[k]] for k in pick])
                sel = sel[mask[sel]]
                if len(sel) < 100:
                    continue
                gg, yy = gid[sel], y[sel]
                for n, s in scores:
                    draws[n].append(wgroup_fast(s[sel], gg, yy))
            out = []
            for n, _ in scores:
                v = np.asarray(draws[n], float)
                v = v[np.isfinite(v)]
                out.append(f"{n} [{np.percentile(v, 2.5):.3f}, {np.percentile(v, 97.5):.3f}]")
            if len(scores) >= 2 and draws["探针"] and draws["策略"]:
                dv = np.asarray(draws["探针"]) - np.asarray(draws["策略"])
                dv = dv[np.isfinite(dv)]
                lo, hi = np.percentile(dv, 2.5), np.percentile(dv, 97.5)
                out.append(f"缝 [{lo:+.3f}, {hi:+.3f}] {'下界>0 ✓' if lo > 0 else '下界≤0 ✗'}")
            print(f"     {unit:<4}95% CI（{B} 次）：" + "  ".join(out))


def check(path):
    d = np.load(path, allow_pickle=False)
    y = d["y"].astype(bool)
    gid = groups_of(np.array([f"{s}|{k}" for s, k in zip(d["state"], d["kind"])]))
    for name, key in (("探针/pexec", "pexec"), ("策略/logit", "logit"), ("头/head", "head")):
        if key not in d.files:
            continue
        sc = d[key].astype(float)
        a, b = wgroup_slow(sc, gid.tolist(), y), wgroup_fast(sc, gid, y)
        print(f"  {name:<12} 慢 {a:.10f}   快 {b:.10f}   差 {abs(a - b):.3e}  {'✓' if abs(a - b) < 1e-12 else '★✗'}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--check":
        for p in args[1:]:
            print(f"##### 自校验 {p}")
            check(p)
    else:
        paths = [a for a in args if not a.isdigit()]
        nums = [a for a in args if a.isdigit()]
        B = int(nums[0]) if nums else 400
        seed = int(nums[1]) if len(nums) > 1 else 0
        for p in paths:
            boot(p, B, seed)
