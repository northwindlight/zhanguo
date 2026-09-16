# -*- coding: utf-8 -*-
"""**"地开发到哪一步了"** —— 单国 300 回合里，领土 / 未建格 / 建筑分布 / 建造速度与国库的关系。

为什么单独一件（2026-09-16）：`probe_spend_v10_v11.py` 只看终局的消费·领土·剩余金，
而"为什么 v11plus 拿两倍的地、消费却少 7.6%"这个问题在终局数字里看不出来。
本探针逐回合记四样，把机制摊开：

    t  领土 / 真·未建格 / 国库金 / 国库木 / 本回合建造数 / 建筑总数

外加一条**集中度**统计（有建筑的格数、座/格、建筑最多的 10 格占比）—— 实测（40×40 seed 900000 t=200）：

| 版本 | 领土 | 建筑 | 有建筑的格 | 座/格 | 前十格占比 |
|---|---|---|---|---|---|
| v10 | 159 | 124 | 49 | 2.5 | 51% |
| v11plus | 209 | 99 | **29** | 3.4 | **63%** |

⇒ 两个 AI 都是**塔式**发展（建筑扎堆在少数核心格，按 ROI 排序挑格子 —— 这本身是理性的），
而 v11plus 抢地快一倍（~2 格/回合 vs ~1.1），于是"开发到的格"反而更少 ⇒ 产出/收入上不去
⇒ 终局消费略低。建造速度两边都是 ~1~2 座/回合，中段国库金只有 30~120（**钱是紧的**）。

★ **一个坑（我自己踩过）**：`t["buildings"]` 是**全键字典**（`{'林场': 4, '农场': 0, …}`），
  所以 `if not t["buildings"]` **恒为 False** —— 那样数出来的"未建格"永远是 0。
  真·未建 = **所有键的值都是 0**（`not any(t["buildings"].values())`）。

    python3 experiments/probe_econ_coverage.py 40 4 300            # 边长 图数 回合
    python3 experiments/probe_econ_coverage.py 16 8 300 v10,v11,v11plus
"""
from __future__ import annotations

import importlib
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rule_ai  # noqa: E402
from mp import World  # noqa: E402

_BUILDS = [0]
_orig_build = World.build


def _counting_build(self, name, x, y, building):
    """数"本回合真建了几座"（引擎 `build` 成功才算）。"""
    ok, msg = _orig_build(self, name, x, y, building)
    if ok:
        _BUILDS[0] += 1
    return ok, msg


World.build = _counting_build


def play(version: str, size: int, seed: int, turns: int, agent: str = "秦") -> list:
    """跑一局，逐回合记 `(回合, 领土, 未建, 金, 木, 本回合建造数, 建筑总数)`。"""
    try:
        importlib.import_module(f"ruleai.{version}.grouping").clear()
    except (ModuleNotFoundError, AttributeError):
        pass
    _, fn = rule_ai.resolve(version)
    w = World(size=size, seed=seed, nations=[agent], max_turns=turns)
    rng = random.Random(0xB4BE)                  # 与 teacher_baseline 同款固定流
    rows = []
    for t in range(turns):
        before = _BUILDS[0]
        fn(w, agent, rng, max_actions=10 ** 9)
        w.resolve_turn()
        own = w.own_tiles(agent)
        bld = {p: sum(w.tiles[p]["buildings"].values()) for p in own}
        res = w.nations[agent].res
        rows.append((t + 1, len(own),
                     sum(1 for n in bld.values() if not n),   # 真·未建（见模块说明的坑）
                     int(res["黄金"]), int(res["木头"]),
                     _BUILDS[0] - before, sum(bld.values())))
        if t + 1 < turns:
            w.begin_turn()
    return rows


def coverage(w: World, agent: str = "秦") -> str:
    """集中度一行：有建筑的格 / 座·格 / 前十格占比。"""
    own = w.own_tiles(agent)
    per = sorted((sum(w.tiles[p]["buildings"].values()) for p in own), reverse=True)
    tot = sum(per)
    built = sum(1 for n in per if n)
    return (f"有建筑的格 {built:>4}（均 {tot / max(1, built):.1f} 座/格）  "
            f"前十格占 {sum(per[:10])}/{tot}（{100 * sum(per[:10]) / max(1, tot):.0f}%）")


def main() -> int:
    size, maps, turns = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
    versions = sys.argv[4].split(",") if len(sys.argv) > 4 else ["v10", "v11plus"]
    seeds = [900_000 + i for i in range(maps)]
    marks = [m for m in (50, 100, 200, 300, 500) if m <= turns]
    print(f"单国 {size}x{size} × {turns} 回合 × {maps} 图（中位）"
          f"　列 = 领土/未建/金/木/建·回合/建筑\n")
    print(f"{'版本':<9}{'图':>4}" + "".join(f"{'t=' + str(m):>30}" for m in marks))
    for ver in versions:
        trace_all = [play(ver, size, s, turns) for s in seeds]
        row = f"{ver:<9}{maps:>4}"
        for m in marks:
            med = [st.median(tr[m - 1][i] for tr in trace_all) for i in range(1, 7)]
            row += f"{'%d/%d/%d/%d/%d/%.2f' % tuple(med):>30}"
        print(row)
        tail = [[st.median(r[i] for r in tr[-50:]) for i in range(1, 7)] for tr in trace_all]
        med = [st.median(t[i] for t in tail) for i in range(6)]
        print(f"{'':<9}{'末50':>4}" + f"{'%d/%d/%d/%d/%d/%.2f' % tuple(med):>30}")
    return 0


if __name__ == "__main__":
    sys.exit(main())