# 同策略市场退化回归

入口 [run.py](run.py) 全程离线创建合成八国世界，不读取真实配置、密钥、日志或存档。默认地图 24×24；所有国家使用指定版本的规则 AI。v13 各国抽签种子为世界种子加国家序号，所以结果可以复现。默认实际新局则从地图种子和国家 ID 派生抽签种子。

```bash
python3 experiments/rule_market/run.py --versions v11plus v13 --seeds 73 20261023 42 --turns 100 --economy-only --out /tmp/rule-market-economic.json
python3 experiments/rule_market/run.py --versions v11plus v13 --seeds 73 --turns 80 --out /tmp/rule-market-full.json
python3 experiments/rule_market/run.py --versions v13 --seeds 20261023 --turns 12 --experimental --out /tmp/rule-market-experimental.json
```

`--economy-only` 只屏蔽军事移动和攻击，经济规划仍包含征兵、军需、兵营和电厂，避免扩张改变资源禀赋，专门观察相同经济策略对有限市场的影响。完整对照保留原军事分组、进攻和占领。实验发行版使用测试里的确定性议会后端，全体批准；这验证客户端工厂、审核协议与私有战斗推演接线，不评价真实议会的审核质量，也不会调用在线模型。

脚本每十回合记录现金、建筑、兵数、市场库存与报价；`production_statuses` 是从真实生产回执累计的“建筑×回合”次数。`energy-shortage` 不能与 `missing-input` 合并解释：电厂缺燃料会同时影响自身及用电建筑，后者只记直接缺配方材料的建筑。开局市政厅没有电厂，第一回合的八次停电是原开局结构导致。

回执失败独立记录，不以“脚本没有崩溃”作为通过条件。库存触底次数、产出、建设速度和军力都应结合看；更多现金或更少缺料不自动等于战略更强。不同策略有不同库存和现金偏好，不把市场价格不变当作目标，也不要求价格永不下降。

本轮结果见 [results](results/)，策略说明见 [v13](../../ruleai/v13/README.md)。

2026-10-29当前引擎复核见[经济层对照与托管前置](../../docs/reviews/规则AI经济层对照与托管前置-20261029.md)，与2026-10-23历史结果分开记录。`inspect_current.py`用新的单国合成局检查撤销后的施工次数、队列预占、工程院候选、整单报价与回本排序口径；不修改策略：

```bash
python3 experiments/rule_market/inspect_current.py --out archive/previews/economy-audit-20261029/interface-probes.json
```

追查现金来源、首期停电和投资未执行的实际分支，可运行`trace_cash.py`。它记录正式回执并跟踪原策略函数，不改决策；每国金币账要求实际余额与已记录流量对平：

```bash
python3 experiments/rule_market/trace_cash.py --seed 73 --turns 100 --out archive/previews/economy-audit-20261029/cash-trace-73.json
```
