# -*- coding: utf-8 -*-
"""`_agg_damage` 的 C 版（M2）必须与 Python 版**逐位相同**，**包括表的顺序**。

★ 为什么单独钉这一处：`tests/test_rl_dp_identity.py` 钉的是**整条 DP 的出口**，
  它对「agg 表内部」只是**间接**覆盖 —— 表里两条目相加的顺序、或首次触达序变了，
  只要最后 `repr` 到同一个数就看不出来。而这张表是 DP 的**输入**，
  它一变，`dist`/`rh` 的求和顺序就跟着变 ⇒ 观测特征静默漂移。
  ⇒ 这里直接比对**表本身**：键的**顺序** + 每个值的 `repr(float)`（往返精确）。

★ 覆盖怎么来的（不是随手挑的几个签名）：
  先把 `CP._FAST = None` 逼纯 Python 跑完整条 DP，**窥探** `_agg_damage`，
  把 DP 真正到达过的**每一个签名**连同它算出的表一起收下来（用例 = `test_rl_dp_identity`
  那 8 个局面，含 5 方混战 ⇒ 6^5=7776 条组合那档也盖到），
  再拿**同一批签名**喂 C 版对拍。⇒ DP 会走到的形状，这里都比对过。

★ 三个容易悄悄写错的点，各有专门的断言：
  · **组合序** = `itertools.product`（**最后一方最快**）⇒ 靠键序比对；
  · **`p_die` 的累加** = 按组合序 `+=`（不是先分组再求和）⇒ 靠值的 `repr` 比对；
  · **银行家舍入**（`round(18.5)=18`，不是"四舍五入"的 19）⇒ `TestTiesRoundToEven`
    把数字**钉在字面上**，与实现无关地描述口径。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rl import combat_probs as CP                     # noqa: E402
from test_combat_probs import stage                   # noqa: E402
from test_rl_dp_identity import KWARGS                # noqa: E402


def _spec(tab):
    """表的**可比对形态**：`[(伤害向量, repr(概率)), …]` —— 顺序就是迭代顺序。"""
    return [(dv, repr(p)) for dv, p in tab.items()]


def collect_by_python(b):
    """用**纯 Python** 跑一遍 DP，把 DP 到达过的每个签名与它的表收下来。"""
    out = []
    real = CP._agg_damage

    def spy(bb, sig):
        tab = real(bb, sig)                 # ★ 此时 _FAST=None ⇒ 拿的是 Python 那份
        out.append((sig, _spec(tab)))
        return tab

    keep_fast, keep_err = CP._FAST, CP._FAST_ERR
    CP._FAST = None
    CP._agg_damage = spy
    try:
        CP.assess(b)
    finally:
        CP._agg_damage = real
        CP._FAST, CP._FAST_ERR = keep_fast, keep_err
    return out


class TestAggDamageBitExact(unittest.TestCase):
    """DP 到达过的**每个签名**：C 表 == Python 表（键序 + 每个值的位型）。"""

    @classmethod
    def setUpClass(cls):
        if CP._FAST is None:
            raise unittest.SkipTest(f"扩展没编译（可选）：{CP._FAST_ERR}")

    def test_every_signature_the_dp_reaches_matches(self):
        n_tables = widest = merged = 0
        for label, kw in KWARGS.items():
            with self.subTest(label):
                w, cell = stage(**kw)
                b = CP.build(w, *cell)
                self.assertIsNotNone(b, f"{label} 没摆出战斗")
                seen = collect_by_python(b)
                self.assertTrue(seen, f"{label} 一个签名都没到达 —— 用例空了")
                widest = max(widest, max(6 ** len(sig) for sig, _ in seen))
                b.agg_cache.clear()          # ★ C 分支只在 **miss** 上走
                for sig, spec in seen:
                    self.assertEqual(_spec(CP._agg_damage(b, sig)), spec,
                                     f"{label}：签名 {sig} 的 agg 表与 Python 版不一致"
                                     "（顺序或位型变了 ⇒ DP 的求和顺序会跟着变）")
                    if len(spec) < 6 ** len(sig):
                        merged += 1
                    n_tables += 1
        self.assertGreaterEqual(n_tables, 30, f"只比对到 {n_tables} 张表，覆盖不够")
        self.assertGreaterEqual(widest, 6 ** 4, "没盖到 4 方以上的指数项（6^n 那条）")
        self.assertGreater(merged, 0, "没有一张表发生过**归并** ⇒ 累加顺序没被比对到")
        self.assertIsNone(CP._FAST_ERR, f"C 扩展报过错（被回退吞掉了）：{CP._FAST_ERR}")

    def test_dead_side_shapes(self):
        """**死方**这两类形状要单独够到 —— DP 后半程全是它。"""
        w, cell = stage(sides={"甲": ["步", "骑", "民"], "乙": ["步", "骑"], "丙": ["骑"]},
                        retreat=("甲", 2, 50), engaged=("甲", "乙"))
        b = CP.build(w, *cell)
        st = [b.init[f] for f in b.order]
        sigs = [tuple(CP._atk_sig(s) for s in st),            # 三方全活
                tuple(CP._atk_sig(s) for s in st[:2]) + ((),),
                ((),) + tuple(CP._atk_sig(s) for s in st[1:]),
                tuple(CP._atk_sig(s) for s in [st[0], (), st[2]])]
        keep_fast, keep_err = CP._FAST, CP._FAST_ERR
        CP._FAST = None
        want = [(s, _spec(CP._agg_damage(b, s))) for s in sigs]
        CP._FAST = keep_fast
        b.agg_cache.clear()
        for sig, spec in want:
            with self.subTest(live=sum(1 for x in sig if x)):
                self.assertEqual(_spec(CP._agg_damage(b, sig)), spec)
        CP._FAST_ERR = keep_err


class TestTiesRoundToEven(unittest.TestCase):
    """★ 字面钉住 ties-to-even：三方各拿 1 支 50 攻的步军互殴，1 号骰面
    `power = 50*75//100 = 37`，分打**两个**活敌人 ⇒ `share = 18.5` ⇒
    Python `round(18.5)` = **18**（就近取偶），每方被两人打 ⇒ 伤害向量 `(36,36,36)`。
    写成"四舍五入"会得到 `(38,38,38)` ⇒ 整张表的键全换。
    """

    def _hand_battle(self, sides=("甲", "乙", "丙")):
        n = len(sides)
        return CP.Battle(
            x=0, y=0, owner=None, order=tuple(sides),
            attacker=frozenset(sides),
            enemies={f: tuple(g for g in sides if g != f) for f in sides},
            soak={f: 0 for f in sides},
            init={f: (("步", 100, False, 100),) for f in sides},
            enemy_idx=tuple(tuple(j for j in range(n) if j != i) for i in range(n)),
            soak_list=tuple(0 for _ in sides))

    def test_bankers_value_is_pinned(self):
        b = self._hand_battle()
        sig = tuple((("步", False),) for _ in b.order)
        tab = CP._agg_damage(b, sig)
        self.assertIn((36, 36, 36), tab, "1 号骰面三方互殴应各吃 36 ⇒ 舍入口径变了？")
        self.assertNotIn((38, 38, 38), tab, "出现 38 = 按「四舍五入」进了位，"
                                           "而 `round()` 是就近取偶")

    def test_c_matches_python_here_too(self):
        if CP._FAST is None:
            self.skipTest(f"扩展没编译（可选）：{CP._FAST_ERR}")
        b = self._hand_battle()
        sig = tuple((("步", False),) for _ in b.order)
        keep_fast, keep_err = CP._FAST, CP._FAST_ERR
        CP._FAST = None
        want = _spec(CP._agg_damage(b, sig))
        CP._FAST = keep_fast
        b.agg_cache.clear()
        self.assertEqual(_spec(CP._agg_damage(b, sig)), want)
        CP._FAST_ERR = keep_err


class TestFallbackIsNotSilent(unittest.TestCase):
    """C 出问题 ⇒ 结果仍是 Python 那一份，**且原因被记下来**（仓库纪律：静默失效最贵）。"""

    def test_broken_extension_falls_back_and_records(self):
        w, cell = stage(**KWARGS["3v3 混编"])
        b = CP.build(w, *cell)
        sig = tuple(CP._atk_sig(s) for s in (b.init[f] for f in b.order))
        keep_fast, keep_err = CP._FAST, CP._FAST_ERR
        CP._FAST = None
        want = _spec(CP._agg_damage(b, sig))

        class Boom:
            @staticmethod
            def agg_damage(*a, **k):
                raise RuntimeError("故意的：扩展炸了")

        b.agg_cache.clear()
        CP._FAST = Boom()
        try:
            self.assertEqual(_spec(CP._agg_damage(b, sig)), want, "回落后结果变了")
            self.assertIsNotNone(CP._FAST_ERR, "回落了却没留下原因 ⇒ 那是静默失效")
            self.assertIn("故意的", CP._FAST_ERR)
        finally:
            CP._FAST, CP._FAST_ERR = keep_fast, keep_err


if __name__ == "__main__":
    unittest.main(verbosity=2)
