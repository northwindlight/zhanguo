# -*- coding: utf-8 -*-
"""两层的**公共动作账本**：谁做了什么、还剩多少额度，都记在这里。

为什么要有它：引擎给每个国家每回合的动作数是**有限**的（看海默认 12 个），
两层不能各记一本账 —— 那必然出现"经济段把额度花光、军事段一个动作都发不出"
（真发生过）。所以额度只有一本，且**军事段预留**卡在买卖上（`economy` 里调用）。
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
