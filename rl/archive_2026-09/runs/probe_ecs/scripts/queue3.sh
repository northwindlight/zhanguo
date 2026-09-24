#!/usr/bin/env bash
# Queue 3: after queue2, gradient-noise probes (BC, ppo_v10 ckpt_5), then teacher ceiling.
set -u
cd "$HOME/zhanguo"
LOGDIR=rl/runs/probe_ecs
until grep -q 'queue2 finished' "$LOGDIR/queue.log" 2>/dev/null; do sleep 30; done
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
       VECLIB_MAXIMUM_THREADS=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PYTHONPATH=.
PY="$HOME/.venv/bin/python"

job() {  # tag script args...
  local tag="$1"; shift
  echo "[$(date -u '+%F %T UTC')] start $tag $*" | tee -a "$LOGDIR/queue.log"
  local t0=$SECONDS
  { echo "# cmd: $PY -u $*"; "$PY" -u "$@"; } > "$LOGDIR/$tag.log" 2>&1
  echo "[$(date -u '+%F %T UTC')] done  $tag rc=$? $((SECONDS - t0))s" | tee -a "$LOGDIR/queue.log"
}

job gradnoise_bc_ep100_12x200   experiments/probe_grad_noise.py rl/runs/bc_cont/ep100.pt rl/runs/ppo_v10/ckpt_5.pt 12 200 100
job gradnoise_v10_ckpt5_12x200  experiments/probe_grad_noise.py rl/runs/ppo_v10/ckpt_5.pt rl/runs/ppo_v10/ckpt_5.pt 12 200 100
job teacher_ceiling_4x70        experiments/probe_teacher_ceiling.py 4 70
job teacher_ceiling_4x200       experiments/probe_teacher_ceiling.py 4 200

echo "[$(date -u '+%F %T UTC')] queue3 finished" | tee -a "$LOGDIR/queue.log"
