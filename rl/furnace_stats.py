# -*- coding: utf-8 -*-
"""把**各个炉子**的 iter 行解析成一张表：**胜场分布 + 方差 + 平局率**。

    python rl/furnace_stats.py <日志…>          # 可以给多个（glob 也行）

iter 行的形状（`rl/train.py:858`）：
    [   4] 局数2 截断1 胜场{'甲': 0, '乙': 0, …} 先手胜0 平均回合400.0 | 上场 […] | 网络 {…}

★ 口径（都写清楚，免得误读）：
  · `局数` = **打完的局**（被 `--max-steps` 截断的**不进**这里，单独数在 `截断N`）；
  · `胜场{p: n}` = **本 iter** 的胜场（`train.py:831` 是 `sum(1 for i in infos …)`）——
    **不是累计** ⇒ 可以直接算方差；
  · `先手胜` = 本 iter"先手赢"的局数，健康值 = 局数/k（先手在 k 国之间轮换）。

★★★ 2026-09-26 实测（用户：「顺便看看各个炉子的胜场和方差」）：

| 炉子 | iter | 打完局 | **有胜方** | 胜场分布 | 先手胜 | 每iter胜场方差 |
|---|---|---|---|---|---|---|
| ECS 记忆臂(t-max 400) | 10 | 20 | **45%** | 甲5 乙2 丙2 | 22% | 0.690（均值 0.90） |
| ECS 第 6 炉(t-max 150) | 86 | 170 | **28%** | 丙18 乙18 甲11 丁1 | 19% | 0.340（均值 0.56） |

⇒ ★★★ **最要紧的一条：平局率极高** —— 打完的局里只有 **28%~45% 有胜方**，
  其余全是**打满 `t_max` 的平局**（150 回合上限那炉 72% 是平局）。
  ⇒ **大部分局对策略不产生任何胜负信号**，只剩势函数在塑形。
⇒ ★ **先手胜率 19~22% 是健康的**（3~5 国时健康值 ≈1/k = 20~33%）——
  `first-streak-limit` 那条闸门没有误报。
⇒ ★ **方差不异常**：每 iter 胜场数的方差 ≈ 它的均值（0.34 vs 0.56、0.69 vs 0.90），
  与泊松式计数过程一致 ⇒ 没有"某几轮集中爆发"这类结构性问题。
  ★ 胜场分布的不均（甲5 乙2 丙2、丁1）是**小样本噪声**（当前臂总共才 9 局有胜负）。

★★★ **连带的一条结构性后果（记着，别当成某一炉的偶然）**：
  `League.record` **只在校验出胜负时调**（平局不记）⇒ 平局率一高，**池子就拿不到数据**。
  实测：箱子那两个库里的 `L*@memN` 在训成员**全是"局0 胜0"**。
  而 `retire()` 要"评级 + RD ≤ `retire_rd`"，评级要够多**有胜负的局**才压得下 RD
  ⇒ **淘汰规则实际上很少触发**，池子一直停在初始的 5 份 —— 与"攒池"的设想是偏的。

★★ **这条也牵扯 ④b 的判决**：10 个 iter 只有 9 局分出胜负 ⇒ 胜负信号这么薄，
  "记忆读路径没接上"和"信号本来就淡"**分不开**。⇒ ④b 卡在 0.0% 时，
  **不能直接判成"记忆的问题"**（这是我差点犯的错）。
"""
from __future__ import annotations

import glob
import re
import sys
from collections import defaultdict

LINE = re.compile(
    r"^\[\s*(\d+)\]\s*局数(\d+)(?:\s*截断(\d+))?\s*胜场(\{[^}]*\})"
    r"\s*先手胜(\d+)\s*平均回合([\d.]+)")


def parse(path):
    iters = []
    for ln in open(path, errors="replace"):
        m = LINE.match(ln)
        if not m:
            continue
        it, done, cut, wins, first, turns = m.groups()
        w = {k: int(v) for k, v in re.findall(r"'([^']+)':\s*(\d+)", wins)}
        iters.append(dict(it=int(it), done=int(done), cut=int(cut or 0),
                          wins=w, first=int(first), turns=float(turns)))
    return iters


def meand(xs):
    if not xs:
        return float("nan")
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def report(name, iters):
    if not iters:
        print(f"{name:<34} （没有 iter 行）")
        return
    done = sum(i["done"] for i in iters)
    cut = sum(i["cut"] for i in iters)
    tot = defaultdict(int)
    for i in iters:
        for k, v in i["wins"].items():
            tot[k] += v
    winsum = sum(tot.values())
    first = sum(i["first"] for i in iters)
    turns = [i["turns"] for i in iters if i["turns"] == i["turns"]]
    per_iter_wins = [sum(i["wins"].values()) for i in iters]
    print(f"\n{name}")
    print(f"  iter {len(iters)}  打完 {done} 局  截断 {cut} 局（{cut / max(1, done + cut):.0%}）"
          f"  平均回合 {sum(turns) / len(turns):.1f}" if turns else "")
    print(f"  有胜负的局 {winsum}（{winsum / max(1, done):.0%} 的**打完局**有胜方，"
          f"其余是打满 t_max 的平局）")
    print(f"  胜场分布: " + "  ".join(f"{k}={v}" for k, v in sorted(tot.items()) if v))
    if winsum:
        ks = sorted(v for v in tot.values() if v)
        print(f"  先手胜 {first}/{winsum} = {first / winsum:.0%}"
              f"（均匀时先手在 k 国间轮换 ⇒ 健康值随 k 不同）")
    print(f"  ★ **每 iter 胜场数的方差** = {meand(per_iter_wins):.3f}"
          f"（均值 {sum(per_iter_wins) / len(per_iter_wins):.2f}）")
    print(f"  ★ 每 iter「打完局数」的方差 = {meand([i['done'] for i in iters]):.3f}")


if __name__ == "__main__":
    seen = {}
    for pat in sys.argv[1:]:
        for f in sorted(glob.glob(pat)):
            try:
                it = parse(f)
            except OSError:
                continue
            if it:
                seen[f] = it
    # 按 iter 数从多到少
    for f in sorted(seen, key=lambda f: -len(seen[f])):
        report(f"{f}  [{len(seen[f])} iter]", seen[f])
    print(f"\n（共 {len(seen)} 个有 iter 的炉子）")
