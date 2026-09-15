# -*- coding: utf-8 -*-
"""v11 入口：**经济层 + 军事层**（两层分离，见 `ruleai/__init__.py`）。

    expand_rule_turn_v11(world, name, rng=None, max_actions=40, on_action=None, on_result=None)

一个回合 = 建账本 → 跑经济层 → 跑军事层 → 交账。入口**只有这四行**：
层内的东西（一榜一账、编组状态机、判定式）都不在这里，这是刻意的
—— 想换掉一层，改那一层就行。

★ 与 v10 的关系：v10 一个字节没动（它仍是缺省基线，也是 RL 线的 BC 老师）。
  `DEFAULT_RULE_AI` 也还是 `v10`；要用 v11 得在配置里写 `"rule_ai": "v11"`。

★ RL 线提醒：v11 若要当 BC 老师，`rl/bc.py` 的 teacher 分派是硬编码 if/elif，
  得单独加一支；`HORIZON` 已按惯例在这个模块级暴露（`set_horizon` 改的就是它）。
"""
from __future__ import annotations

import random

from . import Ledger, economy, military
from .economy import HORIZON                          # noqa: F401  转口（RL 的 set_horizon 认它）


def expand_rule_turn_v11(world, name: str, rng: random.Random | None = None,
                         max_actions: int = 40, on_action=None, on_result=None) -> list:
    """跑一个回合：**经济层 → 军事层**，共用一个动作账本。"""
    ledger = Ledger(world, name, max_actions, on_action, on_result)
    economy.run(ledger, world, name)
    military.run(ledger, world, name)
    return ledger.acts
