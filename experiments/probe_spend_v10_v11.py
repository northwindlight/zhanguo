# -*- coding: utf-8 -*-
"""v10 vs v11plus：**单国 300 回合**的终局消费 / 领土 / 剩余金 —— 逐图配对。

为什么单国（用户 2026-09-16）：「一图一国，不要多国对战」—— 多国一开战，消费里就混进了
"谁先动手、谁背刺"的运气成分；这轮要看的是**规则 AI 自己的花钱效率**，所以一图一国。

口径与 `rl/train.py:teacher_baseline`（在 `feat/rl` 分支）逐条对齐，别另立一套：

  · 建局：`World(size=16, seed=900000+i, nations=["秦"], max_turns=300)`
    —— 图集用 900_000+i（与 `probe_teacher_ceiling.py` 的贪心/评估档同源；`map_seed` 不另给）
  · 老师：`rng = random.Random(0xB4BE)`、`max_actions=10**9`、每回合 `resolve_turn()` 后 `begin_turn()`
  · 编组是**模块内存**（`grouping._STATE`）⇒ **每局开跑前 `clear()`**（不清就跨局漏，2026-09-15 栽过）
  · 抖动恒关（main 上本来就没有抖动器）

报三样（用户 2026-09-16 口径）：**终局总消费**、**领土格数**、**剩余黄金**；
另附消费构成（建造 / 征兵 / 军费）与军队·建筑数 —— "剩余金"这个病只在构成里看得见
（军费那栏就是 `MIL_SHARE` 管的那部分，用户：v11 借了 v10 的 0.30，「没有实地测过」）。

用法：

    python3 experiments/probe_spend_v10_v11.py                     # 8 图 × 300 回合：v10 vs v11plus
    python3 experiments/probe_spend_v10_v11.py --versions v10,v11,v11plus
    python3 experiments/probe_spend_v10_v11.py --shares 0.10,0.15,0.20,0.30,0.45,0.60
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import rule_ai  # noqa: E402
from balance import MARKET  # noqa: E402
from mp import World  # noqa: E402

# 库存里"非金"的那几样（黄金单独算：国库黄金**就是钱**，按面值，不折市价）
STOCK_GOODS = ("木头", "矿石", "石油", "装备", "补给")


def _clear_state(version: str) -> None:
    """清掉该代的编组内存（v11/v11plus 的 `grouping._STATE` 住在模块里）。"""
    import importlib
    try:
        importlib.import_module(f"ruleai.{version}.grouping").clear()
    except (ModuleNotFoundError, AttributeError):
        pass                                  # 单文件版（v10…）没有这个模块，正常


def set_mil_share(version: str, value: float) -> bool:
    """给这一代设 `MIL_SHARE`（读时取模块全局，所以直接改模块属性即可），设完**回读自证**。"""
    import importlib
    try:
        mod = importlib.import_module(f"ruleai.{version}.economy")
    except ModuleNotFoundError:
        return False
    before = getattr(mod, "MIL_SHARE", None)
    if before is None:
        return False
    mod.MIL_SHARE = float(value)
    got = getattr(mod, "MIL_SHARE")
    if abs(got - float(value)) > 1e-12:
        raise RuntimeError(f"{version} 的 MIL_SHARE 没设上：{got} != {value}")
    return True


def play(version: str, size: int, seed: int, turns: int, agent: str = "秦",
         gold_trace: list | None = None) -> dict:
    """跑一局（单国、看海口径），返回终局那几样数。

    `gold_trace` 给了就往里记**每回合开局**的 `(回合, 国库金)` —— "剩余金"这个病要看
    中段那条曲线，光看终局会被"最后都在花"掩盖（用户 2026-09-16 点名要这条）。
    """
    _, fn = rule_ai.resolve(version)
    _clear_state(version)
    w = World(size=size, seed=seed, nations=[agent], max_turns=turns)
    rng = random.Random(0xB4BE)                      # 与 teacher_baseline 同款固定流
    for t in range(turns):
        if gold_trace is not None:
            gold_trace.append((t + 1, int(w.nations[agent].res["黄金"])))
        fn(w, agent, rng, max_actions=10 ** 9)
        w.resolve_turn()
        if t + 1 < turns:
            w.begin_turn()
    own = w.own_tiles(agent)
    bld = {}
    for p in own:
        for b, c in w.tiles[p].get("buildings", {}).items():
            bld[b] = bld.get(b, 0) + c
    spend = dict(w.spend.get(agent) or {})
    res = w.nations[agent].res
    return {
        "spend": w.spend_total(agent),
        "build": float(spend.get("build", 0.0)),
        "recruit": float(spend.get("recruit", 0.0)),
        "supply": float(spend.get("supply", 0.0)),
        "tiles": len(own),
        "gold": int(res["黄金"]),
        # "剩余金"的病不止国库那点现金：货堆在仓里也是钱。这里按**当时市价**折一份，
        # 与 `gold` 分开报（黄金是钱，按面值；其它货才折价）。
        "stock": float(sum(res.get(g, 0) * MARKET.get(g, 0) for g in STOCK_GOODS)),
        "armies": len(w.nation_armies(agent)),
        "buildings": sum(bld.values()),
    }


def boot_ci(vals: list[float], n: int = 20000, seed: int = 12345) -> tuple[float, float]:
    """对**地图**做 bootstrap（口径同 `probe_mil_share.py`：别只看均值）。"""
    rng = random.Random(seed)
    k = len(vals)
    ms = sorted(sum(vals[rng.randrange(k)] for _ in range(k)) / k for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--versions", default="v10,v11plus")
    ap.add_argument("--maps", type=int, default=8)
    ap.add_argument("--turns", type=int, default=300)
    ap.add_argument("--size", type=int, default=16)
    ap.add_argument("--seed0", type=int, default=900_000)
    ap.add_argument("--shares", default="", help="给了就只跑 v11plus 的 MIL_SHARE 扫描（逗号分隔）")
    ap.add_argument("--gold-trace", action="store_true",
                    help="另打一张国库金曲线表（回合 50/100/200/300 + 100 回合后峰值/中位）")
    a = ap.parse_args()

    versions = [v.strip() for v in a.versions.split(",") if v.strip()]
    seeds = [a.seed0 + i for i in range(a.maps)]
    print(f"单国 {a.size}x{a.size} × {a.turns} 回合 × {a.maps} 图"
          f"（种子 {seeds[0]}..{seeds[-1]}），老师 rng=0xB4BE、max_actions=无上限\n")
    gold_share = None

    if a.shares:
        shares = [float(x) for x in a.shares.split(",")]
        rows = []
        for share in shares:
            ok = set_mil_share("v11plus", share)
            assert ok, "v11plus 没有 MIL_SHARE —— 版本名写错了？"
            res = [play("v11plus", a.size, s, a.turns) for s in seeds]
            rows.append((share, res))
        print(f"{'MIL_SHARE':>10}{'消费(中位)':>14}{'消费(均值)':>14}{'领土':>8}"
              f"{'剩余金':>10}{'余货折金':>11}{'军费':>11}{'征兵':>10}{'建造':>11}")
        for share, res in rows:
            sp = [r["spend"] for r in res]
            print(f"{share:>10.2f}{st.median(sp):>14,.0f}{st.mean(sp):>14,.0f}"
                  f"{st.median(r['tiles'] for r in res):>8.1f}"
                  f"{st.median(r['gold'] for r in res):>10,.0f}"
                  f"{st.median(r['stock'] for r in res):>11,.0f}"
                  f"{st.median(r['supply'] for r in res):>11,.0f}"
                  f"{st.median(r['recruit'] for r in res):>10,.0f}"
                  f"{st.median(r['build'] for r in res):>11,.0f}")
        set_mil_share("v11plus", 0.30)            # 还原默认（别把测出来的东西留在进程里）
        return 0

    data: dict[str, list[dict]] = {}
    traces: dict[str, list[list]] = {}
    for ver in versions:
        data[ver] = []
        traces[ver] = []
        for s in seeds:
            tr: list = [] if a.gold_trace else []
            r = play(ver, a.size, s, a.turns, gold_trace=tr if a.gold_trace else None)
            if a.gold_trace:
                traces[ver].append(tr)
            data[ver].append(r)
            print(f"  {ver:<8} 图 {s}: 消费 {r['spend']:>9,.0f}（建造 {r['build']:>7,.0f} / "
                  f"征兵 {r['recruit']:>7,.0f} / 军费 {r['supply']:>7,.0f}）"
                  f"  领土 {r['tiles']:>4}  剩余金 {r['gold']:>7,}  余货 {r['stock']:>7,.0f}"
                  f"  军队 {r['armies']:>3}  建筑 {r['buildings']:>3}")

    print(f"\n=== 逐图配对（图 {len(seeds)} 张）===")
    head = f"{'版本':<10}{'消费中位':>12}{'消费均值':>12}{'领土中位':>10}{'剩余金中位':>12}"
    print(head + f"{'余货折金':>11}{'军费中位':>11}{'军费占比':>10}")
    for ver in versions:
        res = data[ver]
        sp = [r["spend"] for r in res]
        print(f"{ver:<10}{st.median(sp):>12,.0f}{st.mean(sp):>12,.0f}"
              f"{st.median(r['tiles'] for r in res):>10.1f}"
              f"{st.median(r['gold'] for r in res):>12,.0f}"
              f"{st.median(r['stock'] for r in res):>11,.0f}"
              f"{st.median(r['supply'] for r in res):>11,.0f}"
              f"{st.median(r['supply'] / r['spend'] for r in res) * 100:>9.1f}%")

    if a.gold_trace:
        print(f"\n=== 国库金曲线（中位，逐图取中位后再跨图取中位）===")
        print(f"{'版本':<10}{'t=50':>10}{'t=100':>10}{'t=200':>10}{'t=300':>10}"
              f"{'100 后峰值':>12}{'末 50 回合中位':>16}"
              f"{'金>500 的回合数':>16}{'金>1000 的回合数':>17}")
        for ver in versions:
            cols = []
            for t in (50, 100, 200, 300):
                cols.append(st.median([tr[t - 1][1] for tr in traces[ver] if len(tr) >= t]))
            peak = st.median([max(g for _, g in tr) for tr in traces[ver]])
            tail = st.median([st.median([g for _, g in tr[-50:]]) for tr in traces[ver]])
            i500 = st.median([sum(1 for _, g in tr if g > 500) for tr in traces[ver]])
            i1000 = st.median([sum(1 for _, g in tr if g > 1000) for tr in traces[ver]])
            print(f"{ver:<10}{cols[0]:>10,.0f}{cols[1]:>10,.0f}{cols[2]:>10,.0f}"
                  f"{cols[3]:>10,.0f}{peak:>12,.0f}{tail:>16,.0f}"
                  f"{i500:>16,.0f}{i1000:>17,.0f}")

    if len(versions) == 2 and len(seeds) >= 4:
        va, vb = versions
        d_sp = [b["spend"] - a["spend"] for a, b in zip(data[va], data[vb])]
        d_ti = [b["tiles"] - a["tiles"] for a, b in zip(data[va], data[vb])]
        d_go = [b["gold"] - a["gold"] for a, b in zip(data[va], data[vb])]
        lo, hi = boot_ci(d_sp)
        print(f"\n=== {vb} − {va}（逐图配对，bootstrap 95% CI 对地图重采样）===")
        print(f"  消费 {st.mean(d_sp):>+10,.0f}  [{lo:+,.0f}, {hi:+,.0f}]"
              f"   逐图：{['%+.0f' % x for x in d_sp]}")
        print(f"  领土 {st.mean(d_ti):>+10.1f}   逐图：{['%+d' % x for x in d_ti]}")
        print(f"  剩余金 {st.mean(d_go):>+9,.0f}   逐图：{['%+d' % x for x in d_go]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())