# -*- coding: utf-8 -*-
"""逐行对拍两份 strat npz：判「Pi 送来的那批」是不是 ECS 自己跑的（同机同种子 ⇒ 应逐位全等）。"""
import sys

import numpy as np

A, B = sys.argv[1], sys.argv[2]
a, b = np.load(A, allow_pickle=False), np.load(B, allow_pickle=False)
print(f"A = {A}\nB = {B}\n")
print(f"字段 A {sorted(a.files)}\n字段 B {sorted(b.files)}")
if len(a["y"]) != len(b["y"]):
    print(f"\n★行数不同：A {len(a['y'])}  B {len(b['y'])} ⇒ 不是同一批数据（轨迹已分叉）")
same_len = len(a["y"]) == len(b["y"])
n = min(len(a["y"]), len(b["y"]))
for k in ("state", "ep", "turn", "kind", "sub", "amount", "y"):
    if k in a.files and k in b.files:
        eq = (a[k][:n] == b[k][:n])
        print(f"  {k:7s} 相同 {eq.mean():.4%}" + ("" if eq.all() else f"   首个不同处 idx={int(np.argmax(~eq))}"))
for k in ("pexec", "logit", "prior"):
    if k in a.files and k in b.files:
        d = np.abs(a[k][:n].astype(float) - b[k][:n].astype(float))
        print(f"  {k:7s} 最大绝对差 {np.nanmax(d):.3e}   逐位全等 {'✓' if np.nanmax(d) == 0 else '✗'}")
print("\n每局行数 A:", np.bincount(a["ep"].astype(int)))
print("每局行数 B:", np.bincount(b["ep"].astype(int)))
print("\n判决：全部逐位全等 ⇒ 同一批（同机同种子可复现）；否则 ⇒ 不同机/不同代码，不可混算")
