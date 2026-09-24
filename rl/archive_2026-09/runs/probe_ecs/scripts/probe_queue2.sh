#!/usr/bin/env bash
# Queue 2: ratio/drift probe first, then the 8x200 validity runs.
set -u
cd "$HOME/zhanguo"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
       VECLIB_MAXIMUM_THREADS=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PYTHONPATH=.
PY="$HOME/.venv/bin/python"
LOGDIR="rl/runs/probe_ecs"
mkdir -p "$LOGDIR"

job() {  # tag script args...
  local tag="$1"; shift
  local log="$LOGDIR/$tag.log"
  echo "[$(date -u '+%F %T UTC')] start $tag $*" | tee -a "$LOGDIR/queue.log"
  local t0=$SECONDS
  { echo "# cmd: $PY -u $*"
    "$PY" -u "$@"; } > "$log" 2>&1
  local rc=$?
  echo "[$(date -u '+%F %T UTC')] done  $tag rc=$rc $((SECONDS - t0))s" | tee -a "$LOGDIR/queue.log"
}

job ratio_drift_ckpt5_to_35_2x200 experiments/probe_ratio_drift.py rl/runs/ppo_v10/ckpt_5.pt rl/runs/ppo_v10/ckpt_35.pt 2 200 960 32
job ratio_s0_ckpt35_2x200         experiments/probe_ratio_drift.py rl/runs/ppo_v10/ckpt_35.pt - 2 200 960 32

job validity_bc_ep100_8x200   experiments/probe_validity.py rl/runs/bc_cont/ep100.pt 8 200
job validity_v10_ckpt5_8x200  experiments/probe_validity.py rl/runs/ppo_v10/ckpt_5.pt 8 200
job validity_v10_ckpt35_8x200 experiments/probe_validity.py rl/runs/ppo_v10/ckpt_35.pt 8 200

echo "[$(date -u '+%F %T UTC')] queue2 finished" | tee -a "$LOGDIR/queue.log"
