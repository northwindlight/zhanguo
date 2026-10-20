# -*- coding: utf-8 -*-
"""★ **引擎的原生标识符** —— RL 线直接用**内容 ID**，不再另起一套短名。

    为什么不留中文短名
    ─────────────────
    短名（"步"/"平原"/"市政厅"）是**显示名**，而显示名**随 locale 变**
    （`native:infantry` 在 zh-CN 是"步兵"、在 en-US 是"Infantry"）
    ⇒ 拿显示名当字典键，换个语言就会**静默**错位（一列 one-hot 悄悄换了含义，
    而且**不报错**）。内容 ID 才是稳定标识符，也正是主线为根治这一类问题
    引入的东西（旧的 `mp.py` 就是拿显示名当键的老味道）。

    ⇒ 本文件现在**只剩"国家显示名 ↔ 国家内容 ID"**这一件事：
      引擎要求 owner 必须是内容 ID（`engine/identifiers.py`），
      而沙盒的国家叫甲/乙/丙（`rl/vocab.py:PLAYER_NAMES`）——
      ★ 引擎自己的 API 就是「**id + 显示名**」两分
      （`nations.create(nation_id, name, …)`），所以这不是"另起一套命名空间"，
        而是**用引擎本来就有的那套**：id 给引擎、名字给人看。
"""
from __future__ import annotations

# ★ 野人：引擎**没有内容 ID** —— 它是"按需合成的虚拟单位"，
#   引擎自己就用字面量 "野人" 当它的 owner（见 `mods/native/army/domain_objects.py`）。
#   ⇒ RL 线沿用引擎的这个字面量（不是我们自造的）。
BARBARIAN = "野人"

# ★ 建筑：内容 ID（引擎的 `mods/native/buildings/types.py`）。
TOWN_HALL = "native:town-hall"
CASTLE = "native:castle"
WATCHTOWER = "native:watchtower"

# ★★ **十字开局**的 5 格（中心 + 上下左右）—— 旧引擎的 `mp.CROSS`。
#   沙盒的国土下界就是"这 5 格"（见 `rl/sandbox.py:territory_range`），
#   而新引擎**没有**"开局自动铺十字"的概念 ⇒ 门面自己铺。
CROSS = ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0))
