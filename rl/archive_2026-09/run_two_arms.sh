#!/usr/bin/env bash
T=/home/northwind/.claude/jobs/556a2faa/tmp
R=/home/northwind/projects/python/zhanguo
cd "$T/wt_eval"
CK=("$T/bc_ep400.pt" "$R/rl/runs/gpu_pull/ppo_candx/ckpt_115.pt" \
    "$R/rl/runs/gpu_pull/ppo_candx/ckpt_150.pt" "$R/rl/runs/gpu_pull/ppo_candx/ckpt_170.pt")
echo "===== 贪心臂（--det --ent）====="
"$R/.venv/bin/python" -u experiments/probe_invalid_rate.py "${CK[@]}" --seed 900000 --reps 1 --det --ent
echo
echo "===== 采样臂（--ent，同一批状态上量熵）====="
"$R/.venv/bin/python" -u experiments/probe_invalid_rate.py "${CK[@]}" --seed 900000 --reps 1 --ent
