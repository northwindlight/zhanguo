#!/bin/bash
# C++ 对 Python 参照的全量回归：规则 AI 参照 ref-*.json + 场景 sc*.json（自动交易 scH 不测：RL 不暴露）
cd -- "$(dirname -- "$0")" || exit 1
binary=${1:-./zhanguo}
fail=0
check() {  # 名 seed size
  python3 ref2txt.py refs/$1.json $2 $3 /tmp/$1.txt >/dev/null 2>&1 || { echo "$1: convert FAIL"; fail=1; return; }
  "$binary" replay /tmp/$1.txt /tmp/$1.dump >/dev/null 2>&1 || { echo "$1: replay FAIL"; fail=1; return; }
  r=$(python3 cmp.py refs/$1.json /tmp/$1.dump | tail -1); echo "$1: $r"
  echo "$r" | grep -q "bad turns=0" || fail=1
}
for f in "ref-7001-16-100-2 7001 16" "ref-7002-24-100-2 7002 24" "ref-7003-16-100-4 7003 16" "ref-7004-24-100-4 7004 24" \
         "ref-7005-16 7005 16" "ref-7005-24-150-2 7005 24" "ref-7006-28-100-3 7006 28" \
         "scA 7005 16" "scB 7005 16" "scC 7005 20" "scD 7005 16" "scE 7005 16" "scE2 7005 16" "scF 7005 16" "scG 7003 20" "scI 7003 20"; do
  set -- $f; check $1 $2 $3
done
if [ "$fail" = 0 ]; then
  echo "ALL PASS"
else
  echo "HAS FAILURES"
  exit 1
fi
