"""Mirror committed PPO checkpoints from an SSH host or a local work directory."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from checkpoint import read_report, verify
from retire_weight import retire


def rsync(*args, listing=False):
    result = subprocess.run(["rsync", "-a", "-s", "--timeout=300", *args], check=True,
                            text=True, stdout=subprocess.PIPE if listing else None)
    return result.stdout


def retire_source(source, games, row):
    if ":" not in source:
        retire(Path(source), games, row["sha256"], row["initial_sha256"])
        return
    host, directory = source.split(":", 1)
    args = ["python3", "-", directory, str(games), row["sha256"], row["initial_sha256"]]
    with Path(__file__).with_name("retire_weight.py").open("rb") as script:
        subprocess.run(["ssh", "--", host, shlex.join(args)], stdin=script, check=True)


def sync(source, dest, anchor, keep_every, prune_source):
    # Take one listing so an unfinished .pt can never become a restore point.
    listing = rsync("--list-only", "--", source + "/", listing=True)
    files = {m[1] for line in listing.splitlines()
             if (m := re.search(r"\s(games-[0-9]+\.(?:json|pt))$", line))}
    reports = sorted((name for name in files if name.endswith(".json")),
                     key=lambda name: int(name[6:-5]))
    if not reports:
        return
    origin = anchor.get("initial_sha256", anchor["sha256"])
    base = anchor.get("games", 0)
    marker = dest / "verified.json"
    previous = json.loads(marker.read_text()) if marker.exists() else None
    if previous and previous["initial_sha256"] != origin:
        raise ValueError("Destination belongs to a different training run")
    current = previous["games"] if previous else base - 1
    with tempfile.TemporaryDirectory(prefix=".incoming-", dir=dest) as directory:
        stage = Path(directory)
        for name in reports:
            games = int(name[6:-5])
            if games <= current or games < base:
                continue
            rsync("--", source + "/" + name, str(stage) + "/")
            row = read_report(stage / name)
            if row["initial_sha256"] != origin or games == base and row["sha256"] != anchor["sha256"]:
                raise ValueError(f"Checkpoint lineage differs at {name}")
            pt = row["checkpoint"]
            if pt not in files:
                if name == reports[-1]:
                    raise ValueError(f"The latest published checkpoint is missing: {pt}")
                # A remote retention policy may already have retired this weight file.
                (stage / name).replace(dest / name)
                continue
            rsync("--", source + "/" + pt, str(stage) + "/")
            verify(stage / name).replace(dest / pt)
            (stage / name).replace(dest / name)
            (stage / "verified.json").write_text(json.dumps(row) + "\n")
            (stage / "verified.json").replace(marker)
            current = games
            print(f"Verified {pt} {row['sha256']}", flush=True)
    if keep_every and marker.is_file():
        # Keep both newest restore points and milestones relative to this campaign’s anchor.
        weights = sorted(dest.glob("games-*.pt"), key=lambda p: int(p.stem[6:]))
        for path in weights[:-2]:
            n = int(path.stem[6:])
            if n < base or (n - base) % keep_every == 0:
                continue
            row = read_report(path.with_suffix(".json"))
            if row["initial_sha256"] == origin and n <= current:
                if prune_source:
                    verify(path.with_suffix(".json"))
                    retire_source(source, n, row)
                path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="SSH_ALIAS:/absolute/run/work or a local work directory")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--anchor", required=True, type=Path, help="Trusted PPO batch report or accepted value.json")
    parser.add_argument("--watch", action="store_true", help="Poll every 25 seconds and retry transport failures")
    parser.add_argument("--keep-every", type=int, default=0, help="Keep local milestones and the two latest weights")
    parser.add_argument("--prune-source", action="store_true",
                        help="Also retire verified non-milestone weights on the producer")
    args = parser.parse_args()
    if args.keep_every < 0:
        parser.error("--keep-every must be non-negative")
    if args.prune_source and not args.keep_every:
        parser.error("--prune-source requires --keep-every")
    source = args.source.rstrip("/")
    dest = args.destination.resolve()
    if ":" not in source and Path(source).resolve() == dest:
        parser.error("Choose a separate backup directory")
    dest.mkdir(parents=True, exist_ok=True)
    anchor = read_report(args.anchor)
    with (dest / ".backup.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            try:
                sync(source, dest, anchor, args.keep_every, args.prune_source)
            except subprocess.CalledProcessError:
                if not args.watch:
                    raise
            if not args.watch:
                return
            time.sleep(25)


if __name__ == "__main__":
    main()
