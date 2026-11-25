"""Private GPU worker for one adaptive PPO segment."""
from base64 import b64decode
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile

SOURCES = None
PARENT_SHA = None
PARENT_NAME = None
START = None
STOP = None
LEGACY_SHA = "96decbb52664d91d9e6ff425faa87a26e4d75df10c7f15588b5f61ee02ade489"


def digest(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f,"sha256").hexdigest()


def verified_input(name, sha, parent=None):
    paths = [p for p in Path("/kaggle/input").rglob(name) if parent is None or p.parent.name == parent]
    if len(paths) != 1 or digest(paths[0]) != sha:
        raise ValueError(f"Invalid authorized input {name}")
    return paths[0]


def main():
    import torch

    parent = verified_input(PARENT_NAME, PARENT_SHA, "work")
    legacy = verified_input("script-code.bundle", LEGACY_SHA)
    workers = torch.cuda.device_count()
    if workers not in (1, 2):
        raise RuntimeError("Adaptive PPO requires one or two CUDA devices")
    root = Path(tempfile.mkdtemp(prefix="zhanguo-ppo-"))
    work = Path("/kaggle/working/work")
    work.mkdir(exist_ok=True)
    with ZipFile(BytesIO(b64decode(SOURCES))) as archive:
        archive.extractall(root / "src")
    with ZipFile(legacy) as archive:
        for name in archive.namelist():
            if name.startswith("python/"):
                archive.extract(name, root / "legacy")
    source = root / "src"
    build = root / "build"
    subprocess.run(["cmake", "-S", str(source/"cpp"), "-B", str(build), "-DCMAKE_BUILD_TYPE=Release",
                    "-DZHANGUO_EVAL_BRIDGE=ON", "-DBUILD_TESTING=OFF"], check=True)
    subprocess.run(["cmake", "--build", str(build), "--target", "zhanguo_eval", "-j3"], check=True)
    env = dict(os.environ, PYTHONPATH=str(root/"legacy/python"), OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="2")
    driver = source / "analysis/autoregressive-policy/thought_ppo.py"
    common = [sys.executable, "-u", driver, "--library", build/"libzhanguo_eval.so",
              "--checkpoint", parent, "--workers", workers]
    print(json.dumps(dict(stage="ppo_cuda_ready", parent_sha256=PARENT_SHA,
                          devices=[torch.cuda.get_device_name(i) for i in range(workers)])), flush=True)
    if START == 0:
        preflight = root / "preflight"
        subprocess.run(list(map(str, [*common, "--output", preflight, "--stop-games", workers,
                                      "--size", 24, "--turns", 2, "--samples-per-seat", 2, "--ppo-epochs", 1])),
                       check=True, env=env)
        proof = json.loads((preflight/"state.json").read_text())
        if not any(row["accepted"] and row["actor_norm"] > 0 and row["critic_norm"] > 0 for row in proof["ppo"]):
            raise RuntimeError("CUDA preflight did not update actor and critic")
        before = torch.load(parent, map_location="cpu", weights_only=True, mmap=True)["model"]
        after = torch.load(preflight/proof["checkpoint"], map_location="cpu", weights_only=True, mmap=True)["model"]
        for group in ("thought.", "action_query.", "value."):
            if not any(not torch.equal(x, after[k]) for k, x in before.items() if k.startswith(group)):
                raise RuntimeError(f"CUDA preflight left {group} weights untouched")
        print(json.dumps(dict(stage="ppo_cuda_preflight_passed", games=workers, ppo=proof["ppo"])), flush=True)
    print(json.dumps(dict(stage="ppo_production_begin", start=START, stop=STOP)), flush=True)
    result = subprocess.run(list(map(str, [*common, "--output", work, "--stop-games", STOP])), env=env)
    manifest = work/"state.json"
    if not manifest.exists():
        result.check_returncode()
        raise ValueError("Adaptive PPO did not publish a checkpoint")
    state = json.loads(manifest.read_text())
    if not START < state["games"] <= STOP or digest(work/state["checkpoint"]) != state["sha256"]:
        raise ValueError("Incomplete adaptive PPO boundary")
    complete = result.returncode == 0 and state["games"] == STOP
    segment = dict(complete=complete, exit_code=result.returncode, games=state["games"], sha256=state["sha256"])
    (work/"segment.json").write_text(json.dumps(segment))
    if not complete:
        print(json.dumps(dict(stage="ppo_segment_interrupted", **segment)), flush=True)


if __name__ == "__main__":
    main()
