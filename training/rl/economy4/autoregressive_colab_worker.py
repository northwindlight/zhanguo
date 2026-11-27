"""Run one verified autoregressive BC continuation on the Colab VM."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from zipfile import ZipFile

CONTENT = Path("/content")
ROOT = CONTENT / "zhanguo-autoregressive-bc"
WORK = ROOT / "work"
SRC = ROOT / "src"
CORPUS = ROOT / "corpus"
LEGACY = ROOT / "legacy"
ENV = dict(os.environ, OMP_NUM_THREADS="2", OPENBLAS_NUM_THREADS="1",
           PYTHONPATH=str(LEGACY / "python"))


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify(path, expected):
    if digest(path) != expected:
        raise ValueError(f"Checksum mismatch for {path.name}")
    return path


def write(path, row):
    temp = path.with_suffix(".partial")
    temp.write_text(json.dumps(row))
    temp.replace(path)


def assemble(name, parts, expected):
    path = CONTENT / name
    if not path.exists():
        temp = path.with_suffix(".partial")
        with temp.open("wb") as output:
            for item in parts:
                part = verify(CONTENT / item["name"], item["sha256"])
                with part.open("rb") as source:
                    shutil.copyfileobj(source, output)
        verify(temp, expected)
        temp.replace(path)
    return verify(path, expected)


def run(args, name):
    with (WORK / name).open("a") as stream:
        subprocess.run(list(map(str, args)), env=ENV, stdout=stream,
                       stderr=subprocess.STDOUT, check=True)


def prepare(config, transfer):
    corpus = assemble("script-data.bundle", transfer["corpus_parts"], transfer["corpus_sha256"])
    parent = assemble(f"autoregressive-parent-{transfer['parent_epoch']:02}.pt",
                      transfer["parent_parts"], transfer["parent_sha256"])
    campaign = verify(CONTENT / "autoregressive-campaign.zip", transfer["campaign_sha256"])
    legacy = verify(CONTENT / "script-code.bundle", transfer["legacy_sha256"])
    with ZipFile(campaign) as archive:
        archive.extractall(SRC)
    with ZipFile(corpus) as archive:
        archive.extractall(CORPUS)
    with ZipFile(legacy) as archive:
        for name in archive.namelist():
            if name.startswith("python/"):
                archive.extract(name, LEGACY)
    shutil.copyfile(verify(CONTENT / "autoregressive-colab-bc-train.py", config["trainer_sha256"]),
                    SRC / "bc_train.py")
    return parent


def evaluate(checkpoint, config):
    code = ROOT / "thought-code"
    build = ROOT / "thought-build"
    bundle = verify(CONTENT / "autoregressive-thought-eval.zip", config["thought_sha256"])
    with ZipFile(bundle) as archive:
        archive.extractall(code)
    run(["cmake", "-S", code / "cpp", "-B", build, "-DCMAKE_BUILD_TYPE=Release",
         "-DZHANGUO_EVAL_BRIDGE=ON", "-DBUILD_TESTING=OFF"], "evaluation-build.log")
    run(["cmake", "--build", build, "--target", "zhanguo_eval", "-j2"], "evaluation-build.log")
    run([sys.executable, "-u", code / "analysis/autoregressive-policy/evaluate_thought.py",
         "--library", build / "libzhanguo_eval.so", "--checkpoint", checkpoint,
         "--output", ROOT / "evaluation", "--maps", 4, "--warmup-maps", 8,
         "--size", 24, "--turns", 100, "--device", "cuda", "--workers", 1,
         "--threads", 1], "evaluation.log")


def main(mode):
    import torch

    config = json.loads((CONTENT / "autoregressive-colab-config.json").read_text())
    transfer = json.loads((CONTENT / "autoregressive-transfer.json").read_text())
    if torch.cuda.device_count() != 1 or "T4" not in torch.cuda.get_device_name(0):
        raise RuntimeError("Expected one Tesla T4")
    parent_epoch = transfer["parent_epoch"]
    index = ROOT / "index.pkl"
    base = [sys.executable, "-u", SRC / "bc_train.py", "--data", CORPUS]
    command = [*base, "--index", index, "--device", "cuda", "--precision", "fp16",
               "--workers", 1, "--accumulate", 2]
    if mode == "preflight":
        parent = prepare(config, transfer)
        parent_state = torch.load(parent, map_location="cpu", weights_only=True)
        if parent_state["epoch"] != parent_epoch:
            raise ValueError("Verified parent epoch mismatch")
        del parent_state
        run([*base, "--output", WORK, "--write-index", index], "inventory.log")
        log = WORK / "preflight.log"
        if log.exists():
            log.unlink()
        run([*command, "--resume", parent, "--output", ROOT / "preflight", "--stop-epoch",
             parent_epoch + 1, "--limit", 2, "--preflight"], log.name)
        rows = []
        for line in log.read_text().splitlines():
            if line.startswith("{"):
                rows.append(json.loads(line))
        steps = [row for row in rows if row.get("stage") == "bc_step"]
        if len(steps) != 1 or not steps[0]["weight_update"] or steps[0]["grad_norm"] is None:
            raise RuntimeError("Preflight failed to make a finite optimizer update")
        write(WORK / "preflight.json", dict(passed=True, parent_epoch=parent_epoch,
              sha256=transfer["parent_sha256"], gpu=torch.cuda.get_device_name(0),
              torch=str(torch.__version__), step=steps[0]))
        return
    preflight = json.loads((WORK / "preflight.json").read_text())
    if not preflight["passed"] or preflight["sha256"] != transfer["parent_sha256"]:
        raise RuntimeError("Resume identity differs from preflight")
    parent = verify(CONTENT / f"autoregressive-parent-{parent_epoch:02}.pt",
                    transfer["parent_sha256"])
    resume_epoch = parent_epoch
    if (WORK / "state.json").exists():
        state = json.loads((WORK / "state.json").read_text())
        parent = verify(WORK / state["checkpoint"], state["sha256"])
        resume_epoch = state["epoch"]
    if resume_epoch < 10:
        run([*command, "--resume", parent, "--output", WORK, "--stop-epoch", 10], "bc.log")
    state = json.loads((WORK / "state.json").read_text())
    if state["epoch"] != 10 or state["preflight"]:
        raise ValueError("BC continuation did not reach epoch 10")
    checkpoint = verify(WORK / state["checkpoint"], state["sha256"])
    evaluate(checkpoint, config)
    write(WORK / "done.json", dict(epoch=10, sha256=state["sha256"]))


if __name__ == "__main__":
    WORK.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[1]
    with (ROOT / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            main(mode)
        except Exception as error:
            write(WORK / (mode + "-failure.json"), dict(error=type(error).__name__, message=str(error)))
            raise
