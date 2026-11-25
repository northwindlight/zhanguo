"""Build the native sampler using the CUDA-enabled PyTorch wheel on an L4 runtime."""

import argparse
import os
from pathlib import Path
import subprocess

import torch


def build(source, output, jobs):
    torch_root = Path(torch.__file__).resolve().parent
    abi = int(torch.compiled_with_cxx11_abi())
    subprocess.run([
        "cmake", "-S", str(source), "-B", str(output), "-DCMAKE_BUILD_TYPE=Release",
        "-DZHANGUO_TORCH=ON", "-DBUILD_TESTING=OFF",
        f"-DZHANGUO_TORCH_WHEEL={torch_root}", f"-DZHANGUO_TORCH_ABI={abi}"
    ], check=True)
    subprocess.run(["cmake", "--build", str(output), "--target", "zhanguo_torch", "--parallel", str(jobs)], check=True)
    return output / "zhanguo_torch"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    p.add_argument("--build", type=Path, default=Path("build-colab"))
    p.add_argument("--jobs", type=int, default=min(4, len(os.sched_getaffinity(0))))
    args = p.parse_args()
    print(f"PyTorch {torch.__version__}, CUDA {torch.version.cuda}, CPUs {len(os.sched_getaffinity(0))}", flush=True)
    if not torch.cuda.is_available():
        raise RuntimeError("the Colab runtime must have a CUDA-enabled PyTorch wheel and GPU")
    print(torch.cuda.get_device_properties(0), flush=True)
    print(build(args.source.resolve(), args.build.resolve(), args.jobs), flush=True)


if __name__ == "__main__":
    main()
