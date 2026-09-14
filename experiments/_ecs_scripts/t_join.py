# -*- coding: utf-8 -*-
"""把 T（老师 128 图×两基点 逐图消费）与 `_map_cmp.py`（同 256 图的起始 5 格 Σ品位/成本加权）
拼成一张 CSV，并给两个基点各自的 **Spearman ρ（起始Σ vs 老师消费）** 与分层均值 —— 只报数，判读归 Pi。

用法：python t_join.py <q5_teacher_128_maps.log> <teacher200_map_cmp_256.raw> <out.csv>
"""
import csv
import re
import sys

import numpy as np

LOG, RAW, OUT = sys.argv[1], sys.argv[2], sys.argv[3]

spend = {}
cur = None
for line in open(LOG, encoding="utf-8"):
    m = re.search(r"种子基点 (\d+)", line)
    if m:
        cur = int(m.group(1))
        continue
    m = re.search(r"seed (\d+)）：老师消费\s*([\d,]+)", line)
    if m and cur is not None:
        spend[int(m.group(1))] = int(m.group(2).replace(",", ""))

q5, cw = {}, {}
seed = None
for line in open(RAW, encoding="utf-8"):
    m = re.match(r"seed (\d+)", line)
    if m:
        seed = int(m.group(1))
        continue
    m = re.match(r"\s+起始 5 格.*?Σ=(\d+)\s+成本加权=([\d.]+)", line)
    if m and seed is not None:
        q5[seed] = int(m.group(1))
        cw[seed] = float(m.group(2))

print(f"老师逐图 {len(spend)} 条；起始品位 {len(q5)} 条；"
      f"两边都有 = {len(set(spend) & set(q5))}；只在消费侧 {sorted(set(spend) - set(q5))[:5]}；"
      f"只在品位侧 {sorted(set(q5) - set(spend))[:5]}")


def _avg_rank(a):
    """**并列取平均名次**的标准秩。
    ⚠ 我第一版用 argsort(argsort(x))：并列按数组顺序给**互不相同**的名次 ⇒
    在零值占 60% 的列（黄金）上，ρ 随输入顺序漂 —— 那不是"口径之一"，是顺序依赖的假数。
    Pi 10:50 独立复算抓到这一族（它的平均秩版是标准做法）。"""
    a = np.asarray(a, float)
    order = np.argsort(a, kind="mergesort")
    r = np.empty(len(a))
    r[order] = np.arange(1, len(a) + 1)
    sa = a[order]
    out = np.empty(len(a))
    i = 0
    while i < len(sa):
        j = i
        while j + 1 < len(sa) and sa[j + 1] == sa[i]:
            j += 1
        out[order[i:j + 1]] = (r[order[i]] + r[order[j]]) / 2
        i = j + 1
    return out


def spearman(x, y):
    return float(np.corrcoef(_avg_rank(x), _avg_rank(y))[0, 1])


rows = []
for base in (900000, 800000):
    seeds = sorted(s for s in spend if s >= base and s < base + 128)
    sp = np.array([spend[s] for s in seeds], float)
    qq = np.array([q5[s] for s in seeds], float)
    cc = np.array([cw[s] for s in seeds], float)
    print(f"\n=== 基点 {base}（{len(seeds)} 图）===")
    print(f"  Spearman ρ(消费, 起始Σ品位)   = {spearman(sp, qq):+.3f}")
    print(f"  Spearman ρ(消费, 起始成本加权) = {spearman(sp, cc):+.3f}")
    med = np.median(qq)
    lo, hi = qq < med, qq >= med
    print(f"  起始Σ 中位 {med:.0f}：低半区 消费均值 {sp[lo].mean():,.0f}（n={lo.sum()}）  "
          f"高半区 {sp[hi].mean():,.0f}（n={hi.sum()}）  比 {sp[hi].mean() / sp[lo].mean():.2f}×")
    for s in seeds:
        rows.append([base, s, q5[s], cw[s], spend[s]])

with open(OUT, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["base", "seed", "start5_sigma", "start5_costw", "teacher_spend"])
    w.writerows(rows)
print(f"\nCSV 已写：{OUT}（{len(rows)} 行）")
