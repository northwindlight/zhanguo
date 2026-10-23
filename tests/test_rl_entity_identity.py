# -*- coding: utf-8 -*-
"""★★ **多国身份与关系**（报告 §8.4）—— 军队 token 的**实体引用**。

§6.1 修完"军队所属方被脚下格主顶替"之后，还剩一个洞：

    归属 one-hot 只说「**是敌国**」⇒ **说不清两支敌军来自同一个敌国，
    还是来自两个互相交战的敌国**。

★ 修法：给军队 token 加两列（`vocab.A_ENT_N` / `vocab.A_ENT_SLOT`）——
  · `A_ENT_N`    = **视野里**与它**同一关系类别**的**不同外交实体**有几个；
  · `A_ENT_SLOT` = 它所属实体的**规范槽号**（同实体同槽）。

★ 三条口径（报告 §8.4 的验收）：
  · **只用玩家可知的事实**（槽号由**观察到的军队的相对位置**排序得出，
    **不用国家名、不用真值**）⇒ 换局重新编号**不影响**；
  · **有界**（最多 `vocab.ENT_SLOTS` 个槽）⇒ 未知国家再多也**不越界**；
  · 关系只来自**公开的外交关系**。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from tests.rl_deps import has_torch, torch_or_skip, new_army
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None

if has_torch():
    from rl import encode as E                           # noqa: E402
    from rl import vocab as V                           # noqa: E402
    from rl.sandbox import Sandbox                      # noqa: E402
else:
    Sandbox = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _units(w):
    return {u.name: u for u in w.world.units}


def _scenario(same_country: bool):
    """**同样位置、兵种、血量**的两支敌军，只是**所属国**不同。

    · `same_country=True`  ⇒ 两支都是**同一个敌国**的军（一个外交实体）；
    · `same_country=False` ⇒ 一支乙、一支丙（**两个互相交战的敌国**）。

    ★ 三国开局 `war=True` ⇒ 甲/乙/丙**两两交战** ⇒ 关系类别对两支军**完全相同**
      （都是 `RIVAL`）⇒ 观测里**唯一**的差别就是**实体归属**。
    """
    sb = Sandbox(seed=0, size=8, n_nations=3, t_max=20, war=True).reset(
        random_starts=False)
    w = sb.env
    # ★★ 敌军**真删**再**真造**在**同一格**（native 的 `disband` + `seed`）——
    #   旧做法是改门面自记的军队表（改 x/y、再把整个列表换掉），
    #   引擎那边根本不知道 ⇒ 观测里读到的位置与引擎的真值能分叉。
    for a in list(w.armies()):
        if w.owner_of(a) == "甲":
            continue
        if same_country and w.owner_of(a) == "丙":
            continue                      # ★ 这一支整个不存在 ⇒ 只剩**同一个敌国**
        w.call("native:army", "disband", a.owner, a.id)
    for nation in ("乙", "丙"):
        if same_country and nation == "丙":
            continue
        for _ in range(2):
            new_army(w, nation, (2, 1))  # ★ 全部摆在**同一格**（同位置/兵种/hp）
    return sb


def _enemy_rows(sb):
    """窗口里**敌军**那几行的实体两列（+ 归属类别）。"""
    mask = E.vision_of(sb, "甲")
    win, _wm, armies = E.encode_window(sb, "甲", mask)
    n_cls = len(V.OWNER_CHANNELS)
    out = []
    for i, a in enumerate(armies):
        if a["owner"] == "甲":
            continue                 # ★ 只看**敌军**那几行（我方是 SELF，不是敌国）
        cls = int(np.argmax(win["a"][i][V.A_OWNER0:V.A_OWNER0 + n_cls]))
        out.append((a["owner"], cls,
                    float(win["a"][i][V.A_ENT_N]),
                    float(win["a"][i][V.A_ENT_SLOT])))
    return out


class TestEntityIdentityIsEncoded(unittest.TestCase):
    """§8.4 的主验收。"""

    def test_same_enemy_country_vs_two_enemy_countries(self):
        """★★★ **同样位置/兵种/血量**的两支敌军：来自**同一敌国** vs 来自
        **两个互相交战的敌国** ⇒ **编码必须能区分**。

        ★ 两种局里关系类别**完全相同**（都是 `RIVAL`）⇒ 唯一差别是**实体归属**。
        """
        a = _enemy_rows(_scenario(True))
        b = _enemy_rows(_scenario(False))
        self.assertTrue(a and b, "用例前提：两局都得有敌军在窗口里")
        self.assertTrue(all(r[1] == V.OWN_RIVAL for r in a + b),
                        "用例前提：两支都得是敌国（否则差别不是身份）")
        # ★ 实体数：1 vs 2
        self.assertAlmostEqual(a[0][2], 1.0 / V.ENT_SLOTS, places=6,
                               msg="同一敌国 ⇒ 同类实体数该是 1")
        self.assertAlmostEqual(b[0][2], 2.0 / V.ENT_SLOTS, places=6,
                               msg="两个敌国 ⇒ 同类实体数该是 2")
        # ★ 槽号：同一实体同槽 / 不同实体不同槽
        self.assertAlmostEqual(a[0][3], a[1][3], places=6,
                               msg="同一个国家的两支军该拿**同一个槽**")
        self.assertNotAlmostEqual(b[0][3], b[1][3], places=6,
                                   msg="两个国家的军该拿**不同的槽**")

    def test_same_entity_gets_the_same_slot(self):
        """★ **同实体同槽**（这条是"身份"能表达"谁和谁一伙"的前提）。"""
        rows = _enemy_rows(_scenario(True))
        slots = {r[3] for r in rows}
        self.assertEqual(len(slots), 1, "同一个敌国的军队拿到了不同的槽")

    def test_own_armies_and_enemies_get_different_slots(self):
        """★ 我方实体与敌方实体**不同槽**（槽是"实体"的，不是"关系"的）。"""
        sb = _scenario(False)
        mask = E.vision_of(sb, "甲")
        win, _wm, armies = E.encode_window(sb, "甲", mask)
        mine = {float(win["a"][i][V.A_ENT_SLOT])
                for i, a in enumerate(armies) if a["owner"] == "甲"}
        foes = {float(win["a"][i][V.A_ENT_SLOT])
                for i, a in enumerate(armies) if a["owner"] != "甲"}
        self.assertTrue(mine and foes)
        self.assertFalse(mine & foes, "我方与敌方拿到了同一个槽")

    def test_entity_count_uses_only_observed_armies(self):
        """★ **不偷看**：一个**视野里没有军队**的实体**不计数**
        （否则就把"看得见的敌军有多少伙"泄露成了真值）。"""
        sb = _scenario(True)
        mask = E.vision_of(sb, "甲")
        win, _wm, armies = E.encode_window(sb, "甲", mask)
        foes = [i for i, a in enumerate(armies) if a["owner"] != "甲"]
        self.assertTrue(foes)
        # ★ 丙**存在**（它有厅）但视野里没有它的军 ⇒ 同类实体数只能是 1
        self.assertAlmostEqual(float(win["a"][foes[0]][V.A_ENT_N]),
                               1.0 / V.ENT_SLOTS, places=6)


class TestEntityReferenceIsLabelFree(unittest.TestCase):
    """§8.4：「**换一局重新编号不应改变策略**」⇒ 槽号不许依赖名字/编号。"""

    def test_slot_does_not_depend_on_the_country_name(self):
        """★ 同一个（关系类别, 几何）⇒ **同一个槽**，与国家叫什么无关。

        ★ 用**替身世界**直接验这条契约：只换"哪个名字对应哪个实体"，
          槽号必须**一模一样**。
        """
        class _StubW:
            def __init__(self, owner_ent, members):
                self.owner_ent = owner_ent
                self.members = members

            def entity(self, name):
                return self.owner_ent[name]

            def entity_members(self, ent):
                return self.members[ent]

            def allied(self, a, b):
                return False

            def war_between(self, a, b):
                return True

            def nations(self):
                return tuple(self.entities)

        class _StubSb:
            def __init__(self, w):
                # ★ 编码层拿的是**能力客户端**（`sb.env`），不是旧门面的 `sb.env`
                self.env = w

            def core_of(self, name):
                return (0, 0)

        armies_a = [{"id": 1, "gid": 11, "owner": "甲", "x": 0, "y": 0, "hp": 100},
                    {"id": 1, "gid": 12, "owner": "乙", "x": 3, "y": 0, "hp": 100},
                    {"id": 1, "gid": 13, "owner": "丙", "x": 5, "y": 0, "hp": 100}]
        armies_b = [{"id": 1, "gid": 11, "owner": "甲", "x": 0, "y": 0, "hp": 100},
                    {"id": 1, "gid": 12, "owner": "戊", "x": 3, "y": 0, "hp": 100},
                    {"id": 1, "gid": 13, "owner": "己", "x": 5, "y": 0, "hp": 100}]
        w_a = _StubW({"甲": "e0", "乙": "e1", "丙": "e2"},
                     {"e0": ["甲"], "e1": ["乙"], "e2": ["丙"]})
        w_b = _StubW({"甲": "e0", "戊": "e1", "己": "e2"},
                     {"e0": ["甲"], "e1": ["戊"], "e2": ["己"]})
        slot_a, n_a = E._entity_slots(_StubSb(w_a), "甲", armies_a)
        slot_b, n_b = E._entity_slots(_StubSb(w_b), "甲", armies_b)
        self.assertEqual(slot_a, slot_b,
                         "只改了国家名字，槽号却变了 ⇒ 槽号依赖了名字")
        self.assertEqual(n_a, n_b, "只改了国家名字，实体数却变了")

    def test_army_ids_are_not_used_as_keys(self):
        """★★ 军队的 `id` 是**本国序号**（甲的第 1 支与乙的第 1 支都是 `id=1`）。

        ★ 用 `id` 做键 ⇒ **不同国家的同号军队互相覆盖** ⇒ 槽号串了，
          而**不报错**（表现为"所有军队拿到同一个槽"）。
        """
        sb = _scenario(False)
        mask = E.vision_of(sb, "甲")
        win, _wm, armies = E.encode_window(sb, "甲", mask)
        ids = [a["id"] for a in armies]
        gids = [a.get("gid") for a in armies]
        self.assertGreater(len(set(ids)), 1, "用例前提：应当有重号的军队")
        self.assertEqual(len(set(gids)), len(gids), "gid 应当全局唯一")
        slots = {float(win["a"][i][V.A_ENT_SLOT]) for i, a in enumerate(armies)}
        self.assertGreater(len(slots), 1, "所有军队拿到同一个槽 ⇒ 键用错了")


class TestEntitySlotsAreBounded(unittest.TestCase):
    """§8.4：「**未知国家数量不导致固定表越界**」。"""

    def test_more_entities_than_slots_stays_in_range(self):
        """★ 实体数**超过槽数** ⇒ 多出来的归"其它"槽，**不许越界**、**不许报错**。"""
        sb = _scenario(False)
        mask = E.vision_of(sb, "甲")
        with mock.patch.object(V, "ENT_SLOTS", 2):
            win, _wm, armies = E.encode_window(sb, "甲", mask)
            slots = win["a"][:, V.A_ENT_SLOT]
            ns = win["a"][:, V.A_ENT_N]
        self.assertTrue(np.all(slots >= 0.0) and np.all(slots <= 1.0),
                        f"槽号越界了：{sorted(set(np.round(slots, 3).tolist()))}")
        self.assertTrue(np.all(ns >= 0.0) and np.all(ns <= 1.0),
                        f"实体数越界了：{sorted(set(np.round(ns, 3).tolist()))}")
        # ★ 槽数只有 2 ⇒ 至少有一个实体只能拿"其它"槽（= 1.0）
        self.assertIn(1.0, set(np.round(slots, 3).tolist()),
                      "实体数超过槽数时，超出的那个该拿'其它'槽")

    def test_normal_game_never_overflows(self):
        """★ 正常局（≤6 国 ⇒ ≤6 个实体）本来就用不满 8 个槽 ⇒ **不会**出现"其它"槽。"""
        sb = _scenario(False)
        mask = E.vision_of(sb, "甲")
        win, _wm, armies = E.encode_window(sb, "甲", mask)
        slots = win["a"][:, V.A_ENT_SLOT]
        self.assertTrue(np.all(slots < 1.0),
                        "正常局不该出现'其它'槽（ENT_SLOTS=8 用不满）")


if __name__ == "__main__":
    unittest.main()
