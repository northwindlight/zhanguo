# -*- coding: utf-8 -*-
"""战斗明细进观测（任务 #9）的守卫 —— 钉的是**口径**，不是"跑得通"。

背景（用户 2026-09-24）：「看战斗能不能赢（**输赢和同归的概率**是什么），
mv 和 atk 的增援会改变什么，**撤退保住军队的概率**是多少」⇒ 「**当特征**」。
精确概率由 `rl/combat_probs.py` 算（引擎口径的 DP），本文件管的是**怎么进观测**。

钉七件事，每条都对着一条**会静默出错**的口径：

  1. ★ **网格里的概率 = 引擎推演的概率** —— 预言机是 `combat_probs` 本体
     （而它自己又被 `test_combat_probs.py` 拿**引擎**当预言机钉住）。
  2. ★★ **不偷看**：看不见的敌军增援**不许**影响观测。这是本线最忌的一类错
     （v9 当年堵的就是"越权读全图"），而且**错起来不报错**。
     测法要点：**必须**同时断言"不过滤的那个值 ≠ 过滤后的值" ——
     否则两个都相等只是因为增援根本没起作用，那条测试就是空的（我踩过这个）。
  3. ★★ **接触口径**（`scoring.CB_CONTACT_VISION`）—— 实测：引擎视野 =
     自家地块及八邻，**不含野战军自己站的那格** ⇒ 交战格在 16×16 上
     可见率只有 **0%~7%**。死守 `visible_to` 的话这个特征在大地图上等于没做。
     口径："我的军正在这格上打" ⇒ 这一格的战斗明细算得出来。
     这条钉两件事：**默认开着时野战的军 token 有概率**、**关掉时两边一起关**。
  4. ★ **每帧重算** —— 用户：「每个回合都要按照当前状态重算概率」。
     改一个 hp 再看，通道必须变（防的是"某处偷偷缓存了 Odds"）。
  5. **轮数分布**单调、且等于 `Odds.round_bins()`（边界读先验表）。
  6. **撤退那一列**逐军不同、回去核对 `retreat_odds`；没在打的军恒 1.0。
  7. **先验表是活的** —— `override(PROB_ROUND_SCALE=…)` /
     `override(ROUND_BIN_EDGES=…)` 必须**当场**改变通道（改表不生效是踩过的坑）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                  # noqa: E402

from rl import combat_probs as CP                    # noqa: E402
from rl import encode, scoring as S                  # noqa: E402
from rl import vocab as V                            # noqa: E402
from rl.sandbox import Sandbox                       # noqa: E402


# ===========================================================================
# 场景脚手架
# ===========================================================================
def place(w, cell, sides, engaged=("甲",), moved=-1, hp=100):
    """把 `sides = {"甲": ["步",…], "乙": […]} 摆到 `cell` 上（**清掉原有军队**）。

    `engaged` 里的势力挂 `engaged=True`（= 进攻方，`_resolve_battles` 的入口条件）。
    """
    x, y = cell
    w.armies.clear()
    out = []
    for name, kinds in sides.items():
        for i, k in enumerate(kinds):
            gid, seq = w._new_army(name)
            a = {"id": seq + i, "gid": gid, "name": f"{name}{seq}x{i}", "type": k,
                 "hp": hp, "x": x, "y": y, "owner": name,
                 "moved_turn": moved, "engaged": name in engaged}
            w.armies.append(a)
            out.append(a)
    return out


def tiles_of(w, name):
    return sorted(c for c, t in w.tiles.items() if t["owner"] == name)


def adj(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1


def sb_of(size=14, seed=3):
    return Sandbox(seed=seed, size=size).reset()


def home_cell(sb, name="甲"):
    """我方视野内、我方拥有的一格（**必在观测框内** ⇒ 网格通道有位置）。"""
    vis = encode.vision_of(sb, "甲")
    return next(c for c in tiles_of(sb.world, name) if c in vis)


def remote_cell(sb, name="乙"):
    """`name` 的**视野外**一格（用来测"看不见的那一档"）。"""
    vis = encode.vision_of(sb, "甲")
    return next(c for c in tiles_of(sb.world, name) if c not in vis)


def deep_cell(sb, name="乙"):
    """`name` 的**深处**一格：它**和它的八邻**都不在甲视野里。

    ★ 测"看不见的增援"必须用这个：只要战斗格旁边有一格在视野内，
      摆在那一格的增援就**该**被算进去，整条测试就空了。
    """
    vis = encode.vision_of(sb, "甲")
    for c in tiles_of(sb.world, name):
        ring = [c] + [tuple(nb) for nb in sb.world.neighbors(*c)]
        if all(nb not in vis for nb in ring):
            return c
    raise AssertionError("找不到深处的格（换个 seed/尺寸）")


def frame_of(sb, me="甲"):
    mask = encode.vision_of(sb, me)
    armies = encode.window_armies(sb, me, mask)
    return encode.combat_of(sb, me, mask, armies), mask, armies


def oracle(w, cell):
    """★ 预言机：直接问 `combat_probs` 要那一格的 `Odds`。"""
    return CP.assess(CP.build(w, cell[0], cell[1]))


# ★★ 军队 token 的**行内偏移**：`vocab.A_CB_*` 是**尾段内**的下标，不是整行的 ——
#   直接拿它索引整行会读到别的列（我第一版测试就这么错的：读到的其实是归属 one-hot）。
T = encode.ARMY_TAIL0


def row_of_token(win, armies, army):
    """按**身份**（`is`）找行 —— 别用 `list.index`：两个字段相同的军 dict 会**相等**，
    那样拿到的是**别人的行**（而且不报错）。"""
    i = next(k for k, a in enumerate(armies) if a is army)
    return win["a"][i]


# ===========================================================================
class TestGridChannels(unittest.TestCase):
    """① 网格里的概率必须**等于**引擎推演的概率（不是"长得像"）。"""

    def test_grid_matches_combat_probs(self):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.world, cell, {"甲": ["步", "步"], "乙": ["步"]})
        o = oracle(sb.world, cell)
        mask = encode.vision_of(sb, "甲")
        g = encode.encode_grid(sb, "甲", mask, frame=encode.combat_of(
            sb, "甲", mask, encode.window_armies(sb, "甲", mask)))
        ys, xs = (g[V.GRID_CB_ACTIVE] > 0).nonzero()
        self.assertEqual(len(ys), 1, "本场景只该有一个交战格")
        i, j = int(ys[0]), int(xs[0])
        self.assertAlmostEqual(float(g[V.GRID_CB_PWIN, i, j]), o.p_win["甲"], places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_PLOSE, i, j]), o.p_lose["甲"], places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_PDRAW, i, j]), o.p_draw, places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_PHOLD, i, j]), o.p_hold["甲"], places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_EROUNDS, i, j]),
                               min(1.0, o.e_rounds / S.PROB_ROUND_SCALE), places=6)
        self.assertAlmostEqual(float(g[V.GRID_CB_ELOSS, i, j]),
                               min(1.0, o.e_loss["甲"] / S.PROB_LOSS_SCALE), places=6)
        # ★ 2 步打 1 步：赢面必须**明显**大于输面（防"全是 0.5"这种静默均匀）
        self.assertGreater(o.p_win["甲"], 0.6)

    def test_grid_all_zero_without_battle(self):
        """没在打 ⇒ 一格都不写（`ACTIVE` 那列就是给"没在打"和"概率恰好 0"分家的）。"""
        sb = sb_of()
        sb.world.armies.clear()
        g = encode.encode_grid(sb, "甲", encode.vision_of(sb, "甲"))
        for ch in range(V.GRID_CB_ACTIVE, V.GRID_CHANNELS):
            self.assertEqual(float(np.abs(g[ch]).sum()), 0.0, f"通道 {ch} 该是全 0")


class TestContactVision(unittest.TestCase):
    """③ 接触口径：**野战（视野外的交战格）也要有概率**，且开关能两边一起关。"""

    def setUp(self):
        self.sb = sb_of()
        self.cell = remote_cell(self.sb)
        self.armies = place(self.sb.world, self.cell, {"甲": ["步"], "乙": ["步", "步"]})
        self.mine = next(a for a in self.armies if a["owner"] == "甲")
        self.mask = encode.vision_of(self.sb, "甲")
        self.assertNotIn(self.cell, self.mask,
                         "本场景的前提：这格**看不见**（否则这条测试是空的）")

    def test_token_carries_battle_out_of_vision(self):
        """★ 仗打在我身上 ⇒ 我的军 token 上必须有这场仗的概率。"""
        win, _, armies = encode.encode_window(self.sb, "甲", self.mask)
        row = row_of_token(win, armies, self.mine)
        o = oracle(self.sb.world, self.cell)
        self.assertEqual(row[T + V.A_CB_ACTIVE], 1.0)
        self.assertAlmostEqual(float(row[T + V.A_CB_PWIN]), o.p_win["甲"], places=6)
        self.assertAlmostEqual(float(row[T + V.A_CB_PLOSE]), o.p_lose["甲"], places=6)
        self.assertGreater(o.p_lose["甲"], 0.3, "1 步打 2 步：输面该是实打实的")

    def test_flag_off_suppresses_both(self):
        """关掉口径 ⇒ **军队 token 与网格一起**归零（不能只关一半）。"""
        with S.override(CB_CONTACT_VISION=False):
            win, _, armies = encode.encode_window(self.sb, "甲", self.mask)
            row = row_of_token(win, armies, self.mine)
            self.assertEqual(float(np.abs(row[T:]).sum()), 0.0,
                             "关掉接触口径后，军队 token 的战斗明细尾段该是全 0")
            g = encode.encode_grid(self.sb, "甲", self.mask, frame=encode.combat_of(
                self.sb, "甲", self.mask, armies))
            self.assertEqual(float(np.abs(g[V.GRID_CB_ACTIVE]).sum()), 0.0)
        # 开关还原后必须**立刻**回来（`override` 的还原是这条测试的一半）
        win2, _, armies2 = encode.encode_window(self.sb, "甲", self.mask)
        self.assertEqual(row_of_token(win2, armies2, self.mine)[T + V.A_CB_ACTIVE], 1.0)


# ===========================================================================
class TestNoLeak(unittest.TestCase):
    """② 看不见的敌军增援**不许**进观测（本线最忌的偷看）。"""

    def _stage(self):
        sb = sb_of()
        cell = deep_cell(sb)                       # 乙 的深处：它和八邻都看不见
        # 战斗格：甲 1 步（进攻）打 乙 1 步（守）
        place(sb.world, cell, {"甲": ["步"], "乙": ["步"]})
        self.assertTrue((cell[0], cell[1]) not in encode.vision_of(sb, "甲"))
        # 增援：乙 的一支军，摆在**紧挨战斗格**的另一格上（一步就能到）——
        #   `deep_cell` 保证了这一格**也不在视野里**（否则它就该被算进去）
        near = next(c for c in sorted(sb.world.tiles) if c != cell and adj(c, cell))
        gid, seq = sb.world._new_army("乙")
        rein = {"id": seq, "gid": gid, "name": "乙rein", "type": "步", "hp": 100,
                "x": near[0], "y": near[1], "owner": "乙", "moved_turn": -1,
                "engaged": False}
        sb.world.armies.append(rein)
        return sb, cell, rein

    def test_invisible_reinforcement_not_counted(self):
        sb, cell, rein = self._stage()
        w = sb.world
        self.assertNotIn((rein["x"], rein["y"]), encode.vision_of(sb, "甲"),
                         "前提：这支增援也看不见")
        b = CP.build(w, *cell)
        self.assertIn(rein, CP.reachable_reinforcements(w, b, "乙"),
                      "前提：它**真的够得着**（否则这条测试是空的）")

        frame, mask, _ = frame_of(sb, "甲")
        got = frame.reinf[cell].p_win["甲"]
        cur = frame.cells[cell].p_win["甲"]
        raw = CP.assess_with_reinforcements(w, b).p_win["甲"]

        # ★★ 这两条缺一不可：先证明"不过滤时确实不一样"，再说"过滤后一样"。
        self.assertLess(raw, cur - 1e-9,
                        "不过滤时必须**真的**变差 —— 否则增援压根没起作用，"
                        "下面那条 equal 就是空的（我踩过这种假测试）")
        self.assertAlmostEqual(got, cur, places=9,
                               msg="看不见的增援**不许**影响观测的增援概率")

    def test_own_reinforcement_is_counted(self):
        """我方自己的军**不**过滤（我本来就知道自己的军在哪）——增援通道是活的。"""
        sb = sb_of()
        cell = remote_cell(sb)
        place(sb.world, cell, {"甲": ["步"], "乙": ["步", "步"]})
        near = next(c for c in sorted(sb.world.tiles)
                    if c != cell and adj(c, cell))
        gid, seq = sb.world._new_army("甲")
        sb.world.armies.append({"id": seq, "gid": gid, "name": "甲rein", "type": "步",
                                "hp": 100, "x": near[0], "y": near[1], "owner": "甲",
                                "moved_turn": -1, "engaged": False})
        frame, _, _ = frame_of(sb, "甲")
        self.assertGreater(frame.reinf[cell].p_win["甲"],
                           frame.cells[cell].p_win["甲"] + 1e-6,
                           "我方增援到了 ⇒ 赢面必须变好（否则增援通道是死的）")


# ===========================================================================
class TestRecomputeEveryFrame(unittest.TestCase):
    """④ 每帧重算 —— 改状态必须改通道（防"某处偷偷缓存了 Odds"）。"""

    def test_hp_change_changes_channels(self):
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.world, cell, {"甲": ["步"], "乙": ["步"]})
        foe = next(a for a in armies if a["owner"] == "乙")
        win0, _, arm0 = encode.encode_window(sb, "甲")
        p0 = float(row_of_token(win0, arm0, armies[0])[T + V.A_CB_PWIN])
        foe["hp"] = 10                              # 敌残血 ⇒ 赢面该明显上升
        win1, _, arm1 = encode.encode_window(sb, "甲")
        p1 = float(row_of_token(win1, arm1, armies[0])[T + V.A_CB_PWIN])
        self.assertGreater(p1, p0 + 0.05,
                           f"hp 变了概率没变（{p0:.3f} → {p1:.3f}）⇒ 有东西被缓存了")

    def test_frame_odds_is_per_call(self):
        """`frame_odds` 每次调用都重算（不返回同一个对象/同一份答案）。"""
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.world, cell, {"甲": ["步"], "乙": ["步"]})
        f1, _, _ = frame_of(sb)
        sb.world.armies[1]["hp"] = 5
        f2, _, _ = frame_of(sb)
        self.assertNotEqual(f1.cells[cell].p_win["甲"], f2.cells[cell].p_win["甲"])


# ===========================================================================
class TestRoundBins(unittest.TestCase):
    """⑤ 轮数分布：单调、且等于 `Odds.round_bins()`（边界读先验表）。"""

    def test_bins_monotone_and_match(self):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.world, cell, {"甲": ["步"], "乙": ["步", "步"]})
        o = oracle(sb.world, cell)
        win, _, armies = encode.encode_window(sb, "甲")
        row = row_of_token(win, armies, armies[0])
        bins = [float(row[T + V.A_CB_R0 + k]) for k in range(V.CB_ROUND_BINS)]
        np.testing.assert_allclose(bins, o.round_bins(), atol=1e-6)
        for a, b in zip(bins, bins[1:]):
            self.assertLessEqual(a, b + 1e-9, f"累积分布必须单调：{bins}")
        self.assertEqual(bins[-1], 1.0, "最后一个档（≤8 轮）该覆盖全部质量")


# ===========================================================================
class TestRetreatColumn(unittest.TestCase):
    """⑥ 撤退保命那一列。"""

    def test_not_engaged_is_one(self):
        sb = sb_of()
        sb.world.armies.clear()
        cell = home_cell(sb)
        gid, seq = sb.world._new_army("甲")
        sb.world.armies.append({"id": seq, "gid": gid, "name": "甲0", "type": "步",
                                "hp": 100, "x": cell[0], "y": cell[1], "owner": "甲",
                                "moved_turn": -1, "engaged": False})
        win, _, armies = encode.encode_window(sb, "甲")
        row = row_of_token(win, armies, sb.world.armies[0])
        self.assertEqual(row[T + V.A_CB_ACTIVE], 0.0)
        self.assertEqual(row[T + V.A_RETREAT], 1.0, "没在打 ⇒ 撤了当然活")

    def test_engaged_matches_retreat_odds(self):
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.world, cell, {"甲": ["步"], "乙": ["步", "骑"]})
        mine = next(a for a in armies if a["owner"] == "甲")
        mine["hp"] = 20          # ★ 残血 ⇒ 撤了**不一定**活（满血时一轮死不掉，撤了必活）
        win, _, arm = encode.encode_window(sb, "甲")
        got = float(row_of_token(win, arm, mine)[T + V.A_RETREAT])
        want = CP.retreat_odds(sb.world, cell[0], cell[1], mine)
        self.assertAlmostEqual(got, want, places=6)
        self.assertLess(want, 1.0, "残血 1 步对 步+骑：撤退不是保票")

    def test_retreat_is_per_army(self):
        """同一格上两支军撤了活下来的概率**不一样** ⇒ 这个量只能挂在军队 token 上。"""
        sb = sb_of()
        cell = home_cell(sb)
        armies = place(sb.world, cell, {"甲": ["步", "步"], "乙": ["步", "骑"]})
        sb.world.armies[0]["hp"] = 8                # 一支残血
        win, _, arm = encode.encode_window(sb, "甲")
        vals = [float(row_of_token(win, arm, a)[T + V.A_RETREAT])
                for a in armies if a["owner"] == "甲"]
        self.assertNotAlmostEqual(vals[0], vals[1], places=3,
                                  msg=f"两支军撤退保命概率相同（{vals}）⇒ 说明没逐军算")


# ===========================================================================
class TestPerSideToken(unittest.TestCase):
    """每条军队 token 带**它自己那一方**的概率（两国共用一套编码 ⇒ 自对弈不失衡）。"""

    def test_each_token_carries_its_own_side(self):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.world, cell, {"甲": ["步", "步"], "乙": ["步"]})
        o = oracle(sb.world, cell)
        win, _, armies = encode.encode_window(sb, "甲")
        mine = next(a for a in armies if a["owner"] == "甲")
        foe = next(a for a in armies if a["owner"] == "乙")
        self.assertAlmostEqual(float(row_of_token(win, armies, mine)[T + V.A_CB_PWIN]),
                               o.p_win["甲"], places=6)
        self.assertAlmostEqual(float(row_of_token(win, armies, foe)[T + V.A_CB_PWIN]),
                               o.p_win["乙"], places=6)
        # 同一场仗的两个视角来自**同一个** `Odds` ⇒ 不可能各自漂开。
        # ★ 别忘了 p_draw（同归于尽）：`赢+赢` 不等于 1，`赢+赢+同归` 才等于 1
        self.assertAlmostEqual(o.p_win["甲"] + o.p_win["乙"] + o.p_draw, 1.0, places=6)


# ===========================================================================
class TestPriorsAreLive(unittest.TestCase):
    """⑦ 先验表是活的（改表不生效是踩过的坑：`from … import` 会把值绑死）。"""

    def _row(self, **kw):
        sb = sb_of()
        cell = home_cell(sb)
        place(sb.world, cell, {"甲": ["步"], "乙": ["步"]})
        with S.override(**kw):
            win, _, armies = encode.encode_window(sb, "甲")
        return row_of_token(win, armies, armies[0])

    def test_loss_scale_live(self):
        """★ 用**不饱和**的一档比：默认 100 时 e_loss≈100 恰好顶到 1.0，
        拿 10 去比两边都是 1.0 ⇒ 那条断言会是**空的**（我第一版就这么写的）。"""
        big = float(self._row(PROB_LOSS_SCALE=1000.0)[T + V.A_CB_ELOSS])
        small = float(self._row(PROB_LOSS_SCALE=100.0)[T + V.A_CB_ELOSS])
        self.assertNotAlmostEqual(big, small, places=4,
                                  msg="改 PROB_LOSS_SCALE 通道没变 ⇒ 尺度是写死的")
        self.assertAlmostEqual(small / big, 10.0, places=4)

    def test_round_edges_live(self):
        base = [float(self._row()[T + V.A_CB_R0 + k]) for k in range(V.CB_ROUND_BINS)]
        wide = [float(self._row(ROUND_BIN_EDGES=(2, 4, 6, 8, 10))[T + V.A_CB_R0 + k])
                for k in range(V.CB_ROUND_BINS)]
        self.assertNotEqual(base, wide, "改轮数边界通道没变 ⇒ 边界是写死的")

    def test_contact_flag_is_a_tunable(self):
        self.assertIn("CB_CONTACT_VISION", S._TUNABLE)
        with S.override(CB_CONTACT_VISION=False):
            self.assertFalse(S.CB_CONTACT_VISION)
        self.assertTrue(S.CB_CONTACT_VISION, "override 必须还原")


if __name__ == "__main__":
    unittest.main()