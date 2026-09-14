# -*- coding: utf-8 -*-
"""D1 汇总：把 trunk_linear 落盘的 4 份 npz 拼成一张表（排序列 + 偏移诊断 + 缝）。
撞墙率那一列由 probe_validity 的日志单独提供（G），这里只留位置。
用法：python /tmp/gap_table.py <npz>... [--turn-split]
"""
import collections
import sys

import numpy as np

SPLIT = "--turn-split" in sys.argv
PATHS = [a for a in sys.argv[1:] if not a.startswith("--")]


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


def center(sc, st, uniq, idx_of):
    out = np.array(sc, float)
    pos = np.searchsorted(uniq, st)
    cnt = np.bincount(pos, minlength=len(uniq))
    tot = np.bincount(pos, weights=out, minlength=len(uniq))
    return out - (tot / cnt)[pos]


def analyze(path, mask=None, label=""):
    d = np.load(path, allow_pickle=False)
    y = d["y"].astype(bool)
    st, ep = d["state"], d["ep"]
    cell_ep = [f"{k}|{s}|{a}|{e}" for k, s, a, e in zip(d["kind"], d["sub"], d["amount"], ep)]
    st_kind = [f"{s}|{k}" for s, k in zip(st, d["kind"])]
    if mask is None:
        mask = np.ones(len(y), bool)
    uniq = np.unique(st)
    idx_of = {s: i for i, s in enumerate(uniq)}
    out = {}
    for name, key in (("probe", "pexec"), ("policy", "logit"), ("head", "head")):
        sc = d[key].astype(float)
        cen = center(sc, st, uniq, idx_of)
        sm = np.array([sc[st == s].mean() for s in uniq])
        sd = np.array([sc[st == s].std() for s in uniq])
        out[name] = dict(
            st_kind=wgroup(sc, st_kind, y, mask),
            gap_ep=wgroup(sc, cell_ep, y, mask),
            gap_ep_c=wgroup(cen, cell_ep, y, mask),
            globe=auc(sc[mask], y[mask])[0],
            offratio=sm.std() / max(float(np.median(sd)), 1e-9))
    out["_meta"] = dict(rows=int(mask.sum()), states=len(np.unique(st[mask])), eps=len(np.unique(ep[mask])),
                        base=float(y[mask].mean()), trained=bool(d["trained"][0]) if "trained" in d.files else None,
                        path=path, label=label)
    return out


NAMES = {"RANDOM": "随机主干", "rl_runs_bc_cont_ep100.pt": "BC ep100",
         "rl_runs_bc1_ckpt_30.pt": "bc1 ckpt_30（锚 0.5）", "rl_runs_entann_ckpt_30.pt": "entann ckpt_30（无锚）",
         "rl_runs_ppo_v10_ckpt_5.pt": "v10 ckpt_5", "rl_runs_ppo_v10_ckpt_35.pt": "v10 ckpt_35"}


def label_of(p):
    base = p.split("__")[-1].replace(".npz", "")
    return NAMES.get(base, base)


for tag, mk in ([("全部", None)] + ([(f"回合<70", lambda d: d["turn"] < 70), (f"回合≥70", lambda d: d["turn"] >= 70)] if SPLIT else [])):
    rows = []
    for p in PATHS:
        d = np.load(p, allow_pickle=False)
        mask = np.ones(len(d["y"]), bool) if mk is None else mk(d)
        rows.append(analyze(p, mask, label_of(p)))
    if tag == "全部":
        m = rows[0]["_meta"]
        print(f"### D1 汇总（driver=bc1/ckpt_30；{m['rows']} 个候选 / {m['states']} 个状态 / {m['eps']} 局；"
              f"基准可执行率 {m['base']:.3f}）")
    print(f"\n=== {tag}（{int(rows[0]['_meta']['rows'])} 行）===")
    print(f"  {'主干':<22}{'探针 状态内同种类':>18}{'策略 状态内同种类':>18}{'探针−策略':>11}"
          f"{'缝(同局)原':>12}{'缝(同局)去偏移':>16}{'探针偏移/散布':>14}{'策略偏移/散布':>14}")
    for r in rows:
        pr, po = r["probe"], r["policy"]
        print(f"  {r['_meta']['label']:<22}{pr['st_kind']:>18.3f}{po['st_kind']:>18.3f}"
              f"{pr['st_kind'] - po['st_kind']:>+11.3f}{pr['gap_ep'] - po['gap_ep']:>+12.3f}"
              f"{pr['gap_ep_c'] - po['gap_ep_c']:>+16.3f}{pr['offratio']:>14.2f}{po['offratio']:>14.2f}")
    print(f"  {'':<22}（跨状态列：探针原值 / 去偏移；策略原值 / 去偏移）")
    for r in rows:
        pr, po, hd = r["probe"], r["policy"], r["head"]
        extra = "" if r["_meta"]["trained"] else "  ← 自带头是随机初始化，head 列无意义"
        print(f"  {r['_meta']['label']:<22} 探针 {pr['gap_ep']:.3f}/{pr['gap_ep_c']:.3f}   "
              f"策略 {po['gap_ep']:.3f}/{po['gap_ep_c']:.3f}   头 {hd['gap_ep']:.3f}/{hd['gap_ep_c']:.3f}"
              f"   全局 探针 {pr['globe']:.3f} 策略 {po['globe']:.3f} 头 {hd['globe']:.3f}{extra}")
