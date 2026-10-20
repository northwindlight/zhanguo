# 实现审计离线验收

只创建合成世界和临时存档，不读取真实对局、不调用外部模型。

```bash
.venv/bin/python experiments/implementation_audit/benchmark.py
.venv/bin/python experiments/implementation_audit/smoke.py
.venv/bin/python -m unittest tests.test_audit_repairs
```

`benchmark.py [源码目录]` 对同一 24×24、seed=73 的八国场景测量：224 支正规军，每块初始领土一座瞭望塔、三座木材能源厂。预热一次后测 3 次国0观察，接口计数只覆盖这 3 次观察；另测初始状态、system、工具 schema 和一次移动后的差分 token。数量来自本地 token 估算器，墙钟是本机一次测量，不是服务端延迟或费用预测。

`results.json` 对照包含 G1/G2/G12 的 `329b7a3` 与本轮修复。对基线可先向专门临时目录解包该提交的源码，再将目录传给同一脚本；无需复制配置或存档。`facts_sha256` 比较 view/army/econ 三份玩家事实；比较前仅将无主地名置空，以排除这项有意修复。

`smoke.py` 跑原生三国 40 周期、实验版三国 6 周期，中途保存并读档，从同一状态两路继续，断言整个主档状态完全一致，再验证离线结算不改写存档。实验版议长由本地模拟对象替代。测试退出后临时目录自动清理。
