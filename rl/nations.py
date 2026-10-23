# -*- coding: utf-8 -*-
"""RL 线的国家**词表**：中文短名 ⇄ 引擎内容 ID。

★ 为什么需要
  ──────────
  引擎的 owner 必须是**内容 ID**（`engine/identifiers.py` 的正则：
  全 ASCII 小写 + 命名空间），而 RL 线的国家叫"甲/乙/丙"（`rl/vocab.py:PLAYER_NAMES`）。
  ⇒ 这里做**一一对应**：`甲 ⇄ rl:n0`。

★ 这**不是**影子状态
  ────────────────
  它是**纯函数**（按词表下标算）：不缓存引擎状态、不随局面变化、
  不跨局漂移 ⇒ 同一个短名在任何一局、任何一个世界里都是同一个 ID。

  ⚠ 旧 `rl/engine.py` 的 `_NationRegistry` 是"**首次见到才分配**"的进程级表 ——
    那是隐式状态：这局先建"乙"再建"甲"，两者的 ID 就和反序时不同，
    而**什么都不报错**。已删除。
"""
from __future__ import annotations

from .vocab import PLAYER_NAMES

# ★ 引擎内容 ID 的命名空间前缀（`rl:n0` / `rl:n1` …）。
NATION_PREFIX = "rl:n"


def index_of(name: str) -> int:
    """中文短名 → 词表下标。"""
    try:
        return PLAYER_NAMES.index(name)
    except ValueError:
        raise KeyError(f"未知国家短名：{name!r}；词表：{PLAYER_NAMES}") from None


def ident(name: str) -> str:
    """中文短名 → 引擎内容 ID。"""
    return f"{NATION_PREFIX}{index_of(name)}"


def name(ident_: str) -> str:
    """引擎内容 ID → 中文短名（非 RL 国家原样返回）。"""
    if not ident_.startswith(NATION_PREFIX):
        return ident_
    try:
        return PLAYER_NAMES[int(ident_[len(NATION_PREFIX):])]
    except (ValueError, IndexError):
        return ident_


def is_ours(ident_: str) -> bool:
    """这个内容 ID 是不是 RL 线自己的国家。"""
    return ident_.startswith(NATION_PREFIX)
