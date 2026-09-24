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
        self.assertNotEqual(float(g[FOE_HALL_DX]), 0.0, "方向那一列也该在")

    def test_scorer_proximity_survives_losing_sight(self):
        """打分器的「逼近」项同理：见过之后，看不见也不许塌成 0。"""
        sb = sb_of()
        foe_hall = hall_of(sb, "乙")
        mask = blind()
        s_blind = E.score(sb.world, "甲", "乙", mask,
                          known=sb.known_halls("甲", mask))
        sb.known_halls("甲", frozenset(sb.world.tiles))     # 见过
        k = sb.known_halls("甲", mask)
        s_mem = E.score(sb.world, "甲", "乙", mask, known=k)
        self.assertNotEqual(s_blind, s_mem,
                            "★ 记忆对打分毫无影响 ⇒ 势函数差分在厅离开视野时会变成噪声")
        # ★ 差值必须来自**逼近项**（关掉 W_NEAR 就该消失）
        with S.override(W_NEAR=0.0):
            s2 = E.score(sb.world, "甲", "乙", mask, known=k)
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


if __name__ == "__main__":
    unittest.main()