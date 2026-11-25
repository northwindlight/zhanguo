# 战国 C++ 采样器

C++ 进程内执行候选生成、迷雾观测、W1/W2/W3/v11plus 对手、回合结算和 LibTorch 批量推理。沿用 `zhanguo-rl` 的双人课程、终局国力回报及回合级 GRU。2026-11-24 用户将每国每回合游戏行动预算提高到 **60**；失败尝试也消耗预算，结束回合独立处理。候选 0 是结束回合；预算耗尽时只保留结束回合，其余时候保持原动作可执行性规则。无效动作仍进入采样记录。

预算在每次观测更新：全局通道29为剩余预算/60，30为已用预算/60，31为绝对上限/100（当前0.6）。每个国家独立计数，下一回合重置；回合摘要沿用这三个通道。模型维度保持不变，旧权重可加载但未学过新增通道的语义，不能据此视为适配新观测。当前用户选择随机初始化后混合BC十轮再PPO，训练尚未启动。审计与后续方案见 `../analysis/ppo-credit-audit-20261124/REPORT.txt`。

Python 训练参照为 `zhanguo-rl` 的 `c045602` 及其创盟缓存修复版本。ZGRL3 轨迹对应国家 40、地块 72 和回合摘要 80 维的策略。将导出器和更新器的 `--python-root` 都指向包含这些维度的仓库。更新器读取 C++ 轨迹并输出 Python 检查点。

## Colab L4

把本目录源文件和 Python 参照目录放到同一个 L4 runtime。下列命令假定当前目录为 C++ 源码目录，Python 位于 `/content/zhanguo-rl`，已有 PPO 或 BC 训练检查点 `/content/ckpt.pt`。

```bash
python3 build_colab.py --build /content/build-zhanguo --jobs 4
python3 export_policy.py /content/ckpt.pt /content/policy.pt \
  --python-root /content/zhanguo-rl --native-bf16 --fixture /content/fixture.pt
/content/build-zhanguo/zhanguo_torch verify /content/policy.pt /content/fixture.pt cuda native
/content/build-zhanguo/zhanguo_torch sample /content/policy.pt cuda \
  64 64 13 60 7001 4 native /content/rollout-001
python3 ppo_update.py /content/ckpt.pt /content/rollout-001 /content/ckpt-001.pt \
  --python-root /content/zhanguo-rl --device cuda
```

`sample` 的数字依次是局数、并行批大小、地图边长、回合上限、首个种子和环境线程数。后续采样用新检查点重新导出，指定新的种子区间及轨迹目录。需要跨轮固定 KL 锚时，将 BC 检查点传给更新器的 `--reference`。

`native` 对应 `--native-bf16` 导出，策略使用 BF16，回合摘要和 GRU 使用 FP32。省略导出选项时可用 `fp32` 或动态 `bf16`。轨迹按局流式写盘，完成后生成 `.zgrl` 文件。文件含原始回合摘要、敌情辅助目标、紧凑观测、候选索引、旧 logp、旧价值估计和终局回报。`read_trajectory.py` 能直接读取 Python PPO 的 episode tuple。

导出器按检查点的 `cfg` 选择 full 或 xattn2 架构。

## W3 对手

给 `sample` 加 `--opps w3:1` 会让模型每局打 W3。 `--opps rule:0.25,w3:0.75` 按相对权重配对，每局仍由种子分配学习者座位。省略开关时，默认池依次为 `rule`、`w1_25_2`、`w1_40_3`、`w2`，权重为 25%、30%、25%、20%。`w1b` 也是 `w1_40_3` 的可用名称。

从 Python `rl.cxloop` 启动 PPO 时，设置 `ZHANGUO_SAMPLE_OPPS=w3:1` 并传 `--updater ppo_update.py`。命令行 `--opps` 优先于环境变量。

W3 在央行开启的顺序回合中按军费预算扩编。它会购入期报，并用可见地图和延迟间谍回报判断敌军与路线。被宣战后会优先处理受威胁的市政厅，尝试增建城堡并调回步兵。

首都的瞭望台在开局电力之后优先建设。满足主动宣战条件时，从靠近已知敌国领土的己方边缘挑一处非首都地块。若还没有瞭望台，先下单即可宣战，施工结算遵循引擎顺序。

## 本地验收

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build -j4
./run_all.sh "$PWD/build/zhanguo"
python3 tests/obs_parity.py build/zhanguo_obs_probe --python-root "$PYTHON_REFERENCE"
python3 tests/engine_parity.py build/zhanguo_engine_trace --python-root "$PYTHON_REFERENCE" --seeds 100 --turns 60 --learner
python3 tests/w3_parity.py build/zhanguo_w3_trace --python-root "$PYTHON_REFERENCE" --seeds 20
```

已有独立 LibTorch 时，用 `-DZHANGUO_TORCH=ON -DCMAKE_PREFIX_PATH=/path/to/libtorch` 配置，然后构建 `zhanguo_torch`。CPU 烟测的导出器可用 `--new-test-policy` 生成小模型。

`run_all.sh` 校验 16 个参照局，含动作成败、库存、建筑、军队 HP、市场、战争、联盟、条约和贷款本金。自动交易场景 scH 在本轮范围之外。批量采样器当前入口为双人课程，三国以上完整课程及所有联盟议案类型尚需专项差分。

## 吞吐复测

`sample` 默认每步用一个推理批。设置 `ZHANGUO_SAMPLE_BUCKET_MIN=32` 可按候选数分为至多两批，每批至少 32 行，且分桶须能减少至少 5% 的补齐候选行。两批的输出按原槽位合并，再统一抽样和回读。日志给出分桶步数、前向次数和补齐后的总行数。

短程对照可同时设 `ZHANGUO_SAMPLE_VERIFY_BUCKETS=1`，让发生分桶的每一步用同一观测和 GRU 状态再跑一次单批前向，报告最大概率和价值偏差。测吞吐时关闭该对照。需要拆分时间时用 `ZHANGUO_SAMPLE_PROFILE=1`，它会在 GPU 分段边界同步。

`tests/bench_python.py` 使用 Python 原有 worker、策略及采样顺序。`tests/compare_benchmarks.py` 比较相同种子池的决策吞吐、局数、回报、完局回合数和胜负。L4 实测与原始日志在 [benchmarks/colab-l4-20261120](benchmarks/colab-l4-20261120/)。
