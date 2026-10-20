# -*- coding: utf-8 -*-
"""★ **词表翻译**：RL 线的**中文短名** ↔ 引擎的**内容 ID**。

    为什么单独一个模块
    ──────────────────
    规则表在**引擎**里（`mods/native/army/parameters.py` 等），键是内容 ID
    （`native:infantry`）；RL 线的**观测词表**（`rl/vocab.py`）用中文短名
    （`步`/`骑`/`民`、`平原`/`森林`…）。

    ⇒ 翻译表放在**这里**（两边都只依赖它），而不是塞进 `rl/engine.py` 或
    `rl/rules.py` —— 那两个会**互相 import**（`rules` 要表的入口，
    `engine` 要 `rules` 查满血），拆开就没有环了。

    ★ 本模块**只做键的对应**，不含任何数值 —— 数值一律现取引擎的表
      （见 `rl/rules.py`）。
"""
from __future__ import annotations

# 兵种：中文短名 → 内容 ID
UNIT_KINDS = {
    "步": "native:infantry",
    "骑": "native:cavalry",
    "民": "native:militia",
}
KIND_OF = {v: k for k, v in UNIT_KINDS.items()}

# 地形：中文短名 → 内容 ID
TERRAINS = {
    "平原": "native:plain",
    "森林": "native:forest",
    "丘陵": "native:hill",
    "山地": "native:mountain",
    "沙漠": "native:desert",
}
TERRAIN_OF = {v: k for k, v in TERRAINS.items()}

# 建筑：内容 ID → 中文短名（门面上报"建筑名"时用）
TOWN_HALL = "native:town-hall"
CASTLE = "native:castle"
WATCHTOWER = "native:watchtower"
BUILDING_NAME = {TOWN_HALL: "市政厅", CASTLE: "城堡", WATCHTOWER: "瞭望塔"}
# ★ 反向（中文短名 → 内容 ID）—— 门面"改建筑座数"时要用。
KIND_OF_NAME = {v: k for k, v in BUILDING_NAME.items()}

# ★★ **十字开局**的 5 格（中心 + 上下左右）—— 旧引擎的 `mp.CROSS`。
#   沙盒的国土下界就是"这 5 格"（见 `rl/sandbox.py:territory_range`），
#   而新引擎**没有**"开局自动铺十字"的概念 ⇒ 门面自己铺。
CROSS = ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0))

# ★★ **建筑名清单**（旧口径：`balance.BUILDINGS` 的键）—— 旧引擎每个地块的
#   `buildings` 字典**预置全部键**（值为 0 表示"没有"），
#   而新引擎的 `buildings` 是**实例列表**（没建就是没有这个键）。
#   ⇒ 门面把 `tile["buildings"]` 补成**全键零值 + 实际有的**，
#   RL 线（`encode.py` / `military.py`）按 `t["buildings"].get("市政厅", 0)` 读才不出错。
BUILDINGS = ("城堡", "林场", "农场", "矿场", "石油厂", "黄金矿场", "木材能源厂",
             "石油能源厂", "补给厂", "装备厂", "兵营", "市政厅", "瞭望塔",
             "外交中心", "工程院", "军屯")
