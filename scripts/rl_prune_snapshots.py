# -*- coding: utf-8 -*-
"""★ 联赛快照**抽样保留**（磁盘闸）—— 把"只增不删"的池子按抽样裁一刀。

★ 为什么需要：联赛快照（`league_*_snaps/*.pt`）**每冻一份就多一份**，
  而池子"只增不删" ⇒ 磁盘迟早满 ⇒ 实测满盘的表现是
  **`sqlite3.OperationalError: disk I/O error`**（联赛库打不开）
  ⇒ **训练每轮一启动就死**，而外面的定时器还在拿**同一个旧存档**反复对打
  ⇒ 一夜之间"跑了 30 轮"，其实**一个 iter 都没训**。

★ 裁剪口径（默认）：
  · **最新的 K 份**全留（K=12）—— 最近的在训/主力成员都要用到；
  · 更老的**每隔 N 份留 1 份**（N=3 ⇒ 抽样保留约 1/3）；
  · 删掉的文件**连同 DB 里的那一行一起删** ⇒ 不留悬空引用
    （否则 `league.net_of(mid)` 会去读一个不存在的文件）。

★ **默认只演练**（dry-run）：先看会删什么，再加 `--apply` 真删。

用法：
  python3 scripts/rl_prune_snapshots.py --db rl/runs/league_sig1.db \
      --dir rl/runs/league_sig1_snaps [--keep 12] [--every 3] [--apply]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="联赛库（SQLite）")
    ap.add_argument("--dir", required=True, help="快照目录")
    ap.add_argument("--keep", type=int, default=12, help="最新保留几份（默认 12）")
    ap.add_argument("--every", type=int, default=3, help="更老的每隔几份留 1 份（默认 3）")
    ap.add_argument("--apply", action="store_true", help="真删（默认只演练）")
    args = ap.parse.parse_args() if False else ap.parse_args()

    if not os.path.isfile(args.db):
        print(f"★ 找不到联赛库：{args.db}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT mid, path, born FROM members WHERE path IS NOT NULL "
        "ORDER BY born DESC, mid DESC").fetchall()
    if not rows:
        print("★ 库里没有带 path 的快照成员 ⇒ 不用裁")
        return 0

    keep, drop = [], []
    for i, (mid, path, born) in enumerate(rows):
        if i < args.keep:
            keep.append((mid, path, born))
        elif (i - args.keep) % args.every == 0:
            keep.append((mid, path, born))
        else:
            drop.append((mid, path, born))

    freed = 0
    for mid, path, born in drop:
        try:
            if path and os.path.isfile(path):
                freed += os.path.getsize(path)
        except OSError:
            pass

    print(f"★ 快照成员 {len(rows)} 份 ⇒ 保留 {len(keep)} / 删 {len(drop)}"
          f"（最新 {args.keep} 份全留，更老的每 {args.every} 份留 1）")
    print(f"  ⇒ 可释放约 {freed // 1024 // 1024} MB")
    if not args.apply:
        print("  （演练模式；加 --apply 真删）")
        for mid, path, born in drop[:10]:
            print(f"    会删 {mid}  born={born}")
        if len(drop) > 10:
            print(f"    …还有 {len(drop) - 10} 份")
        return 0

    for mid, path, born in drop:
        try:
            if path and os.path.isfile(path):
                os.remove(path)
        except OSError as exc:
            print(f"  ★ 删文件失败 {path}: {exc}", file=sys.stderr)
        conn.execute("DELETE FROM members WHERE mid=?", (mid,))
    conn.commit()
    print(f"  ⇒ 已删 {len(drop)} 份快照 + 对应 DB 行；释放约 {freed // 1024 // 1024} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
