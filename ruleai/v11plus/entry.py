# -*- coding: utf-8 -*-
"""v11plus 入口：**经济层 + 军事层**（两层分离，见 `ruleai/v11plus/__init__.py`）。

    expand_rule_turn_v11plus(world, name, rng=None, max_actions=40, on_action=None, on_result=None)

一个回合 = 建账本 → 跑经济层 → 跑军事层 → 交账。入口**只有这四行**：
层内的东西（一榜一账、编组状态机、判定式）都不在这里，这是刻意的
—— 想换掉一层，改那一层就行。

★ 与 v10/v11 的关系：v10 一个字节没动（它仍是缺省基线）；**v11 已冻结**
  （历史成绩归它，不再改）；本模块 = v11 整包副本 + 2026-09-16 那批修，
  细节见 `ruleai/v11plus/__init__.py`。配置里写 `"rule_ai": "v11plus"` 用它。

★ RL 线提醒（`feat/rl` 分支）：这条线若要当 BC 老师，那边的 `rl/bc.py`
  teacher 分派是硬编码 if/elif，得单独加一支；**经济层按"不设回合限制"跑**
  （ROI 榜不按剩余回合过滤，用户 2026-09-16），不再读 `world.max_turns + 20`。
"""
from __future__ import annotations

import random

from . import economy, military
from .ledger import Ledger


def expand_rule_turn_v11plus(world, name: str, rng: random.Random | None = None,
                         max_actions: int = 40, on_action=None, on_result=None) -> list:
    """跑一个回合：**经济层 → 军事层**，共用一个动作账本。"""
    ledger = Ledger(world, name, max_actions, on_action, on_result)
    economy.run(ledger, world, name)
    military.run(ledger, world, name)
    return ledger.acts
