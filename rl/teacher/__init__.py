# -*- coding: utf-8 -*-
"""★ **RL 线的"老师"** —— 基于**世界真值**的军事层（评估 / 编组 / 寻路 / 候选）。

    为什么这里有��份"抄来的" v11plus
    ─────────────────────────────
    主线的 `ruleai/v11plus/` 在 2026-10 的重构里被**整体改写**了：
    它现在是一个**玩家 API 客户端**（`entry.py` 第一行原话：
    「Active rule client; receives a PlayerSession, never a World」）
    —— 拿的是**公开事实**（`player.observe()` 里的 `facts`），
    **看不见未探索的地形、也拿不到引擎的可达集真值**。

    而 RL 沙盒要的是**另一回事**：
      · 老师要在**世界真值**上做评估（`env.defense_pct`、`env.defenders`、
        `env.reachable（引擎的可达集）`）—— 那是**训练信号**，不是玩家视角；
      · 沙盒**自己**驱动世界（没有 `PlayerSession`、没有 `Session`），
        所以主线那套客户端**根本接不上**。

    ⇒ 本包是**RL 线自己的一份世界真值版** v11plus 部件（评估 / 编组 / 寻路 /
      候选 / 军事 / 账本），逐字取自旧线（`origin/feat/rl-tactics:ruleai/v11plus/`），
      只把**两处死依赖**换掉：
        · `from game import …` / `from balance import …` ⇒ `rl/rules.py`
          （读主线的表，不复制数值）；
        · `from ruleai.v11plus import …` ⇒ 本包内部互引。

    ★ **"抄"这件事是既定的**：旧 `rl/military.py` 的开头就写着
      「军事动作走 `rl/military.py`：**抄** v11plus 的部件」——
      老师本来就是 RL 线自己的东西；这里只是把它**依赖的那几块**也一并搬过来，
      免得主线一改写法，RL 线就断。
"""
from __future__ import annotations

from . import combat, grouping, ledger, military, pathfind, targeting

__all__ = ["combat", "grouping", "ledger", "military", "pathfind", "targeting"]
