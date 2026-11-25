"""Private Kaggle worker for the verified BC10 thought-budget comparison."""

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
CHECKPOINT_SHA = None
LEGACY_SHA = "96decbb52664d91d9e6ff425faa87a26e4d75df10c7f15588b5f61ee02ade489"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def exact_input(name, expected, parent=None):
    matches = [p for p in Path("/kaggle/input").rglob(name)
               if parent is None or p.parent.name == parent]
    if len(matches) != 1 or digest(matches[0]) != expected:
        raise ValueError(f"missing, duplicate or wrong input: {name}")
    return matches[0]


def main():
    import torch

    parent = exact_input("epoch-10.pt", CHECKPOINT_SHA, "work")
    legacy = exact_input("script-code.bundle", LEGACY_SHA)
    devices = torch.cuda.device_count()
    if devices not in (1, 2):
        raise RuntimeError("thought evaluation requires CUDA")
    with tempfile.TemporaryDirectory(prefix="zhanguo-thought-") as temporary:
        root = Path(temporary)
        code = root / "code"
        with ZipFile(BytesIO(b64decode(SOURCES))) as source:
            source.extractall(code)
        with ZipFile(legacy) as source:
            for name in source.namelist():
                if name.startswith("python/"):
                    source.extract(name, root)
        build = root / "build"
        subprocess.run(["cmake", "-S", str(code / "cpp"), "-B", str(build),
                        "-DCMAKE_BUILD_TYPE=Release", "-DZHANGUO_EVAL_BRIDGE=ON", "-DBUILD_TESTING=OFF"], check=True)
        subprocess.run(["cmake", "--build", str(build), "--target", "zhanguo_eval", "-j3"], check=True)
        print(json.dumps(dict(stage="thought_eval_cuda_ready", checkpoint=CHECKPOINT_SHA,
                              devices=[torch.cuda.get_device_name(i) for i in range(devices)])), flush=True)
        env = dict(os.environ, PYTHONPATH=str(root / "python"), OPENBLAS_NUM_THREADS="1")
        command = [sys.executable, "-u", str(code / "analysis/autoregressive-policy/evaluate_thought.py"),
                   "--library", str(build / "libzhanguo_eval.so"), "--checkpoint", str(parent),
                   "--output", "/kaggle/working/work", "--maps", "4", "--warmup-maps", "8",
                   "--size", "24", "--turns", "100", "--device", "cuda", "--workers", str(devices), "--threads", "1"]
        subprocess.run(command, check=True, env=env)


if __name__ == "__main__":
    main()
