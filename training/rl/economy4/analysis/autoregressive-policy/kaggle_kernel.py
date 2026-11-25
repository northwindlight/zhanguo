"""Template for a finite Kaggle BC segment."""

from base64 import b64decode
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

EPOCH = None
STATE_SHA = None
SOURCES = None
LEGACY_SHA = "96decbb52664d91d9e6ff425faa87a26e4d75df10c7f15588b5f61ee02ade489"
CORPUS_SHA = "f81ff13ba26d6e0692a7d82126ed418bae3fb6a39197118a47705e000342680b"


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def single(name, parent=None):
    entries = [path for path in Path("/kaggle/input").rglob(name)
               if parent is None or path.parent.name == parent]
    if len(entries) != 1:
        raise ValueError(f"ambiguous input {name}")
    return entries[0]


def log(stage, **fields):
    print(json.dumps(dict(stage=stage, **fields)), flush=True)


def main():
    import torch

    root = Path("/kaggle/working")
    src, work, corpus = root / "src", root / "work", root / "corpus"
    work.mkdir(exist_ok=True)
    legacy, data = single("script-code.bundle"), single("script-data.bundle")
    if digest(legacy) != LEGACY_SHA or digest(data) != CORPUS_SHA:
        raise ValueError("private BC inputs changed")

    with ZipFile(legacy) as archive:
        for member in archive.namelist():
            if member.startswith("python/"):
                archive.extract(member, root / "legacy")
    with ZipFile(data) as archive:
        archive.extractall(corpus)
    with ZipFile(BytesIO(b64decode(SOURCES))) as archive:
        archive.extractall(src)
    env = dict(os.environ, PYTHONPATH=str(root / "legacy" / "python"), OPENBLAS_NUM_THREADS="1",
               OMP_NUM_THREADS="2")
    devices = torch.cuda.device_count()
    if devices not in (1, 2):
        raise RuntimeError("BC requires one or two CUDA GPUs")
    stop = min(EPOCH + 2, 10)
    log("bc_kernel_cuda", devices=[torch.cuda.get_device_name(i) for i in range(devices)],
        torch=str(torch.__version__), epoch=EPOCH, stop=stop, cpu=os.cpu_count())

    script = src / "bc_train.py"
    index = root / "index.pkl"
    subprocess.run([sys.executable, str(script), "--data", str(corpus), "--output", str(work),
                    "--write-index", str(index)], check=True, env=env)

    command = list(map(str, [sys.executable, "-u", script, "--data", corpus, "--index", index,
                            "--device", "cuda", "--precision", "fp16", "--workers", devices]))

    if EPOCH == 0:
        preflight = root / "preflight"
        check = [*command, "--output", str(preflight), "--stop-epoch", "2", "--limit", "2",
                 "--seed", "20261126", "--preflight"]
        logs = []
        with subprocess.Popen(check, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env) as process:
            for line in process.stdout:
                print(line, end="", flush=True)
                logs.append(line)
            rc = process.wait()
        if rc:
            raise RuntimeError("CUDA preflight failed")
        rows = []
        for line in logs:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        steps = [row for row in rows if row.get("stage") == "bc_step"]
        if len(steps) < 2 or not all(row["weight_update"] for row in steps):
            raise RuntimeError("CUDA preflight requires real finite optimizer updates")
        log("bc_cuda_preflight_passed", updates=len(steps), dense_and_sparse=True, devices=devices)

    resume = []
    if EPOCH:
        source = single(f"epoch-{EPOCH:02}.pt", parent="work")
        if digest(source) != STATE_SHA:
            raise ValueError("wrong resume model or optimizer")
        header = torch.load(source, map_location="cpu", weights_only=True)
        if header["epoch"] != EPOCH:
            raise ValueError("wrong resume epoch")
        del header
        log("bc_resume_verified", epoch=EPOCH, sha256=STATE_SHA)
        resume = ["--resume", str(source)]
    log("bc_production_begin", epoch=EPOCH + 1, stop=stop, world=devices)
    subprocess.run([*command, "--output", str(work), "--stop-epoch", str(stop), *resume], check=True, env=env)
    state = json.loads((work / "state.json").read_text())
    if state["epoch"] != stop or digest(work / state["checkpoint"]) != state["sha256"]:
        raise ValueError("incomplete BC segment")
    log("bc_segment_complete", epoch=stop, sha256=state["sha256"])


if __name__ == "__main__":
    main()
