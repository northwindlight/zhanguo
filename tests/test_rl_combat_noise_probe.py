# -*- coding: utf-8 -*-
"""`rl/combat_noise_probe.py` 的守卫 —— 钉的是**闸门本身**，不是探针的结论。

★ 为什么值得单独钉（2026-09-30）：
  这个探针的产物是"DP 有没有说谎"这个判断。而**它的假阳性比假阴性贵** ——
  我当天就栽过一次：对拍台只复原 `armies`+`tiles`（漏了"攻下核心领地 ⇒ 失主亡国"
  这个派生状态）⇒ 跑出 `TV = 0.997`，**眼看就要当成 DP 的 bug 报上去**。
  所以"探针自己坏了"必须能被**自动**认出来，而不是靠我那天恰好去翻日志。

★★ 这里钉三件事，每件对着一个会静默松掉的形状：
  ① `_inconsistent` —— **闸门的判据**。它松了（阈值调大/判据改错）就再也抓不到坏对拍台。
  ② `tv_floor` —— **噪声底**。没有它，"TV = 0.05" 到底是 DP 错了还是 MC 抽样抖动，
     根本无从判断（这个项目栽过好几次"判据坐在噪声里"）。
  ③ 猴补的 `_spread` 计量 —— 它给的是**未夹 0 的伤害总量**，正是 DP `e_loss` 的口径；
     口径一变（比如哪天有人把它改成夹过的），两边的数就**不再可比**、而且不报错。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np                                        # noqa: E402

from rl import combat_noise_probe as P                    # noqa: E402
from rl import combat_probs as CP                         # noqa: E402
from rl.sandbox import Sandbox                            # noqa: E402


def _row(**kw) -> dict:
    r = {"e_loss_dp": 100.0, "e_raw_mc": 100.0, "sd_raw_mc": 5.0, "_trials": 1000,
         "tv": 0.01, "tv_floor": 0.02, "has_retreat": False}
    r.update(kw)
    return r


class TestGate(unittest.TestCase):
    """① 闸门判据。"""

    def test_consistent_row_is_silent(self):
        self.assertEqual(P._inconsistent(_row()), "",
                         "自洽的一场被误报了 —— 闸门太紧会天天狼来了")

    def test_loud_when_engine_never_fought(self):
        """★ 这就是 2026-09-30 那个真事故的形状：DP 说掉 60 hp，引擎一点没掉。"""
        why = P._inconsistent(_row(e_loss_dp=60.0, e_raw_mc=0.1, sd_raw_mc=2.0))
        self.assertIn("原始伤害", why, f"没抓住：{why!r}")

    def test_loud_when_distribution_is_wrong(self):
        why = P._inconsistent(_row(tv=0.99, tv_floor=0.006))
        self.assertIn("TV", why, f"没抓住：{why!r}")

    def test_it_scales_with_mc_error_not_with_a_fixed_number(self):
        """★ 判据必须**跟着 MC 的标准误走**：样本越多，能容忍的差越小。

        （固定的绝对阈值在小样本上会误报、在大样本上会漏报 —— 两头都是错的。）
        """
        small = _row(e_loss_dp=102.0, e_raw_mc=100.0, sd_raw_mc=5.0, _trials=100)
        big = _row(e_loss_dp=102.0, e_raw_mc=100.0, sd_raw_mc=5.0, _trials=100000)
        self.assertEqual(P._inconsistent(small), "",
                         "n=100、se=0.5 时 2hp 的差本就在噪声里 —— 不该报")
        self.assertNotEqual(P._inconsistent(big), "",
                            "样本大了 1000 倍、2hp 的差还判自洽 ⇒ 判据没跟标准误走")

    def test_retreat_battles_are_exempt_from_the_damage_gate(self):
        """★ 撤退减伤那条分支**不走 `_spread`** ⇒ 原始伤害统计不全，判据不适用。"""
        self.assertEqual(
            P._inconsistent(_row(e_loss_dp=60.0, e_raw_mc=0.1, has_retreat=True)), "",
            "撤退局的伤害判据没被豁免 —— 会拿一个量不全的数去定罪")


class TestNoiseFloor(unittest.TestCase):
    """② 噪声底：没有它 TV 这个数没有意义。"""

    def _battle(self, sides):
        sb = Sandbox(seed=7, size=12).reset()
        w = sb.world
        cell = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == "乙")
        w.tiles[cell]["terrain"] = "平原"
        w.armies.clear()
        for name, kinds in sides.items():
            for i, k in enumerate(kinds):
                gid, seq = w._new_army(name)
                w.armies.append({"id": seq + i, "gid": gid, "name": f"{name}{i}",
                                 "type": k, "hp": 100, "x": cell[0], "y": cell[1],
                                 "owner": name, "moved_turn": -1,
                                 "engaged": name == "甲"})
        return w, cell

    def test_deterministic_battle_has_zero_floor(self):
        """一边倒（p 是 0/1）⇒ 抽样抖动为 0 ⇒ 噪声底必须是 0。"""
        w, cell = self._battle({"甲": ["步"] * 6, "乙": ["民"]})
        b = CP.build(w, *cell)
        o = CP.assess(b)
        self.assertGreater(o.p_win["甲"], 0.999, "用例前提：这仗必须是一边倒")
        self.assertLess(P.tv_floor(b, o, 1000), 1e-6)

    def test_floor_shrinks_as_sqrt_of_trials(self):
        """★ 噪声底 ∝ 1/√M —— 这条不成立就说明公式写错了（会把 MC 抖动当信号）。"""
        w, cell = self._battle({"甲": ["步"], "乙": ["步"]})
        b = CP.build(w, *cell)
        o = CP.assess(b)
        f1, f16 = P.tv_floor(b, o, 1000), P.tv_floor(b, o, 16000)
        self.assertAlmostEqual(f1 / f16, 4.0, delta=0.05,
                               msg="翻 16 倍样本，噪声底该降到 1/4")


class TestRawDamageHook(unittest.TestCase):
    """③ 猴补量到的必须是**未夹 0 的伤害总量**（= DP `e_loss` 的口径）。"""

    def test_hook_is_installed_and_does_not_change_the_engine(self):
        """猴补只**多记一笔**，引擎行为逐位不变（同一 rng 下 hp 轨迹一致）。"""
        self.assertTrue(getattr(P.mp.World, "_raw_hooked", False), "计量钩子没装上")
        P._RAW.clear()
        sb = Sandbox(seed=5, size=12).reset()
        w = sb.world
        cell = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == "乙")
        w.armies.clear()
        for name, hp in (("甲", 100), ("乙", 100)):
            gid, seq = w._new_army(name)
            w.armies.append({"id": seq, "gid": gid, "name": name, "type": "步", "hp": hp,
                             "x": cell[0], "y": cell[1], "owner": name,
                             "moved_turn": -1, "engaged": name == "甲"})
        w.rng.seed(3)
        w._resolve_battles()
        raw = P._RAW.get((cell, "甲"), 0) + P._RAW.get((cell, "乙"), 0)
        self.assertGreater(raw, 0, "钩子没记到伤害")

    def test_overkill_makes_raw_strictly_bigger_than_clamped(self):
        """★ 掉血一定能被**过量击杀**超掉 —— 两个数不是同一个量，别混着比。

        做法：给一支 **1 hp** 的军挨一刀，它承受的伤害远超它拥有的血。
        """
        P._RAW.clear()
        sb = Sandbox(seed=6, size=12).reset()
        w = sb.world
        cell = next(c for c, t in sorted(w.tiles.items()) if t["owner"] == "乙")
        w.armies.clear()
        for name, hp in (("甲", 100), ("乙", 1)):
            gid, seq = w._new_army(name)
            w.armies.append({"id": seq, "gid": gid, "name": name, "type": "步", "hp": hp,
                             "x": cell[0], "y": cell[1], "owner": name,
                             "moved_turn": -1, "engaged": name == "甲"})
        w.rng.seed(11)
        w._resolve_battles()
        raw = P._RAW.get((cell, "乙"), 0)
        self.assertGreater(raw, 1, f"1hp 的军挨了一刀，原始伤害只有 {raw} ⇒ 不是未夹口径")
        self.assertEqual([a for a in w.armies if a["owner"] == "乙"], [],
                         "用例前提：那支 1hp 的军该死")


if __name__ == "__main__":
    unittest.main()
