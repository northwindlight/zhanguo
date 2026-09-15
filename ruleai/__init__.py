# -*- coding: utf-8 -*-
"""规则 AI 的**分层实现**（v11）：经济层与军事层分离，各自不知道对方内部。

    ruleai/
      __init__.py   ← 你在这里：共享的**动作账本**（两层唯一的公共接口）
      v11.py        ← 入口：`expand_rule_turn_v11` = 经济层 + 军事层
      economy.py    ← 经济层：钱/料/建造/征兵（产兵权在这里）
      military.py   ← 军事层：编组 → 判定式 → 寻路 → 出手
      grouping.py   ← 军事层部件：编组状态机 + 全局求解器
      combat.py     ← 军事层部件：难度判定式（直接调引擎伤害公式）
      pathfind.py   ← 军事层部件：视野掩码 + 多回合代价场 + 本回合落点
      targeting.py  ← 军事层部件：候选池

★ 分层的唯一理由是**让两层各自可读、可测、可替换**：经济层不知道编组怎么编，
  军事层不知道钱怎么花；要换掉其中一层（比如把经济换成更聪明的预算分配），
  另一层一行都不用动。用户 2026-09-15 定。

★ 两层共享的只有三样：`world`、`Ledger`（动作账本 + 额度）、以及
  `balance.py` 里的数值。**不许互相 import** —— 那是这份分层唯一要守的纪律。
"""
from __future__ import annotations


class Ledger:
    """**两层的公共动作账本**：谁做了什么、还剩多少额度，都记在这里。

    为什么要有它：引擎给每个国家每回合的动作数是**有限**的（看海默认 12 个），
    两层不能各记一本账 —— 那必然出现"经济段把额度花光、军事段一个动作都发不出"
    （真发生过）。所以额度只有一本，且**军事段预留**卡在买卖上（`economy` 里调用）。
    """

    def __init__(self, world, name: str, max_actions: int, on_action=None, on_result=None):
        self.world = world
        self.name = name
        self.max_actions = int(max_actions)
        self.on_action = on_action
        self.on_result = on_result
        self.acts: list[tuple[str, dict, bool, str]] = []

    def full(self, reserve: int = 0) -> bool:
        """额度是不是快用完了（`reserve` = 还要给后面留几个动作）。"""
        return len(self.acts) >= self.max_actions - reserve

    def do(self, tool, args, fn, *a, **k) -> bool:
        """记一笔动作：额度内才真调引擎，异常一律兜住（与 v10 的 `do` 同形）。"""
        if len(self.acts) >= self.max_actions:
            return False
        if self.on_action is not None:
            self.on_action(tool, args)
        try:
            ok, msg = fn(*a, **k)
        except Exception as e:                       # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        if self.on_result is not None:
            self.on_result(tool, args, bool(ok))
        self.acts.append((tool, args, bool(ok), str(msg)))
        return bool(ok)
