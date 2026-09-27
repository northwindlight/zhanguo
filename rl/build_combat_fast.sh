#!/usr/bin/env bash
# 编译**可选**加速扩展 `rl/_combat_fast.c`。
#
#   bash rl/build_combat_fast.sh
#
# ★ 编不出来**不影响功能** —— `combat_probs` 里 import 失败就整条回落纯 Python。
#   所以这个脚本不需要进 CI，也不用保证在任何机器上都能编。
# ★ 产物是 `rl/_combat_fast<EXT_SUFFIX>`（如 `.cpython-313-aarch64-linux-gnu.so`），
#   **按架构各编各的**：Pi(aarch64) 与 ECS(amd64) 的 .so 不能混用。
# ★ 不加 `-march=native`：产物可能被 rsync 到别的机器，那会直接 SIGILL。
#   想榨最后一点：`CFLAGS="-O3 -march=native" bash rl/build_combat_fast.sh`
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

PY="${ZHANGUO_PY:-}"
if [ -z "$PY" ]; then
  for c in "$ROOT/.venv/bin/python" "$HOME/.venv/bin/python" /root/venv/bin/python; do
    [ -x "$c" ] && PY="$c" && break
  done
fi
[ -n "$PY" ] || PY="$(command -v python3)"
echo "python: $PY"

INC="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["include"])')"
EXT="$("$PY" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")')"
OUT="$HERE/_combat_fast$EXT"
CC="${CC:-cc}"
# ★★ `-ffp-contract=off` **不能省**：aarch64 上 GCC 默认 `-ffp-contract=fast`，
#   会把 `er += p * (1.0 + sr)` 这类式子编成 **FMA**（**一次**舍入），
#   而 CPython 那边是"先乘再乘、两次舍入" ⇒ 最后 1~2 个 ulp 就对不上了。
#   实测：不加这个开关，8 个局面里 5 个的 `e_loss`/`e_rounds` 差 1 ulp
#   （`dist` 全同，只有 `er`/`ehp` 这种累加量漂）。
# shellcheck disable=SC2086
${CC} ${CFLAGS:--O2} -ffp-contract=off -fPIC -shared -o "$OUT" "$HERE/_combat_fast.c" -I"$INC"

echo "编好 → $OUT"
"$PY" - <<'PYEOF'
import importlib, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import rl.combat_probs as CP
print("_FAST =", "可用" if CP._FAST is not None else f"**不可用**（{CP._FAST_ERR}）")
PYEOF
