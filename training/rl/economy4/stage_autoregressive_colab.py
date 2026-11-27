"""Stage exact BC corpus, code and parent checkpoint to the allocated Colab VM."""

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time

import watch_script_bc_colab as transport

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "autoregressive-bc-colab-results"
SESSION = "zg-autoregressive-bc-t4"
CHUNK = 8 * 1024 * 1024


def publish(path, payload):
    transport.publish(path, payload)


def split(path, directory):
    with path.open("rb") as stream:
        parts = []
        for n, data in enumerate(iter(lambda: stream.read(CHUNK), b"")):
            name = f"autoregressive-parent.part-{n:03}"
            dest = directory / name
            digest = hashlib.sha256(data).hexdigest()
            if not dest.exists():
                dest.write_bytes(data)
            if transport.digest(dest) != digest:
                raise ValueError("parent chunk changed")
            parts.append({"name": name, "sha256": digest})
    return parts


def main():
    transfer = OUT / "transfer"
    transfer.mkdir(parents=True, exist_ok=True)
    transport.OUT, transport.SESSION = OUT, SESSION
    previous = ROOT / "script-bc-colab-results/transfer"
    corpus = json.loads((previous / "script-transfer.json").read_text())
    verified = json.loads((ROOT / "autoregressive-bc-results/verified.json").read_text())
    parent = Path(verified["local_checkpoint"])
    if transport.digest(parent) != verified["sha256"]:
        raise ValueError("verified BC parent SHA mismatch")
    parts = split(parent, transfer)
    manifest = transfer / "autoregressive-transfer.json"
    code = ROOT / "autoregressive-bc-results/campaign-code.zip"
    publish(manifest, dict(parent_epoch=verified["epoch"], parent_sha256=verified["sha256"],
                           parent_parts=parts, corpus_parts=corpus["parts"], corpus_sha256=corpus["data_sha256"],
                           legacy_sha256=corpus["code_sha256"], campaign_sha256=transport.digest(code)))
    jobs = [(previous / row["name"], row["name"]) for row in corpus["parts"]]
    jobs += [(transfer / row["name"], row["name"]) for row in parts]
    jobs += [(manifest, manifest.name), (code, "autoregressive-campaign.zip"),
             (ROOT / "pure-script-bc/code-data/script-code.bundle", "script-code.bundle")]
    pending = [(file, name) for file, name in jobs if not (transfer/f"uploaded-{name}.json").exists()]
    total, sent = len(jobs), len(jobs)-len(pending)
    publish(OUT / "live.json", dict(state="transferring", uploaded=sent, total=total,
                                   verified_epoch=verified["epoch"], time=time.time()))
    with ThreadPoolExecutor(max_workers=3) as pool:
        work = [pool.submit(transport.upload, file, name) for file, name in pending]
        for job in as_completed(work):
            job.result()
            sent += 1
            publish(OUT/"live.json", dict(state="transferring",uploaded=sent,total=total,
                                         verified_epoch=verified["epoch"],time=time.time()))
    publish(OUT/"staged.json", dict(session=SESSION,parent_epoch=verified["epoch"],
                                    parent_sha256=verified["sha256"],time=time.time()))
    publish(OUT/"live.json", dict(state="staged",uploaded=sent,total=total,
                                  verified_epoch=verified["epoch"],time=time.time()))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
        publish(OUT/"failure.json", dict(error=type(error).__name__,message=str(error),time=time.time()))
        raise
