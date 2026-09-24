#!/usr/bin/env bash
T=/home/northwind/.claude/jobs/556a2faa/tmp
R=/home/northwind/projects/python/zhanguo
cd "$T/wt_eval"
CK="$R/rl/runs/gpu_pull/ppo_exec/ckpt_185.pt"
echo "===== β=0 ====="
timeout 1200 $R/.venv/bin/python -u experiments/probe_invalid_rate.py "$CK" --seed 900000 --reps 1
echo "===== β=1（900s 超时，防它在这儿也卡）====="
timeout 900 $R/.venv/bin/python -u experiments/probe_invalid_rate.py "$CK" --seed 900000 --reps 1 --beta 1.0 \
  || echo "★★ β=1 超时未完成 —— 就是它卡"
