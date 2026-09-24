#!/usr/bin/env bash
# Sequential probe_validity queue for ECS; mirrors rl/run_ecs.sh thread/log conventions.
set -u
cd "$HOME/zhanguo"
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
       VECLIB_MAXIMUM_THREADS=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PYTHONPATH=.
PY="$HOME/.venv/bin/python"
LOGDIR="rl/runs/probe_ecs"
mkdir -p "$LOGDIR"

run() {  # tag ckpt eps turns
  local log="$LOGDIR/validity_$1_$3x$4.log"
  echo "[$(date -u '+%F %T UTC')] start $1 $2 $3x$4" | tee -a "$LOGDIR/queue.log"
  local t0=$SECONDS
  { echo "# cmd: $PY -u experiments/probe_validity.py $2 $3 $4"
    echo "# md5: $(md5sum "$2")"
    "$PY" -u experiments/probe_validity.py "$2" "$3" "$4"; } > "$log" 2>&1
  local rc=$?
  echo "[$(date -u '+%F %T UTC')] done  $1 $3x$4 rc=$rc $((SECONDS - t0))s" | tee -a "$LOGDIR/queue.log"
}

run sanity_ppo_v6_ckpt30 rl/runs/bc/ckpt_30.pt 8 30

run bc_ep100     rl/runs/bc_cont/ep100.pt  8 60
run v10_ckpt5    rl/runs/ppo_v10/ckpt_5.pt  8 60
run v10_ckpt35   rl/runs/ppo_v10/ckpt_35.pt 8 60

run bc_ep100     rl/runs/bc_cont/ep100.pt  8 200
run v10_ckpt5    rl/runs/ppo_v10/ckpt_5.pt  8 200
run v10_ckpt35   rl/runs/ppo_v10/ckpt_35.pt 8 200

echo "[$(date -u '+%F %T UTC')] queue finished" | tee -a "$LOGDIR/queue.log"
