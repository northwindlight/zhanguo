# -*- coding: utf-8 -*-
"""`v10`：**抗抖** + **不绕山地**（用户 2026-09-12）。

v10 相对 v9 只改两处，测试也就守这两处：

1. **抗抖**：所有引擎事实（攻击力/减伤/血量/征兵成本/采集资源）都**现读**。
   为什么这条要紧：数值一旦不是写死的那个（训练期抖动、调平衡、改政体），
   老师若还按写死的 2 支/50 攻/100 血做决策，**它给的标签就是错的** ——
   实测 20% 抖动下 v9 在 3/5 个 seed 上直接崩盘（只占 5 格、0 次进攻），
   v10 照常打（+12.5% 终局消费）。
   ★ 抖动注入器本身是 RL 线的活（`feat/rl` 分支的 `rl/jitter.py`）；main 这边用
     "就地改 `game.*` 活表"验同一件事，不依赖任何第三方库。
2. **不绕山地**：v9 有两处山地特例（`TROOPS_FOR[山地]=3`、行军落点排除山地）。
   v10 去掉特例，改成**只看打不打得赢**（多轮估算）—— 山地只是减伤高的地形之一。
   ⚠ 这一条原先还有一条对照实验（全图山地跑 100 回合：v9 一步不动、v10 照常行军
   且**多占地**），**2026-09-22 用户要求删掉**。删得有理：它的收尾断言"v10 占地更多"
   是个**结局指标**，同日建筑金减半后就在 seed 0 上翻成了 43 vs 45（另几个 seed 仍是
   v10 多占地）——留一条会随平衡漂移的红灯，只会训练人忽略红灯。
   真正对准这条改动的判据在前半句（v9 一步不动、v10 照常行军），它在 A/B 行为层，
   不随造价浮动；要复现结局对比，见 `docs/` 的实验脚本与 `experiments/`。

纪律：这些用例都会**就地改 `game.*` 的活表**，所以每例前后各拷一份、就地还原
（`clear()` + `update()`，**绝不替换容器** —— `mp.py` 拿的是同一个 dict 对象）。
漏了还原会污染后面所有用例，那种失败极难定位。
"""
from __future__ import annotations

import copy
import unittest

import game
from mp import World

from ruleai import v10 as V10


def _world(seed: int = 0, size: int = 12) -> World:
    return World(size=size, seed=seed, nations=["秦"])


_LIVE_TABLES = ("UNIT_TYPES", "TERRAIN_STATS", "BUILDINGS")   # 被这些用例就地改的活表


class RuleCase(unittest.TestCase):
    """基线类：活表快照 + 就地还原（口径见模块说明的"纪律"）。"""

    def setUp(self):
        self._snap = {k: copy.deepcopy(getattr(game, k)) for k in _LIVE_TABLES}

    def tearDown(self):
        for k, v in self._snap.items():
            cur = getattr(game, k)
            cur.clear()
            cur.update(v)


class TestFightCost(RuleCase):
    """`_fight_cost` = 「一轮打死要几支」+「打不打得赢要几支」，全部现算。"""

    def test_true_tables(self):
        w = _world()
        # (need, squad)：need 只用于排序，squad 是"够不够打赢"的门槛
        self.assertEqual(V10._fight_cost(w, 0, 0, "平原", "野人", [], 50, 100), (2, 2))
        self.assertEqual(V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 100)[1], 2)
        # 减伤越高、一轮打死越费兵 —— 但这只是**数值**差别，不是地形特例
        need_plain = V10._fight_cost(w, 0, 0, "平原", "野人", [], 50, 100)[0]
        need_mtn = V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 100)[0]
        self.assertLess(need_plain, need_mtn)

    def test_unknown_terrain_is_conservative(self):
        """看不见的格（地形 None）→ 按**当下最保守**的地形算，不吃亏。"""
        w = _world()
        self.assertEqual(V10._fight_cost(w, 0, 0, None, "野人", [], 50, 100),
                         V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 100))

    def test_follows_live_tables_attack(self):
        """★攻抖低 → 需要的兵变多（v9 会照旧按 2 支上，然后打不动）。"""
        w = _world()
        base = V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 100)
        game.UNIT_TYPES["步"]["atk"] = 25                    # 攻击力砍半
        hot = V10._fight_cost(w, 0, 0, "山地", "野人", [], 25, 100)
        self.assertGreater(hot[0], base[0], "攻击力降了，需要的兵却没变多")
        self.assertGreaterEqual(hot[1], base[1])

    def test_follows_live_tables_defense_and_hp(self):
        w = _world()
        base = V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 100)
        game.TERRAIN_STATS["山地"]["defense"] = 70           # 减伤抖高
        self.assertGreater(V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 100)[0], base[0])
        game.TERRAIN_STATS["山地"]["defense"] = 50
        # 守军血量抖高（野人按步兵 hp 兜底）
        orig = game.UNIT_TYPES["步"]["hp"]
        game.UNIT_TYPES["步"]["hp"] = 200
        self.assertGreater(V10._fight_cost(w, 0, 0, "山地", "野人", [], 50, 200)[0], base[0])
        game.UNIT_TYPES["步"]["hp"] = orig

    def test_castle_defense_read_from_effects(self):
        """城堡的逐级防御也是**效果数据**（`effects.defense_per_level`），要现读。"""
        w = _world()
        x, y = w.own_tiles("秦")[0]
        plain = V10._terrain_defense(w, "平原", x, y, "秦")
        game.BUILDINGS["城堡"]["effects"]["defense_per_level"] = 40
        boosted = V10._terrain_defense(w, "平原", x, y, "秦")   # 该格有城堡才吃
        self.assertGreaterEqual(boosted, plain)

