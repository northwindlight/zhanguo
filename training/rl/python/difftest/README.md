# 差分测试：新引擎（main）参照对局 → legacy-rl 回放

- `rec_new.py`（在新引擎仓库里跑）：N 国 v13plus 规则 AI 对局，录下**每个动作及结果**，加每回合末快照（库存/建筑/军队/市场）。
  `python3 rec_new.py <seed> <size> <turns> <out.json> [nations]`
- `replay_legacy.py`：读 ref，把同样的动作逐条放到本引擎，逐回合比对库存、建筑、领地、军队（按格汇总 HP，容差 1e-6）、
  市场价/库存/基准，以及每笔买卖的实付金。`python3 replay_legacy.py <本仓库路径> <ref.json> <seed> <size>`
- `dump_map.py`：两边 `MapGen` 逐格对比（地形/资源/地名）。

结果（2026-11-19）：地图 8800 格地形仅 8 格不同；5 局（2/4 国，16/24 图，30–100 回合）全部逐回合一致。
未覆盖：外交/投票/附庸/央行/拆除/城堡（v13plus 不用），需另造覆盖这些的脚本局。
