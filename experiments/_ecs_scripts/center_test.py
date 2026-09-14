# -*- coding: utf-8 -*-
"""「头的跨状态列不稳」是不是**每状态偏移（标定）**造成的？—— 纯 npz 可验，不跑环境。

做法：把每个分数减去**它所在状态的均值**（去掉状态级偏移），再算同一列 AUC。
  · 若去偏移后两台机器**对上** ⇒ 差异全部来自"头的绝对水平随状态漂移"，不是排序能力
  · 若去偏移后仍**差很多** ⇒ 头的排序本身就依赖抽到哪批状态
顺带报每个分数在状态间的偏移分布（状态均值的 std / 极差），这是"漂移"的直接量。
"""
import collections
import sys

import numpy as np


def auc(s, lab):
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


def wgroup(scores, keys, y):
    g = collections.defaultdict(list)
    for j in range(len(y)):
        g[keys[j]].append(j)
    num = den = 0.0
    for js in g.values():
        js = np.asarray(js)
        a, p = auc(scores[js], y[js])
        if p:
            num += a * p
            den += p
    return num / den if den else float("nan")


def center(scores, st):
    out = np.array(scores, float)
    for s in np.unique(st):
        m = st == s
        out[m] -= out[m].mean()
    return out


for path in sys.argv[1:]:
    d = np.load(path, allow_pickle=False)
    y = d["y"].astype(bool)
    st = d["state"]
    cell = [f"{k}|{s}|{a}" for k, s, a in zip(d["kind"], d["sub"], d["amount"])]
    print(f"\n##### {path}")
    for name, sc in (("头 pexec", d["pexec"]), ("策略 logit", d["logit"])):
        sm = np.array([sc[st == s].mean() for s in np.unique(st)])
        sd = np.array([sc[st == s].std() for s in np.unique(st)])
        raw = wgroup(sc, cell, y)
        cen = wgroup(center(sc, st), cell, y)
        print(f"  {name:<10} 跨状态(跨局) 原始 {raw:.3f}   去状态均值后 {cen:.3f}   差 {cen - raw:+.3f}")
        print(f"  {'':<10} 状态均值 std {sm.std():.3f}  极差 {sm.max() - sm.min():.3f}   "
              f"状态内 std 中位 {np.median(sd):.3f}   ⇒ 偏移/散布比 {sm.std() / max(np.median(sd), 1e-9):.2f}")
