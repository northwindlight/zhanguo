# -*- coding: utf-8 -*-
"""同一个 ckpt_5、同一份脚本（md5 f18c5b8c）、同一组参数（4 200 97 20），两台机器产出的 npz 逐列对拍。
分组口径全部自己重算，两种「同候选跨状态」都报（跨局 cell / 同局 cell|ep），免得跨探针口径串味。"""
import collections
import sys

import numpy as np


def auc(scores, labels):
    s = np.asarray(scores, float)
    lab = np.asarray(labels, bool)
    npos, nneg = int(lab.sum()), int((~lab).sum())
    if npos == 0 or nneg == 0:
        return float("nan"), 0
    order = np.argsort(s, kind="mergesort")
    ss = s[order]
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
    return (ranks[lab].sum() - npos * (npos + 1) / 2) / (npos * nneg), npos * nneg


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


for path in sys.argv[1:]:
    d = np.load(path, allow_pickle=False)
    y = d["y"].astype(bool)
    st, ep, kind = d["state"], d["ep"], d["kind"]
    cell = [f"{k}|{s}|{a}" for k, s, a in zip(kind, d["sub"], d["amount"])]
    cell_ep = [f"{c}|{e}" for c, e in zip(cell, ep)]
    st_kind = [f"{s}|{k}" for s, k in zip(st, kind)]
    print(f"\n##### {path}")
    print(f"  行数 {len(y)}  状态 {len(set(st.tolist()))}  局 {sorted(set(ep.tolist()))}  "
          f"基准可执行率 {y.mean():.3f}")
    print(f"  每局行数 {np.bincount(ep.astype(int)).tolist()}")
    print(f"  {'分数':<12}{'全局':>8}{'状态内':>9}{'状态内同种类':>14}{'跨状态(跨局)':>14}{'跨状态(同局)':>14}")
    for name, sc in (("exec_head", d["pexec"]), ("策略 logits", d["logit"]), ("先验", d["prior"])):
        if not np.isfinite(sc).all() and name == "exec_head":
            print(f"  {name:<12}  缺失/随机")
            continue
        print(f"  {name:<12}{auc(sc, y)[0]:>8.3f}{wgroup(sc, st.tolist(), y):>9.3f}"
              f"{wgroup(sc, st_kind, y):>14.3f}{wgroup(sc, cell, y):>14.3f}{wgroup(sc, cell_ep, y):>14.3f}")
    if np.isfinite(d["pexec"]).all():
        for tag, keyf in (("跨局", cell), ("同局", cell_ep)):
            h = wgroup(d["pexec"], keyf, y)
            p = wgroup(d["logit"], keyf, y)
            print(f"  头−策略（同候选跨状态，{tag}分组）= {h:.3f} − {p:.3f} = {h - p:+.3f}")
