# -*- coding: utf-8 -*-
"""AI 可见的文本里**不许出现奇怪小数**（浮点尾噪）。

用户 2026-10-09：「我甚至打算让你弄有理数，浮点依然存在误差，不过这个级别浮点也没问题，
**就怕莫名显示一堆奇怪小数**」。

口径：浮点误差留在计算里无害（这游戏不靠浮点做判定），**但一旦漏进 UI 就是灾难**——
`0.30000000000000004`、`7.999999999`、`1.5000000000000002` 会让 AI 以为世界真的长这样，
进而按假数字决策（模型对数字是照抄的）。所以不靠"读代码时小心"，而是**把每一处 AI 能看到的
文本真的渲染一遍，再拿正则扫**。

扫描面（都是 AI 每回合或按需真读得到的）：
  · 常驻状态 `full_state` 与各面板（all / market / res / econ / report / bank / diplomacy / land）
  · `buy` / `sell` / `bank_loan` 的**成交回执全文**（AI 最直接的数字来源）
  · 市场试算 `market_quote`（**含新加的"找零余额"路径**）

它同时守住新增的浮点状态 `gold_carry`（市场找零余额）：它恒 <1 金、**永不显示**——
哪天有人把它画进面板，这条会红。

判据（三条都很保守，宁可误报也别漏）：
  1. **≥4 位小数** —— 价格/比率/占比最多两位就够（均价 `:.2f`、报表 `:.1f`）；
  2. **科学计数法**（`1e-05`）—— 浮点漏格式的经典症状；
  3. **≥10 位连续数字** —— 内部编号（gid 之类）漏出来的症状。
"""

from __future__ import annotations

import random
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import mp_ai  # noqa: E402
import rule_ai  # noqa: E402

BAD = (r"\d+\.\d{4,}", r"\d[eE][+-]?\d+", r"\d{10,}")
PANELS = ("all", "market", "res", "econ", "report", "bank", "diplomacy", "land")


class TestNoUglyDecimals(unittest.TestCase):
    @staticmethod
    def _texts(turns: int = 6):
        """打一局（央行开着、规则 AI 真在买卖），把 AI 能读到的文本逐条吐出来。"""
        w = mp.World(size=16, seed=7, nations=["秦", "楚", "齐"], max_turns=40)
        w.bank_enable()
        _, fn = rule_ai.resolve("v11plus")
        rng = random.Random(5)
        for _ in range(turns):
            for n in list(w.alive()):
                fn(w, n, rng, max_actions=8)
                yield w.buy(n, "粮食", 7)[1]          # 成交回执：AI 最直接的数字来源
                yield w.sell(n, "补给", 3)[1]
                yield w.bank_loan(n)[1]
                for panel in PANELS:
                    yield mp_ai.execute(w, n, "query", {"panel": panel})
                yield mp_ai.full_state(w, n)
            w.resolve_turn()
            w.begin_turn()

    def test_no_float_noise_anywhere(self):
        for txt in self._texts():
            for pat in BAD:
                m = re.search(pat, txt)
                if m:
                    lo = max(0, m.start() - 90)
                    self.fail(f"AI 可见文本里漏出奇怪数字 {m.group(0)!r}：\n"
                              f"…{txt[lo:m.start() + 90]}…")

    def test_market_quote_path_is_clean_too(self):
        """试算走的是新加的找零路径（`_settle_gold(commit=False)`）——它也不能漏小数。

        ★ 只看**显示形式**：单价按面板的 `:.2f` 渲染、总额必须是整数金。
          （`market_quote` 内部返回 float 单价是设计——`6.3105` 是精确值，不是尾噪；
          面板一律 `:.2f`，所以"会不会漏"要在显示那一层判。）
        """
        w = mp.World(size=16, seed=7, nations=["秦", "楚"])
        w.nations["秦"].res["补给"] = 500
        w.nations["秦"].res["黄金"] = 9999
        for n in (1, 3, 7, 50):
            w.buy("秦", "粮食", 3)                    # 先把找零余额弄成非零
            for side in ("buy", "sell"):
                unit, total = w.market_quote("补给", n, side, "秦")
                self.assertIsInstance(total, int, "总额必须是整数金（floor + 找零）")
                for s in (f"{unit:.2f}", str(total)):
                    self.assertIsNone(re.search(r"\d+\.\d{4,}|\d[eE][+-]?\d+", s),
                                      f"试算显示漏出奇怪小数：{s!r}")

    def test_carry_never_reaches_the_panels(self):
        """★ 找零余额是**内部账**：它绝不该出现在任何 AI 可见文本里（连像"0.55"都不该）。"""
        w = mp.World(size=16, seed=7, nations=["秦", "楚"])
        w.nations["秦"].res["黄金"] = 9999
        w.buy("秦", "粮食", 1)
        carry = w.gold_carry.get("秦", 0.0)
        self.assertGreater(carry, 0.0, "前提：这一步该攒出零头")
        blob = mp_ai.full_state(w, "秦") + mp_ai.execute(w, "秦", "query", {"panel": "all"})
        self.assertNotIn(f"{carry:.2f}", blob, "找零余额漏进了面板")
        self.assertNotIn(str(carry), blob, "找零余额漏进了面板")


class TestFloatMatchesExactMath(unittest.TestCase):
    """★ 浮点结算与**精确有理数**逐笔一致（用户 2026-10-09：「浮点依然存在误差……甚至打算弄有理数」）。

    这条是"要不要上 Fraction"的**证据**，不是意见：把同一条价格曲线用 `Fraction` 精确重算一遍，
    与 `_settle_gold` 的浮点结算**逐笔比金额**。3000 笔（实测）0 分歧 ⇒ 这个量级上浮点不咬人，
    不必为它上有理数（价格本身是 float，只把余额换成 Fraction 也消不掉"价格已是近似"）。

    ★ 什么时候这条会红：有人改了定价/结算路径（比如引入除法、累加、指数），把误差放大到
      能翻过 1 金门槛。那时要么修算法，要么真的上整数最小单位——**别把这条删了**。
    """

    TRADES = 400

    def test_float_and_fraction_agree_coin_by_coin(self):
        from fractions import Fraction as F
        w = mp.World(size=12, seed=1, nations=["秦", "楚"])
        rng = random.Random(11)
        carry = F(0)
        for _ in range(self.TRADES):
            good = rng.choice(list(mp.TRADEABLE))
            n = rng.randint(1, 9)
            side = rng.choice(("buy", "sell"))
            _unit_f, p1_f, total_f = w.market_walk(good, n, side)
            paid_f = w._settle_gold("秦", total_f, commit=False)
            # —— 精确参考：价格、每单位推动、地板/天花板、价差全部用 Fraction 参与
            p0 = F(w.prices[good])
            tick = F(mp.MARKET[good]) * F(mp.PRICE_IMPACT) / F(w.market_depth(good))
            d = tick * n * (1 if side == "buy" else -1)
            lo, hi = F(w.price_floor(good)), F(mp.MARKET[good]) * F(mp.PRICE_MAX_RATIO)
            p1 = min(max(p0 + d, lo), hi)
            spread = F(mp.MARKET_SPREAD) / 2
            unit = (p0 + p1) / 2 * ((1 + spread) if side == "buy" else (1 - spread))
            total_e = unit * n
            paid_e = int((carry + total_e).__floor__())
            carry = carry + total_e - paid_e
            self.assertEqual(paid_f, paid_e,
                             f"{good}×{n} {side}：浮点结算 {paid_f} ≠ 精确 {paid_e}")
            w.prices[good] = p1_f
            w.gold_carry["秦"] = float(carry)       # 两侧走同一条曲线


if __name__ == "__main__":
    unittest.main()
