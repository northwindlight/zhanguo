# -*- coding: utf-8 -*-
"""「缝」到底是不是**每状态偏移**造成的？—— 对 trunk_linear 落盘的逐行分数做同一套诊断。

对每个被测主干的 npz（pexec=交叉拟合线性探针分、logit=策略 logits、head=自带头原始分）报：
  · 同候选跨状态 AUC：两种分组（cell|ep 同局 = trunk_linear 打印的那列；cell 跨局 = strat 的那列）
  · 每列都报「原始」与「去掉所在状态均值」两版
  · 状态偏移/状态内散布 之比（头 pexec 在 ckpt_5 上实测 ≈1.0，策略 ≈0.25 —— 这个比值大就说明该列被偏移主导）
判据见 rlmail2/to_pi.md（缝_去偏移 ≥ +0.10 ⇒ 缝不是偏移假象；< +0.05 而原始 ≥ +0.15 ⇒ 判偏移假象）。
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


def wgroup(scores, keys, y, mask=None):
    g = collections.defaultdict(list)
    idx = range(len(y)) if mask is None else np.nonzero(mask)[0]
    for j in idx:
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
    st, ep, turn = d["state"], d["ep"], d["turn"]
    cell = [f"{k}|{s}|{a}" for k, s, a in zip(d["kind"], d["sub"], d["amount"])]
    cell_ep = [f"{c}|{e}" for c, e in zip(cell, ep)]
    st_kind = [f"{s}|{k}" for s, k in zip(st, d["kind"])]
    trained = bool(d["trained"][0]) if "trained" in d.files else None
    print(f"\n##### {path}")
    print(f"  行数 {len(y)}  状态 {len(np.unique(st))}  局 {len(np.unique(ep))}  基准可执行率 {y.mean():.3f}"
          f"  自带头{'已训' if trained else '随机（head 列无意义）'}")
    res = {}
    for name, key in (("线性探针", "pexec"), ("策略 logits", "logit"), ("自带头", "head")):
        if key not in d.files:
            continue
        sc = d[key].astype(float)
        if not np.isfinite(sc).all():
            print(f"  {name:<10} 有非有限值，跳过")
            continue
        sm = np.array([sc[st == s].mean() for s in np.unique(st)])
        sd = np.array([sc[st == s].std() for s in np.unique(st)])
        cen = center(sc, st)
        row = {}
        for lbl, keys in (("同局", cell_ep), ("跨局", cell)):
            row[f"raw_{lbl}"] = wgroup(sc, keys, y)
            row[f"cen_{lbl}"] = wgroup(cen, keys, y)
        row["st_kind"] = wgroup(sc, st_kind, y)
        row["offset_ratio"] = sm.std() / max(float(np.median(sd)), 1e-9)
        row["globe"] = auc(sc, y)[0]
        res[name] = row
        print(f"  {name:<10} 全局 {row['globe']:.3f} | 状态内同种类 {row['st_kind']:.3f} | "
              f"跨状态(同局) 原 {row['raw_同局']:.3f} 去偏移 {row['cen_同局']:.3f} | "
              f"跨状态(跨局) 原 {row['raw_跨局']:.3f} 去偏移 {row['cen_跨局']:.3f} | "
              f"偏移/散布 {row['offset_ratio']:.2f}")
    if "线性探针" in res and "策略 logits" in res:
        for lbl in ("同局", "跨局"):
            for pre in ("raw", "cen"):
                g = res["线性探针"][f"{pre}_{lbl}"] - res["策略 logits"][f"{pre}_{lbl}"]
                print(f"  ★缝（探针−策略，{lbl}分组，{'原始' if pre == 'raw' else '去状态偏移'}）= {g:+.3f}")
        print(f"  ★缝（探针−RANDOM 基线请跨文件比：本文件探针 状态内同种类 {res['线性探针']['st_kind']:.3f}）")
    for tag, m in (("回合<70", turn < 70), ("回合≥70", turn >= 70)):
        if m.sum() < 200 or "线性探针" not in res:
            continue
        g = (wgroup(d["pexec"], cell_ep, y, m) - wgroup(d["logit"], cell_ep, y, m))
        gc = (wgroup(center(d["pexec"], st), cell_ep, y, m) - wgroup(center(d["logit"], st), cell_ep, y, m))
        print(f"  {tag}：缝(同局) 原 {g:+.3f}  去偏移 {gc:+.3f}   （{int(m.sum())} 行）")
