# -*- coding: utf-8 -*-
"""★★ **重要地点成为完整的目标集合**（报告 §8.3）。

报告 §8.3 的原话：

    「目前**全局摘要主要给出我方、盟友、敌方相对我家最近一座厅**的位置及数量摘要。
      **其余厅依赖网格局部读取**，不利于**多核心攻防**。
      增加**可变长重要地点组**……数量可以有预算，但**截取策略必须显式记录**，
      不能只留下距离我家最近的一个。」

★ 验收（报告 §8.3）：
    「**第二座己方核心受威胁**、**两个敌方核心价值不同**、**盟友核心需要支援**；
      变化发生在**非最近目标**上时，**输入和决策通路仍能识别**。」
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
from tests.rl_deps import has_torch, torch, torch_or_skip  # ★ 没装 torch 时是 None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if has_torch():
    from rl import encode as E                           # noqa: E402
    from rl import vocab as V                           # noqa: E402
    from rl.model import build_model                    # noqa: E402
    from rl.sandbox import Sandbox                      # noqa: E402
    from rl.train import collate                        # noqa: E402
else:
    Sandbox = build_model = None


def load_tests(loader, tests, pattern):
    """★ 官方"整份跳过"入口。"""
    if not has_torch():
        raise unittest.SkipTest(torch_or_skip())
    return tests


def _game(size=12, n=3, seed=0):
    sb = Sandbox(seed=seed, size=size, n_nations=n, t_max=20, war=True).reset(
        random_starts=False)
    return sb


def _foe_halls(sb, me="甲"):
    """敌国的**已落成**厅格（真值，只是用来**造 fixture**）。"""
    w = sb.world
    foes = E.enemies_of(w, me)
    out = []
    for f in foes:
        for cell, t in w.tiles.items():
            if t["owner"] == f and t["buildings"].get("native:town-hall", 0) > 0:
                out.append(cell)
    return sorted(out)


class TestImportantPlacesAreComplete(unittest.TestCase):
    """§8.3：非最近的目标**不许被丢**。"""

    def test_group_shape_is_bounded(self):
        """★ 数量**有预算**（变长但有上限）⇒ 观测形状不会失控。"""
        sb = _game()
        rows, dropped = E.important_places(sb, "甲", E.vision_of(sb, "甲"))
        self.assertEqual(rows.shape, (V.HALL_SLOTS, V.HALL_WIDTH))
        self.assertGreaterEqual(dropped, 0)

    def test_every_known_hall_becomes_a_token(self):
        """★★ **每一座我知道的厅**都要成一条 token（不是只有最近那座）。"""
        sb = _game()
        halls = _foe_halls(sb)
        self.assertGreaterEqual(len(halls), 2, "用例前提：这一局得有两座敌厅")
        # ★ **合法的情报注入**（`hall_memory.tell` = 外部情报那条正路）
        sb.halls.tell("甲", {c: "乙" for c in halls})
        rows, _dropped = E.important_places(sb, "甲", E.vision_of(sb, "甲"))
        n_rival = int((rows[:, V.HALL_REL0 + V.OWN_RIVAL] > 0).sum())
        self.assertEqual(n_rival, len(halls),
                         f"我知道 {len(halls)} 座敌厅，输入里只有 {n_rival} 座 ⇒ 被丢了")

    def test_change_on_a_far_target_changes_the_input(self):
        """★★★ 变化发生在**非最近目标**上时，输入**必须**跟着变
        （报告 §8.3 的验收原话）。"""
        sb = _game()
        halls = _foe_halls(sb)
        self.assertGreaterEqual(len(halls), 2, "用例前提：这一局得有两座敌厅")
        # ★ 按**离我家核心的距离**排 ⇒ 找出"较远"的那座
        hx, hy = sb.core_of("甲")
        far = max(halls, key=lambda c: max(abs(c[0] - hx), abs(c[1] - hy)))
        sb.halls.tell("甲", {c: "乙" for c in halls})
        mask = E.vision_of(sb, "甲")
        before, _ = E.important_places(sb, "甲", mask)
        # ★ 只把**较远那座**的主人改掉（记忆里的主人）
        sb.halls.tell("甲", {far: "丙"})
        after, _ = E.important_places(sb, "甲", mask)
        self.assertFalse(np.array_equal(before, after),
                         "改了**非最近**那座敌厅，输入却没变 ⇒ 通路认不出来")

    def test_truncation_is_explicit(self):
        """★★ 超过预算时，**截掉几个**要能被查到（报告 §8.3：「截取策略必须显式记录」）。"""
        sb = _game(size=16, n=3)
        mask = E.vision_of(sb, "甲")
        # ★ 注入**多到超预算**的厅（用**合法**的情报注入口）
        cells = []
        w = sb.world
        for cell, t in w.tiles.items():
            if t["buildings"].get("native:town-hall", 0) > 0:
                cells.append(cell)
        self.assertTrue(cells)
        sb.halls.tell("甲", {c: "乙" for c in cells})
        rows, dropped = E.important_places(sb, "甲", mask)
        self.assertEqual(rows.shape[0], V.HALL_SLOTS)
        # ★ 截断数 = 总数 − 留下的数（口径可核对）
        total = len(cells) + 1          # +1 = 我自己的厅
        self.assertEqual(dropped, max(0, total - V.HALL_SLOTS))

    def test_relation_uses_public_diplomacy_not_the_ground(self):
        """★ 关系按**公开的外交关系**判（不靠格主）—— 报告 §8.4 的口径。"""
        sb = _game()
        rows, _ = E.important_places(sb, "甲", E.vision_of(sb, "甲"))
        mine = rows[0]
        self.assertAlmostEqual(float(mine[V.HALL_REL0 + V.OWN_SELF]), 1.0, places=6,
                               msg="我家核心的厅没被标成'我的'")


class TestImportantPlacesDoNotBreakTheLayout(unittest.TestCase):
    """★ 新增 token 组**不许**挤动军队段的稳定下标（报告 §8.3 明写）。"""

    def test_army_token_index_is_still_stable(self):
        sb = _game()
        obs = E.obs_of(sb, "甲", sb.legal())
        b = collate([obs])
        net = build_model()
        x, wm = net.encode_window(b["win"], b["win_mask"])
        n_a = b["win"]["a"].shape[1]
        self.assertEqual(net.groups[-1], "h", "重要地点组应当在最后")
        self.assertTrue(bool(wm[:, 1:1 + n_a].all()),
                        "军队段不再'全亮' ⇒ 下标被挤动了")
        self.assertGreater(int(wm[:, 1:1 + n_a].sum()), 0,
                           "军队段里没有 token 了 ⇒ 下标被挤动了")

    def test_the_group_is_present_in_the_batch(self):
        sb = _game()
        obs = E.obs_of(sb, "甲", sb.legal())
        self.assertIn("h", obs["win"])
        self.assertEqual(obs["win"]["h"].shape, (V.HALL_SLOTS, V.HALL_WIDTH))


if __name__ == "__main__":
    unittest.main()
