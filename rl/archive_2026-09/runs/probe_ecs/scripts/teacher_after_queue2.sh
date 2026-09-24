#!/usr/bin/env bash
set -u
cd "$HOME/zhanguo"
LOGDIR=rl/runs/probe_ecs
until grep -q 'queue2 finished' "$LOGDIR/queue.log" 2>/dev/null; do sleep 30; done
export OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
       VECLIB_MAXIMUM_THREADS=1 PYTHONIOENCODING=utf-8 PYTHONUNBUFFERED=1 PYTHONPATH=.
for args in "4 70" "4 200"; do
  tag="teacher_ceiling_${args// /x}"
  echo "[$(date -u '+%F %T UTC')] start $tag" | tee -a "$LOGDIR/queue.log"
  t0=$SECONDS
  { echo "# cmd: python -u experiments/probe_teacher_ceiling.py $args"
    "$HOME/.venv/bin/python" -u experiments/probe_teacher_ceiling.py $args; } > "$LOGDIR/$tag.log" 2>&1
  echo "[$(date -u '+%F %T UTC')] done  $tag rc=$? $((SECONDS - t0))s" | tee -a "$LOGDIR/queue.log"
done
echo "[$(date -u '+%F %T UTC')] teacher queue finished" | tee -a "$LOGDIR/queue.log"
