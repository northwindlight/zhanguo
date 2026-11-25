"""Paired terminal and per-turn comparison of each scripted opponent."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import random
import subprocess
import sys


def numbers(frame):
    return [tuple(word if i == 0 else float(word) for i, word in enumerate(line.split()))
            for line in frame if line]


def compare(seed, probe, size, turns, sample_learner):
    import ruleai.v11plus.grouping as grouping
    from rl.actors import make_pool
    from rl.env import Env
    from rl.candidates import TYPE_ID
    from rl.obs import BLD, RES5, TERR, TRADEABLE, UNITS

    grouping.clear()
    rng = random.Random(seed)
    action_rng = random.Random(seed ^ 0x12345678)
    name, opponent = rng.choices(make_pool(), weights=(0.25, 0.3, 0.25, 0.2))[0]
    seat = rng.randrange(2)
    env = Env(size, 2, turns, {1 - seat: opponent}).reset(seed)
    w = env.world
    names = {n: i for i, n in enumerate(w.order)}
    cmd = [probe, str(seed), str(size), str(turns)]
    if sample_learner:
        cmd += ["--learner"]
    output = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.splitlines()
    first = output.pop(0)
    assert first == f"{seat} {name}"
    cpp, moves = {}, {}
    for line in output:
        cols = line.split()
        if cols[0] == "done":
            winner_cpp, turns_cpp, reward_cpp = float(cols[1]), float(cols[2]), float(cols[3])
        elif cols[0] == "turn":
            turn = int(cols[1])
            frame = cpp[turn] = []
        elif cols[0] == "action":
            moves.setdefault(turn, []).append(tuple(map(int, cols[1:])))
        else:
            frame.append(line)

    def rows():
        for n in w.order:
            yield ("stock", names[n], *(w.res(n, g) if n in w.nations else 0 for g in ["黄金", *TRADEABLE]))
        for (x, y), t in sorted(w.tiles.items()):
            if t["owner"] is not None:
                yield ("tile", x, y, names[t["owner"]], TERR.index(t["terrain"]),
                       *(t["resources"].get(r, 0) for r in RES5),
                       *(t["buildings"].get(b, 0) for b in BLD),
                       *(t.get("pending", {}).get(b, 0) for b in BLD))
        for a in w.armies:
            if a["owner"] in names:
                yield ("army", names[a["owner"]], a["id"], a["x"], a["y"], UNITS.index(a["type"]), a["hp"],
                       a.get("moved_turn", -1), int(bool(a.get("engaged"))))

        for p in w.proposals:
            if p["kind"] == "联盟":
                yield ("proposal", 4, names[p["a"]], -1)
            else:
                a, b = p["A"].removeprefix("国:"), p["B"].removeprefix("国:")
                k = 1 if p["kind"] == "共同防御" else 3 if p.get("subject") else 2
                blocs = {f"盟:{b['name']}": 1000 + i for i, b in enumerate(w.blocs, 1)}
                yield ("proposal", k, names.get(a, blocs.get(a, -1)), names.get(b, blocs.get(b, -1)))

    mismatch = None
    while (s := env.advance()) is not None:
        if mismatch is None:
            a, b = numbers(cpp.get(w.turn, [])), list(rows())
            if len(a) != len(b):
                mismatch = (w.turn, f"rows C++={len(a)} Python={len(b)}")
            for aa, bb in zip(a, b):
                if aa[0] != bb[0] or any(abs(x - y) > 1e-6 for x, y in zip(aa[1:], bb[1:], strict=True)):
                    mismatch = (w.turn, f"C++ {aa} Python {bb}")
                    break
        if sample_learner:
            applied = []
            for _ in range(12):
                cs = env.candidates(s)
                i = action_rng.randrange(len(cs))
                c = cs[i]
                applied.append((i, TYPE_ID[c.kind]))
                if c.kind == "end_turn":
                    break
                env.act(s, c)
            if mismatch is None and applied != moves.get(w.turn, []):
                mismatch = (w.turn, f"actions C++ {moves.get(w.turn)} Python {applied}")
        env.end_turn(s)
    rw = env.rewards()[seat]
    win = env.winner() if env.winner() is not None else -1
    return seed, name, mismatch, rw, reward_cpp, w.turn, turns_cpp, win, winner_cpp


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("probe", type=Path)
    p.add_argument("--python-root", type=Path, default=Path.home() / "zhanguo-rl")
    p.add_argument("--seeds", type=int, default=8)
    p.add_argument("--turns", type=int, default=60)
    p.add_argument("--size", type=int, default=13)
    p.add_argument("--start-seed", type=int, default=7001)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--learner", action="store_true", help="sample up to 12 learner candidates per turn")
    opts = p.parse_args()
    sys.path.insert(0, str(opts.python_root))
    probe = str(opts.probe.resolve())
    with ProcessPoolExecutor(max_workers=opts.workers) as pool:
        futures = [pool.submit(compare, seed, probe, opts.size, opts.turns, opts.learner)
                   for seed in range(opts.start_seed, opts.start_seed + opts.seeds)]
        failures, terminals = 0, 0
        for f in futures:
            seed, opponent, mismatch, py, cpp, t_py, t_cpp, w_py, w_cpp = f.result()
            bad_terminal = abs(py - cpp) > 1e-6 or t_py != t_cpp or w_py != w_cpp
            failures += bool(mismatch)
            terminals += bad_terminal
            if bad_terminal and not mismatch:
                print(f"{seed} {opponent}: terminal reward {py:.9f} / {cpp:.9f}, turns {t_py} / {t_cpp}, winner {w_py} / {w_cpp}", flush=True)
            if mismatch and failures <= 12:
                print(f"{seed} {opponent} first mismatch {mismatch}, final reward {py:.6f} / {cpp:.6f}", flush=True)
        print(f"{opts.seeds} pairs, {failures} have snapshot differences, {terminals} have terminal differences")
        if failures or terminals:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
