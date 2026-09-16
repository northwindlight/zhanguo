# -*- coding: utf-8 -*-
"""v10 vs v11（现役 `v11plus`）**单国单图**对照：300 回合后的终局消费 / 领土 / 剩余金。

## 为什么单国单图（用户 2026-09-16 口径）

「不要多国对战，一图一国」—— 排除外交与多国交战这两个变量，两代规则 AI 的差别就只剩
**自己想怎么花**：扩张打野人、建产能、征兵、卖余量。这样"消费差"才是**策略差**，
而不是"这局运气好打了个弱邻国"。

## 三个指标

| 指标 | 取自 | 含义 |
|---|---|---|
| 终局消费 | `world.spend_total(name)` | 全期累计支出 = 本仓的目标函数（支出法 GDP 的骨架） |
| 领土 | `len(world.own_tiles(name))` | 拿了几块地 |
| 剩余金 | `world.nations[name].res["黄金"]` | **终局没花掉的钱** —— 两代都偏高（钱躺着不产出分数） |

## 口径（与仓库其它测算对齐）

- 单国 `秦`，`size=40`、`turns=300`、8 张图（`seed = seed0 + i`，缺省基点 `900_000`
  —— 与 RL 评估那套贪心图同基点，方便和旧数对齐）；
- **视野 = `turns + 20`**：本仓从 v10 起不设默认视野，跑局的人把"本局总回合数"放进
  `world.max_turns` 即可（`mp.World(max_turns=...)`）；
- 每局开始清一次编组状态（v11 的 `grouping._STATE` 是**模块内存**，不清会跨局漏）；
- `jitter` 一律真值（沙盒里 RL 线不在，天然干净）；
- 固定 RNG 流（`0xB4BE`，与仓库其它探针同款），逐图确定性可复跑。

## `--mil-share`

`v11plus/economy.py` 的 `MIL_SHARE`（军费占收入的上限）**是从 v10 抄来的 30%，没实地测过**
（用户 2026-09-16 原话）。这个开关只在本进程里 `setattr` 那个模块常量（**不动仓库文件**），
用来扫它的最优点：`--mil-share 0.15,0.30,0.60`。

用法：
    python3 experiments/spend_v10_vs_v11.py                          # v10 vs v11plus，8 图
    python3 experiments/spend_v10_vs_v11.py --maps 3 --turns 120     # 冒烟
    python3 experiments/spend_v10_vs_v11.py --versions v11plus --mil-share 0.15,0.30,0.60
"""
from __future__ import annotations

import argparse
import random
import statistics as st
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rule_ai  # noqa: E402
from mp import World  # noqa: E402

NAME = "秦"


def clear_state(version: str) -> None:
    """清该代的编组内存状态（v11/v11plus 有 `grouping`；单文件那几代没有，跳过）。"""
    try:
        import importlib
        importlib.import_module(f"ruleai.{version}.grouping").clear()
    except (ModuleNotFoundError, AttributeError):
        pass


def set_mil_share(value: float | None) -> None:
    """只在本进程里改 `MIL_SHARE` 模块常量（**不写文件**）；`None` = 不动。"""
    if value is None:
        return
    import importlib
    mod = importlib.import_module("ruleai.v11plus.economy")
    mod.MIL_SHARE = float(value)


def play(version: str, seed: int, *, size: int, turns: int, mil_share=None) -> dict:
    """一图一国跑满 `turns` 回合，返回三个指标。"""
    _, fn = rule_ai.resolve(version)
    clear_state(version)
    set_mil_share(mil_share)
    w = World(size=size, seed=seed, nations=[NAME], max_turns=turns)
    rng = random.Random(0xB4BE)
    for t in range(turns):
        fn(w, NAME, rng, max_actions=10 ** 9)
        w.resolve_turn()
        if t + 1 < turns:
            w.begin_turn()
    return {"seed": seed,
            "消费": w.spend_total(NAME),
            "领土": len(w.own_tiles(NAME)),
            "剩余金": w.nations[NAME].res["黄金"]}


def summarize(rows: list[dict]) -> dict:
    return {k: st.median([r[k] for r in rows]) for k in ("消费", "领土", "剩余金")}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--versions", default="v10,v11plus")
    ap.add_argument("--maps", type=int, default=8)
    ap.add_argument("--seed0", type=int, default=900_000)
    ap.add_argument("--size", type=int, default=40)
    ap.add_argument("--turns", type=int, default=300)
    ap.add_argument("--mil-share", default="",
                    help="只需 v11plus：在本进程里改 economy.MIL_SHARE（逗号分隔可多值）")
    a = ap.parse_args()

    shares = [float(x) for x in a.mil_share.split(",") if x.strip()] or [None]
    seeds = [a.seed0 + i for i in range(a.maps)]
    print(f"# 单国（{NAME}）单图 · {a.size}x{a.size} · {a.turns} 回合 · "
          f"{len(seeds)} 张图（seed {seeds[0]}..{seeds[-1]}）")
    print(f"# 版本 {a.versions}"
          + (f" · MIL_SHARE 扫描 {shares}" if shares != [None] else ""))

    for version in a.versions.split(","):
        version = version.strip()
        for share in shares:
            rows, t0 = [], time.time()
            for seed in seeds:
                rows.append(play(version, seed, size=a.size, turns=a.turns,
                                 mil_share=share if version.startswith("v11") else None))
            s = summarize(rows)
            tag = f"{version}" + (f"@MIL_SHARE={share}" if share is not None else "")
            print(f"\n## {tag}   共 {time.time() - t0:.1f}s")
            print(f"{'seed':>9}{'消费':>12}{'领土':>7}{'剩余金':>12}")
            for r in rows:
                print(f"{r['seed']:>9}{r['消费']:>12,.0f}{r['领土']:>7}{r['剩余金']:>12,}")
            print(f"{'中位':>9}{s['消费']:>12,.0f}{s['领土']:>7}{s['剩余金']:>12,}")
            print(f"{'均值':>9}{st.mean([r['消费'] for r in rows]):>12,.0f}"
                  f"{st.mean([r['领土'] for r in rows]):>7.1f}"
                  f"{st.mean([r['剩余金'] for r in rows]):>12,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())