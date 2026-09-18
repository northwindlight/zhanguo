# -*- coding: utf-8 -*-
"""`v10`：**抗抖** + **不绕山地**（用户 2026-09-12）。

v10 相对 v9 只改两处，测试也就守这两处：

1. **抗抖**：所有引擎事实（攻击力/减伤/血量/征兵成本/采集资源）都**现读**。
   为什么这条要紧：数值一旦不是写死的那个（训练期抖动、调平衡、改政体），
   老师若还按写死的 2 支/50 攻/100 血做决策，**它给的标签就是错的** ——
   实测 20% 抖动下 v9 在 3/5 个 seed 上直接崩盘（只占 5 格、0 次进攻），
   v10 照常打（+12.5% 终局消费）。
   ★ 抖动注入器是 RL 线的活（`rl/jitter.py`，只在 `feat/rl`）；main 侧没有它，
     用"就地改 `game.*` 活表"验同一件事。**本分支两者都在**：`TestJitterRegression`
     走注入器，其余用例就地改表 —— 还原统一走下面 `RuleCase` 的活表快照。
2. **不绕山地**：v9 有两处山地特例（`TROOPS_FOR[山地]=3`、行军落点排除山地）。
   v10 去掉特例，改成**只看打不打得赢**（多轮估算）—— 山地只是减伤高的地形之一。

纪律：这些用例都会**就地改 `game.*` 的活表**，所以每例前后各拷一份、就地还原
（`clear()` + `update()`，**绝不替换容器** —— `mp.py` 拿的是同一个 dict 对象）。
漏了还原会污染后面所有用例，那种失败极难定位。
"""
from __future__ import annotations

import copy
import random
import unittest

import game
from mp import World
from rl import jitter

from ruleai import v10 as V10


def _world(seed: int = 0, size: int = 12) -> World:
    return World(size=size, seed=seed, nations=["秦"])


# 被这些用例就地改的活表。★`MARKET` 是 2026-09-18 补的：抖动注入器**也改市价**
# （`rl/jitter.py` 的 `game.MARKET.update`），而快照里原先没有它 ⇒ 抖过的粮/矿/油/装备价
# **漏进后面所有用例**（实测残留 4 个商品）。
_LIVE_TABLES = ("UNIT_TYPES", "TERRAIN_STATS", "BUILDINGS", "MARKET")


class RuleCase(unittest.TestCase):
    """基线类：活表快照 + 就地还原（口径见模块说明的"纪律"）。"""

    def setUp(self):
        self._snap = {k: copy.deepcopy(getattr(game, k)) for k in _LIVE_TABLES}

    def tearDown(self):
        # ★把注入器的**记录**也清掉（`MARKET` 已由上面的快照还原，这里管的是状态）：
        #   `_REC` 留着非空会让 `rl/scale.py` 以为"抖动正生效"而拒绝 `apply()`。
        # ⚠ **只在真抖过时碰它** —— 注入器第一次 `apply` 才抓真值快照；若在从没抖过的进程里
        #   无条件 `restore()`，它会把**当前**（可能已被本用例就地改过的）表当成"真值"记下来，
        #   那比原来的漏还原更毒（后面 `jitter.restore()` 会"还原"成被改过的值）。
        if jitter.current() is not None:
            jitter.apply(0, 0)          # 等价 `restore()`：清记录 + 还原 MARKET 等
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
        from ruleai import v9 as V9

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


class TestDegenerateEpisodeGuard(unittest.TestCase):
    """★退化局守卫（`rl/bc.py` 的 `episode_is_degenerate`）。

    抖动过大时老师可能整局"启动不起来"（领地停在开局 5 格、0 次进攻），
    那种局的样本几乎全是 end_turn —— 收进缓冲等于**教学生"别动"**。
    实测（20 图 × 80 回合）：±20% 健康局最少 12 格；±35% 起出现 ≤7 格的退化局。
    """

    def test_open_cross_is_always_degenerate(self):
        from rl.bc import episode_is_degenerate
        for seen in ([], [30], [12, 40, 33, 28]):
            self.assertTrue(episode_is_degenerate(5, seen), "开局 5 格 = 一格没打下来")

    def test_measured_gap(self):
        """实测间隔：退化 ≤7、健康 ≥12 —— 阈值要落在这中间，且不许误伤健康局。"""
        from rl.bc import episode_is_degenerate
        seen = [12, 22, 28, 33, 40, 50]          # ±20% 实测的一批
        for bad in (5, 6, 7):
            self.assertTrue(episode_is_degenerate(bad, seen), f"{bad} 格该判退化")
        for ok in (12, 22, 30, 40, 50):
            self.assertFalse(episode_is_degenerate(ok, seen), f"{ok} 格不该判退化")

    def test_relative_threshold_not_absolute(self):
        """阈值相对化：同一格数在"大盘面"里是退化、在"小盘面"里正常。

        （绝对阈值会随回合数/地图尺寸漂 —— 而"比别的局差一大截"是稳定信号。）
        """
        from rl.bc import episode_is_degenerate
        self.assertTrue(episode_is_degenerate(10, [40, 45, 50, 42]))
        self.assertFalse(episode_is_degenerate(10, [12, 11, 13, 12]))

    def test_few_samples_uses_floor(self):
        from rl.bc import episode_is_degenerate
        self.assertTrue(episode_is_degenerate(7, [50]))     # 样本少 → 只看 floor
        self.assertFalse(episode_is_degenerate(12, [50]))

    def test_dagger_episodes_are_never_degenerate(self):
        """★DAgger 局一律不判退化：那时领地反映的是**学生**，而学生不会扩张
        正是要打标签的东西（踩过：`--dagger-from 21` 一开，后半程 21 局全被丢弃，
        那半炉一个样本没进缓冲；且丢弃局不做梯度步 ⇒ 整炉只跑了 3.6 小时就"完成"）。"""
        from rl.bc import episode_is_degenerate
        for tiles in (5, 7, 12, 30):
            self.assertFalse(episode_is_degenerate(tiles, [30, 28, 33], turns=70,
                                                   student_driven=True))
        self.assertTrue(episode_is_degenerate(5, [30, 28, 33], turns=70))

    def test_short_episodes_are_never_degenerate(self):
        """★回合数不够时判据不成立：扩张本来就晚（首攻中位第 21 回合），
        短回合的冒烟/调试跑法本来就只有开局那 5 格 —— 别把它的样本丢掉。"""
        from rl.bc import episode_is_degenerate
        for turns in (8, 12, 30, 39):
            self.assertFalse(episode_is_degenerate(5, [], turns=turns),
                             f"{turns} 回合不该按退化处理")
        self.assertTrue(episode_is_degenerate(5, [], turns=70))


if __name__ == "__main__":
    unittest.main()


class TestTeacherHorizon(unittest.TestCase):
    """★老师的规划窗口口径（用户 2026-09-15：「v10 起，不设默认视野，
    恒等于回合数加 20」）。

    ## 为什么口径长这样

    原先 v10/v11/v12 各有一个 `HORIZON = 200` 常量，要外部 `set_horizon()` 覆盖才对准，
    而**设不上是静默的**：`mod.HORIZON = n` 对 v10（单文件）管用、对 v11/v12（包）
    只是给 `entry` 造了个新变量，经济层读的 `<代>.economy.HORIZON` 纹丝不动
    ⇒ 它们全程按 200 规划，而 v10 真的被设上了（500 回合那轮两边口径差 2.6 倍，
    一度被误读成"v11/v12 长局塌到 25%"）。
    历史教训（2026-09-11）：漏设时 70 回合的局按 200 回合规划，扩张明显变少
    （实测 20 图：领地 28→19、进攻 24→17）。

    ⇒ **能被设错的旋钮，不如没有旋钮**：现在口径唯一 ——
      `视野 = world.max_turns + 20`，由跑局的人把本局长度放进 world。
    """

    def test_no_horizon_knob_left(self):
        """★旋钮必须**彻底消失** —— 留一个"调了也没用"的接口正是当初那个坑。"""
        for ver, modname in (("v10", "ruleai.v10"), ("v11", "ruleai.v11"),
                             ("v12", "ruleai.v12")):
            with self.subTest(ver=ver):
                import importlib
                mod = importlib.import_module(modname)
                self.assertFalse(hasattr(mod, "HORIZON"),
                                 f"{ver} 还留着 HORIZON 旋钮（应当没有默认视野）")
                self.assertFalse(hasattr(mod, "set_horizon"),
                                 f"{ver} 还留着 set_horizon 接口")

    def test_get_teacher_takes_only_the_version(self):
        """`get_teacher` 不再收回合数/窗口（收了就是又在暗示"可以设"）。"""
        import inspect
        from rl.bc import get_teacher
        params = list(inspect.signature(get_teacher).parameters)
        self.assertEqual(params, ["which"],
                         f"get_teacher 签名是 {params} —— 视野不该再从这里设")

    def test_plan_window_follows_world_max_turns(self):
        """★决定性：**只改 `world.max_turns`**，老师的行为必须跟着变。

        同一张图、同一根种子、同一版老师，唯一的差别是"本局多长"——
        若行为不变，说明视野没接上 world（那正是 2026-09-15 那个 bug 的形状）。
        """
        import random
        from ruleai import v10
        from rl.env import ZhanguoEnv
        out = []
        for mt in (70, 500):
            env = ZhanguoEnv(map_size=16, rivals=("楚",), max_turns=mt)
            env.reset(900000)                    # ★world 要 reset 之后才存在
            w = env.world
            w.max_turns = mt
            rng = random.Random(0xB4BE)
            for _ in range(70):
                w.begin_turn()
                v10.expand_rule_turn_v10(w, env.agent, rng, max_actions=10 ** 9)
                w.resolve_turn()
            out.append((w.spend_total(env.agent), len(w.own_tiles(env.agent))))
        self.assertNotEqual(out[0], out[1],
                            f"改 world.max_turns 老师却没反应（{out}）—— 视野没接上 world")

    def test_set_episode_horizon_writes_world(self):
        """每局把**本局**长度写进 world（块调度里 BC 70 / DAgger 100 混着跑）。"""
        from rl.bc import set_episode_horizon
        from rl.env import ZhanguoEnv
        env = ZhanguoEnv(map_size=8, max_turns=10)
        env.reset(0)                             # ★world 要 reset 之后才存在
        set_episode_horizon(env.world, 100)
        self.assertEqual(env.world.max_turns, 100)

    def test_run_rule_writes_world(self):
        """`compare.run_rule` 也要把本局长度写进 world（它直接用引擎跑老师）。"""
        from rl.compare import run_rule
        from rl.env import ZhanguoEnv
        env = ZhanguoEnv(map_size=8, max_turns=10)
        run_rule(env, seed=0, turns=10, which="v10")
        self.assertEqual(env.world.max_turns, 10)


class TestBcLabelMatching(unittest.TestCase):
    """`rl/bc.py:match()` 的兜底语义（标签质量）。

    踩过：老师一次卖 20~49 个，而 `AMOUNTS` 上限 16 ⇒ 旧的"取同类别第一个候选"
    兜底给出 **amount 1** —— 标签变成「有 35 个余量 → 只卖 1 个」（实测占样本 2.3%）。
    """

    def _acts(self, kind, sub, amounts):
        from rl.env import Action
        return [Action(kind, sub, None, 0, n) for n in amounts]

    def test_exact_wins(self):
        from rl.bc import match
        acts = self._acts("sell", "木头", (1, 2, 8, 16))
        self.assertEqual(match(acts, ("sell", "木头", None, 0, 8)), 2)

    def test_fallback_picks_nearest_amount_not_first(self):
        from rl.bc import match
        acts = self._acts("sell", "木头", (1, 2, 8, 16))
        # 老师想卖 20（超出档位上限）→ 该给 16，而不是第一个（1）
        i = match(acts, ("sell", "木头", None, 0, 20))
        self.assertEqual(acts[i].amount, 16, "兜底该取最接近的档位")
        i = match(acts, ("sell", "木头", None, 0, 7))
        self.assertEqual(acts[i].amount, 8)

    def test_cross_kind_is_not_matched(self):
        from rl.bc import match
        acts = self._acts("buy", "木头", (1, 16))
        self.assertIsNone(match(acts, ("sell", "木头", None, 0, 20)))
