#!/bin/bash
# 回归：全部参照/场景对局逐一回放，打印一行结论
R=${1:-$HOME/zhanguo-rl}
for f in "ref-7005-16 7005 16" "ref-7001-16-100-2 7001 16" "ref-7002-24-100-2 7002 24" "ref-7003-16-100-4 7003 16" "ref-7004-24-100-4 7004 24" "ref-7005-24-150-2 7005 24" "ref-7006-28-100-3 7006 28" "scA 7005 16" "scB 7005 16" "scC 7005 20" "scD 7005 16" "scE 7005 16" "scE2 7005 16" "scF 7005 16" "scG 7003 20" "scH 7005 16" "scI 7003 20"; do
  set -- $f
  out=$(python3 replay2.py $R $1.json $2 $3 1 2>&1)
  echo "$1: first_bad=$(echo "$out" | grep -oP 'first_bad_turn \K\S+') action_mismatch=$(echo "$out" | grep -oP 'success mismatches \K\d+') $(echo "$out" | grep -q Traceback && echo CRASH)"
done
