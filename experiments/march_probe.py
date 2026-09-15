# -*- coding: utf-8 -*-
"""行军/编队/目标选择探针：**v10 vs v11**（同种子、同尺寸、单国看海）。

## 为什么要有它

`expand_rule_v10.py` 的文件头引着几个"行军/进攻"数字（行军 1338.8 → 1115.2、
进攻 249.7 → 293.8），那是当年**一次性手工跑出来的、仓库里没有复现工具**
（`experiments/README.md` 里记着这个缺口）。v11 换掉了寻路/编队/目标选择三件事，
就必须有一把能重复量的尺子 —— 否则"更好"只是说法。

## 量什么（每条都对着一个具体的病）

| 指标 | 定义 | 针对的毛病 |
|---|---|---|
| `行军/进攻` | 动作计数 | 动作构成（钱有没有花在打仗上） |
| `首次进攻` | 第一次成功 `attack` 的回合 | 编队成军快不快 |
| `停滞回合` | 满血、未交战、**本回合有额度却一次没动**的军队·回合数 | 卡住 / 原地打转（v10 撞地形就会这样） |
| `倒退次数` | 某军回到**两回合前**所在格（pos[t]==pos[t-2]≠pos[t-1]） | **抖动**（v10 多目标轮流拽同一支军） |
| `倒退率` | `倒退次数 / 行军次数` | ★抖动要**按每步**比：地拿得多、步数就多，绝对次数自然涨（看绝对数会误判） |
| `每动一格` | 一步 `move` 平均挪了几格（切比雪夫位移 / move 次数） | 地形盲的单步贪心（v11 该 >1：骑兵一回合 2 格） |
| `领土` | 终局国土格数 | 总效果 |
| `到 N 格` | 领土首次达到 N 的回合（曲线指标，抗方差） | 比终值稳，避免"最后十回合的偶然" |
| `消费` | `world.spend_total` | 项目主指标（中位，不用均值） |

## 跑法

    PYTHONUTF8=1 .venv311/python.exe experiments/march_probe.py
    PYTHONUTF8=1 .venv311/python.exe experiments/march_probe.py --seeds 0,1,2 --turns 120
    PYTHONUTF8=1 .venv311/python.exe experiments/march_probe.py --versions v10,v11 --size 30

★ 结论只对**同一次运行**内的两个版本可比（各版口径不同，见 `rule_ai.py` 历代表）。
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
from game import unit_max_hp  # noqa: E402
from mp import World  # noqa: E402


def run(ver: str, seed: int, size: int, turns: int) -> dict:
    """单国（秦）跑 `turns` 回合，统计行军/编队相关指标。**确定性**：同参同结果。"""
    _, fn = rule_ai.resolve(ver)
    w = World(size=size, seed=seed, nations=["秦"])
    rng = random.Random(seed)
    moves = atks = stalled = backward = 0
    displaced = 0
    first_atk = None
    hist: dict[int, list] = {}            # 军 id → 位置历史（查倒退）
    hit = {}
    w.begin_turn()
    for t in range(turns):
        snap = {a["id"]: (a["x"], a["y"]) for a in w.nation_armies("秦")}
        acts = fn(w, "秦", rng, max_actions=40)
        for a in w.nation_armies("秦"):    # 记位置（含本回合新征的）
            hist.setdefault(a["id"], []).append((a["x"], a["y"]))
        for aid, path in hist.items():
            if len(path) >= 3 and path[-1] == path[-3] and path[-1] != path[-2]:
                backward += 1
        for tool, args, ok, msg in acts:
            if not ok:
                continue
            if tool == "move":
                moves += 1
                aid = args.get("army_id")
                if aid in snap:
                    ox, oy = snap[aid]
                    displaced += max(abs(args["x"] - 1 - ox), abs(args["y"] - 1 - oy))
            elif tool == "attack":
                atks += 1
                if first_atk is None:
                    first_atk = w.turn
        # 停滞：本回合有额度（满血、未交战）却一动没动的军队
        for a in w.nation_armies("秦"):
            if a["hp"] >= unit_max_hp(a) and not a.get("engaged") \
                    and a.get("moved_turn") != w.turn:
                stalled += 1
        n = len(w.own_tiles("秦"))
        for mark in (10, 20, 30):
            hit.setdefault(mark, None)
            if hit[mark] is None and n >= mark:
                hit[mark] = w.turn
        w.resolve_turn()
        if t + 1 < turns:
            w.begin_turn()
    out = {"行军": moves, "进攻": atks, "停滞回合": stalled, "倒退次数": backward,
           "倒退率": (backward / moves) if moves else 0.0,
           "每动一格": (displaced / moves) if moves else 0.0,
           "首次进攻": first_atk if first_atk is not None else turns,
           "领土": len(w.own_tiles("秦")), "军队": len(w.nation_armies("秦")),
           "消费": w.spend_total("秦")}
    for mark, turn in hit.items():
        out[f"到{mark}格"] = turn if turn is not None else turns
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="v10 vs v11 行军/编队/目标选择探针")
    ap.add_argument("--versions", default="v10,v11")
    ap.add_argument("--seeds", default=",".join(str(s) for s in range(12)))
    ap.add_argument("--size", type=int, default=30)
    ap.add_argument("--turns", type=int, default=150)
    args = ap.parse_args()
    vers = [v.strip() for v in args.versions.split(",") if v.strip()]
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]

    print(f"\n{'=' * 96}\n行军/编队/目标选择探针  {args.size}×{args.size} · "
          f"{args.turns} 回合 · 单国(秦) · {len(seeds)} 个种子\n{'=' * 96}")
    rows: dict[str, list[dict]] = {}
    for ver in vers:
        rows[ver] = [run(ver, s, args.size, args.turns) for s in seeds]

    keys = ["领土", "到10格", "到20格", "到30格", "首次进攻", "行军", "进攻",
            "每动一格", "停滞回合", "倒退次数", "倒退率", "消费", "军队"]
    print(f"\n{'指标':<10}" + "".join(f"{v:>14}" for v in vers) + "   中位差（后-前）")
    for k in keys:
        vals = [st.median(r[k] for r in rows[v]) for v in vers]
        delta = vals[-1] - vals[0] if len(vals) > 1 else 0.0
        print(f"{k:<10}" + "".join(f"{x:>14,.1f}" for x in vals) + f"{delta:>+15,.1f}")

    if len(vers) == 2:
        a, b = vers
        print(f"\n逐种子配对（{a} → {b}）：正数 = {b} 更好")
        for k in ("领土", "到20格", "首次进攻", "停滞回合", "倒退次数", "行军"):
            diffs = [rows[b][i][k] - rows[a][i][k] for i in range(len(seeds))]
            if k in ("到20格", "首次进攻", "停滞回合", "倒退次数"):
                win = sum(1 for d in diffs if d < 0)        # 越小越好
            else:
                win = sum(1 for d in diffs if d > 0)
            tie = sum(1 for d in diffs if d == 0)
            print(f"  {k:<8} {b} 更好 {win:>3}/{len(seeds)}  平 {tie:>2}  "
                  f"中位差 {st.median(diffs):>+8,.1f}  逐种子 " +
                  " ".join(f"{d:+.0f}" for d in diffs))
    print("\n★ 消费按中位报（跨图方差极大）；'到N格' 是曲线指标，比终值抗方差。")
    print("★ 两端只在**本次运行**内可比 —— 各版口径不同（见 rule_ai.py 历代表）。")


if __name__ == "__main__":
    main()