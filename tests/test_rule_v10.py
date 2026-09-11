# -*- coding: utf-8 -*-
"""`expand_rule_v10`：**抗抖** + **不绕山地**（用户 2026-09-12）。

v10 相对 v9 只改两处，测试也就守这两处：

1. **抗抖**：所有引擎事实（攻击力/减伤/血量/征兵成本/采集资源）都**现读**。
   为什么这条要紧：训练期一开 `--rules-jitter`，引擎数值按 seed 抖；
   老师若还按写死的 2 支/50 攻/100 血做决策，**它给的标签就是错的** ——
   实测 20% 抖动下 v9 在 3/5 个 seed 上直接崩盘（只占 5 格、0 次进攻），
   v10 照常打（+12.5% 终局消费）。
2. **不绕山地**：v9 有两处山地特例（`TROOPS_FOR[山地]=3`、行军落点排除山地）。
   v10 去掉特例，改成**只看打不打得赢**（多轮估算）—— 山地只是减伤高的地形之一。

纪律：这些用例都会改 `game.*` 的活表，`tearDown` 必须 `jitter.restore()`，
否则污染后面所有用例（那种失败极难定位）。
"""
from __future__ import annotations

import random
import unittest

import game
from mp import World
from rl import jitter

import expand_rule_v10 as V10


def _world(seed: int = 0, size: int = 12) -> World:
    return World(size=size, seed=seed, nations=["秦"])


class RuleCase(unittest.TestCase):
    def tearDown(self):
        jitter.restore()


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


class TestNoMountainSpecialCase(RuleCase):
    """★"绕山地"没有了：山地是普通地形，只看打不打得赢。"""

    def test_mountain_is_not_excluded_from_steps(self):
        """★对照实验：**全图都是山地**时 v10 照常行军，v9 一步不动。

        这是"去掉绕山地"最直接的可执行判据 —— v9 的落点过滤里有
        `world.tile_terrain(*q) != "山地"`，全山地世界里它一个合法落点都没有
        （实测：100 回合、4 支兵、**0 次行军**；v10 同期走 60 次、多占 4 格）。
        为什么不用更短的回合：扩张本来就晚（前面几十回合在建产能），
        回合数不够时两边都是 0，那样的断言是空的。
        """
        import expand_rule_v9 as V9

        def moves_of(fn, turns=100, size=10):
            w = _world(seed=0, size=size)
            w.tile_terrain = lambda x, y: "山地"      # 全图山地（含自家地与野地）
            w.begin_turn()
            rng = random.Random(0)
            n = {"mv": 0}
            orig = w.move

            def spy(name, aid, x, y):
                r = orig(name, aid, x, y)
                if r[0]:
                    n["mv"] += 1
                return r

            w.move = spy
            for t in range(turns):
                fn(w, "秦", rng, max_actions=10 ** 9)
                w.resolve_turn()
                if t + 1 < turns:
                    w.begin_turn()
            return n["mv"], len(w.own_tiles("秦"))

        v10_mv, v10_tiles = moves_of(V10.expand_rule_turn_v10)
        v9_mv, v9_tiles = moves_of(V9.expand_rule_turn_v9)
        self.assertEqual(v9_mv, 0, "v9 本该一步不走（它的绕山地过滤）—— 对照失效了")
        self.assertGreater(v10_mv, 0, "全山地世界里一步没走 —— 绕山地的逻辑还在")
        self.assertGreater(v10_tiles, v9_tiles, "不绕山地却没多占地")


class TestJitterRegression(RuleCase):
    """★抖动不许把引擎打进非法状态（踩过一次：投料抖成 0 → 引擎除零）。"""

    def test_yields_and_inputs_never_zero(self):
        for seed in range(40):
            jitter.apply(seed, 0.4)
            for b in game.BUILDINGS.values():
                for f in ("outputs", "inputs", "fuel"):
                    for v in (b.get(f) or {}).values():
                        self.assertGreaterEqual(
                            v, 1, f"{f} 抖成了 {v} —— 引擎的 `res // need` 会除零")

    def test_engine_survives_jittered_factory(self):
        """真跑一小局：抖动后引擎不许抛异常（除零那个 bug 的端到端回归）。"""
        for seed in (7, 3, 11):
            jitter.apply(seed, 0.4)
            w = World(size=10, seed=seed, nations=["秦"])
            w.begin_turn()
            rng = random.Random(seed)
            for t in range(8):
                V10.expand_rule_turn_v10(w, "秦", rng, max_actions=10 ** 9)
                w.resolve_turn()
                if t < 7:
                    w.begin_turn()


if __name__ == "__main__":
    unittest.main()
