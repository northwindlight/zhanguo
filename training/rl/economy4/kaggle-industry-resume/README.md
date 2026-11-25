# Kaggle 工业 BC 接续

2026-11-23，用户明确要求由 Kaggle 接续 Colab 任务。四轮 BC 在上传期间于 Colab 完成，第四轮原始检查点已按远端 inventory 的 SHA-256 校验并拉回。因此当前提交只接独立回测，不重复已完成的训练。

- 私有运行：`northwindlight/zhanguo-industry-bc-resume`。
- 私有输入：`northwindlight/zhanguo-industry-bc-eval-handoff-20261123`；约 100MiB，含原经济基座、最新第四轮检查点、优化器、代码和既有评估报告。
- 千局示范和经济锚全部保留本机；它们不参与本次剩余回测，因此不用再次上传。
- 训练和游戏规则保持原版本；通过 `ZHANGUO_ENV_THREADS` 将 Kaggle 环境线程数调整到 4。
- Kaggle 原生编译 C++/LibTorch，验证 CUDA 实际运行后再释放旧 Colab。
- 检查第四轮的 12 图表现，然后在新的 24 图/96 席上对照原经济模型和已选 BC 模型，均为 100 回合、无脚本接管。
- 双 T4 可用时，末次原模型与 BC 模型的对照分配到两张卡并行，每个进程用两个环境线程。配对地图种子均为 297000。
- 原验收条件不变：平均土地至少 10、饿死席比至多 5%、最后十回合名义 GDP 至少达到基座的 80%。不自动开启 PPO。

## 入口

`prepare.py --evaluation-only` 生成校验包；`kernel/resume.py` 是 Kaggle 前台任务入口。`continue_when_uploaded.py --evaluation-only` 等待私有数据集处理完成，再提交任务并启动 systemd 结果回拉服务。

`watch_kaggle_industry.py` 位于项目根目录。确认新机 CUDA 烟测成功后，停止旧 Colab watcher 并释放 L4；完成后下载结果、校验选定模型及本地原始样本、生成交付 ZIP。

本机状态目录：`kaggle-industry-results/`。关注 `handoff.log`、`runtime-live.log`、`status.json`、`colab-released.json`、`verified.json`；它们分别表示提交进度、远端日志、远端状态、旧机释放及最终本机校验。真实完成与否以这些文件核实。

## 原始训练接续能力

`prepare.py` 不带选项可打完整语料与检查点包，Kaggle 入口保留从未完成的 BC 轮继续训练的能力。本次完整语料上传已被更小的回测包取代，不能将旧大包上传进度当作当前运行状态。

20:24 已核实：Kaggle 双 T4、PyTorch 2.11.0+cu130；C++ 编译和真实 CUDA 四席采样烟测成功，已进入第四轮独立回测。`colab-released.json` 已写，旧 L4 已释放。后续训练验收和本机最终回拉完成须继续看 `verified.json`。
