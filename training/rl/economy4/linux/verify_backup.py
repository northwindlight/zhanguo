"""Exercise resume, interrupted copies and retention without a running trainer."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from backup import sync
from checkpoint import latest, read_report


def publish(root, n, origin):
    checkpoint = root / f"games-{n:04}.pt"
    content = str(n).encode()
    checkpoint.write_bytes(content)
    row = dict(games=n, checkpoint=checkpoint.name, sha256=hashlib.sha256(content).hexdigest(),
               initial_sha256=origin, ppo=[dict(accepted=True)])
    report = checkpoint.with_suffix(".json")
    report.write_text(json.dumps(row))
    return report


def main():
    with TemporaryDirectory() as work:
        base = Path(work)
        source, dest = base / "trainer work", base / "verified"
        source.mkdir()
        dest.mkdir()
        origin = hashlib.sha256(b"warm").hexdigest()
        seed = publish(source, 4, origin)
        anchor = read_report(seed)
        for n in (8, 12, 16, 20):
            publish(source, n, origin)
        sync(str(source), dest, anchor, 16, True)
        expected = {"games-0004.pt", "games-0016.pt", "games-0020.pt"}
        for p in (source, dest):
            assert {f.name for f in p.glob("*.pt")} == expected
            assert len(list(p.glob("games-*.json"))) == 5
        assert latest(dest, seed).name == "games-0020.pt"
        publish(source, 24, origin)
        (source / "games-0024.pt").write_bytes(b"corrupt")
        try:
            sync(str(source), dest, anchor, 16, True)
        except ValueError as error:
            assert "checksum" in str(error)
        else:
            raise AssertionError("A corrupt checkpoint was published")
        assert not (dest / "games-0024.json").exists()
        assert json.loads((dest / "verified.json").read_text())["games"] == 20
        publish(source, 24, origin)
        sync(str(source), dest, anchor, 16, True)
        assert latest(dest, seed).name == "games-0024.pt"
        other = base / "other"
        other.mkdir()
        wrong_seed = publish(other, 4, "different")
        try:
            latest(dest, wrong_seed)
        except ValueError:
            pass
        else:
            raise AssertionError("A different run was accepted")


if __name__ == "__main__":
    main()
