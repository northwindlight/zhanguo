#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    printf 'Usage: bash %s HARDWARE_VULKAN_URI [SOFTWARE_VULKAN_URI]\n' "$0" >&2
    exit 2
fi

source_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
work_dir="$(mktemp -d -t iree-layernorm.XXXXXX)"
cd "$work_dir"
printf 'Artifacts: %s\n' "$work_dir"

python3 - <<'PY'
import numpy as np

x = ((np.arange(384, dtype=np.float32) * 7) % 31 - 15).reshape(1, 2, 192)
centered = x - x.mean(axis=-1, keepdims=True)
expected = centered / np.sqrt((centered * centered).mean(axis=-1, keepdims=True) + np.float32(1e-5))
np.save("input.npy", x)
np.save("expected.npy", expected)
PY

common=(--iree-input-type=torch --iree-opt-level=O0)
iree-compile "$source_dir/layernorm.mlir" "${common[@]}" \
    --iree-hal-target-device=local --iree-hal-local-target-device-backends=llvm-cpu \
    --iree-llvmcpu-target-cpu=host -o cpu.vmfb
iree-compile "$source_dir/layernorm.mlir" "${common[@]}" \
    --iree-hal-target-device=vulkan -o generic.vmfb
mkdir spirv
iree-compile "$source_dir/layernorm.mlir" "${common[@]}" \
    --iree-hal-target-device=vulkan --iree-vulkan-target=adreno \
    "--iree-hal-dump-executable-binaries-to=$work_dir/spirv" -o adreno.vmfb

check() {
    local module="$1" device="$2" label="$3"
    iree-run-module --device="$device" --module="$module.vmfb" --function=main \
        --input=@input.npy --output="@$label.npy"
    python3 - "$label" <<'PY'
import sys
import numpy as np

label = sys.argv[1]
got = np.load(label + ".npy")
reference = np.load("expected.npy")
assert got.shape == reference.shape
error = np.abs(got - reference)
print(f"{label}: max_abs={error.max():.9g}, finite={np.isfinite(got).all()}")
PY
}

check cpu local-task cpu
check generic "$1" generic-hardware
check adreno "$1" adreno-hardware
if [[ $# -eq 2 ]]; then
    check generic "$2" generic-software
    check adreno "$2" adreno-software
fi

if [[ -n "${ADRENO_SUBGROUP_SIZE:-}" ]]; then
    [[ "$ADRENO_SUBGROUP_SIZE" =~ ^[0-9]+$ ]]
    iree-compile "$source_dir/layernorm.mlir" "${common[@]}" \
        --iree-hal-target-device=vulkan --iree-vulkan-target=adreno \
        --compile-to=executable-sources -o codegen-input.mlir
    python3 - "$ADRENO_SUBGROUP_SIZE" <<'PY'
from pathlib import Path
import sys

text = Path("codegen-input.mlir").read_text()
old = "subgroup_size_choices = [64]"
assert old in text, "The compiler target profile differs from the one tested"
Path("codegen-adjusted.mlir").write_text(text.replace(old, f"subgroup_size_choices = [{sys.argv[1]}]"))
PY
    iree-compile codegen-adjusted.mlir --compile-from=executable-sources --iree-opt-level=O0 \
        --iree-hal-target-device=vulkan --iree-vulkan-target=adreno -o adjusted.vmfb
    check adjusted "$1" "adreno-subgroup-$ADRENO_SUBGROUP_SIZE"
fi

if [[ "${ADRENO_REQUIRE_SUBGROUP64:-0}" == 1 ]]; then
    iree-compile "$source_dir/layernorm.mlir" "${common[@]}" \
        --iree-hal-target-device=vulkan --iree-vulkan-target=adreno \
        --compile-to=executable-configurations -o configured.mlir
    python3 - <<'PY'
from pathlib import Path

text = Path("configured.mlir").read_text()
old = "workgroup_size = [192, 1, 1]>"
assert text.count(old) == 2
Path("configured-required64.mlir").write_text(text.replace(old, "workgroup_size = [192, 1, 1] subgroup_size = 64>"))
PY
    mkdir spirv-required64
    iree-compile configured-required64.mlir --compile-from=executable-configurations \
        --iree-opt-level=O0 --iree-hal-target-device=vulkan --iree-vulkan-target=adreno \
        "--iree-hal-dump-executable-binaries-to=$work_dir/spirv-required64" -o required64.vmfb
    check required64 "$1" adreno-required64
    for shader in spirv/*.spv; do
        if cmp -s "$shader" "spirv-required64/${shader##*/}"; then
            printf 'Identical SPIR-V: %s\n' "${shader##*/}"
        fi
    done
fi

if command -v spirv-val >/dev/null; then
    for shader in spirv/*.spv; do
        printf '\n%s\n' "$shader"
        spirv-val --target-env vulkan1.3 "$shader" || true
    done
fi
