"""Paired comparison of fixed-seed C++ and Python sampling runs."""

import argparse
from pathlib import Path

import numpy as np


def parse(path):
    records, elapsed = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("game "):
            record = dict(token.split("=", 1) for token in line.split()[1:])
            seed = int(record["seed"])
            if seed in records:
                raise ValueError(f"duplicate seed {seed} in {path}")
            records[seed] = record
        elif line.startswith("completed="):
            elapsed = dict(token.split("=", 1) for token in line.split())
    if elapsed is None or int(elapsed["completed"]) != len(records):
        raise ValueError(f"incomplete benchmark {path}")
    return records, elapsed


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("python", type=Path)
    p.add_argument("cpp", type=Path)
    opts = p.parse_args()
    py, py_run = parse(opts.python)
    cpp, cpp_run = parse(opts.cpp)
    if sorted(py) != sorted(cpp):
        raise ValueError("seed pools differ")
    for seed in py:
        assert all(py[seed][k] == cpp[seed][k] for k in ("opp", "seat")), seed

    py_reward = np.array([float(py[s]["reward"]) for s in sorted(py)])
    cpp_reward = np.array([float(cpp[s]["reward"]) for s in sorted(py)])
    delta = cpp_reward - py_reward
    rng = np.random.default_rng(141)
    resamples = rng.integers(0, len(delta), size=(10000, len(delta)))
    lo, hi = np.percentile(delta[resamples].mean(1), [2.5, 97.5])
    endpoints = np.union1d(py_reward, cpp_reward)
    ecdf_delta = (cpp_reward[:, None] <= endpoints).astype(np.int8) - (py_reward[:, None] <= endpoints)
    ks = float(np.abs(ecdf_delta.mean(0)).max())
    signs = (rng.integers(0, 2, size=(10000, len(delta)), dtype=np.int8) * 2 - 1).astype(np.float32)
    null_ks = np.abs(signs @ ecdf_delta.astype(np.float32) / len(delta)).max(1)
    pvalue = (np.count_nonzero(null_ks >= ks - 1e-7) + 1) / (len(null_ks) + 1)
    print(f"games={len(py)} python_decisions_per_s={float(py_run['decisions_per_second']):.2f} "
          f"cpp_decisions_per_s={float(cpp_run['decisions_per_second']):.2f} "
          f"decision_ratio={float(cpp_run['decisions_per_second']) / float(py_run['decisions_per_second']):.3f} "
          f"games_per_hour_ratio={float(py_run['seconds']) / float(cpp_run['seconds']):.3f}")
    print(f"python_reward={py_reward.mean():.6f} cpp_reward={cpp_reward.mean():.6f} "
          f"delta={delta.mean():+.6f} delta_95ci=[{lo:+.6f},{hi:+.6f}] paired_ks={ks:.3f} p={pvalue:.3f}")
    for name, records in [("python", py), ("cpp", cpp)]:
        values = list(records.values())
        print(f"{name} turns={np.mean([int(v['turns']) for v in values]):.3f} "
              f"wins={sum(v['winner'] == v['seat'] for v in values)} "
              f"losses={sum(v['winner'] not in ('-1', v['seat']) for v in values)}")


if __name__ == "__main__":
    main()
