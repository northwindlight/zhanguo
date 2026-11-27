"""Validate the published metadata before using or archiving a checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path


def read_report(path):
    row = json.loads(path.read_text())
    if path.name == "value.json":
        if row.get("accepted") is not True or row.get("checkpoint") != "value-warm.pt":
            raise ValueError(f"Value warmup was not accepted: {path}")
    else:
        name = f"games-{row['games']:04d}"
        if (row["games"] < 1 or path.name != name + ".json" or row.get("checkpoint") != name + ".pt"
                or not row.get("initial_sha256") or not any(step["accepted"] for step in row["ppo"])):
            raise ValueError(f"Invalid PPO batch: {path}")
    return row


def verify(path):
    row = read_report(path)
    checkpoint = path.parent / row["checkpoint"]
    with checkpoint.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != row["sha256"]:
            raise ValueError(f"Checkpoint checksum mismatch: {checkpoint}")
    return checkpoint


def latest(directory, seed):
    reports = sorted(directory.glob("games-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    if not reports:
        if seed is None:
            raise ValueError("The first run needs value.json or a verified games-NNNN.json and its .pt")
        return verify(seed)
    if seed is not None:
        first = read_report(seed)
        last = read_report(reports[-1])
        if (first.get("initial_sha256", first["sha256"]) != last["initial_sha256"]
                or first.get("games", 0) > last["games"]
                or first.get("games") == last["games"] and first["sha256"] != last["sha256"]):
            raise ValueError("The seed and output directory refer to different training checkpoints")
    return verify(reports[-1])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("seed", type=Path, nargs="?")
    args = parser.parse_args()
    print(latest(args.directory, args.seed).resolve())
