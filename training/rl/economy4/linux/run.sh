#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 || $# -gt 2 ]]; then
    echo "Usage: run.sh RUN_DIR [SEED_REPORT]" >&2
    exit 2
fi
src=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$1/work"
run=$(cd "$1" && pwd)
exec 9>"$run/.train.lock"
flock -n 9 || { echo "PPO is already running in $run" >&2; exit 1; }
py=${PPO_PYTHON:-python3}
parent=$("$py" "$src/linux/checkpoint.py" "$run/work" "${@:2}")
export PYTHONPATH="$src/analysis/dynamic-turn-dual-ppo-20261124/reference/python${PYTHONPATH:+:$PYTHONPATH}"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2
n=${PPO_GPUS:-$("$py" -c 'import torch; print(min(2, torch.cuda.device_count()))')}
parallel=(--batch-inference --rollouts-per-device 2 --cuda-graph-history)
case "$n" in
    2) parallel+=(--distributed-update) ;;
    1) ;;
    *) echo "Expected one or two visible CUDA devices, got $n" >&2; exit 1 ;;
esac
exec "$py" -u "$src/analysis/autoregressive-policy/thought_ppo.py" \
    --checkpoint "$parent" --library "$run/build/libzhanguo_eval.so" --output "$run/work" \
    --device cuda --workers "$n" "${parallel[@]}" --threads 2 --stop-games "${PPO_TARGET:-2000}"
