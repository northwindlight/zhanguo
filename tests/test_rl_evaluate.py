# -*- coding: utf-8 -*-
"""`rl/evaluate.py` 打分器的守卫 —— 盟友那 50% 与"灭国"那一大笔。

用户 2026-09-24 两条口径：

  ① 「评分系统中应该加入**盟友的评分**，盟友评分**除了地皮分以外**，应该和**自己的
     评分机制一致**，但是**分数只有 50%**」
  ② 「**多国的情况下拿下一两个市政厅游戏并没有结束**，应该也纳入评分，**分数非常高**」

★ 写这个测试时踩到的**第一个坑**：第一版用 `World(size=14, seed=5, nations=[…])` 造局，
  而那个 World 里各国**一支军都没有** ⇒ `min_dist` 双方都是 99、国土又相等、hp 全 0
  ⇒ `score` 恒等于 **0.0**，断言 `0.0 == 0.0` **全绿但什么都没证明**。
  ⇒ 这里的每个场景都**先摆军队**，并额外 `assert` 分数非零 —— 否则测试会再次"骗人"。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mp import World                                # noqa: E402
from rl import evaluate as E                        # noqa: E402
from ruleai.v11plus.pathfind import vision_mask      # noqa: E402
from rl import scoring as S                         # noqa: E402


def mk_world(nations=("甲", "乙", "丙"), size=16, seed=5, war=("甲", "丙")):
    """造局并**摆上军队**（不摆军队的话所有项都是 0，测试会假绿）。"""
    w = World(size=size, seed=seed, nations=list(nations))
    w.declare_war(*war)                    # ★ 先宣战再结盟（在盟国宣战会变成投票）
    for nm in nations:
        core = next((c for c, t in sorted(w.tiles.items()) if t["owner"] == nm), None)
        if core is None:
            continue
        for i in range(2):
            gid, seq = w._new_army(nm)
            w.armies.append({"id": seq + i, "gid": gid, "name": f"{nm}{seq}",
                             "type": "步", "hp": 100 - 10 * i,
                             "x": core[0], "y": core[1], "owner": nm,
                             "moved_turn": -1, "engaged": False})
    return w


def mk_tile(w, owner, *, hall=False):
    """造一块**结构完整**的地块（照抄一个真地块的键）。

    ⚠ 手写 `{"owner":…, "terrain":…, "buildings":…}` 是**不够**的 —— `_conquer` 要
      `t["name"]`、`_defense_pct` 要 `buildings["城堡"]` ⇒ 少键会在**别的地方**炸
      （我第一版就栽在 `KeyError: 'name'`）。
    """
    t = dict(next(iter(w.tiles.values())))
    t["buildings"] = {k: 0 for k in t["buildings"]}
    t["owner"], t["core"] = owner, None
    if hall:
        t["buildings"]["市政厅"] = 1
    return t


def core(w, nm):
    return next((c for c, t in sorted(w.tiles.items())
                 if t["owner"] == nm and t["buildings"].get("市政厅", 0) > 0), None)


class TestAllyShare(unittest.TestCase):
    def test_ally_contribution_is_half_of_same_mechanism(self):
        """① 盟友那一份 = **0.5 × 同一套机制（不含国土差）**，且数字非零。

        ★ 结盟这件事**不只是**多出"盟友那一份"：乙从**对手**变成**盟友**，
          它的厅也就从 `−W_HALL`（对手的厅）变成 `+0.5·W_HALL`（盟友的厅）
          ⇒ 总额外变化 `1.5·W_HALL`。这不是 bug —— 把敌人的厅变成盟友的厅，本来就
          是巨大的改善。断言里必须把这一项算进来，否则会误判成"盟友那份算错了"。
        """
        w = mk_world()
        solo = E.score(w, "甲", "丙")
        self.assertNotEqual(solo, 0.0, "★ 分数是 0 —— 这个场景证不了任何事，先把它摆出非零")
        self.assertEqual(E.halls_of(w, "乙"), 1, "乙该有一座厅，这条才有意义")
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        both = E.score(w, "甲", "丙")
        part = E._one(w, "乙", "丙", None, with_tiles=False)     # ★ 不含国土差
        self.assertNotEqual(part, 0.0, "盟友那一份是 0 —— 同上，场景无效")
        # ★ 对手的厅**不进分数**了（用户纠正）⇒ 结盟的额外变化只有"盟友的厅那一份 50%"
        expect = 0.5 * part + 0.5 * S.W_HALL
        self.assertAlmostEqual(both - solo, expect, places=6)
        # ★ 用户 2026-09-24 把数点实了：「盟友赚厅应该有 **250** 进账，丢厅 −250」
        self.assertAlmostEqual(S.W_HALL, S.W_HALL_IN_INFANTRY * S.W_ARMY, places=6,
                               msg="★ 厅必须**等于 10 个步兵的分数**（用户口径），"
                                   "不是两处各写一个数")

    def test_ally_part_excludes_tiles(self):
        """① 盟友那一份**不含地皮分** —— 给盟友加地，它那一份必须**不动**。"""
        w = mk_world()
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        before = E._one(w, "乙", "丙", None, with_tiles=False)
        # 给盟友凭空加三格（直接改归属）
        free = [(x, y) for x in range(w.size) for y in range(w.size)
                if (x, y) not in w.tiles][:3]
        for c in free:
            w.tiles[c] = {"owner": "乙", "terrain": "平原", "buildings": {}, "core": None}
        self.assertGreater(E.tiles(w, "乙"), 5, "地没加上，这条测不出东西")
        self.assertAlmostEqual(E._one(w, "乙", "丙", None, with_tiles=False), before,
                               places=6, msg="★ 盟友那一份跟着地皮变了 ⇒ 没排除地皮分")
        # 对照：**我**那一份是含地皮的 ⇒ 应该跟着变
        mine_before = E._one(w, "甲", "丙", None, with_tiles=True)
        for c in free:
            w.tiles[c]["owner"] = "甲"
        self.assertNotAlmostEqual(E._one(w, "甲", "丙", None, with_tiles=True),
                                  mine_before, places=6, msg="我那一份该含地皮分")

    def test_ally_uses_the_same_mechanism_not_a_copy(self):
        """① "机制一致"要**可验证**：把 `_one` 里任一权重调一下，盟友那一份跟着变。

        防的是"盟友分是另写一份简化公式"—— 那样将来改 `_one` 的构成，盟友就漂开了。
        """
        w = mk_world()
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        before = E.score(w, "甲", "丙")
        old = S.W_ARMY
        try:
            S.W_ARMY = old * 3                      # 只改一个权重
            after = E.score(w, "甲", "丙")
            part = E._one(w, "乙", "丙", None, with_tiles=False)
        finally:
            S.W_ARMY = old
        self.assertNotEqual(before, after)
        # 盟友那一份里 W_ARMY 的贡献 = 0.5 × 3 × 军数（说明它走的是同一个 `_one`）
        self.assertAlmostEqual(after - before, 0.5 * (part - E._one(w, "乙", "丙", None,
                                                                    with_tiles=False))
                               + (3 - 1) * old * len(E.armies(w, "甲")), places=6)

    def test_ally_term_respects_vision(self):
        """★ 盟友那一份也**只对可见视野打分** —— 别让它变成偷看敌军的后门。"""
        w = mk_world(size=24)
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        w.tiles[(0, 0)] = {"owner": "丙", "terrain": "平原", "buildings": {}, "core": None}
        gid, seq = w._new_army("丙")
        w.armies.append({"id": seq, "gid": gid, "name": "丙X", "type": "步", "hp": 100,
                         "x": 0, "y": 0, "owner": "丙", "moved_turn": -1, "engaged": False})
        far = E._one(w, "乙", "丙", frozenset(), with_tiles=False)     # 空视野
        allknow = E._one(w, "乙", "丙", None, with_tiles=False)         # 全知
        self.assertNotEqual(far, allknow,
                            "★ 空视野与全知给出同一个数 ⇒ 盟友那一份没过 mask（偷看）")


def capture_hall(w, attacker, defender):
    """**用真引擎**让 `attacker` 攻占 `defender` 的核心格；返回该格坐标。

    ★ 不自己改 `owner` —— 用户 2026-09-24 那条纠正的**承重事实**是"打下一座厅之后
      那格归谁、厅还在不在"，必须由 `resolve_turn()` 说了算。实测：`甲厅 1→2`、
      建筑（含市政厅）保留 ⇒ 拿下**已经**体现在"我的厅 +1"里。
    """
    cx, cy = core(w, defender)
    w.armies[:] = [a for a in w.armies
                   if not (a["owner"] == defender and (a["x"], a["y"]) == (cx, cy))]
    gid, seq = w._new_army(attacker)
    w.armies.append({"id": seq, "gid": gid, "name": f"{attacker}{seq}", "type": "步",
                     "hp": 100, "x": cx, "y": cy, "owner": attacker,
                     "moved_turn": -1, "engaged": True})
    w.resolve_turn()
    assert w.owned_by(cx, cy) == attacker, "攻占没成功，这条测试的前提不成立"
    return (cx, cy)


def _far_own_tile(w, who, from_cell):
    """`who` 名下**非核心、且离 `from_cell` 最远**的一格 —— 在那上面加厅可以隔离变量。

    ★ 为什么非要"最远"：`_one` 的逼近/威胁项用 `min over 厅` 算距离。若新厅比核心
      **更近**敌人，`foe_d` 就会变小、威胁项跟着变 ⇒ 分数变化里混进别的东西，
      断言就不再是"只有厅那一项"。挑最远的那格 ⇒ `min` 不变 ⇒ 隔离干净。
    """
    return max((c for c, t in w.tiles.items()
                if t["owner"] == who and t["buildings"].get("市政厅", 0) == 0),
               key=lambda c: max(abs(c[0] - from_cell[0]), abs(c[1] - from_cell[1])))


class TestHallScore(unittest.TestCase):
    """★ 用户 2026-09-24 的四条口径：拿下一座厅/多国不结束/拿下≠灭国/赚丢都记分。"""

    def _world(self):
        w = mk_world(nations=("甲", "乙", "丙", "丁"), war=("甲", "丙"))
        return w

    def test_capturing_a_rival_hall_scores_very_high(self):
        """② 攻下对手一座厅 ⇒ 那格**归我**（厅跟着走）⇒ 我的厅 +1 ⇒ `+W_HALL`。"""
        w = self._world()
        before = E.score(w, "甲", "丙")
        self.assertNotEqual(before, 0.0, "场景无效（分数为 0）")
        n0 = E.halls_of(w, "甲")
        capture_hall(w, "甲", "丁")
        self.assertEqual(E.halls_of(w, "甲"), n0 + 1, "打下对手的厅，那座厅该归我")
        # ★ 别断言"恰好 +W_HALL"：攻占同时**多了一格地**（+`W_TILE`）并挪动了逼近项
        #   —— 实测差额 517 = 500 + 1 + 16。断言"厅那一项整额到账"就够。
        self.assertGreaterEqual(E.score(w, "甲", "丙") - before, S.W_HALL)
        # ★ 口径是**关系**（厅 = N 个步兵），不是"压过某某之和"那种我自己编的断言。
        self.assertAlmostEqual(S.W_HALL, S.W_HALL_IN_INFANTRY * S.W_ARMY, places=6)
        self.assertGreater(S.W_HALL, 5 * S.W_ARMY, "打下对手一座厅要顶得上好几支军")

    def test_enemy_hall_count_alone_scores_nothing(self):
        """★★ 用户那句的反面：**对手的厅数本身与我无关**。

        把对手的厅打掉（没变成我的），我的分数就该**一动不动** —— 上一版在这里会 +500，
        那既是**重复计**（拿下时"我的厅 +1"已经记过），又造成"侦察到对手的厅反而扣分"。
        """
        w = self._world()
        before = E.score(w, "甲", "丙")
        dx, dy = core(w, "丁")
        w.tiles[(dx, dy)]["buildings"]["市政厅"] = 0      # 丁 的厅没了，但没归我
        self.assertEqual(E.halls_of(w, "甲"), 1, "我的厅数不该变")
        self.assertAlmostEqual(E.score(w, "甲", "丙"), before, places=6,
                               msg="★ 对手少了几座厅不该改变我的分数")

    def test_two_halls_per_nation_partial_capture_scores(self):
        """★ 「**拿下市政厅不代表灭国**，例如每个国家有两个市政厅呢？」"""
        w = self._world()
        fx, fy = [(x, y) for x in range(w.size) for y in range(w.size)
                  if (x, y) not in w.tiles][0]
        w.tiles[(fx, fy)] = mk_tile(w, "丙", hall=True)
        self.assertEqual(E.halls_of(w, "丙"), 2)
        before = E.score(w, "甲", "丙")
        capture_hall(w, "甲", "丙")                       # 拿下其中一座
        self.assertTrue(w.has_townhall("丙"), "丙还有一座厅 ⇒ **没亡国**")
        self.assertIsNone(E.terminal(w, "甲"), "★ 只拿一座厅**不该**判成胜局")
        self.assertGreaterEqual(E.score(w, "甲", "丙") - before, S.W_HALL,
                                "★ 只拿一座厅没记分 —— 就是「拿下≠灭国」那条口径"
                                "（差额里还会有那一格地与逼近项，所以只断下界）")

    def test_gaining_and_losing_my_own_hall_both_score(self):
        """★ 「**赚厅和丢厅都记分了吗**」—— 我自己的厅，赚一座 +、丢一座 −。

        ★ 在**已有的自家格**上加厅（不是占新格）⇒ 国土数不变 ⇒ 变化里只有厅那一项。
        """
        w = self._world()
        before = E.score(w, "甲", "丙")
        fx, fy = _far_own_tile(w, "甲", core(w, "丙"))
        w.tiles[(fx, fy)]["buildings"]["市政厅"] = 1
        built = E.score(w, "甲", "丙")
        self.assertAlmostEqual(built - before, S.W_HALL, places=6,
                               msg="★ 赚厅没记分（或与丢厅不对称）")
        w.tiles[(fx, fy)]["buildings"]["市政厅"] = 0
        self.assertAlmostEqual(E.score(w, "甲", "丙"), before, places=6, msg="★ 丢厅没记分")

    def test_ally_hall_scores_at_half(self):
        """盟友的厅按 50% 记（与盟友那一份同折扣）；盟友亡**不是**进账。"""
        w = self._world()
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        before = E.score(w, "甲", "丙")
        fx, fy = _far_own_tile(w, "乙", core(w, "丙"))
        w.tiles[(fx, fy)]["buildings"]["市政厅"] = 1          # 盟友多一座厅
        self.assertAlmostEqual(E.score(w, "甲", "丙") - before,
                               S.ALLY_SHARE * S.W_HALL, places=6,
                               msg="盟友赚厅该只加 50%")
        w.tiles[(fx, fy)]["buildings"]["市政厅"] = 0          # 又丢了
        self.assertAlmostEqual(E.score(w, "甲", "丙"), before, places=6,
                               msg="盟友丢厅该只扣 50%")
        self.assertEqual(S.ALLY_DEAD, 0.0, "盟友亡不该变成进账")

    def test_all_rivals_dead_is_the_win(self):
        """③ 只剩**一个实体** ⇒ `+INF`；2 人局里"对手亡"就是这种情况 ⇒ 不重复计。"""
        w = mk_world(nations=("甲", "丙"), war=("甲", "丙"))
        cx, cy = core(w, "丙")
        w.tiles[(cx, cy)]["buildings"]["市政厅"] = 0
        self.assertEqual(E.score(w, "甲", "丙"), S.INF, "2 人局对手亡该直接是 +INF")
        self.assertEqual(E.rival_nations(w, "甲"), ["丙"])

    def test_alliance_victory_counts_as_my_win(self):
        """★ 「应该是**联盟胜利**或者单国胜利」—— 盟友还活着也算我赢。"""
        w = mk_world(nations=("甲", "乙", "丙"), war=("甲", "丙"))
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        self.assertEqual(E.terminal(w, "甲"), None, "两家实体 ⇒ 还没定局")
        cx, cy = core(w, "丙")
        w.tiles[(cx, cy)]["buildings"]["市政厅"] = 0
        self.assertEqual(E.terminal(w, "甲"), S.INF, "★ 只剩我的实体 ⇒ 联盟胜利")
        self.assertEqual(E.terminal(w, "乙"), S.INF, "盟友视角同样是赢")
        # ★ 我战死但我的盟赢了 ⇒ 也算我赢（"联盟胜利"的含义）
        ax, ay = core(w, "甲")
        w.tiles[(ax, ay)]["buildings"]["市政厅"] = 0
        self.assertEqual(E.terminal(w, "甲"), S.INF, "★ 我死了但联盟赢了 ⇒ 仍算赢")

    def test_distance_terms_cover_every_hall(self):
        """★ 「距离市政厅的厅，应该**对每个市政厅都生效**」—— 不能只看第一座。

        做法：让**第二座**厅旁边站着敌军；若距离项只认第一座，这个威胁就看不见。
        """
        w = self._world()
        ax, ay = core(w, "甲")
        fx, fy = [(x, y) for x in range(w.size) for y in range(w.size)
                  if (x, y) not in w.tiles and max(abs(x - ax), abs(y - ay)) > 4][0]
        w.tiles[(fx, fy)] = mk_tile(w, "甲", hall=True)
        far = E._one(w, "甲", "丙", None, with_tiles=True)     # 第二座厅旁没威胁
        gid, seq = w._new_army("丙")
        w.armies.append({"id": seq, "gid": gid, "name": "丙T", "type": "步", "hp": 100,
                         "x": fx + 1, "y": fy, "owner": "丙",
                         "moved_turn": -1, "engaged": False})
        near = E._one(w, "甲", "丙", None, with_tiles=True)
        self.assertLess(near, far,
                        "★ 敌军贴着我**第二座**厅，威胁项却没反应 ⇒ 距离项只认了第一座厅")


class TestScoringTableIsLive(unittest.TestCase):
    """★★ 先验表必须**当场可调** —— 用户：「打分标准是**先验**的，可能要**经常调**」。

    这一条防的是那个**不报错**的经典写法：`evaluate.py` 里若写成
    `from .scoring import W_ARMY`，值就被**绑死在导入时**了 —— 之后改
    `scoring.W_ARMY` 对打分毫无影响，而一切看起来都正常（引擎那边吃过同一个亏）。
    """

    def test_weights_are_read_live(self):
        w = mk_world()
        base = E.score(w, "甲", "丙")
        with S.override(W_ARMY=S.W_ARMY * 3):          # 只改一个权重
            bumped = E.score(w, "甲", "丙")
        self.assertAlmostEqual(bumped - base, 2 * S.W_ARMY * len(E.armies(w, "甲")),
                               places=6, msg="★ 改 scoring 表对打分没影响 ⇒ 值被绑死了")
        self.assertAlmostEqual(E.score(w, "甲", "丙"), base, places=6,
                               msg="`override` 退出后没还原")

    def test_hall_weight_and_ally_share_are_tunable(self):
        """厅那一项也要能调（`W_HALL` / `ALLY_SHARE`），且派生量是**乘出来的**。"""
        w = mk_world(nations=("甲", "乙", "丙", "丁"), war=("甲", "丙"))
        w.blocs.append({"name": "L", "chief": "甲", "members": ["甲", "乙"]})
        fx, fy = _far_own_tile(w, "乙", core(w, "丙"))
        # ★ `before`/`after` 必须**在同一个 override 里**取 —— 否则差里混进了
        #   "权重变了"这一项（第一版就是栽在这：250 vs 50，因为 before 用的是 500 那版）。
        with S.override(W_HALL=100.0):
            before = E.score(w, "甲", "丙")
            w.tiles[(fx, fy)]["buildings"]["市政厅"] = 1
            after = E.score(w, "甲", "丙")
        self.assertAlmostEqual(after - before, S.ALLY_SHARE * 100.0, places=6,
                               msg="盟友赚一座厅 = W_HALL × ALLY_SHARE，要能跟着调")

    def test_override_rejects_unknown_names(self):
        """写错名字要**当场报错**，不能静默无效（否则"调了但没生效"查不出来）。"""
        with self.assertRaises(KeyError):
            with S.override(W_ARMIES=1):               # 拼错了
                pass

    def test_describe_and_as_dict(self):
        d = S.as_dict()
        self.assertIn("W_HALL", d)
        self.assertIn("REWARD_TANH_SCALE", d)
        self.assertIn("W_HALL=", S.describe())


if __name__ == "__main__":
    unittest.main()

# ===========================================================================
class TestThreatNeedsOnlyMyHall(unittest.TestCase):
    """★★ "守家"那两项**只需要我自己的厅**，与"知不知道敌厅"无关。

    用户 2026-09-24 加安全/防御两项的原话：
      「怎么不可能回防，5 支军队赖着主城不动压根输不了，是目前的打分模型**没有奖励
        防御，没有安全扣分机制**」。
    而原实现把「逼近」和「威胁/守家」塞在**同一个** `if my_halls and foe_halls:` 里
    ⇒ 在"**自己找厅**"那一版（敌厅还没找到）里，**守家一分没有**
    —— 防御梯度整局缺席，而那恰恰是这一版最需要的东西。

    判据（为什么可以拆）：`foe_d` 走的是 `min_dist(world, enemy, …)` =
    **敌军**到我的厅的距离，**不是**敌厅到我的厅 ⇒ 跟敌厅在不在视野里毫无关系。

    ★ 每条都带**反断言**（否则"分数变了"可能来自别的项 —— 我在这份测试里踩过
      "全 0 相等"那种假绿，见文件头）。
    """

    def _setup(self, size=24, seed=5, far_gap=7):
        """摆一个"**敌厅在视野外**、但我有一块**远处的自家地**"的局面。

        ★ 为什么要自己造那块地：开局各国只有**一格**（核心），
          "把军挪远点但还在我地盘上"这个动作**根本不存在**（实测最远 1 格），
          上一版就栽在这 —— `assertGreater(dist, THREAT_R)` 直接红。
        ★ `mask` 直接**手工给**（我的视野 ∪ 那块远地，**不含敌厅**）：
          这里测的是**打分器**的口径，不是引擎的视野（视野另有 `TestVisionMask` 钉）。
        """
        w = mk_world(nations=("甲", "乙", "丙"), size=size, seed=seed, war=("甲", "乙"))
        my_hall = E.hall_cells(w, "甲")[0]
        foe_hall = E.hall_cells(w, "乙")[0]
        # 远处的一块自家地：离我的厅 ≥ far_gap 格、离敌厅也远（别把敌厅圈进视野）
        far = next(c for c in sorted(
            (x, y) for x in range(w.size) for y in range(w.size))
            if max(abs(c[0] - my_hall[0]), abs(c[1] - my_hall[1])) >= far_gap
            and max(abs(c[0] - foe_hall[0]), abs(c[1] - foe_hall[1])) >= far_gap
            and c not in w.tiles)
        w.tiles[far] = mk_tile(w, "甲")
        mask = frozenset(set(vision_mask(w, "甲")) | {far})
        self.assertNotIn(foe_hall, mask, "本场景的前提：敌厅在视野外")
        self.assertIn(far, mask, "那块远地得看得见（否则敌军挪过去就换了一档可见性）")
        return w, mask, my_hall, foe_hall, far

    def _foe_army(self, w):
        return next(a for a in w.armies if a["owner"] == "乙")

    def _my_armies(self, w):
        return [a for a in w.armies if a["owner"] == "甲"]

    def test_setup_is_non_vacuous(self):
        """前提必须成立：**敌厅真的看不见**、而且分数**不是恒 0**。"""
        w, mask, my_hall, foe_hall, far = self._setup()
        self.assertNotIn(foe_hall, mask, "本场景的前提：敌厅在视野外")
        self.assertEqual(E.hall_cells(w, "乙", mask), [],
                         "自己找厅 + 空记忆时，看不见的敌厅必须数不到")
        s = E.score(w, "甲", "乙", mask=mask)
        self.assertNotEqual(s, 0.0, "分数恒 0 ⇒ 下面的比较全是空的")

    def test_threat_is_alive_without_enemy_hall(self):
        """敌军逼近我的厅 ⇒ 扣分；把它挪远 ⇒ 不扣。**敌厅始终看不见。**

        ★ 2026-09-25 起不再需要关掉 `W_GUARD`（守家加成**已停用**，见下一条）。
        """
        w, mask, my_hall, foe_hall, far = self._setup()
        foe = self._foe_army(w)
        # 离我的厅 1 格（威胁圈内），且**仍在视野内**（贴着我自己的厅 ⇒ 一定看得见）
        near = next(c for c in w.neighbors(*my_hall) if c in mask and c != my_hall)
        foe["x"], foe["y"] = near
        s_near = E.score(w, "甲", "乙", mask=mask)
        self.assertLessEqual(E.min_dist(w, "乙", my_hall, mask), S.THREAT_R)
        foe["x"], foe["y"] = far          # 挪远（仍在视野内 ⇒ 其它项一字不变）
        s_far = E.score(w, "甲", "乙", mask=mask)
        self.assertLess(s_near, s_far,
                        "敌军逼近我的厅却没扣分 ⇒ 威胁项在「自己找厅」这一版里是死的")
        # ★ 对照：把威胁关掉 ⇒ 这个差必须消失（证明差只来自这一项）
        with S.override(W_THREAT=0.0):
            foe["x"], foe["y"] = near
            a = E.score(w, "甲", "乙", mask=mask)
            foe["x"], foe["y"] = far
            b = E.score(w, "甲", "乙", mask=mask)
        self.assertAlmostEqual(a, b, places=9, msg="W_THREAT=0 还有差 ⇒ 差不是威胁项来的")

    def test_threat_needs_the_enemy_to_be_exposed(self):
        """★★ **威胁必须"暴露"才成立**（这是用户 2026-09-25 明说正确的那一处）：

            「这里**本来就是暴露出的敌人越多防御越有价值，没暴露的也无法虚空防守**」

        ⇒ 这是全套打分里**唯一允许读敌军位置**的地方，而它的迷雾门控**不是毛病**
          （看不见的敌人本来就防不了）。判据：把敌军摆到我厅边上，但 `mask` 里
          **看不见那一格** ⇒ 威胁扣分**必须为 0**。
        """
        w, mask, my_hall, foe_hall, far = self._setup()
        foe = self._foe_army(w)
        near = next(c for c in w.neighbors(*my_hall) if c in mask and c != my_hall)
        foe["x"], foe["y"] = near
        s_vis = E.score(w, "甲", "乙", mask=mask)
        # ★ 只把**那一格**从视野里挖掉（其余不变）⇒ 威胁项该整块消失
        hole = frozenset(c for c in mask if c not in (near, near))
        s_blind = E.score(w, "甲", "乙", mask=hole)
        self.assertGreater(s_blind, s_vis,
                           "敌军看不见时也照样扣威胁分 ⇒ 变成「虚空防守」了")
        with S.override(W_THREAT=0.0):
            self.assertAlmostEqual(E.score(w, "甲", "乙", mask=hole),
                                   E.score(w, "甲", "乙", mask=mask), places=9,
                                   msg="W_THREAT=0 后两者还不等 ⇒ 差不是威胁项来的")

    def test_guard_bonus_is_retired(self):
        """★★ **守家加成已停用**（用户 2026-09-25：「其实**扣分就行了**」）。

        判据带**反向控制**：把 `W_GUARD` 调成 50（原值 5 的十倍），分数**必须一字不变**
        —— 否则说明它还在被读（那"停用"就是假的）。
        同时钉住：把军从厅边调到远处自家地，分数**不变**（没有守家收益了）。
        """
        w, mask, my_hall, foe_hall, far = self._setup()
        foe = self._foe_army(w)
        foe["x"], foe["y"] = next(c for c in w.neighbors(*my_hall)
                                  if c in mask and c != my_hall)   # 制造威胁
        mine = self._my_armies(w)
        for a in mine:
            a["x"], a["y"] = my_hall
        s_guard = E.score(w, "甲", "乙", mask=mask)
        with S.override(W_GUARD=50.0):
            self.assertAlmostEqual(E.score(w, "甲", "乙", mask=mask), s_guard, places=9,
                                   msg="改 W_GUARD 还影响分数 ⇒ 它没真停用")
        for a in mine:                     # 调远（仍在我地盘上 ⇒ 只有"守家"那一项会变）
            a["x"], a["y"] = far
        self.assertAlmostEqual(E.score(w, "甲", "乙", mask=mask), s_guard, places=9,
                               msg="守家还有加分 ⇒ 「扣分就行了」没落实")


    def test_proximity_still_needs_both_halls(self):
        """★★ 「逼近」项**仍然**要两边都知道厅 —— 拆开不能顺手把偷看放进来。

        测法：敌厅看不见时，把**我的军**挪到敌厅旁边（我并不知道那是厅）
        ⇒ 分数**必须一字不变**（否则等于隔雾点名敌厅位置）。
        """
        w, mask, my_hall, foe_hall, far = self._setup()
        self.assertNotIn(foe_hall, mask)
        mine = self._my_armies(w)
        for a in mine:
            a["x"], a["y"] = my_hall
        s0 = E.score(w, "甲", "乙", mask=mask)
        for a in mine:                              # 摸到敌厅边上（但看不见 ⇒ 不该有收益）
            a["x"], a["y"] = foe_hall
        s1 = E.score(w, "甲", "乙", mask=mask)
        self.assertAlmostEqual(s0, s1, places=9,
                               msg="看不见敌厅时「逼近」项却动了 ⇒ 那个项泄漏了敌厅位置")
