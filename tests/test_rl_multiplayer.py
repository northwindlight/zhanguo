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

    def test_glob_foe_aggregates_cover_every_rival(self):
        """三国各 5 支军 ⇒ `foe_armies` 该数到 **10**（不是一个对手的 5）。"""
        sb = sb_of(size=16)
        for p in sb.players:
            self.assertEqual(len(sb.armies_of(p)), 5)
        g = encode.encode_glob(sb, "甲", BLIND, known={})
        self.assertAlmostEqual(float(g[V.GLOB.index("foe_armies")]), 10 / 8.0, places=6,
                               msg="敌国兵力只数了一个对手 ⇒ 另一个对手被静默漏掉")
        # 国土同理：三国各 5 格 ⇒ 敌国国土合计 10（÷ 边长²）
        self.assertAlmostEqual(float(g[V.GLOB.index("foe_tiles")]), 10 / 256.0, places=6)

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

        ★ 双向（缺一条就是空测试）：
          · **看得见** ⇒ 打掉**丙**的军 / 打掉**乙**的军，**都**要让分数变
            （乙 = "原来那个对手"，丙 = "被静默漏掉的那个"）。
          · **看不见**（空 mask）⇒ 打谁分数都**一分不变**（防偷看那条同时钉住）。
          · 差值**正好** = `W_HP × 一支军的血`（残留的敌方 hp 项；击杀那一项已改成
            累计计数，见 `test_kills_term_is_cumulative_and_mine_only`）。
        """
        from rl import scoring as S
        want = S.W_HP * 100                        # 一支满血步兵的 hp 项 = 0.02×100 = 2

        def kill_diff(who, mask):
            sb = sb_of(size=16)
            w = sb.world
            s0 = E.score(w, "甲", mask=mask, known={})
            [a for a in w.armies if a["owner"] == who][0]["hp"] = 0
            return E.score(w, "甲", mask=mask, known={}) - s0

        seen = frozenset(sb_of(size=16).world.tiles)      # 全看见（军队都在自家核心上）
        for who in ("乙", "丙"):
            self.assertAlmostEqual(kill_diff(who, seen), want, places=6,
                                   msg=f"打掉「{who}」的军分数没动 ⇒ 这个对手不进分数")
        for who in ("乙", "丙"):                          # ★ 反向：看不见就必须一分不变
            self.assertAlmostEqual(kill_diff(who, BLIND), 0.0, places=6,
                                   msg=f"看不见「{who}」的军却影响分数 ⇒ 偷看")

    def test_kills_term_is_cumulative_and_mine_only(self):
        """★★ 击杀那一项（用户 2026-09-25：「只计算**我军**杀掉的敌军来加分」）。

        · 我的击杀数进分数（`W_KILL × 支数`），且**不需要视野**（单调计数）；
        · **对手**的击杀数**不进我的分数**（那是"我被杀了"的份，不能变成加分）。
        """
        from rl import scoring as S
        sb = sb_of(size=16)
        base = E.score(sb.world, "甲", mask=BLIND, known={})
        for n in (1, 3):
            got = E.score(sb.world, "甲", mask=BLIND, known={}, kills={"甲": n})
            self.assertAlmostEqual(got - base, S.W_KILL * n, places=6,
                                   msg=f"击杀 {n} 支该加 {S.W_KILL * n} 分")
        self.assertAlmostEqual(
            E.score(sb.world, "甲", mask=BLIND, known={}, kills={"乙": 9}), base,
            places=6, msg="对手的击杀数不该给我加分")
        # ★ 不传账本 ⇒ 当成 0（**不是**退回"数看得见的敌人"那条老路）
        self.assertAlmostEqual(base, E.score(sb.world, "甲", mask=BLIND, known={}),
                               places=6)


class TestKillLedger(unittest.TestCase):
    """★★ 归因规则（引擎不记账，`mp.py` 又不许改 ⇒ 只能按"结算瞬间同格共处"归因）。"""

    class _W:
        def __init__(self, armies):
            self.armies = armies

    def test_attributes_to_co_located_owners(self):
        from rl.sandbox import KillLedger
        led = KillLedger()
        before = {1: ("甲", 3, 3), 2: ("乙", 3, 3), 3: ("丙", 7, 7)}
        led.observe(self._W([{"id": 1, "owner": "甲"}]), before)   # 乙那支（id 2）死了
        self.assertEqual(led.kills_by("甲"), 1, "同格的甲该记一笔")
        self.assertEqual(led.kills_by("丙"), 0, "丙不在那一格，不该记")

    def test_mutual_destruction_still_counts(self):
        """★ 互殴同归于尽**照样算**（用结算**前**的快照 ⇒ 死者也能当凶手）。"""
        from rl.sandbox import KillLedger
        led = KillLedger()
        before = {1: ("甲", 3, 3), 2: ("乙", 3, 3)}
        led.observe(self._W([]), before)                # 两个都死了
        self.assertEqual(led.kills_by("甲"), 1)
        self.assertEqual(led.kills_by("乙"), 1)

    def test_no_one_co_located_means_no_credit(self):
        """★ 饿死/撤退/无人同格 ⇒ **没人记账**（别把"敌人自己没了"算成我的功劳）。"""
        from rl.sandbox import KillLedger
        led = KillLedger()
        before = {1: ("乙", 5, 5)}
        led.observe(self._W([]), before)
        self.assertEqual(led.all(), {})

    def test_ledger_is_monotone_and_per_nation(self):
        from rl.sandbox import KillLedger
        led = KillLedger()
        led.observe(self._W([]), {1: ("乙", 1, 1), 2: ("甲", 1, 1)})
        self.assertEqual(led.kills_by("甲"), 1)
        led.observe(self._W([{"id": 9, "owner": "甲"}]), {})        # 没死人
        self.assertEqual(led.kills_by("甲"), 1, "没死人还涨 ⇒ 不是计数是乱加")


if __name__ == "__main__":
    unittest.main()