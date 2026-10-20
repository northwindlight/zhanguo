"""Offline arithmetic checks for the accompanying article; no model calls."""
import json
import math
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent

def read(name):
    return json.loads((HERE / name).read_text(encoding="utf-8"))

def close(a, b):
    assert math.isclose(a, b, abs_tol=1e-7), (a, b)

scores = read("scores.json")
weights = {row["id"]: row["weight"] for row in scores["rubric"]}
assert sum(weights.values()) == 100
for model in scores["models"]:
    ratings = model["ratings"]
    assert set(ratings) == set(weights)
    assert all(v is None or 0 <= v <= 5 for v in ratings.values())
    close(sum(weights[k] for k, v in ratings.items() if v is not None), model["observed_weight"])
    close(sum(weights[k] * v / 5 for k, v in ratings.items() if v is not None), model["observed_points"])

metrics = read("evidence.json")["metrics"]
turns = read("turns.json")
for key, blocks in turns.items():
    assert len(blocks) == 72
    assert len({(b["period"], b["nation"]) for b in blocks}) == 72
    assert {b["period"] for b in blocks} == set(range(9))
    close(sum(b["duration"] for b in blocks), metrics[key]["actor_wall_seconds"])
    close(sum(b["calls"] for b in blocks), metrics[key]["king_calls"])
    close(median(b["duration"] for b in blocks), metrics[key]["actor_median_seconds"])
    for nation in metrics[key]["per_nation"]:
        subset = [b for b in blocks if b["nation"] == nation["name"]]
        close(sum(b["duration"] for b in subset), nation["seconds"])
        close(sum(b["calls"] for b in subset), nation["calls"])
    # Nearest-rank P90, all times include council waits.
    durations = sorted(b["duration"] for b in blocks)
    print(key, "P50", median(durations), "P90", durations[math.ceil(.9 * len(durations)) - 1])

receipts = {r["id"]: r for r in read("evidence.json")["selected_receipts"]}
churn = [receipts[i]["receipt"]["data"]["churn"] for i in [974, 976, 983, 984, 989, 990]]
close(churn[-1]["loss"], 5.5)
assert sum(x["loss"] for x in churn) > 28
assert receipts[1192]["receipt"]["ok"] is False
assert receipts[1201]["receipt"]["ok"] is True
assert receipts[1201]["arguments"]["action"] == "disband"
historical = json.loads((HERE / "../../game-records/eight-nations-20261018/report-count-audit.json").read_text(encoding="utf-8"))["counts"]
gemini = [historical[n] for n in "秦赵楚"]
for field, expected in {"actions": 1561, "failed": 103, "query": 654, "建造": 184, "build_success": 109}.items():
    assert sum(row[field] for row in gemini) == expected
print("OK: weights, four provisional scores, 144 completed turns, aggregates, selected receipts, Gemini historical counts")

# v0.2 uses a shared 14-period window and leaves all macro-economic ratings empty.
assert scores["version"] == "0.2"
assert all(m["ratings"]["economy"] is None for m in scores["models"])
assert all(m["ratings"]["strategy"] is None for m in scores["models"])
followup = read("followup-evidence.json")
for key, blocks in read("followup-turns.json").items():
    group = followup["groups"][key]
    assert len(blocks) == 112
    assert len({(b["period"], b["nation"]) for b in blocks}) == 112
    assert {b["period"] for b in blocks} == set(range(14))
    close(sum(b["duration"] for b in blocks), group["seconds"])
    close(sum(b["calls"] for b in blocks), group["calls"])
    durations = sorted(b["duration"] for b in blocks)
    close(median(durations), group["p50_seconds"])
    close(durations[math.ceil(.9 * len(durations)) - 1], group["p90_seconds"])
    for nation in group["per_nation"]:
        rows = [b for b in blocks if b["nation"] == nation["nation"]]
        close(sum(b["duration"] for b in rows), nation["seconds"])
        close(sum(b["calls"] for b in rows), nation["calls"])
    assert len(group["board14"]) == 8
    assert len(group["economy"]) == 8
    for row in group["economy"]:
        assert set(row["categories"]) == {"recruit", "military", "administration"}
        close(sum(row["categories"].values()), row["consumption_0_13"])
    close(sum(r["attempts"] for r in group["audit"]["per_nation"]), group["audit"]["attempts"])
    close(sum(r["unsuccessful"] for r in group["audit"]["per_nation"]), group["audit"]["unsuccessful"])
assert [m["observed_points"] for m in scores["models"]] == [20, 18.5, 11.5, 0]
print("OK: v0.2 scores, 224 aligned completed turns, consumption categories and audit totals; no short-horizon economic score")
