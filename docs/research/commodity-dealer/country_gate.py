"""Pin a verified behavior checkpoint when the rolling 16-world mean exceeds 100."""
import argparse
from collections import deque
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import time


def save(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    tmp.replace(path)


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def qualifying(work, latest, after, lineage):
    history = deque(maxlen=16)
    last = None
    for f in sorted(work.glob("games-*.json"), key=lambda p: int(p.stem.split("-")[1])):
        n = int(f.stem.split("-")[1])
        if n > latest:
            break
        j = json.loads(f.read_text())
        if j["initial_sha256"] != lineage or j["games"] != n:
            raise ValueError(f"Report identity mismatch: {f}")
        rows = j["metrics"]
        if not rows:
            continue
        if last != n-len(rows):
            history.clear()
        last = n
        for r in rows:
            g = r["final_nominal_gdp"]
            if r["turns"] != 100 or len(r["seats"]) != 4 or not math.isfinite(g):
                raise ValueError(f"Invalid GDP measurement at {n}")
            if not math.isclose(g, statistics.mean(s["nominal_gdp"] for s in r["seats"])):
                raise ValueError(f"GDP differs from seat data at {n}")
            history.append(g)
        if n <= after or len(history) < 16:
            continue
        mean = statistics.mean(history)
        if mean > 100:
            yield n, n-len(rows), mean


def step(work, out, evaluator):
    if (out/"qualified.json").exists():
        return True
    latest = json.loads((work.parent/"verified.json").read_text())
    after = 0
    failed = sorted(out.glob("failed-*.json"))
    if failed:
        after = max(int(p.stem.split("-")[1]) for p in failed) + 15
    pending = out/"candidate.json"
    if pending.exists():
        active = json.loads(pending.read_text())
        if (out/f"failed-{active['games']:04}.json").exists():
            pending.unlink()
    pin = out/"country.pt"
    if not pending.exists():
        for n, source, screen_mean in qualifying(work, latest["games"], after, latest["initial_sha256"]):
            meta = work/f"games-{source:04}.json"
            if not meta.exists():
                continue
            src = json.loads(meta.read_text())
            if src["initial_sha256"] != latest["initial_sha256"] or src["games"] != source:
                raise ValueError("Parent identity mismatch")
            file = meta.with_suffix(".pt")
            try:
                # Same filesystem as the backup. Link before hashing so rotation is safe.
                temp = out/"country.partial"
                temp.unlink(missing_ok=True)
                os.link(file, temp)
            except FileNotFoundError:
                continue
            if sha(temp) != src["sha256"]:
                raise ValueError("Parent checkpoint failed checksum")
            temp.replace(pin)
            save(pending, dict(games=n, behavior_games=source, sha256=src["sha256"],
                               lineage=src["initial_sha256"], screen_mean=screen_mean,
                               size=24, turns=100, seeds_start=90_000_000+32*n))
            break
    if not pending.exists() or evaluator is None:
        return False
    meta = json.loads(pending.read_text())
    if sha(pin) != meta["sha256"]:
        raise ValueError("Pinned behavior checkpoint failed checksum")
    result = out/"holdout.json"
    subprocess.run([str(evaluator), str(pending), str(pin), str(result)], check=True)
    obj = json.loads(result.read_text())
    if obj["sha256"] != meta["sha256"] or obj["size"] != 24 or obj["turns"] != 100:
        raise ValueError("Holdout identity mismatch")
    if obj["seeds"] != list(range(meta["seeds_start"],meta["seeds_start"]+32)):
        raise ValueError("Holdout seed mismatch")
    seats = obj["final_nominal_gdp_by_seat"]
    if len(seats) != 32 or any(len(row) != 4 or not all(map(math.isfinite,row)) for row in seats):
        raise ValueError("Incomplete held-out worlds")
    scores = [statistics.mean(row) for row in seats]
    means = [statistics.mean(scores[:16]),statistics.mean(scores[16:])]
    record = dict(**meta, holdout_means=means, heldout=obj)
    if min(means) > 100:
        save(out/"qualified.json", dict(**record, checkpoint=pin.name,
            next_stage="train_dealer_when_v2_environment_is_validated"))
        return True
    save(out/f"failed-{meta['games']:04}.json",record)
    # Archive the verdict first; recover cleanly if interrupted between these operations.
    pending.unlink()
    return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("work", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--evaluator", type=Path)
    p.add_argument("--once", action="store_true")
    args = p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/".lock").open("a") as lock:
        fcntl.flock(lock.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                done=step(args.work,args.output,args.evaluator)
                if done or args.once:
                    return
            except (OSError,ValueError,subprocess.SubprocessError) as e:
                print(f"Gate error: {e}",flush=True)
                if args.once:
                    raise
            time.sleep(45)


if __name__=="__main__":
    main()
