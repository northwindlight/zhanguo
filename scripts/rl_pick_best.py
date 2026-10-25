# -*- coding: utf-8 -*-
"""★ 从联赛池子里**挑最优**（"成品"候选）：把每个冻结快照拼成 ckpt，逐个与**对照**配对评测。

★ 为什么需要"挑"：PPO 在稀疏奖励下是**随机游走** ⇒ 越训不一定越好
  （实测：2 国局 20 iter 后净效果 −7，而策略集中度 0.999 ⇒ 基本没动 ⇒ 那是噪声）。
  ⇒ "成品"应当是**池子里实测最好的那一份**，而不是**最后训出来的那一份**。

★ 流程：
  ① 从联赛库里取**最新 N 份**冻结快照（`kind='snap'` 且有 path）；
  ② 每份拼成一个最小 ckpt（`{"nets": {0: weights}, "meta": {"fingerprint": …}}`）
     ⇒ `rl.head2head --a <它>` 直接能读；
  ③ 逐个与**对照**（基线打自己，同种子）在**同一批种子**上逐局配对
     ⇒ 净效果 = 训练臂A侧胜 − 对照A侧胜，符号检验判显著。

用法：
  python3 scripts/rl_pick_best.py --db rl/runs/league_two.db \
      --snap-dir rl/runs/league_two_snaps --baseline rl/runs/baseline_init.pt \
      --nations 2 --t-max 60 --seeds 12 --seed0 8000 --top 6
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def build_ckpt(snap_path: str, out_path: str) -> bool:
    """把一份联赛快照（`{"weights":…, "fingerprint":…}`）拼成可读的 ckpt。"""
    import torch
    blob = torch.load(snap_path, map_location="cpu", weights_only=False)
    weights = blob.get("weights")
    if not weights:
        return False
    torch.save({"nets": {0: weights},
                "meta": {"fingerprint": blob.get("fingerprint") or {}, "iters": 0}},
               out_path)
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True)
    ap.add_argument("--snap-dir", required=True)
    ap.add_argument("--baseline", required=True, help="固定基线 ckpt")
    ap.add_argument("--control", required=True, help="对照记录（基线打自己）JSONL")
    ap.add_argument("--nations", type=int, default=2)
    ap.add_argument("--t-max", type=int, default=60)
    ap.add_argument("--size", type=int, default=8)
    ap.add_argument("--seeds", type=int, default=12)
    ap.add_argument("--seed0", type=int, default=8000)
    ap.add_argument("--top", type=int, default=6, help="取最新几份来评")
    ap.add_argument("--threads", type=int, default=1)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    rows = conn.execute(
        "SELECT mid, path FROM members WHERE path IS NOT NULL "
        "ORDER BY born DESC, mid DESC LIMIT ?", (args.top,)).fetchall()
    if not rows:
        print("★ 池子里没有带 path 的快照 ⇒ 没法挑")
        return 1
    print(f"★ 候选：池子最新 {len(rows)} 份冻结快照（种子 {args.seed0}-，{args.seeds} 局）")

    tmpdir = tempfile.mkdtemp(prefix="rlpick_")
    results = []
    for mid, path in rows:
        if not path or not os.path.isfile(path):
            continue
        ck = os.path.join(tmpdir, "c.pt")
        if not build_ckpt(path, ck):
            continue
        rec = os.path.join(tmpdir, "h.jsonl")
        if os.path.exists(rec):
            os.remove(rec)
        cmd = [sys.executable, "-m", "rl.head2head", "--a", ck, "--b", args.baseline,
               "--seeds", str(args.seeds), "--seed0", str(args.seed0),
               "--size", str(args.size), "--nations", str(args.nations),
               "--t-max", str(args.t_max), "--pool", "1", "--threads", str(args.threads),
               "--record", rec]
        subprocess.run(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(rec):
            print(f"  ★ {mid}: 对打没跑起来")
            continue
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "rl_judge.py"),
                              rec, args.control], cwd=ROOT, capture_output=True, text=True)
        net = None
        for line in out.stdout.splitlines():
            if "净效果" in line:
                for tok in line.replace("**", " ").split():
                    if tok.startswith("+") or tok.startswith("-"):
                        if tok[1:].isdigit():
                            net = int(tok)
        line = [l for l in out.stdout.splitlines() if "净效果" in l]
        print(f"  · {mid}: {line[0].strip() if line else '（没结果）'}")
        if net is not None:
            results.append((net, mid, ck, rec))
    if not results:
        print("★ 没有任何候选有结果")
        return 1
    results.sort(reverse=True)
    best = results[0]
    print(f"═══ 最好的一份：**{best[1]}**（净效果 {best[0]:+d}）⇒ ckpt {best[2]}")
    print(f"    记录 {best[3]}")
    print(f"    ★ 前几名：" + "，".join(f"{m}({n:+d})" for n, m, _, _ in results[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
