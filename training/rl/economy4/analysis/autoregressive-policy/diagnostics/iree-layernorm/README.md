# IREE Adreno LayerNorm 最小复现

包含一个 FP32 LayerNorm，无仿射权重，输入是可确定生成的两行 192 维数据。MLIR 的序列轴保持动态。复现无需战国代码、模型文件或 PyTorch。

在 Linux 上使用 Python 和 NumPy、IREE 编译器与运行时。可选安装 SPIRV-Tools 以运行二进制验证器。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install numpy iree-base-compiler==3.14.0 iree-base-runtime==3.14.0
iree-run-module --list_devices=vulkan
bash reproduce.sh <硬件Vulkan-URI> <软件Vulkan-URI>
```

第二个 URI 可省略。脚本打印临时产物目录和各路径的最大绝对误差。所有编译用 `-O0`。CPU、通用 Vulkan 和 Adreno 目标共用同一个 MLIR 与输入。

## raphael 实测

IREE 编译器 3.14.0rc20261115（`5332c68297a68fafd596277d7686f91009ebf1cb`），Mesa 26.3.1。硬件设备为 Turnip Adreno 640，软件设备为 llvmpipe（LLVM 22.1.8）。

| 编译目标 | 执行设备 | 最大绝对误差 |
| --- | --- | ---: |
| LLVM CPU | CPU | 3.58×10⁻⁷ |
| 通用 Vulkan | Adreno 640 | 3.58×10⁻⁷ |
| 通用 Vulkan | llvmpipe | 3.58×10⁻⁷ |
| Adreno Vulkan | Adreno 640 | 0.381976 |
| Adreno Vulkan | llvmpipe | 4741.76 |

IREE 为 Adreno 目标生成两个 SPIR-V 1.3 模块，两者都带有 `NoSignedWrap`，但未声明 `SPV_KHR_no_integer_wrap_decoration`。两者均未通过 `spirv-val --target-env vulkan1.3`。通用目标生成的两个 SPIR-V 模块都通过验证。把 SPIR-V 二进制的版本头改为 1.4 后，两段着色器都通过验证，计算误差保持不变。这将版本声明问题与这次归约数值错误区分开。

IREE 的 Adreno 目标按 64 个线程划分 subgroup，设备报告默认 subgroup 大小为 128。同一驱动上只改变代码生成阶段的 `subgroup_size_choices` 为 `[128]`，两行输入的误差回到 3.58×10⁻⁷，587 行输入的误差为 1.43×10⁻⁶。可在核对 `vulkaninfo` 的默认 subgroup 大小后，使用 `ADRENO_SUBGROUP_SIZE=128 bash reproduce.sh <硬件Vulkan-URI>` 复跑。编译配置里的两个归约都没有声明运行时所需 subgroup 大小。保留按 64 编译的配置，只给这两个 kernel 加上 `subgroup_size = 64` 后，同一台设备上的误差为 1.19×10⁻⁷。补充声明前后的两段 SPIR-V 逐字节相同，差别来自执行时的 subgroup 要求。可用 `ADRENO_REQUIRE_SUBGROUP64=1 bash reproduce.sh <硬件Vulkan-URI>` 复跑并比较生成的 SPIR-V。这个调整验证了本例，整图是否能通过还受其他下沉策略和共享内存限制影响。
