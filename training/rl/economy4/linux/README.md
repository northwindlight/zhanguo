# Linux 上运行 18M 全回合 PPO

从仓库根目录使用下面的命令。训练依赖 Python 3.11 或更新版本、NumPy、PyTorch、CMake、C++17 编译器及 Linux 的 `flock`。双 RTX 5060 Ti 实机使用 PyTorch 2.10.0+cu128，每卡 16 GB。备份端另需 rsync，可通过 SSH 配置中的别名访问训练机。账户、凭据和检查点由操作者提供。

## 从已保存的批次续训

同一批的 `games-NNNN.json` 和 `games-NNNN.pt` 放在一起。检查点包含模型、两套 Adam 状态、已完成局数、最近 16 局的 GDP 和奖励退坡进度。下面的启动器核对文件名、SHA-256 和 PPO 接受记录。运行目录或种子路径可以包含空格。

```bash
cd training/rl/economy4
export RUN="$HOME/zhanguo-ppo-run"
export PPO_PYTHON=python3
bash linux/setup.sh "$RUN"
nohup bash linux/run.sh "$RUN" /path/to/seed/games-0120.json >>"$RUN/train.log" 2>&1 </dev/null &
```

第一个完整批次写入 `$RUN/work` 后，再次运行 `bash linux/run.sh "$RUN"` 即从最新已发布批次恢复。双卡默认每卡并行两局并同步更新共享模型，单卡可设 `PPO_GPUS=1`。默认总目标为 2,000 局，可设 `PPO_TARGET`。启动器用运行目录上的独占锁防止重复开训。

从带思考的检查点切换到零思考训练时，启动前设置 `PPO_NO_THOUGHT=1`。模型权重、两套优化器状态和土地奖励进度随检查点续接。四席每回合直接进入游戏动作，全回合 PPO 覆盖所有游戏决策。首个新批次会把零思考写入检查点，随后续训会自动沿用。要恢复带思考的训练，可将切换前的检查点与对应报告用于独立运行目录。

## 从纯脚本示范开始

下面的命令沿用上述工作目录和 Python 环境。脚本采 1,000 局，用成功且无断供的示范训练。原批次接纳了 998 局，按整局留出 200 局。BC 随机初始化，训练 10 轮。示例按双卡运行，单卡将 `--workers` 改为 1。

```bash
export PYTHONPATH="$PWD/analysis/dynamic-turn-dual-ppo-20261124/reference/python"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=2
policy=analysis/autoregressive-policy
bash linux/setup.sh "$RUN" --teacher
"$PPO_PYTHON" script_bc_collect.py --sampler "$RUN/build/zhanguo_script" \
    --output "$RUN/corpus" --games 1000 --seed 550100
"$PPO_PYTHON" "$policy/bc_train.py" --data "$RUN/corpus" --output "$RUN/bc" \
    --device cuda --workers 2 --precision fp16 --stop-epoch 10
"$PPO_PYTHON" "$policy/evaluate_thought.py" --library "$RUN/build/libzhanguo_eval.so" \
    --checkpoint "$RUN/bc/epoch-10.pt" --output "$RUN/value" --device cuda --workers 2 \
    --maps 4 --warmup-maps 8 --modes 0
bash linux/run.sh "$RUN" "$RUN/value/value.json"
```

`--modes 0` 对应拟合价值头使用的游戏决策状态。若还要重做固定思考预算的对照回测，去掉该参数即可运行全部四种模式。只有留出集 MSE 改善时才会发布 `value-warm.pt`。随后 PPO 运行四国共享策略的 24×24、100 回合自对弈。每四局更新两轮，覆盖全部席位回合、思考操作和游戏动作。采样和 PPO 更新均使用 FP32。

## 从控制机备份

`--anchor` 指向可信的起点报告，可用价值预热的 `value.json` 或迁移时的 `games-NNNN.json`。源目录支持 SSH 别名和本地路径。每个已发布的权重都在临时目录验证通过后才进入备份目录，`verified.json` 记录最近成功备份的批次。生产端已轮换掉的历史权重可以保留对应的指标报告。省略 `--watch` 可执行一次同步。

```bash
python3 linux/backup.py trainer:/data/zhanguo-ppo/work "$HOME/zhanguo-ppo-backup" \
    --anchor /path/to/seed/games-0120.json --watch --keep-every 16
```

默认只读取源目录。上例保留本机最近两个权重、距起点每 16 局的权重以及同步过的指标报告。若训练磁盘也需要相同轮换，从首次备份起显式加 `--prune-source`。删除旧权重前会核对两端摘要，并确认训练机有两个更新的已提交检查点。生产端轮换需要 Python 3.11 或更新版本。

在有 systemd 用户管理器的主机上，也可以用下面两条命令代替手动启动。第一条在训练机运行，第二条在备份机运行；命令分别从各自检出的 `training/rl/economy4` 目录执行。服务进程退出异常时 30 秒后重试。训练指标和权重仍写到原目录，服务日志可用 `journalctl --user -u zhanguo-ppo` 查看。

```bash
systemd-run --user --unit=zhanguo-ppo --property=Restart=on-failure --property=RestartSec=30s \
    --setenv="PPO_PYTHON=$(command -v "$PPO_PYTHON")" \
    /bin/bash "$PWD/linux/run.sh" "$RUN" /path/to/seed/games-0120.json
systemd-run --user --unit=zhanguo-ppo-backup --property=Restart=on-failure --property=RestartSec=30s \
    "$(command -v python3)" "$PWD/linux/backup.py" trainer:/data/zhanguo-ppo/work \
    "$HOME/zhanguo-ppo-backup" --anchor /path/to/seed/games-0120.json --watch --keep-every 16
```

## 终端训练看板

在备份机的 `training/rl/economy4` 目录启动，把最后一个路径换为含 `verified.json` 的备份目录。看板每 5 秒读取本地报告，支持报告放在备份目录或它的 `work/` 子目录。实际同步间隔由备份服务决定。

```bash
tmux new-session -d -s zg-ppo python3 "$PWD/linux/dashboard.py" "$HOME/zhanguo-ppo-backup"
tmux attach -t zg-ppo
```

按 `1` 至 `4` 查看总览、逐局逐席数据、两轮 PPO 参数和最近八批趋势。用上下箭头或 `PgUp/PgDn` 滚动，按 `Ctrl-b` 再按 `d` 分离。全程每四局计一批。下一批预计完成时间以训练机上批报告的落盘时间为起点，采用最近五批每局耗时的中位数。有实时状态文件 `live.json` 时，进入更新阶段后改用本批实际采样耗时加历史更新耗时；若同步超时则暂停显示预计时间。日期和时刻均为北京时间。

## 验收与其他后端

- `verify_full_ppo.py --library "$RUN/build/libzhanguo_eval.so" --checkpoint <权重>` 用真实 C++ 对局核对覆盖、两种回放和一次策略更新。`verify_parallel_ppo.py` 对照双进程与单进程梯度和权重。
- `verify_cuda_graph.py` 在同权重和同地图上逐决策比较采样路径。`memory_audit.py --help` 提供配对回测，可对一席的私有内存图做空白读取和符号重排探针。
- Kaggle 的 BC、价值预热与 PPO 调度入口分别为 `watch_bc.py`、`watch_thought_eval.py` 和 `watch_thought_ppo.py`。Colab 的 BC 接续由 `stage_autoregressive_colab.py`、`watch_autoregressive_colab.py` 和 `autoregressive_colab_worker.py` 负责，沿用已验证的父轮和私有语料包。这些调度器依赖有访问权限的云端账户。
- [模型、回放与性能验证](../analysis/autoregressive-policy/README.md)记录了训练契约和固定窗口计时。

## 与实体机的核对

2026-11-27 以 `9e02106` 为基线，比对训练机使用的 16 个策略、回放及验收文件、41 个 C++ 目录文件和 64 个参考 Python 文件，内容一致。独立 Linux 目录编译了 C++ 教师和评估桥。8 局采集接纳全部 8 局，产出 739 条示范；18M BC 在抽取的一局训练、一局验证上完成了真实参数更新并写出检查点。实体机在独立运行目录加载第 84 局权重、初始化双卡后退出。原训练的 BC10、通过留出检验的价值头及第 120 局完整检查点摘要见[来源清单](../../SOURCE-MANIFEST.json)。
