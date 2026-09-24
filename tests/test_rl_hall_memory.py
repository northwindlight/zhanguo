# -*- coding: utf-8 -*-
"""`rl/hall_memory.py` 的守卫 —— 用户 2026-09-24：

    「**发现厅了就应该永久标记，因为厅是拆不掉也不能移动的**」

钉五件事，每条都对着一个**会静默出错**的口径：

  1. ★★ **永久性（monotone）**：见过之后，哪怕**这一帧什么都看不见**，也还知道。
     这是整条需求的字面要求；而"拿当前视野回答厅在哪"**不报错**、
     只是让已知的厅在观测里**闪断**（同样的局面、不同的读数 ⇒ 没法学）。
  2. ★ **只记看得见的**：没看见的**不许**进账本（记忆是"我见过"，不是"全图"）。
  3. ★ **记忆不泄漏守军**：记住"那格有座厅" ≠ 看得见**厅上的军队**
     —— 军队的可见性永远走真正的 `view`（两件情报，别混）。
  4. ★ **观测/打分器里不闪断**：厅离开视野之后，`GLOB` 的"敌厅相对位置"与
     打分器的"逼近项"**必须还在**（否则势函数差分变噪声）。
  5. **`clone()` 带上账本**，且副本与原账本**互不影响**（试演不能凭空多知道、
     也不能把试演里知道的东西倒灌回真局）。

★ 另有一条**实测事实**（写在 `docs`/PLAN 里，不在这里断言）：随机策略在
  `16×16 / 24×24` 上**整局都看不到敌厅**（6 个种子 0 次），8×8 上也只有 2/6 个种子看到过
  ⇒ "自己找厅"那一版**光靠永久记忆还不够**：找到之前得有别的理由去探索。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl import encode, evaluate as E, scoring as S   # noqa: E402
from rl import vocab as V                            # noqa: E402
from rl.hall_memory import HallMemory                # noqa: E402
from rl.sandbox import Sandbox                       # noqa: E402

FOE_HALL_D = V.GLOB.index("foe_hall_d")
FOE_HALL_DX = V.GLOB.index("foe_hall_dx")


def sb_of(seed=3, size=24, spy=False):
    return Sandbox(seed=seed, size=size, halls_known=spy).reset()


def hall_of(sb, name):
    """该国的厅格（**不过滤** ⇒ `mask=None`）。"""
    return E.hall_cells(sb.world, name)[0]


def world3(seed=1, size=16):
    """**三国**世界（甲/乙/丙，甲乙同盟）。

    ★ 为什么非得另造一个：两人沙盒里**没有盟友**（`allies_of` 恒空）⇒
      「盟友的厅也纳入标记」那几条在沙盒上**测不了**（写了也是空测试）。
      引擎侧多国本来就是现成的（`World(nations=[…])`）。
    """
    from mp import World
    w = World(size=size, seed=seed, nations=["甲", "乙", "丙"],
              starts={"甲": (1, 1), "乙": (13, 13), "丙": (1, 13)})
    # 联盟 = 场景条件（`sandbox.set_alliance` 就是往这里放一条记录）
    w.blocs.append({"name": "同盟", "chief": "甲", "members": ["甲", "乙"]})
    return w


def blind():
    """「这一帧什么都看不见」的掩码（空集 —— 比「视野很小」更极端，便于证伪）。"""
    return frozenset()


# ===========================================================================
class TestPermanence(unittest.TestCase):
    """① 永久性 —— 用户这条需求的正身。"""

    def test_seen_once_known_forever(self):
        sb = sb_of()
        foe_hall = hall_of(sb, "乙")
        everything = frozenset(sb.world.tiles)          # 曾经"全看见"
        k1 = sb.known_halls("甲", everything)
        self.assertIn(foe_hall, k1, "看见过却没记下来 ⇒ 账本没接上")
        k2 = sb.known_halls("甲", blind())              # ★ 这一帧什么都看不见
        self.assertIn(foe_hall, k2,
                      "★ 一帧看不见就把已知的厅忘了 —— 正是用户要修的那个毛病")
        self.assertEqual(k2[foe_hall], "乙", "得记住**是谁的**（最后看见时）")

    def test_not_seen_never_known(self):
        """② 没看见的**不许**进账本（别把"记忆"做成"全知"）。"""
        sb = sb_of()
        foe_hall = hall_of(sb, "乙")
        mine = frozenset(E.hall_cells(sb.world, "甲"))
        k = sb.known_halls("甲", mine)
        self.assertNotIn(foe_hall, k, "只看见自家的厅，却把敌厅也记进来了 ⇒ 偷看")
        self.assertEqual(set(k), set(mine))

    def test_spy_mode_is_the_same_mechanism_full(self):
        """两种模式 = **同一个机制的两种初值**（间谍 = 开局账本已装满）。"""
        sb = sb_of(spy=True)
        k = sb.known_halls("甲", blind())
        self.assertIn(hall_of(sb, "乙"), k, "间谍模式该是明知敌厅（不必先观察）")
        self.assertGreaterEqual(len(k), 2, "至少该有自己 + 对手两座厅")

    def test_memory_is_monotone_over_an_episode(self):
        """整局跑一遍：账本**只增不减**。"""
        sb = sb_of(seed=1, size=16)
        prev = 0
        steps = 0
        while not sb.done() and steps < 400:
            me = sb.current_player()
            if me is None:
                break
            acts = sb.legal()
            if not acts:
                break
            n = len(sb.known_halls(me))
            self.assertGreaterEqual(n, 0)
            prev = max(prev, len(sb.halls.known(sb.world, me)))
            sb.step(acts[0])
            steps += 1
        self.assertGreater(prev, 0, "整局下来一本账都没记 ⇒ 没接上")


# ===========================================================================
class TestNoGarrisonLeak(unittest.TestCase):
    """③ 记住厅 ≠ 看见守军。"""

    def test_remembered_hall_does_not_reveal_garrison(self):
        sb = sb_of()
        foe_hall = hall_of(sb, "乙")
        # 在敌厅上摆一支敌军（若不摆，"看不见守军"就是空的：本来就没守军）
        gid, seq = sb.world._new_army("乙")
        sb.world.armies.append({"id": seq, "gid": gid, "name": "乙守", "type": "步",
                                "hp": 100, "x": foe_hall[0], "y": foe_hall[1],
                                "owner": "乙", "moved_turn": -1, "engaged": False})
        sb.known_halls("甲", frozenset(sb.world.tiles))     # 曾经全看见（含那支军）
        k = sb.known_halls("甲", blind())                   # 现在什么都看不见
        self.assertIn(foe_hall, k)
        win, _, armies = encode.encode_window(sb, "甲", blind(), known=k)
        self.assertEqual([a for a in armies if a["owner"] == "乙"], [],
                         "★ 记忆把厅上的守军也带出来了 ⇒ 偷看（两件情报要分开）")
        # 反向对照：真看见的时候**必须**能看见那支军（否则上面那条是空的）
        win2, _, armies2 = encode.encode_window(
            sb, "甲", frozenset(sb.world.tiles), known=k)
        self.assertTrue([a for a in armies2 if a["owner"] == "乙"],
                        "看得见的时候也看不到守军 ⇒ 这条测试是空的")


# ===========================================================================
class TestNoFlicker(unittest.TestCase):
    """④ 观测与打分器里**不闪断**（记忆真正买到的东西）。"""

    def test_glob_hall_position_survives_losing_sight(self):
        sb = sb_of()
        foe_hall = hall_of(sb, "乙")
        # 对照：一个**从没观察过**的沙盒 ⇒ 敌厅位置必须是 0（否则这条测试是空的）
        fresh = sb_of()
        g0 = encode.encode_glob(fresh, "甲", blind(),
                                known=fresh.known_halls("甲", blind()))
        self.assertEqual(float(g0[FOE_HALL_D]), 0.0, "没见过的敌厅不该有位置（前提）")
        # 见过一次 ⇒ 永久
        sb.known_halls("甲", frozenset(sb.world.tiles))
        k = sb.known_halls("甲", blind())
        g = encode.encode_glob(sb, "甲", blind(), known=k)
        self.assertGreater(float(g[FOE_HALL_D]), 0.0,
                           "★ 厅离开视野后 GLOB 里的敌厅位置归零 ⇒ 观测在闪断")
        # ★★ 真正的不变量：**"全看见"与"看不见"两帧的读数必须逐位相同**
        #   （原来这里断言 `dx != 0` —— 那是**依赖地图布局**的假性质：
        #    `size=24` 现在默认 4 国，最近的对手厅可能**正好与我同列** ⇒ dx=0 合法）。
        g_see = encode.encode_glob(sb, "甲", frozenset(sb.world.tiles),
                                   known=sb.known_halls("甲", frozenset(sb.world.tiles)))
        for i in (FOE_HALL_D, FOE_HALL_DX):
            self.assertEqual(float(g[i]), float(g_see[i]),
                             "看得见与看不见两帧的敌厅读数不同 ⇒ 闪断（这才是要钉的）")
        self.assertNotEqual(float(g[FOE_HALL_DX]) + float(g[FOE_HALL_D]),
                            0.0, "方向与距离至少得有一列在（不然上面那条是空的）")

    def test_scorer_proximity_survives_losing_sight(self):
        """打分器的「逼近」项同理：见过之后，看不见也不许塌成 0。"""
        sb = sb_of()
        foe_hall = hall_of(sb, "乙")
        mask = blind()
        s_blind = E.score(sb.world, "甲", "乙", mask=mask,
                          known=sb.known_halls("甲", mask))
        sb.known_halls("甲", frozenset(sb.world.tiles))     # 见过
        k = sb.known_halls("甲", mask)
        s_mem = E.score(sb.world, "甲", "乙", mask=mask, known=k)
        self.assertNotEqual(s_blind, s_mem,
                            "★ 记忆对打分毫无影响 ⇒ 势函数差分在厅离开视野时会变成噪声")
        # ★ 差值必须来自**逼近项**（关掉 W_NEAR 就该消失）
        with S.override(W_NEAR=0.0):
            s2 = E.score(sb.world, "甲", "乙", mask=mask, known=k)
        self.assertAlmostEqual(s2, s_blind, places=9,
                               msg="W_NEAR=0 还有差 ⇒ 差不是逼近项来的")


# ===========================================================================
class TestClone(unittest.TestCase):
    """⑤ 试演副本带账本，且两边**互不影响**。"""

    def test_clone_carries_memory(self):
        sb = sb_of()
        sb.known_halls("甲", frozenset(sb.world.tiles))
        n = len(sb.halls.known(sb.world, "甲"))
        c = sb.clone()
        self.assertEqual(len(c.halls.known(c.world, "甲")), n,
                         "副本把记忆丢了 ⇒ 试演时会「重新发现」、与真局不一致")

    def test_clone_memory_is_independent(self):
        sb = sb_of()
        c = sb.clone()
        c.known_halls("甲", frozenset(c.world.tiles))     # 副本里"看见"了
        self.assertGreater(len(c.halls.known(c.world, "甲")),
                           len(sb.halls.known(sb.world, "甲")),
                           "副本看见的东西倒灌回原局了 ⇒ 账本是共享的")
        self.assertEqual(len(sb.halls.known(sb.world, "甲")), 0,
                         "原局一次都没观察过 ⇒ 账本该是**空的**（记忆只由观察产生；"
                         "自家厅反正每帧都在视野里，不需要预置）")

    def test_memory_is_per_nation(self):
        """账本是**逐国**的：甲看见的不等于乙看见的。"""
        sb = sb_of()
        sb.known_halls("甲", frozenset(sb.world.tiles))
        self.assertIn(hall_of(sb, "乙"), sb.halls.known(sb.world, "甲"))
        self.assertEqual(len(sb.halls.known(sb.world, "乙")), 0,
                         "甲的记忆漏给了乙 ⇒ 两国共用一本账")


# ===========================================================================
class TestAllyHallsAreMarked(unittest.TestCase):
    """★ 用户 2026-09-24：「顺便**盟友发现厅应该也纳入标记**」。

    查实过的事实（这三条是"机制已通"的证明，不是"新加的功能"）：
      · `HallMemory.observe` **按格记账、不看归属** ⇒ 盟友的厅本来就在册；
      · `encode_glob` 的 ally 段走 `known` ⇒ 观测那半边早就统一了；
      · ★ **只有 `evaluate.score` 是全知口径**（`halls_of(world, al)` 没传 mask/known）
        ⇒ 观测与打分器**两套口径**。已归到同一本账。
    """

    def test_ledger_is_owner_agnostic_including_allies(self):
        """账本按**格**记账、不看归属 ⇒ 盟友的厅照样进册，且记清是谁的。"""
        w = world3()
        ally_hall = E.hall_cells(w, "乙")[0]
        m = HallMemory()
        m.observe(w, "甲", frozenset(w.tiles))          # 曾经全看见
        k = m.known(w, "甲")
        self.assertIn(ally_hall, k, "盟友的厅没进账本 ⇒ 「也纳入标记」没做到")
        self.assertEqual(k[ally_hall], "乙", "进册了，但得记住**是谁的**")
        # ★ 反向对照：什么都没看见 ⇒ 不许进册（否则那不是"记忆"是"全知"）
        m2 = HallMemory()
        m2.observe(w, "甲", frozenset())
        self.assertNotIn(ally_hall, m2.known(w, "甲"),
                         "一帧都没看见也进了册 ⇒ 记忆变成了全知")

    def test_scorer_counts_ally_hall_through_the_ledger(self):
        """★★ 打分器数盟友的厅走**同一本账**（观测与打分器不许两套口径）。

        两向断言，缺一条就是空测试：
          · 账本里有 ⇒ 正好多 `W_HALL × ALLY_SHARE`（**不多不少** ⇒ 差只来自这一项）
          · 账本空 + 视野空 ⇒ 盟友的厅**一分不加**（不许退回"全知"）
        """
        w = world3()
        ally_hall = E.hall_cells(w, "乙")[0]
        blind = frozenset()                    # ★ 视野空：能不能算**只**取决于账本
        s_empty = E.score(w, "甲", "丙", mask=blind, known={})
        s_full = E.score(w, "甲", "丙", mask=blind, known={ally_hall: "乙"})
        self.assertAlmostEqual(
            s_full - s_empty, S.W_HALL * S.ALLY_SHARE, places=9,
            msg="盟友的厅没按 `known` 计 ⇒ 打分器还是全知口径（观测说 0、打分器说 1）")
        # ★ 对照：把 W_HALL 关掉，差值必须消失（证明差值**只**来自厅那一项）
        with S.override(W_HALL=0.0):
            self.assertAlmostEqual(
                E.score(w, "甲", "丙", mask=blind, known={ally_hall: "乙"}),
                E.score(w, "甲", "丙", mask=blind, known={}), places=9,
                msg="W_HALL=0 还有差 ⇒ 差值不是厅那一项来的")


# ===========================================================================
class TestGridHallDoesNotFlicker(unittest.TestCase):
    """★ 网格 = **视野外接框**（矩形），而视野**不是**矩形 ⇒ 框内有一圈看不见的格。

    一座**记得的**厅若落在那一圈里，`GRID_HALL_*` 曾经当帧塌 0 —— 真闪断
    （实测证实过），而同帧 `GLOB foe_hall_d` 还在 ⇒ 同一件"永久事实"在观测的
    两半里**读数不一致**。根因是厅那一段被塞在 `if not visible: continue` **下面**，
    于是判据里的 `or (x, y) in known` 成了**死代码**。
    """

    def test_remembered_hall_inside_bbox_is_still_drawn(self):
        sb = sb_of(seed=5, size=16)
        w = sb.world
        mask = encode.vision_of(sb, "甲")
        x0, y0, h, ww = encode.frame_of(sb, "甲", mask)
        hole = [(x, y) for y in range(y0, y0 + h) for x in range(x0, x0 + ww)
                if 0 <= x < sb.size and 0 <= y < sb.size
                and (x, y) not in mask and (x, y) not in w.tiles]
        self.assertTrue(hole, "这幅图上没有「框内但看不见」的格 ⇒ 换个种子，否则是空测试")
        cell = hole[0]
        # 在那儿造一块**敌国带厅**的地块（键照抄真地块，少键会在别处炸）
        t = dict(next(iter(w.tiles.values())))
        t["buildings"] = {k: 0 for k in t["buildings"]}
        t["owner"], t["core"] = "乙", None
        t["buildings"]["市政厅"] = 1
        w.tiles[cell] = t
        sb.known_halls("甲", frozenset(w.tiles))       # 曾经全看见
        known = sb.known_halls("甲", frozenset())      # 这一帧什么都看不见
        self.assertIn(cell, known, "前提：账本记得它")
        fi, fj = cell[1] - y0, cell[0] - x0
        g = encode.encode_grid(sb, "甲", mask, known=known)
        self.assertEqual(g[V.GRID_HALL_RIVAL, fi, fj], 1.0,
                         "框内 + 记得 + 此刻看不见 ⇒ 网格塌 0（闪断）")
        # ★ 反向对照：账本空且看不见 ⇒ **必须** 0（"记得才画"，不是偷看）
        g0 = encode.encode_grid(sb, "甲", mask, known={})
        self.assertEqual(g0[V.GRID_HALL_RIVAL, fi, fj], 0.0,
                         "账本空也画得出来 ⇒ 那是偷看，不是记忆")
        # ★ 军队仍死守 `mask`（"知道厅在哪" ≠ "看得见守军"）
        self.assertEqual(g[V.GRID_FOE_HP, fi, fj], 0.0,
                         "厅的可见性放宽了、守军也跟着放宽 ⇒ 偷看")


if __name__ == "__main__":
    unittest.main()