# -*- coding: utf-8 -*-
"""★ 判"出成品"：把**训练臂**和**对照**（基线打自己）在**同一批种子**上逐局配对。

★ 为什么必须跟"对照"比，而不是跟 50% 比：
  这个对打台子**座位/先手偏向很大**（实测：同一批 12 个种子，
  **基线打自己**是 4:8 —— 乙占优）⇒ 训练臂拿到 9:3 里的"9"，
  有一部分是**这批种子的偏向**，不是训练给的。
  ⇒ 正确的量是 **净效果 = 训练臂 A 侧胜 − 对照 A 侧胜**（同种子、同座位）。
  ⇒ 对照**每批种子只算一次**（基线不变）⇒ 缓存起来复用。

★ 判据（默认）：净效果在**每批种子上都 > 0**，且**合并**的符号检验 p < 0.05。

用法：`python /tmp/rl_judge.py <训练臂记录.jsonl> <对照记录.jsonl> ...`
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter


def load(path):
    out = {}
    for line in open(path, encoding="utf-8"):
        if line.strip():
            r = json.loads(line)
            out[int(r["i"])] = r.get("winner")
    return out


def side(w):
    if w is None:
        return "平"
    return "甲" if str(w).startswith("A") else "乙"


def sign_p(pos, neg):
    """双侧符号检验的 p 值（binomial 精确）。"""
    n = pos + neg
    if n == 0:
        return 1.0
    k = min(pos, neg)
    # P(X <= k) * 2，X ~ Binom(n, 0.5)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2 ** n)
    return min(1.0, 2 * tail)


def judge(trained_path, control_path, label=""):
    tr = load(trained_path)
    ct = load(control_path)
    ks = sorted(set(tr) & set(ct))
    d = Counter()
    for i in ks:
        a, b = side(tr[i]), side(ct[i])
        if a == "甲" and b != "甲":
            d[1] += 1
        elif a != "甲" and b == "甲":
            d[-1] += 1
        else:
            d[0] += 1
    ta = sum(1 for i in ks if side(tr[i]) == "甲")
    ca = sum(1 for i in ks if side(ct[i]) == "甲")
    p = sign_p(d[1], d[-1])
    print(f"── {label}：配对 {len(ks)} 局 ⇒ 训练臂A侧 {ta} 胜 / 对照A侧 {ca} 胜"
          f" ⇒ **净效果 {ta - ca:+d}**（逐局 +1×{d[1]}、−1×{d[-1]}、0×{d[0]}）"
          f" ⇒ 符号检验 p={p:.3f}")
    return {"n": len(ks), "trained": ta, "control": ca, "net": ta - ca,
            "pos": d[1], "neg": d[-1], "p": p}


def main() -> None:
    args = sys.argv[1:]
    results = []
    for i in range(0, len(args) - 1, 2):
        results.append(judge(args[i], args[i + 1], label=f"种子集 {i // 2 + 1}"))
    if not results:
        print("用法：rl_judge.py <训练记录.jsonl> <对照记录.jsonl> [...]")
        return
    tot_pos = sum(r["pos"] for r in results)
    tot_neg = sum(r["neg"] for r in results)
    p = sign_p(tot_pos, tot_neg)
    net = sum(r["net"] for r in results)
    allpos = all(r["net"] > 0 for r in results)
    print(f"══ 合计：净效果 {net:+d} 局；逐局 +1×{tot_pos} / −1×{tot_neg}"
          f" ⇒ 符号检验 p={p:.4f}")
    ok = allpos and p < 0.05
    print(f"══ 判据：每批种子净效果都 > 0 ⇒ {'是' if allpos else '否'}；"
          f"p<0.05 ⇒ {'是' if p < 0.05 else '否'} ⇒ "
          f"**{'出成品 ✔' if ok else '还不够 ✘'}**")


if __name__ == "__main__":
    main()
