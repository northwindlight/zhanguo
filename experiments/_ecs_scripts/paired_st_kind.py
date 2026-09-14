# -*- coding: utf-8 -*-
"""配对 bootstrap：两个主干 dump 在**同一批状态**上的「状态内同种类」AUC 之差的 CI。

为什么需要它：D1 的四个 dump 来自**同一次 rollout**（driver=bc1），行与行一一对应
（state/ep/kind/y 完全相同），所以「bc1 的策略 vs entann 的策略」是**逐行配对**的比较，
不该各算各的 CI 再眼看有没有重叠 —— 直接对**差**做重采样，区间会窄得多。

重采样单位：局（cluster bootstrap，20 个独立单位）。统计量与两把尺子的「状态内同种类」同定义。
先自校验：快实现 vs 慢实现必须逐位相同（--check）。

用法：python paired_st_kind.py <npzA> <npzB> [B=400] [seed=0]
"""
import collections
import sys

import numpy as np

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
from boot_st_kind import wgroup_fast, wgroup_slow  # noqa: E402


def gid_of(d):
    keys = np.array([f"{s}|{k}" for s, k in zip(d["state"], d["kind"])])
    _, gid = np.unique(keys, return_inverse=True)
    return gid


A, B = sys.argv[1], sys.argv[2]
NB = int(sys.argv[3]) if len(sys.argv) > 3 else 400
SEED = int(sys.argv[4]) if len(sys.argv) > 4 else 0

a, b = np.load(A, allow_pickle=False), np.load(B, allow_pickle=False)
assert len(a["y"]) == len(b["y"]), (len(a["y"]), len(b["y"]))
for k in ("state", "ep", "turn", "kind", "sub", "amount", "y"):
    assert (a[k] == b[k]).all(), f"★两份 dump 的 {k} 不一致 ⇒ 不是同一批状态，配对无效"
print(f"配对校验 ✓：两份 dump 的 state/ep/turn/kind/sub/amount/y 逐行相同（{len(a['y'])} 行）")
print(f"A = {A}\nB = {B}\n")

y = a["y"].astype(bool)
ep = a["ep"]
gid = gid_of(a)
eps = np.unique(ep)
ep_rows = {e: np.nonzero(ep == e)[0] for e in eps}
rng = np.random.default_rng(SEED)

# 自校验：快 vs 慢
for tag, d in (("A", a), ("B", b)):
    s_slow = wgroup_slow(d["logit"].astype(float), gid.tolist(), y)
    s_fast = wgroup_fast(d["logit"].astype(float), gid, y)
    print(f"  自校验 {tag} 策略：慢 {s_slow:.10f} 快 {s_fast:.10f} 差 {abs(s_slow - s_fast):.2e}")
print()

for name in ("logit", "pexec"):
    lbl = "策略 logits" if name == "logit" else "线性探针"
    for tag, mask in (("全部", np.ones(len(y), bool)), ("回合<70", a["turn"] < 70), ("回合≥70", a["turn"] >= 70)):
        sa = wgroup_fast(a[name].astype(float)[mask], gid[mask], y[mask])
        sb = wgroup_fast(b[name].astype(float)[mask], gid[mask], y[mask])
        ds = []
        rng2 = np.random.default_rng(SEED)
        for _ in range(NB):
            pick = rng2.choice(len(eps), size=len(eps), replace=True)
            sel = np.concatenate([ep_rows[eps[k]] for k in pick])
            sel = sel[mask[sel]]
            if len(sel) < 100:
                continue
            gg, yy = gid[sel], y[sel]
            ds.append(wgroup_fast(a[name].astype(float)[sel], gg, yy)
                      - wgroup_fast(b[name].astype(float)[sel], gg, yy))
        dv = np.asarray(ds, float)
        dv = dv[np.isfinite(dv)]
        lo, hi = np.percentile(dv, 2.5), np.percentile(dv, 97.5)
        pos = int((dv > 0).sum())
        print(f"  {lbl:<10}[{tag:<7}] A {sa:.3f}  B {sb:.3f}  差 {sa - sb:+.3f}   "
              f"95% CI [{lo:+.3f}, {hi:+.3f}]  {'下界>0 ✓' if lo > 0 else ('上界<0 ✓(反向)' if hi < 0 else '跨 0 ✗')}"
              f"   重采样中 A>B 占 {pos / len(dv):.1%}")
