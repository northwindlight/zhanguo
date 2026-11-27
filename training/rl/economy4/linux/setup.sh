#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 || (${2:-} != '' && ${2:-} != --teacher) ]]; then
    echo "Usage: setup.sh RUN_DIR [--teacher]" >&2
    exit 2
fi
src=$(cd "$(dirname "$0")/.." && pwd)
build=$1/build
targets=(zhanguo_eval)
options=(-DZHANGUO_EVAL_BRIDGE=ON -DBUILD_TESTING=OFF -DCMAKE_BUILD_TYPE=Release)
if [[ ${2:-} == --teacher ]]; then
    py=${PPO_PYTHON:-python3}
    wheel=$("$py" -c 'import pathlib,torch; print(pathlib.Path(torch.__file__).parent)')
    abi=$("$py" -c 'import torch; print(int(torch.compiled_with_cxx11_abi()))')
    options+=(-DZHANGUO_TORCH=ON "-DZHANGUO_TORCH_WHEEL=$wheel" "-DZHANGUO_TORCH_ABI=$abi")
    targets+=(zhanguo_script)
fi
cmake -S "$src/cpp" -B "$build" "${options[@]}"
cmake --build "$build" --parallel "${BUILD_JOBS:-4}" --target "${targets[@]}"
