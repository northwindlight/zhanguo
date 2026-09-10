# -*- coding: utf-8 -*-
"""训练状态接口：读 log.csv / train.log，打印一屏看得懂的进度。

    python3 -m rl.status                      # 默认 rl/runs/srv16
    python3 -m rl.status --run rl/runs/fog16 --tail 5

不依赖 torch，随时可看（本地或 ssh 到服务器跑都行）。
"""
from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path

FIELDS = ["iter", "env_steps", "secs", "episodes", "last_spend", "mean_spend",
          "last_tiles", "last_armies", "ent", "kl", "clipfrac"]


def _fmt(v: str, width: int = 10) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v).rjust(width)
    if f != f:                      # nan
        return "-".rjust(width)
    if abs(f) >= 1000:
        return f"{f:,.0f}".rjust(width)
    return f"{f:.3f}".rjust(width)


def main() -> None:
    ap = argparse.ArgumentParser(description="训练状态")
    ap.add_argument("--run", default="rl/runs/srv16", help="训练产物目录")
    ap.add_argument("--tail", type=int, default=6, help="打印最近多少块")
    args = ap.parse_args()
    d = Path(args.run)
    if not d.exists():
        print(f"没有这个目录：{d}")
        return

    # ---- 是否还在跑 / 崩没崩
    log = d / "train.log"
    if log.exists():
        age = time.time() - log.stat().st_mtime
        text = log.read_text(encoding="utf-8", errors="replace")
        crashed = "Traceback" in text
        print(f"日志：{log}  最后写入 {age:.0f}s 前  "
              f"{'⚠ 有 Traceback（崩了）' if crashed else '（无异常）'}")
        if crashed:
            lines = [ln for ln in text.strip().splitlines() if ln.strip()]
            print("  崩溃尾部：")
            for ln in lines[-6:]:
                print("   ", ln[:150])

    # ---- 一行状态（训练侧每块刷新，不依赖 stdout）
    st = d / "status.txt"
    if st.exists():
        print(f"\n最新状态：{st.read_text(encoding='utf-8', errors='replace').strip()}")

    # ---- 逐块指标
    csvp = d / "log.csv"
    if not csvp.exists():
        print("还没有 log.csv（还没跑完第一块）")
        return
    with open(csvp, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("log.csv 是空的（还没跑完第一块）")
        return

    print(f"\n共 {len(rows)} 块 | 最近 {args.tail} 块：")
    hdr = ["块", "步数", "耗时s", "局数", "末局消费", "近3局均", "地", "军", "熵", "KL"]
    print("  " + "".join(h.rjust(10) for h in hdr))
    for r in rows[-args.tail:]:
        cells = [r.get("iter", ""), r.get("env_steps", ""), r.get("secs", ""),
                 r.get("episodes", ""), r.get("last_spend", ""), r.get("mean_spend", ""),
                 r.get("last_tiles", ""), r.get("last_armies", ""),
                 r.get("ent", ""), r.get("kl", "")]
        print("  " + "".join(_fmt(c) for c in cells))

    # ---- 评估历史（确定性策略）
    evals = [r for r in rows if r.get("eval_spend") not in (None, "", "nan")]
    if evals:
        print(f"\n确定性评估（{len(evals)} 次）：")
        for r in evals:
            print(f"  第 {r['iter']:>4} 块  eval消费 {_fmt(r['eval_spend'])}"
                  f"  地 {_fmt(r['eval_tiles'])}  军 {_fmt(r.get('eval_armies',''))}")

    # ---- 小结
    spends = [float(r["last_spend"]) for r in rows
              if r.get("last_spend") not in (None, "", "nan")]
    if spends:
        best = max(spends)
        print(f"\n局末消费：最新 {spends[-1]:,.0f} | 历史最好 {best:,.0f} | "
              f"近 5 局均 {sum(spends[-5:]) / len(spends[-5:]):,.0f}")
    if evals:
        e = [float(r["eval_spend"]) for r in evals]
        print(f"评估消费：最新 {e[-1]:,.0f} | 最好 {max(e):,.0f}"
              f"  （规则 AI 基线 13,856 / 500 回合）")


if __name__ == "__main__":
    main()
