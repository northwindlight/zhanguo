# economy4 18M 策略本地推理

`inference.py` 读取 `games-NNNN.pt` 或仅保存 `model` 字段的 PyTorch 权重，通过 C++ 评估桥让策略控制一席，与三席规则策略对局。默认使用模型检查点对应的自适应思考上限。思考、合法候选、席位历史和行动回执沿用 `SeatTrace`，每局每席创建一个实例。推理入口用 FP32，在 `eval()` 和 `torch.inference_mode()` 下运行。

- `--backend cpu` 使用 PyTorch 的融合 SDPA，棋盘和候选沿用原有 FP32 权重。自注意力合批计算 Q/K/V，交叉注意力合批计算 K/V。加载权重时合成棋盘到解码器 K/V 的两次线性投影，各局面只做一次矩阵乘。
- `--backend vulkan` 将每头至少 131,072 个查询与键配对的注意力交给 ncnn Vulkan。短解码和较小的候选注意力沿用 CPU 路径。其余算子仍由 CPU 执行。默认优先选择硬件设备，`--vulkan-device` 可指定设备索引。输出记录实际设备和两侧的注意力调用次数。较小的地图可能只需要 CPU 调用。
- `--compare-cpu` 用同一份权重在 CPU 上复算 Vulkan 选择的同一条决策路径，核对合法掩码、思考和行动分数以及价值头，记录最大绝对误差和最高分选项的分歧数。

## 构建与运行

以下命令从仓库根目录运行。需要 Python 3.11 或更新版本、CMake 与 C++17 编译器。Vulkan 模式另需 Vulkan 驱动和带 Vulkan 支持的 ncnn。已在 Linux arm64、PyTorch 2.10 CPU 和 ncnn 1.0.20261020 上验证。

```bash
run="$(realpath -m "$PWD/archive/previews/economy4-inference")"
python3 -m venv "$run/.venv"
"$run/.venv/bin/python" -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch==2.10.0'
"$run/.venv/bin/python" -m pip install numpy
# 运行 Vulkan 模式时安装
"$run/.venv/bin/python" -m pip install 'ncnn==1.0.20261020'

cmake -S training/rl/economy4/cpp -B "$run/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=OFF -DZHANGUO_EVAL_BRIDGE=ON
cmake --build "$run/build" --parallel 4 --target zhanguo_eval

analysis="$PWD/training/rl/economy4/analysis"
export PYTHONPATH="$analysis/dynamic-turn-dual-ppo-20261124/reference/python"
weights=/path/to/games-NNNN.inference.pt
"$run/.venv/bin/python" -B "$analysis/autoregressive-policy/inference.py" \
  --checkpoint "$weights" --library "$run/build/libzhanguo_eval.so" \
  --backend vulkan --compare-cpu --size 24 --turns 1 --seed 640020
```

将最后一条命令的 `--backend vulkan --compare-cpu` 改为 `--backend cpu` 即可单独执行 CPU 推理。默认按策略分布采样，可用 `--selection greedy` 取最高分。发布的训练检查点可从经过核对的 `games-NNNN.json` 中找到文件名和 SHA-256。收到独立权重包时，可用 `--checkpoint-sha256` 校验传输结果。

其他 Python 入口可调用 `inference.load_policy(path, backend="vulkan")`，然后为每个席位构造 `SeatTrace(policy)`，依次提交可见事件、局面和选中的候选动作。CPU 与 Vulkan 模式共用权重格式。

## Adreno 640 验证

全部对照使用第 144 局的权重、24×24 地图、种子 640020 和四个大核（`taskset -c 4-7`）。同一回合的 Vulkan 提交次数由 630 减为 156。对照原 CPU 路径，一回合 53 次决策的合法掩码和最高分选项一致，最大分数误差为 1.67×10⁻⁵。新 CPU 与混合 Vulkan 跨两回合对照，81 次决策的合法掩码和最高分选项一致，最大分数误差为 3.72×10⁻⁵。

以下用时是各路径在同一设备交错运行两次的中位数。

| 路径 | 优化前 | 优化后 | 用时下降 |
| --- | ---: | ---: | ---: |
| CPU | 8.45 秒 | 5.77 秒 | 32% |
| 混合 Vulkan | 13.04 秒 | 10.33 秒 | 21% |

CPU 融合注意力在这台机的单局延迟最低。短注意力交给 CPU 后，Vulkan 路径减少同步和张量搬运；587 个棋盘令牌的 FP32 注意力单算子测试中，PyTorch CPU 约 5.17 毫秒，ncnn Vulkan 约 17.70 毫秒。

单独对照 GPU 频率时，混合路径动态频率用时 10.40 秒，固定 585 MHz 用时 9.69 秒。固定频率也复现了试验性 IREE 整图的 `VK_ERROR_DEVICE_LOST`。同一个针对 Adreno 编译的整图模块在 llvmpipe 上也输出错误，通用目标则可运行但与 PyTorch 存在差异。正式 Vulkan 路径继续使用已校验的 ncnn SDPA。

[ncnn Vulkan SDPA 实现](https://github.com/Tencent/ncnn/blob/20261020/src/layer/vulkan/sdpa_vulkan.cpp)
