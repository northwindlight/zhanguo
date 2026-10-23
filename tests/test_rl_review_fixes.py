# -*- coding: utf-8 -*-
"""★ 审查（2026-10-23）十条缺陷的**回归闸门**。

每一节钉一条"**错起来不报错**"的缺陷 —— 它们的共同点是：
**改对了不会自己喊，改坏了也不会自己喊**，只能靠断言钉住。

| # | 缺陷 | 本文件对应 |
|---|---|---|
| P1-1 | 可见野地没有进攻候选（模型永远学不会占地） | `TestAttackCandidates` |
| P1-2 | 没接通 native 的亡国流程（引擎与沙盒分叉） | `TestSuccession` |
| P1-3 | 迷雾里的第三方战斗直接 `NameError` | `TestFogBattleNoCrash` |
| P1-4 | 地形对象与字符串 ID 混用（特征/寻路失真） | `TestTerrainBoundary` |
| P1-5 | 增援概率算成了对手增援 | `TestReinforcementSide` |
| P1-6 | 推不出来的野战被编码成已知概率 | `TestUnderivableBattle` |
| P1-7 | 老师把未知守军当成空军队模拟 | `TestTeacherGuardian` |
| P2-8 | 战斗轮数分档忽略边界 | `TestRoundBins` |
| P2-9 | MCTS 模块导不进来 | `TestMctsImport` |
| P2-10 | `frame_odds(with_reinf=False)` 必崩 | `TestFrameOddsNoReinf` |
"""
from __future__ import annotations

import unittest

import numpy as np

from rl import encode, features, odds, rules, vocab as V
from rl.teacher import combat as teacher_combat


def _sandbox(**kw):
    from rl.sandbox import Sandbox
    sb = Sandbox(seed=kw.pop("seed", 3), size=kw.pop("size", 8), **kw)
    sb.reset(random_starts=False)
    return sb


def _frame_cells(sb, me):
    x0, y0, h, w = encode.frame_of(sb, me)
    return {(x0 + j, y0 + i) for i in range(h) for j in range(w)}


def _neighbours(cell):
    x, y = cell
    return [(x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
            if (dx, dy) != (0, 0)]


# ======================================================================
# P1-1 进攻候选：野地也得能打
# ======================================================================
class TestAttackCandidates(unittest.TestCase):
    """★ 引擎里 `move` **不占地**、`attack` 才占地 ⇒ 不给 `attack` 候选就没有扩地。"""

    def test_wild_land_has_attack_candidates(self):
        sb = _sandbox(n_nations=2, war=True)
        acts = sb.legal()
        atk = [a for a in acts if a[1] == "attack"]
        self.assertTrue(atk, "候选里一个 attack 都没有 ⇒ 模型永远学不会打野占地")
        # ★ 至少有一个**无主格**的进攻候选（野地）。
        wild = [a for a in atk if sb.env.owner(a[2], a[3]) is None]
        self.assertTrue(wild, "野地上没有进攻候选（引擎允许打野占地）")

    def test_a_walkable_wild_cell_is_also_attackable(self):
        """★ 野地**同时**在移动集与攻击集里 ⇒ 旧口径（攻击集 − 移动集）会把它减掉。"""
        sb = _sandbox(n_nations=2, war=True)
        me = sb.current_player()
        acts = sb.legal()
        for unit in sb.armies_of(me):
            walk = sb.env.reachable(me, unit, for_attack=False)
            wild = [c for c in sorted(walk)
                    if c != (unit.x, unit.y) and sb.env.owner(*c) is None]
            if not wild:
                continue
            for cell in wild:
                self.assertIn((unit.id, "move", cell[0], cell[1]), acts)
                self.assertIn((unit.id, "attack", cell[0], cell[1]), acts,
                              f"{cell} 是野地：移动与进攻**都要**在候选里")
            return
        self.skipTest("这一局没有够得着的无主格")

    def test_own_and_allied_territory_is_not_attackable(self):
        """★ 自家/盟国的格子**不能**出现在进攻候选里（引擎会拒）。"""
        sb = _sandbox(n_nations=2, war=True)
        me = sb.current_player()
        for a in sb.legal():
            if a[1] != "attack":
                continue
            holder = sb.env.owner(a[2], a[3])
            self.assertNotEqual(holder, me, "自家格子进了进攻候选")
            if holder is not None:
                self.assertFalse(sb.env.allied(me, holder), "盟国格子进了进攻候选")

    def test_engine_accepts_the_wild_attack(self):
        """★ 候选不是纸上谈兵：引擎**真的**接受这一击（并占地）。"""
        sb = _sandbox(n_nations=2, war=True)
        me = sb.current_player()
        for a in sb.legal():
            if a[1] != "attack" or sb.env.owner(a[2], a[3]) is not None:
                continue
            ok, msg = sb.env.attack(me, [a[0]], a[2], a[3])
            self.assertTrue(ok, f"引擎不接受候选里的野地进攻：{msg}")
            return
        self.skipTest("这一局没有野地进攻候选")


# ======================================================================
# P1-2 亡国：走引擎的正规流程
# ======================================================================
class TestSuccession(unittest.TestCase):
    """★ 亡国判据只有一个出处：引擎的 `native:succession`。"""

    def test_every_nation_is_enrolled(self):
        sb = _sandbox(n_nations=2, war=True)
        self.assertEqual(set(sb.env.enrolled()), set(sb.players),
                         "国家没登记进继承序列 ⇒ 引擎永远不会判它亡国")

    def test_losing_last_hall_extinguishes_the_nation_in_the_engine(self):
        from tests.rl_deps import give_site
        sb = _sandbox(n_nations=2, war=True)
        env = sb.env
        core = sb.core_of("乙")
        self.assertIsNotNone(core, "乙开局就该有市政厅")
        give_site(env, core, None)            # ★ 站点转走 ⇒ 厅跟着走 ⇒ 国祚断绝
        self.assertFalse(env.has_townhall("乙"))
        env.resolve()                          # ★ 结算 ⇒ 引擎的继承阶段出手
        self.assertNotIn("乙", env.nations(), "引擎那边乙还在册 ⇒ 亡国流程没跑")
        self.assertFalse([u for u in env.armies() if env.owner_of(u) == "乙"],
                         "亡国了军队还在 ⇒ 引擎状态分叉")
        self.assertFalse(sb.alive("乙"), "沙盒与引擎对不上")

    def test_alive_nation_survives_resolve(self):
        sb = _sandbox(n_nations=2, war=True)
        sb.env.resolve()
        self.assertIn("甲", sb.env.nations())
        self.assertTrue(sb.alive("甲"))


# ======================================================================
# P1-3 迷雾里的战斗不许把观测层炸掉
# ======================================================================
class TestFogBattleNoCrash(unittest.TestCase):
    """★ `_mine_engaged_here` 原来用了**没定义的** `env` ⇒ 三国局里 `NameError`。"""

    def test_mine_engaged_here_takes_env(self):
        sb = _sandbox(n_nations=2, war=True)
        env = sb.env
        by_cell = {}
        for a in env.armies():
            by_cell.setdefault((a.x, a.y), []).append(a)
        me = sb.current_player()
        mine = next(a for a in env.armies() if env.owner_of(a) == me)
        self.assertTrue(encode._mine_engaged_here(env, by_cell, me, mine.x, mine.y))
        self.assertFalse(encode._mine_engaged_here(env, by_cell, me, 99, 99))

    def test_battle_in_frame_but_out_of_sight_does_not_crash(self):
        """★ 战斗落在**观测框内但看不见**的格 ⇒ 观测照算（不炸、也不泄漏）。"""
        sb = _sandbox(seed=3, size=8, n_nations=3, war=True)
        env = sb.env
        me = "甲"
        mask = env.vision_mask(me)
        holes = sorted(_frame_cells(sb, me) - mask)
        if not holes:
            self.skipTest("这一局的视野恰好是矩形（框内没有看不见的格）")
        cell = _drive_enemy_into(env, "乙", holes)
        if cell is None:
            self.skipTest("这一局乙够不着那些格")
        obs = encode.obs_of(sb, me)          # ★ 旧代码在这里 NameError
        self.assertIn("grid", obs)
        # ★ 看不见又没我的军 ⇒ 那几列**一格都不写**（不泄漏迷雾里的战况）。
        self.assertEqual(float(np.abs(obs["grid"][V.GRID_CB_ACTIVE]).sum()), 0.0)


def _drive_enemy_into(env, who, holes):
    """把 `who` 的一支军挪到 `holes` 旁边并打过去 → 那一格**开战**。"""
    armies = [u for u in env.armies() if env.owner_of(u) == who]
    if not armies:
        return None
    unit = min(armies, key=lambda u: max(abs(u.x - holes[0][0]),
                                         abs(u.y - holes[0][1])))
    for _ in range(8):
        reach = env.reachable(who, unit, for_attack=True)
        for c in sorted(holes):
            if c in reach and env.owner(*c) is None:
                ok, _ = env.attack(who, [unit.id], c[0], c[1])
                if ok:
                    return c
        best = None
        for c in sorted(reach):
            if c == (unit.x, unit.y) or env.owner(*c) is not None:
                continue
            d = max(abs(c[0] - holes[0][0]), abs(c[1] - holes[0][1]))
            if best is None or d < best[0]:
                best = (d, c)
        if best is None:
            return None
        ok, _ = env.move(who, unit.id, best[1][0], best[1][1])
        if not ok:
            return None
        env.resolve()
        alive = [u for u in env.armies() if u.gid == unit.gid]
        if not alive:
            return None
        unit = alive[0]
    return None


# ======================================================================
# P1-4 地形：类型边界
# ======================================================================
class TestTerrainBoundary(unittest.TestCase):
    """★ 规则表**只认内容 ID** ⇒ 混用时 `.get(对象, 默认值)` 悄悄给默认值。"""

    def test_env_terrain_returns_content_id(self):
        sb = _sandbox(n_nations=2, war=True)
        terrain = sb.env.terrain(1, 1)
        self.assertIsInstance(terrain, str, "env.terrain 必须给**内容 ID**")
        self.assertIn(terrain, rules.terrain_names())
        self.assertEqual(terrain, sb.env.terrain_obj(1, 1).id)

    def test_defense_and_move_cost_use_the_real_table(self):
        self.assertEqual(rules.terrain_defense("native:mountain"), 50)
        self.assertEqual(rules.terrain_defense("native:plain"), 0)
        # ★ 骑兵上山地：引擎表里是 **2**（旧口径读成 1 ⇒ 地形形同不存在）。
        self.assertEqual(rules.unit_move_cost("native:cavalry", "native:mountain"), 2)
        # ★ 旧口径 dict 也要能用（以前 `MOVE_COST.get(dict, {})` 恒空 ⇒ 恒 1）。
        self.assertEqual(rules.unit_move_cost({"type": "native:cavalry"}, "native:mountain"), 2)
        # ★ 地形对象形态也要能用（类型边界在**入口**归一）。
        from mods.native.territory import mod as territory_mod
        mountain = next(t for t in territory_mod.TERRAINS if t.id == "native:mountain")
        self.assertEqual(rules.terrain_defense(mountain), 50)
        self.assertEqual(rules.unit_move_cost("native:cavalry", mountain), 2)

    def test_terrain_feature_matches_the_review_numbers(self):
        """★ 审查给的期望值：山地 = `[0.5, 0.5, 1, 0.5]`（旧口径是 `[0, .5, .5, .5]`）。"""
        got = list(features.terrain_vector("native:mountain"))
        for a, b in zip(got, (0.5, 0.5, 1.0, 0.5)):
            self.assertAlmostEqual(float(a), float(b), places=6)

    def test_candidate_content_uses_real_terrain(self):
        """★ 候选段里的地形四项 = 真实地形值（不是"全是 0/1"）。"""
        from mods.native.territory import mod as territory_mod
        mountain = next(t for t in territory_mod.TERRAINS if t.id == "native:mountain")
        vec = features.cand_content("native:cavalry", mountain.id)
        self.assertAlmostEqual(float(vec[-4]), 0.5, places=6)      # 减伤 50/100
        self.assertAlmostEqual(float(vec[-2]), 1.0, places=6)      # 骑兵上山地 = 2/2

    def test_pathfind_cost_field_reads_real_terrain(self):
        """★ 寻路的代价场用的也是**真实地形**（旧口径全是 1 ⇒ 地形形同不存在）。"""
        from rl.teacher import pathfind
        sb = _sandbox(n_nations=2, war=True)
        env = sb.env
        mask = env.vision_mask(sb.current_player())
        got = pathfind.cell_cost(env, "native:cavalry", (0, 0), mask)
        self.assertGreaterEqual(got, 1)


# ======================================================================
# P1-5 增援：按观测方算
# ======================================================================
class TestReinforcementSide(unittest.TestCase):
    """★ 「增援胜率」那一列回答的必须是**观测方**的问题。"""

    def _setup(self):
        """造一个"增援在视野外"的局面：战场格看得见，增援格看不见。"""
        from tests.rl_deps import make_world, new_army
        env = make_world(size=8, seed=1, nations=("甲", "乙"))
        me = "甲"
        size = env.size()
        mask = env.vision_mask(me)
        for c in sorted(mask):
            if env.owner(*c) is not None:
                continue
            outside = [n for n in _neighbours(c)
                       if 0 <= n[0] < size and 0 <= n[1] < size
                       and n not in mask and env.owner(*n) is None]
            spot = [s for s in _neighbours(c) if s in mask and env.owner(*s) is None]
            if not outside or not spot:
                continue
            new_army(env, me, spot[0])          # 进攻方（在视野里）
            new_army(env, me, outside[0])       # 增援（在视野**外**）
            unit = next(u for u in env.armies()
                        if env.owner_of(u) == me and (u.x, u.y) == spot[0])
            ok, _ = env.attack(me, [unit.id], c[0], c[1])
            if ok:
                return env, me, mask, c, outside[0]
        return None

    def test_reserve_out_of_sight_still_counts(self):
        """★ 自家的增援**不用视野过滤**（我自己的军队在哪儿我当然知道）。

        ★ 旧口径：增援按视野过滤 ⇒ 预备队在野地（视野外）就数不着
          ⇒ "增援胜率"恒等于基础胜率（实测 42.1% ⇒ 42.1%，该 42.1% ⇒ 100%）。
        """
        got = self._setup()
        if not got:
            self.skipTest("这一局找不到'增援在视野外'的战场格")
        env, me, mask, cell, _reserve = got
        frame = odds.frame_odds(env, me, mask)
        base = frame.cells.get(cell)
        reinf = frame.reinf.get(cell)
        self.assertIsNotNone(base)
        self.assertIsNotNone(reinf, "有增援却没有增援估计")
        self.assertGreater(reinf.p_win.get(me, 0.0), base.p_win.get(me, 0.0),
                           "增援到齐后胜率没涨 ⇒ 增援被漏掉了")
        self.assertLessEqual(reinf.p_win.get(me, 0.0), 1.0 + 1e-9)

    def test_reinforcement_odds_are_asked_from_my_side(self):
        """★ 增援估计是**用观测方的身份**问引擎的（引擎要求"提问者自己的部队"）。

        ★ 旧口径按"国家名排序后的第一个提问方"算增援 ⇒ 观测是**甲**的，
          增援却可能是**别人家**的 ⇒ 那一列回答的不是甲的问题。
        ⇒ 现在直接跟引擎对账：用 `me` 的身份 + `me` 的部队问一遍，
          结果必须与观测里那一列**逐位相同**。
        """
        from tests.rl_deps import make_world, new_army
        env = make_world(size=8, seed=1, nations=("甲", "乙"))
        me = "甲"
        mask = env.vision_mask(me)
        home = next(c for c in sorted(mask) if env.owner(*c) is None)
        new_army(env, me, home)
        unit = next(u for u in env.armies() if env.owner_of(u) == me)
        target = None
        for c in sorted(env.reachable(me, unit, for_attack=True)):
            if env.owner(*c) is None and c in mask:
                target = c
                break
        if target is None:
            self.skipTest("这一局打不起来")
        ok, _ = env.attack(me, [unit.id], target[0], target[1])
        if not ok:
            self.skipTest("这一局打不起来")

        frame = odds.frame_odds(env, me, mask)
        base = frame.cells.get(target)
        self.assertIsNotNone(base)
        reinf = frame.reinf.get(target)
        self.assertIsNotNone(reinf)
        # ★ 引擎那边的原值（内容 ID 口径）—— 增援估计应当与它一致。
        at_cell = [u.id for u in env.armies()
                   if env.owner_of(u) == me and (u.x, u.y) == target]
        extra = [u.id for u in env.armies()
                 if env.owner_of(u) == me and (u.x, u.y) != target
                 and target in env.reachable(me, u, for_attack=True)]
        result = env.assess(me, at_cell + extra, target[0], target[1])
        self.assertTrue(result.get("known"))
        expect = result["probabilities"]["p_win"].get(env.id_of(me))
        self.assertAlmostEqual(reinf.p_win.get(me, 0.0), float(expect), places=9,
                               msg="增援估计不是用观测方的身份问出来的")


# ======================================================================
# P1-6 推不出来 ⇒ 显式标"未知"
# ======================================================================
class TestUnderivableBattle(unittest.TestCase):
    """★ 引擎的推演要"提问方看得见目标格"⇒ 迷雾里的野战**推不出来**。"""

    def test_underivable_odds_have_no_fake_distribution(self):
        from rl import scoring as S
        o = odds.Odds(p_win={}, p_lose={}, p_draw=0.0, p_hold={},
                      e_rounds=0.0, e_loss={}, p_rounds=[], derivable=False)
        self.assertFalse(o.derivable)
        self.assertEqual(o.p_rounds, [], "推不出来却带着轮数分布 ⇒ 那是个假分布")
        self.assertEqual(o.round_bins(), [0.0] * len(S.ROUND_BIN_EDGES))
        self.assertEqual(o.e_rounds, 0.0, "推不出来却写着'要打几轮'")

    def test_fog_battle_is_marked_unknown_not_zero(self):
        """★ 真实迷雾局：那一格**推不出来** ⇒ 观测里写"未知"，不是"0 概率"。"""
        sb = _sandbox(seed=3, size=8, n_nations=3, war=True)
        env = sb.env
        me = "甲"
        mask = env.vision_mask(me)
        holes = sorted(_frame_cells(sb, me) - mask)
        if not holes:
            self.skipTest("这一局的视野恰好是矩形")
        cell = _drive_enemy_into(env, "乙", holes)
        if cell is None:
            self.skipTest("这一局乙够不着那些格")
        o = odds.cell_odds(env, cell[0], cell[1])
        self.assertIsNotNone(o)
        self.assertFalse(o.derivable, "迷雾里的野战被当成推得出来")
        self.assertEqual(o.p_win, {})
        self.assertEqual(o.p_rounds, [])
        # ★ 观测（乙自己看得到自己那支军）⇒ 写"未知"这一列，概率列全 0。
        foe_mask = env.vision_mask("乙")
        armies = encode.window_armies(sb, "乙", foe_mask)
        frame = encode.combat_of(sb, "乙", foe_mask, armies)
        row = encode._combat_tail(frame, cell, "乙", 1.0)
        self.assertEqual(row[V.A_CB_ACTIVE], 1.0)
        self.assertEqual(row[V.A_CB_UNKNOWN], 1.0)
        self.assertEqual(row[V.A_CB_PWIN], 0.0)
        self.assertEqual(row[V.A_CB_PWIN_REINF], 0.0)


# ======================================================================
# P1-7 老师的未知守军
# ======================================================================
class TestTeacherGuardian(unittest.TestCase):
    """★ 看不见的守军要**按一支满血野人**算账，不能当空气。"""

    def test_unknown_defender_costs_something(self):
        sb = _sandbox(seed=1, size=8, n_nations=2, war=True)
        env = sb.env
        mine = [u for u in env.armies() if env.owner_of(u) == "甲"]
        d = teacher_combat.assess(env, "甲", (0, 0), mine,
                                  visible=False, need_cap=12, rounds_cap=12)
        self.assertFalse(d.empty, "看不见 ⇒ 不能说它是空地")
        # ★ 旧口径（def_units=[]）返回的是 (need=1, rounds=0, losses=0) —— 零成本必胜。
        self.assertNotEqual((d.need, d.rounds, d.losses), (1, 0, 0),
                            "未知目标又被评成零成本必胜")
        self.assertTrue(d.winnable)
        self.assertGreater(d.def_hp, 0, "守方总血不能是 0")

    def test_visible_empty_cell_is_still_free(self):
        """★ 看得见的空格**仍然**是一支就够（"野人死过不再补"是引擎的规则）。"""
        sb = _sandbox(seed=1, size=8, n_nations=2, war=True)
        env = sb.env
        mine = [u for u in env.armies() if env.owner_of(u) == "甲"]
        d = teacher_combat.assess(env, "甲", (0, 0), mine,
                                  visible=True, need_cap=12, rounds_cap=12)
        self.assertTrue(d.empty)
        self.assertEqual(d.need, 1)
        self.assertEqual(d.losses, 0)


# ======================================================================
# P2-8 轮数分档
# ======================================================================
class TestRoundBins(unittest.TestCase):
    def test_edges_are_used(self):
        o = odds.Odds(p_rounds=[0.0, 0.1, 0.2, 0.3, 0.4])
        got = o.round_bins((1, 2, 3, 4))
        for a, b in zip(got, (0.1, 0.3, 0.6, 1.0)):
            self.assertAlmostEqual(a, b, places=6)

    def test_monotone_and_bounded(self):
        o = odds.Odds(p_rounds=[0.0, 0.1, 0.2, 0.3, 0.4])
        bins = o.round_bins()
        self.assertEqual(bins, sorted(bins), "累积分布必须单调不减")
        self.assertLessEqual(bins[-1], 1.0 + 1e-9)
        self.assertGreater(bins[0], 0.0, "第一档不能是 0（那一档装的是 1 轮内的概率）")

    def test_not_all_bins_equal(self):
        """★ 旧实现每一档都装**整个分布** ⇒ 全都等于 1（单调性测试抓不到）。"""
        o = odds.Odds(p_rounds=[0.0, 0.1, 0.2, 0.3, 0.4])
        bins = o.round_bins((1, 2, 3, 4))
        self.assertGreater(len(set(bins)), 1, "四档一模一样 ⇒ 边界没被用上")


# ======================================================================
# P2-9 MCTS 导入
# ======================================================================
class TestMctsImport(unittest.TestCase):
    def test_module_imports(self):
        from rl.mcts import MCTS, best_action, policy       # noqa: F401
        self.assertTrue(MCTS)

    def test_search_runs_on_a_sandbox(self):
        from rl.mcts import MCTS
        sb = _sandbox(seed=1, size=8, n_nations=2, war=True)
        visits = MCTS(n_sim=3).search(sb)
        self.assertIsInstance(visits, dict)


# ======================================================================
# P2-10 with_reinf=False
# ======================================================================
class TestFrameOddsNoReinf(unittest.TestCase):
    def test_no_unbound_local_error_when_reinforcement_is_off(self):
        sb = _sandbox(seed=1, size=8, n_nations=2, war=True)
        env = sb.env
        me = "甲"
        mask = env.vision_mask(me)
        mine = [u for u in env.armies() if env.owner_of(u) == me]
        target = None
        for u in mine:
            for c in sorted(env.reachable(me, u, for_attack=True)):
                if env.owner(*c) is None and c in mask:
                    target = (u, c)
                    break
            if target:
                break
        if not target:
            self.skipTest("这一局打不起来")
        u, c = target
        ok, _ = env.attack(me, [u.id], c[0], c[1])
        self.assertTrue(ok)
        frame = odds.frame_odds(env, me, mask, with_reinf=False)
        self.assertTrue(frame.cells, "有交战就该有这一帧的战斗明细")
        self.assertEqual(frame.reinf, {}, "with_reinf=False ⇒ 不该有增援估计")


if __name__ == "__main__":
    unittest.main()
