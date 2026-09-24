# -*- coding: utf-8 -*-
"""多玩家（#10）的守卫。用户 2026-09-24/25 的口径：

    「多玩家（**3 人起步**）+ ≥3 轮流手 + `n_nations = f(size)`」
    「**随机地图，3-5 个国家，随机大小，默认不结盟，默认无中立**」
    「开局 **5 个空权重**，**随机抽 pt 参与游戏**」

★ 钉的五件事，每条都对着一个**会静默出错**的口径：

  1. **国家数 = `f(size)`，落在 [3,5]** —— 且**单调**（图越大越多）。
  2. ★★ **"几个国家"不进观测形状** —— 军队 token 的归属段是**六类**（同网格），
     不再是「甲/乙」两位。**这条是整件事的地基**：否则多玩家一落地，
     观测宽度就变、基座全废（PLAN §12.6 那条次序警告）。
  3. ★★ **"敌人"是集合，不是一个** —— 原来 `_other(me)` 只取"另一个"
     ⇒ 三国局里第二个对手**整个不进观测**（军不 token、国土/兵力不进 `foe_*`），
     而**不报错**。这条用"敌国兵力 = 所有对手之和"钉死。
  4. **默认无中立 / 默认不结盟** —— 全对宣战；要中立得显式给 `wars=[...]`。
  5. **先手在 k 国之间轮换**（≥3 轮流手）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import encode, evaluate as E, vocab as V    # noqa: E402
from rl.sandbox import Sandbox, n_nations_for       # noqa: E402

BLIND = frozenset()


def sb_of(seed=1, size=8, n=None, **kw):
    return Sandbox(seed=seed, size=size, n_nations=n, **kw).reset()


class TestNationCount(unittest.TestCase):
    """① `n_nations = f(size)`，落在 [3,5]、单调。"""

    def test_within_range_and_monotone(self):
        prev = 0
        for size in range(8, 41):
            k = n_nations_for(size)
            self.assertGreaterEqual(k, 3, f"{size}×{size} 少于 3 国 ⇒ 违背「3 人起步」")
            self.assertLessEqual(k, 5, f"{size}×{size} 多于 5 国 ⇒ 违背「3-5 个国家」")
            self.assertGreaterEqual(k, prev, "国家数必须随地图**单调不减**")
            prev = k
        self.assertEqual(n_nations_for(8), 3)
        self.assertEqual(n_nations_for(32), 5, "32×32 该到 5 国（否则 3-5 只覆盖到一半）")


class TestSandboxMultiNation(unittest.TestCase):
    """②③④ 沙盒本身：k 国、全对宣战、每人有厅、先手可换。"""

    def test_players_and_halls(self):
        sb = sb_of(size=16)
        self.assertEqual(sb.n_nations, 3)
        self.assertEqual(len(sb.players), 3)
        self.assertEqual(sb.players, V.PLAYER_NAMES[:3])
        for p in sb.players:                       # ★ 每国开局都得有厅（否则一出场就死）
            self.assertIsNotNone(sb.core_of(p), f"{p} 没有市政厅")
            self.assertTrue(sb.alive(p), f"{p} 开局就是死的")
            self.assertEqual(len(sb.armies_of(p)), 5, f"{p} 开局不是 5 个步兵")

    def test_default_no_neutral_all_pairs_at_war(self):
        """★ 「默认**无中立**」= 全对宣战（不宣战的话 `_owner_class` 会判成中立国，
        而中立国 mv/atk **都不行** ⇒ 那国一步走不出去、也不挨打，成冻住的石头）。"""
        sb = sb_of(size=16)
        ps = sb.players
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                self.assertTrue(sb.world.war_between(a, b), f"{a}-{b} 没宣战")

    def test_explicit_wars_make_neutrals(self):
        """④ 反过来：要中立国得**显式**给 `wars=`（结盟不够 —— 缺省已全体交战）。"""
        sb = sb_of(size=16, wars=[("甲", "乙")])
        self.assertTrue(sb.world.war_between("甲", "乙"))
        self.assertFalse(sb.world.war_between("甲", "丙"), "丙该是中立国")
        # 中立国那格在观测里必须是 `OWN_NEUTRAL_NATION`（不是 `RIVAL`）——
        #   两类在引擎里的可行动作**相反**，混成一类网络就分不出该不该打。
        cell = sb.core_of("丙")
        self.assertEqual(encode._owner_class(sb.world, "甲", *cell),
                         V.OWN_NEUTRAL_NATION)

    def test_first_rotates_over_all_k(self):
        """⑤ 「**≥3 轮流手**」—— `first` 可以是 k 国里的任意一个。"""
        for p in ("甲", "乙", "丙"):
            sb = sb_of(size=16, first=p)
            self.assertEqual(sb.current_player(), p, f"first={p} 没生效")
        sb = sb_of(size=16, first="丁")            # 不在这一局的名单里 ⇒ 忽略
        self.assertEqual(sb.current_player(), "甲")


class TestShapeIsNationIndependent(unittest.TestCase):
    """★★ ③ 的地基：**几个国家不进观测形状**（多玩家一落地不会废掉基座）。"""

    def test_army_token_and_grid_widths_match_across_nation_counts(self):
        shapes = []
        for n in (3, 5):
            sb = sb_of(size=16, n=n)
            obs = encode.obs_of(sb, sb.players[0], sb.legal())
            shapes.append((obs["grid"].shape[0],                # 网格通道数
                           obs["win"]["a"].shape[1],            # 军队 token 宽
                           obs["win"]["g"].shape[1],            # 全局 token 宽
                           obs["cand"]["cand_marks"].shape[1]))
        self.assertEqual(shapes[0], shapes[1],
                         "3 国与 5 国的观测形状不同 ⇒ 多玩家会作废旧 ckpt（不能这样）")

    def test_army_owner_is_six_class_ownership(self):
        """归属段是**六类**（与网格同一套），不是「甲/乙」两位 one-hot。"""
        self.assertEqual(V.A_WIDTH_RAW,
                         V.A_OWNER0 + len(V.OWNER_CHANNELS) + len(V.UNIT) + 2 + 1 + 1 + 1)
        self.assertEqual(V.A_OWNER0, 0)


class TestEnemiesAreASet(unittest.TestCase):
    """③ ★★ 最要命的一条：**不许静默漏掉一个对手**。"""

    def test_enemies_of_returns_all_rivals(self):
        sb = sb_of(size=16)
        self.assertEqual(set(encode.enemies_of(sb.world, "甲")), {"乙", "丙"})
        # ★ 与打分器**同一口径**（两份实现必然漂移 ⇒ 只允许一份）
        self.assertEqual(sorted(encode.enemies_of(sb.world, "甲")),
                         sorted(E.rival_nations(sb.world, "甲")))

    def test_passing_mask_as_enemy_is_rejected(self):
        """★★ **把 `mask` 传进 `enemy` 位置 ⇒ 当场 `TypeError`**（防偷看的结构闸）。

        为什么必须有：`enemy` 现在可选 ⇒ `score(world, me, mask)`（想省掉 enemy）
        会被**静默**读成 `enemy=mask` 而 `mask` 保持 `None` ⇒ **全知打分**，
        而它**不报错**。我 2026-09-25 写测试时就踩了，那条测试**还通过了**。
        ★ 单靠"`mask` 仅限关键字"**挡不住**：`*` 只挡第 4 个位置参数，
          而危险的那个调用只有 3 个 ⇒ 照样通过（实测）。真正能挡的是
          **`enemy` 拒收集合**（`mask` 是 `frozenset`，"敌人"永远是国名字符串）。
        """
        sb = sb_of(size=16)
        with self.assertRaises(TypeError):
            E.score(sb.world, "甲", BLIND)              # 本意是 mask，会被当成 enemy
        with self.assertRaises(TypeError):
            E.score(sb.world, "甲", frozenset({"乙"}))   # 集合也不行（名字要用 list）
        # ★ 反向对照：**正确的**写法必须照常可用（别把闸门做成"什么都不能传"）
        self.assertIsInstance(E.score(sb.world, "甲", mask=BLIND), float)
        self.assertIsInstance(E.score(sb.world, "甲", "乙", mask=BLIND), float)
        self.assertIsInstance(E.score(sb.world, "甲", ["乙", "丙"], mask=BLIND), float)

    def test_no_foe_scalars_and_no_rule_derived_caps(self):
        """★★ 那五个"对手标量"**必须不在观测里**（用户 2026-09-25：「**全都不要了**」）。

        它们有**两个**病：
          ① **超越真玩家** —— 一次 `mask` 都没过 ⇒ 不侦察就知道对手的国土总量、
             军队总数、总血量；`foe_moved` 更是**战术级**泄露
             （「他们全军都动过了 ⇒ 现在打他」）。
          ② ★ **它们在数"训练用的规则"** —— `*_cap` = `5 + 国土//10` 是**沙盒的补员公式**，
             而「实际如何补员**由 llm 决定**，未来这些规则都会**情景化**」
             ⇒ 把它们当"知识"喂进去，模型学的是**沙盒规则**，不是**战局**。

        留下的 `foe_*` **只许是公开的国祚**（存活 + 位置：亡国是公开事件、
        厅位置走永久记忆账本）。
        """
        banned = {"foe_tiles", "foe_armies", "foe_cap", "foe_hp_frac", "foe_moved",
                  "my_cap"}
        self.assertEqual(banned & set(V.GLOB), set(),
                         f"这些列又回来了：{sorted(banned & set(V.GLOB))}")
        allowed = {"foe_hall", "foe_hall_dx", "foe_hall_dy", "foe_hall_d", "foe_halls"}
        self.assertEqual({c for c in V.GLOB if c.startswith("foe_")}, allowed,
                         "`foe_*` 只许剩**公开的国祚**那几列")
        # ★ 反向对照：观测量本身还得是活的（别把"删干净"做成"整段空掉"）
        sb = sb_of(size=16)
        g = encode.encode_glob(sb, "甲", BLIND, known={})
        self.assertGreater(float(g[V.GLOB.index("my_tiles")]), 0.0, "我自己的国土该还在")
        self.assertEqual(float(g[V.GLOB.index("foe_hall")]), 1.0, "对手存活是公开信息")

    def test_visible_rival_armies_all_enter_tokens(self):
        """两个对手的军，**只要看得见都该进 token**（原来只收"那一个敌人"的）。"""
        sb = sb_of(size=16)
        w = sb.world
        mask = frozenset(w.tiles)                  # 全看见
        armies = encode.window_armies(sb, "甲", mask)
        owners = {a["owner"] for a in armies}
        self.assertEqual(owners, {"甲", "乙", "丙"},
                         "有两个对手的军没进 token ⇒ 又退回「只认一个敌人」")

    def test_scorer_counts_all_rivals(self):
        """打分器：**两个**对手都进分数（原来只有"那一个"进）。

        ★ 战果账本按 `(凶手, 受害者)` 成对记，打分器按**当时的 `foes`** 筛
          ⇒ 「打乙」和「打丙」**都**要计，而「打野人」**一分不计**。
        """
        from rl import scoring as S
        sb = sb_of(size=16)
        base = E.score(sb.world, "甲", mask=BLIND, known={})

        def with_kills(k):
            return E.score(sb.world, "甲", mask=BLIND, known={}, kills=k) - base

        self.assertAlmostEqual(with_kills({("甲", "乙"): 1}), S.W_KILL, places=6,
                               msg="打乙没计 ⇒ 那个对手被静默漏掉")
        self.assertAlmostEqual(with_kills({("甲", "丙"): 1}), S.W_KILL, places=6,
                               msg="打丙没计 ⇒ 那个对手被静默漏掉")
        self.assertAlmostEqual(with_kills({("甲", "乙"): 1, ("甲", "丙"): 1}),
                               2 * S.W_KILL, places=6, msg="两个对手的战果都要计")
        self.assertAlmostEqual(with_kills({("甲", "野人"): 5}), 0.0, places=6,
                               msg="打野人不是「破敌」，不该加分")
        self.assertAlmostEqual(with_kills({("乙", "甲"): 5}), 0.0, places=6,
                               msg="**别人**的战果不该给我加分")

    def test_kills_and_dmg_terms_are_cumulative(self):
        """★★ 两项战果（用户 2026-09-25：「只计算我军杀掉的敌军来加分」
        ＋「`W_HP × (我的血 − 看得见的敌方血)` **也要改，和击杀一样**」）。

        · 累计计数进分数，且**完全不需要视野**（单调 ⇒ 不会闪断）；
        · 只认**打敌国**（成对记账 + 按 `foes` 筛）；
        · 不传账本 ⇒ 当成 0（**不是**退回"数看得见的东西"那条老路）。
        """
        from rl import scoring as S
        sb = sb_of(size=16)
        base = E.score(sb.world, "甲", mask=BLIND, known={})
        for n in (1, 3):
            self.assertAlmostEqual(
                E.score(sb.world, "甲", mask=BLIND, known={},
                        kills={("甲", "乙"): n}) - base,
                S.W_KILL * n, places=6, msg=f"击杀 {n} 支该加 {S.W_KILL * n} 分")
        for hp in (40, 250):
            self.assertAlmostEqual(
                E.score(sb.world, "甲", mask=BLIND, known={},
                        dmg={("甲", "丙"): hp}) - base,
                S.W_HP * hp, places=6, msg=f"打掉 {hp} 点血该加 {S.W_HP * hp} 分")
        # ★★ 两项战果**完全不吃视野** —— 正确的比法是"**隔离出战果的贡献**"：
        #   同一个 mask 下取"带战果 − 不带战果"，两个 mask 的这个差必须**一模一样**。
        #   （直接比两个 mask 的总分是错的：总分里还有 `W_TILE` 那一项，它**仍然吃视野**。）
        seen = frozenset(sb_of(size=16).world.tiles)
        for k, d in (({("甲", "乙"): 2}, None), (None, {("甲", "丙"): 300})):
            def contrib(mask):
                return (E.score(sb.world, "甲", mask=mask, known={}, kills=k, dmg=d)
                        - E.score(sb.world, "甲", mask=mask, known={}))
            self.assertAlmostEqual(contrib(BLIND), contrib(seen), places=6,
                                   msg="战果的贡献随视野变了 ⇒ 又回到「看得见才数」了")
        # ★★ **领土那半也去掉了**（2026-09-25 用户：「每个打分器只对自己国家负责…
        #   完全不需要什么视野地图」）⇒ 把**读敌人**的项关掉之后，分数**与 mask 无关**。
        with S.override(W_NEAR=0.0, W_THREAT=0.0):
            self.assertAlmostEqual(
                E.score(sb.world, "甲", mask=seen, known={}),
                E.score(sb.world, "甲", mask=BLIND, known={}), places=6,
                msg="除威胁系统之外还有项在吃视野 ⇒ 那条口径没落实")


class TestNoFogDependence(unittest.TestCase):
    """★★ 用户 2026-09-25 的口径（覆盖**除威胁系统以外**的全部项）：

        「每个打分器**只对自己国家负责**就行了，例如**甲打了一块地，甲自己的计分器
          加分，乙的扣分**，**完全不需要什么视野地图**」

    而**威胁系统是唯一的例外**，且那一项读敌军位置是**用户明说正确**的：
      「这里本来就是**暴露出的敌人越多防御越有价值，没暴露的也无法虚空防守**」。

    所以能钉死的不变量是：**用真实路径**（厅的位置来自**永久账本** `known_halls`）
    时，打分的**所有其它项都 mask 无关**。
    """

    def test_score_is_mask_independent_except_threat(self):
        from rl import scoring as S
        from rl.sandbox import Sandbox
        for spy in (True, False):
            sb = Sandbox(seed=1, size=16, halls_known=spy).reset()
            w, me = sb.world, "甲"
            if not spy:                        # 自己找厅：先让它"看见过"
                sb.known_halls(me, frozenset(w.tiles))
            emp, full = frozenset(), frozenset(w.tiles)
            a = E.score(w, me, mask=emp, known=sb.known_halls(me, emp))
            b = E.score(w, me, mask=full, known=sb.known_halls(me, full))
            self.assertAlmostEqual(a, b, places=9,
                                   msg=f"spy={spy}：除威胁外还有项吃视野")
        # ★ 反向对照：把敌军摆到我厅边上、**且看得见** ⇒ 威胁项必须让两者分开
        #   （否则上面那条"相等"可能只是因为威胁项整个是死的）
        sb = sb_of(size=16)
        w, me = sb.world, "甲"
        hall = E.hall_cells(w, me)[0]
        foe = next(f for f in sb.players if f != me)
        army = [x for x in w.armies if x["owner"] == foe][0]
        army["x"], army["y"] = hall           # 贴到我的厅上 ⇒ 必在视野内
        self.assertLess(E.score(w, me, mask=frozenset(w.tiles),
                                known=sb.known_halls(me, frozenset(w.tiles))), 1e9)
        with S.override(W_THREAT=0.0):
            emp2 = frozenset()
            x1 = E.score(w, me, mask=emp2, known=sb.known_halls(me, emp2))
            x2 = E.score(w, me, mask=frozenset(w.tiles),
                         known=sb.known_halls(me, frozenset(w.tiles)))
            self.assertAlmostEqual(x1, x2, places=9,
                                   msg="W_THREAT=0 后两者还不等 ⇒ 差不是威胁项来的")


class TestKillLedger(unittest.TestCase):
    """★★ 归因规则（引擎不记账，`mp.py` 又不许改 ⇒ 只能按「结算瞬间同格共处」归因）。

    快照格式：`before = {军id: (主人, x, y, 血)}`；结算后传**还活着的军**（dict 列表）。
    """

    class _W:
        def __init__(self, armies):
            self.armies = armies

    def _led(self, before, after):
        from rl.sandbox import KillLedger
        led = KillLedger()
        led.observe(self._W(after), before)
        return led

    def test_attributes_to_co_located_owners(self):
        led = self._led({1: ("甲", 3, 3, 100), 2: ("乙", 3, 3, 100), 3: ("丙", 7, 7, 100)},
                        [{"id": 1, "owner": "甲", "hp": 100}])     # 乙那支（id 2）死了
        self.assertEqual(led.kills_by("甲"), 1, "同格的甲该记一笔")
        self.assertEqual(led.kills_by("丙"), 0, "丙不在那一格，不该记")
        self.assertEqual(led.dmg_by("甲"), 100, "战死 ⇒ 整条血都算打掉的")

    def test_damage_without_death_counts(self):
        """★ **打伤不打死的血也要记**（用户：「`W_HP …` 也要改，和击杀一样」）。"""
        led = self._led({1: ("甲", 3, 3, 100), 2: ("乙", 3, 3, 100)},
                        [{"id": 1, "owner": "甲", "hp": 100},
                         {"id": 2, "owner": "乙", "hp": 60}])
        self.assertEqual(led.dmg_by("甲"), 40, "该记差额 40")
        self.assertEqual(led.kills_by("甲"), 0, "没死就不算击杀")
        self.assertEqual(led.dmg_by("乙"), 0, "乙没打人")

    def test_mutual_destruction_still_counts(self):
        """★ 互殴同归于尽**照样算**（用结算**前**的快照 ⇒ 死者也能当凶手）。"""
        led = self._led({1: ("甲", 3, 3, 100), 2: ("乙", 3, 3, 100)}, [])
        self.assertEqual(led.kills_by("甲"), 1)
        self.assertEqual(led.kills_by("乙"), 1)
        self.assertEqual(led.dmg_by("甲"), 100)
        self.assertEqual(led.dmg_by("乙"), 100)

    def test_no_one_co_located_means_no_credit(self):
        """★ 饿死/撤退/无人同格 ⇒ **没人记账**（别把"敌人自己没了"算成我的功劳）。"""
        led = self._led({1: ("乙", 5, 5, 100)}, [])
        self.assertEqual(led.snapshot(), ({}, {}))

    def test_barbarians_are_never_credited(self):
        """★ 打**野人**不算"破敌"（原来数 `foe_armies` 时野人本来就不在内）。"""
        led = self._led({1: ("野人", 3, 3, 100), 2: ("乙", 3, 3, 100)},
                        [{"id": 1, "owner": "野人", "hp": 100}])
        self.assertEqual(led.snapshot(), ({}, {}), "野人当凶手不该记账")

    def test_victim_filter_separates_rivals_from_barbarians(self):
        """★★ **成对记账**换来的能力：**只数"打敌国"**（打野人/盟友要能筛掉）。"""
        led = self._led({1: ("甲", 3, 3, 100), 2: ("乙", 3, 3, 100)},
                        [{"id": 1, "owner": "甲", "hp": 100}])
        self.assertEqual(led.kills_by("甲", victims={"乙"}), 1)
        self.assertEqual(led.kills_by("甲", victims={"丙"}), 0)
        self.assertEqual(led.kills_by("甲", victims={"野人"}), 0)

    def test_ledger_is_monotone(self):
        from rl.sandbox import KillLedger
        led = KillLedger()
        led.observe(self._W([]), {1: ("乙", 1, 1, 100), 2: ("甲", 1, 1, 100)})
        self.assertEqual(led.kills_by("甲"), 1)
        led.observe(self._W([{"id": 9, "owner": "甲", "hp": 100}]), {})   # 没死人
        self.assertEqual(led.kills_by("甲"), 1, "没死人还涨 ⇒ 不是计数是乱加")
        self.assertEqual(led.dmg_by("甲"), 100, "空快照不该记血（血数该停在战死那 100）")


if __name__ == "__main__":
    unittest.main()