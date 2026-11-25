"""示范采集落盘（与训练分开）：每局一个 gzip-pickle 文件，可断点续采；小内存机器（ECS 3.3GB）可用。

    python3 -m rl.collect --out demos/w3m --games 480 --workers 2 --teacher w3 --opp w3 --seats -1 --wins-only
    python3 -m rl.bc --demos demos/w3m ...      # 训练端读目录

文件：`<out>/s<seed>.done`（这局采完的标记，不论是否留下示范）+ `<out>/s<seed>_k<seat>.pkl.gz`（留下的一份轨迹）。
轨迹内容与 bc._job 的返回一致：(comp, xs, A, Mk, reward, info)。
"""
from __future__ import annotations

import argparse
import gzip
import json
import multiprocessing as mp
import os
import pickle
import time


def _one(args):
    seed, a = args
    from .bc import _job
    done = os.path.join(a["out"], f"s{seed}.done")
    if os.path.exists(done):
        return seed, None
    t = time.time()
    res = _job((seed, a["size"], a["turns"], a["opp"], a["teacher"], a["seat"]))
    kept = []
    for ep in res:
        info = ep[5]
        won = info["winner"] == info["seat"]
        if a["wins_only"] and not won:
            continue
        fn = os.path.join(a["out"], f"s{seed}_k{info['seat']}.pkl.gz")
        with gzip.open(fn + ".tmp", "wb", compresslevel=4) as f:
            pickle.dump(ep, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(fn + ".tmp", fn)
        kept.append(info["seat"])
    meta = dict(seed=seed, secs=round(time.time() - t, 1), kept=kept,
                infos=[{k: v for k, v in ep[5].items()} for ep in res], decs=[len(ep[0]) for ep in res])
    with open(done, "w") as f:
        json.dump(meta, f, ensure_ascii=False)
    return seed, meta


def load_dir(d: str):
    """读目录里全部留下的轨迹 → [(comp, xs, A, Mk, reward, info)]（按文件名排序，结果确定）。"""
    out = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".pkl.gz"):
            with gzip.open(os.path.join(d, fn), "rb") as f:
                out.append(pickle.load(f))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--games", type=int, default=480)
    ap.add_argument("--seed0", type=int, default=50_000)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--size", type=int, default=13)
    ap.add_argument("--turns", type=int, default=150)
    ap.add_argument("--teacher", default="w3")
    ap.add_argument("--opp", default="w3")
    ap.add_argument("--seats", type=int, default=-1, help="-1 = 镜像两边同时录")
    ap.add_argument("--wins-only", action="store_true")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = dict(out=a.out, size=a.size, turns=a.turns, opp=a.opp, teacher=a.teacher, seat=a.seats, wins_only=a.wins_only)
    jobs = [(a.seed0 + i, cfg) for i in range(a.games)]
    t0, n, kept = time.time(), 0, 0
    with mp.get_context("fork").Pool(a.workers, maxtasksperchild=8) as pool:
        for seed, meta in pool.imap_unordered(_one, jobs):
            n += 1
            if meta is None:
                continue
            kept += len(meta["kept"])
            w = meta["infos"][0]["winner"] if meta["infos"] else None
            print(f"[{n}/{a.games}] seed {seed} winner {w} turns {meta['infos'][0]['turns']} kept {meta['kept']} "
                  f"decs {meta['decs']} {meta['secs']}s | 累计留 {kept} 份, {time.time() - t0:.0f}s", flush=True)
    print("COLLECT_DONE", n, kept, flush=True)


if __name__ == "__main__":
    main()
