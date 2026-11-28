"""Leave one world out. Run with Python and NumPy from any directory."""
import csv
from pathlib import Path

import numpy as np

GOODS = ("food", "wood", "ore", "oil", "equipment", "supply")
DEPTH = np.array([1920, 1920, 1280, 960, 640, 960], dtype=float)
HORIZONS = (1, 3, 10)
RIDGE = 5.0


def load_worlds(path):
    worlds = {}
    with path.open() as source:
        for row in csv.DictReader(source):
            rows = worlds.setdefault(row["seed"], [])
            if int(row["turn"]) != len(rows) + 1:
                raise ValueError("Incomplete or unordered world")
            rows.append([[int(row[g + "_buy"]), int(row[g + "_sell"])] for g in GOODS])
    return {seed: np.asarray(rows, dtype=float) for seed, rows in worlds.items()}


def features(tape, t, good):
    # t is the count of completed turns. Public flows only, through t-1.
    past = tape[:t]
    feature = [float(t == 0), float(t == 1), float(1 < t <= 4),
               1 / (1 + t), np.log1p(t)]
    for n in (1, 3, 8):
        tail = past[-n:]
        recent = tail.mean(axis=0) if len(tail) else np.zeros((len(GOODS), 2))
        # Cross-commodity flow can distinguish a building or import cycle.
        feature.extend(np.log1p(recent.ravel()))
        feature.extend(np.sqrt(recent[good]))
    cumulative = past.sum(axis=0)
    scarcity = (cumulative[:, 0] - cumulative[:, 1]) / DEPTH
    feature.extend(scarcity)
    feature.extend((max(scarcity[good], 0), min(scarcity[good], 0) ** 2))
    return feature


def ridge(train_x, train_y, test_x):
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1
    x = (train_x - mean) / scale
    y_mean = train_y.mean(axis=0)
    coefficients = np.linalg.solve(x.T @ x + RIDGE * np.eye(x.shape[1]),
                                   x.T @ (train_y - y_mean))
    return np.maximum(0, y_mean + (test_x - mean) / scale @ coefficients)


def evaluate(worlds, horizon):
    # Each world runs to 100. The target includes only complete horizons.
    errors = {name: [] for name in ("last", "calendar", "regression")}
    wood = {name: [] for name in errors}
    folds = {}
    for test_seed, test in worlds.items():
        train = [v for k, v in worlds.items() if k != test_seed]
        fold = {name: [] for name in errors}
        for g in range(len(GOODS)):
            def xy(v):
                starts = range(len(v) - horizon + 1)
                x = np.array([features(v, t, g) for t in starts])
                y = np.array([v[t:t+horizon, g].sum(axis=0) for t in starts])
                return x, y

            x0, target = xy(test)
            training = [xy(v) for v in train]
            train_x = np.concatenate([x for x, _ in training])
            train_y = np.concatenate([y for _, y in training])
            calendar = np.mean([y for _, y in training], axis=0)
            recent = []
            for t in range(len(target)):
                window = test[max(0,t-horizon):t, g]
                # Give the trailing baseline the same historical opening expectation.
                recent.append(window.mean(axis=0)*horizon if t else calendar[0])
            predictions = dict(last=recent, calendar=calendar,
                               regression=ridge(train_x, train_y, x0))
            for name, prediction in predictions.items():
                error = np.abs(prediction-target)
                errors[name].append(error)
                fold[name].append(error)
                if g == 1:
                    wood[name].append(error)
        folds[test_seed] = {n: np.mean(v) for n, v in fold.items()}
    print(f"{horizon=:2}  MAE (all goods and sides, units per horizon)")
    for name, error in errors.items():
        values = np.mean(wood[name], axis=(0, 1))
        print(f"  {name:10} all={np.mean(error):.2f}"
              f" wood-buy={values[0]:.2f} wood-sell={values[1]:.2f}")
    print("  per-world regression", ", ".join(f"{s}={d['regression']:.2f}" for s,d in folds.items()))


if __name__ == "__main__":
    data = load_worlds(Path(__file__).with_name("market-volumes.csv"))
    for horizon in HORIZONS:
        evaluate(data, horizon)
