# 战国 RL

本分支从主线分出，`main` 仍是游戏规则依据。这个目录保存 Python 参考引擎、C++/LibTorch 采样和 PPO，以及 PyTorch 自回归模型与 BC。Python 参照提交和实验源码包哈希见 [SOURCE-MANIFEST.json](SOURCE-MANIFEST.json)。

## 18M 自回归策略

18,180,507 参数。局面编码器宽 192、四层，读取本国可见的地图、国家、部队、资源和候选。宽 384 的历史编码器用三层 Gated DeltaNet 更新循环状态，用一层 Qwen Sparse Attention 检索历史块。三层因果解码器读当前局面、历史和 4×4 符号图，再选择内存操作或游戏候选。

每回合初始思考 8–30 步，最近 16 局的四席期末名义 GDP 均值达到 60 后将上限调至 100。思考预算与 60 次游戏动作预算分开。思考操作和当时的观测进入本国历史。相同局面复用 K/V。BC 只监督脚本游戏动作，隐式搜索通过自对弈回报学习。18M 路线包含纯脚本示范、10 轮 BC、价值头预热、C++ 四国自对弈和全回合 PPO。双卡分别采样并同步更新共享策略。 [模型与验证](economy4/analysis/autoregressive-policy/README.md)

[Linux 训练、断点恢复与备份](economy4/linux/README.md)给出从源码或已保存批次启动的命令。

## 源码入口

- [C++ 四国采样](economy4/cpp)及[动态回合和双策略 PPO](economy4/analysis/dynamic-turn-dual-ppo-20261124/README.md)
- [纯脚本 BC 与全局 PPO 契约](economy4/pure-script-bc/CONTRACT.txt)
- [早期 Python/C++ W3 交接](HANDOFF.md)

训练数据与权重由对应实验任务管理。早期 C++ 的 `run_all.sh` 读取 `cpp/refs/` 下的规则差分参照。18M 验证从 `economy4/script-bc-results/chunks/` 读取匹配的 `chunk-0000.zip`。从 `economy4/analysis/autoregressive-policy` 运行下列命令。

```bash
export PYTHONPATH=../dynamic-turn-dual-ppo-20261124/reference/python
OPENBLAS_NUM_THREADS=1 python3 -B verify.py --threads 2
OPENBLAS_NUM_THREADS=1 python3 -B verify_bc.py
```
