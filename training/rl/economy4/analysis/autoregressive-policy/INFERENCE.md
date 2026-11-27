# economy4 18M 策略本地推理

`inference.py` 读取 `games-NNNN.pt` 或仅保存 `model` 字段的 PyTorch 权重，通过 C++ 评估桥让策略控制一席，与三席规则策略对局。默认使用模型检查点对应的自适应思考上限。思考、合法候选、席位历史和行动回执沿用 `SeatTrace`，每局每席创建一个实例。推理入口用 FP32，在 `eval()` 和 `torch.inference_mode()` 下运行。

- `--backend cpu` 使用 PyTorch CPU。
- `--backend vulkan` 通过 ncnn 的 Vulkan SDPA 执行棋盘编码器、候选评分和决策解码器的注意力。权重投影、归一化、前馈网络、Gated DeltaNet、稀疏历史、候选掩码与采样由 PyTorch CPU 执行。ncnn 默认优先选择硬件设备，用 `--vulkan-device` 可指定设备索引。输出列出实际设备和 Vulkan 注意力调用次数。GPU 初始化或算子失败时程序报错。
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

第 144 局快照在 `raphael` 的 Adreno 640（Mesa Turnip）上用 24×24 地图、种子 640020、4 个 CPU 线程完成一回合。两边都执行了 14 次思考操作和 39 次行动选择，所有合法掩码和最高分选项一致。行动分数、思考分数、价值的最大绝对误差依次为 4.08×10⁻⁵、2.80×10⁻⁶ 和 1.91×10⁻⁶。Vulkan 执行了 630 次注意力调用。两次单独运行用时分别为 CPU 8.73 秒、Vulkan 13.23 秒。默认使用 CPU，设备和张量搬运会影响 Vulkan 模式用时。

同一快照跑两回合的配对检查覆盖 24 次思考和 57 次行动选择，最高分选项相同，最大分数误差为 5.85×10⁻⁵，Vulkan 执行了 942 次注意力调用。 新 CPU 入口与 `ddab20e` 的原策略再对照一回合，分数和价值均逐项相同。

[ncnn SDPA 算子说明](https://github.com/Tencent/ncnn/blob/master/docs/developer-guide/operators.md#sdpa)
