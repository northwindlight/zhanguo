"""Retire a mirrored, non-milestone weight on its producer. Invoked by backup.py."""
import hashlib
import json
from pathlib import Path
import sys


def retire(directory, games, digest, origin):
    path = directory / f"games-{games:04d}.pt"
    if not path.exists():
        return
    report = json.loads(path.with_suffix(".json").read_text())
    if (report["initial_sha256"] != origin or report["sha256"] != digest
            or report["checkpoint"] != path.name or report["games"] != games):
        raise ValueError("Source checkpoint identity changed")
    newer = 0
    for p in directory.glob("games-*.pt"):
        if int(p.stem[6:]) > games and p.with_suffix(".json").is_file():
            row = json.loads(p.with_suffix(".json").read_text())
            if row["initial_sha256"] == origin:
                newer += 1
                if newer == 2:
                    break
    if newer < 2:
        raise ValueError("Source needs two newer committed checkpoints")
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
            raise ValueError("Source checkpoint checksum changed")
    path.unlink()


if __name__ == "__main__":
    directory, games, digest, origin = sys.argv[1:]
    retire(Path(directory), int(games), digest, origin)
