# -*- coding: utf-8 -*-
"""市场空转（2026-09-22）：同一回合在同一个商品上又买又卖 ⇒ **当场**提示并算出损失。

用户口径（原话）：「加个功能，如果出现市场空转，例如某个商品在这个回合又买又卖，提示空转，
并计算出损失」＋ 澄清「**不是回合结束时，是每次操作都追踪一次**，例如同一回合先买(不提示)，
然后又卖(卖完后提示)，又买(再提示)」。

所以要钉的是三件事：

1. **每一笔成交判一次**（不是回合末算总账）：第一笔买不提示，随后的卖当场提示；
   再买一次又提示——每次都用**当时的累计**重算，量在长、数在变；
2. **损失算得对**：空转量 = min(买, 卖)（重叠部分才叫倒手），
   损失 = 空转量 ×(买均价 − 卖均价) —— 就是那一进一出真金白银少掉的钱；
3. **不误报**：只买不卖 / 只卖不买 / **跨回合**分批（那是省钱的正确做法）都不提示。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import mp_ai  # noqa: E402

GOOD = "矿石"


def _world(nations=("秦", "楚", "齐", "燕", "赵"), stock=200):
    w = mp.World(size=16, seed=5, nations=list(nations))
    for n in w.order:
        w.nations[n].res[GOOD] = stock
        w.nations[n].res["黄金"] = 5000
    return w


class TestChurnDetected(unittest.TestCase):
    def test_先买不提示_随后卖当场提示(self):
        """用户给的例子第一段：买（不提示）→ 卖（提示）。"""
        w = _world()
        ok, first = w.buy("秦", GOOD, 50)
        self.assertTrue(ok, first)
        self.assertNotIn("空转", first, "只买了还没卖，不算空转")

        ok, second = w.sell("秦", GOOD, 50)
        self.assertTrue(ok, second)
        self.assertIn("市场空转", second)
        self.assertIn("又买又卖", second)
        self.assertIn("净亏", second)

    def test_先卖后买同样提示(self):
        """用户 2026-09-22：「**先卖后买也是空转**」——判据只看"两个方向都成交过"，
        不看谁先谁后（两笔都是白付价差，代价一模一样）。"""
        w = _world()
        ok, first = w.sell("秦", GOOD, 50)
        self.assertTrue(ok, first)
        self.assertNotIn("空转", first, "只卖还没买，不算空转")
        ok, second = w.buy("秦", GOOD, 50)
        self.assertTrue(ok, second)
        self.assertIn("市场空转", second, "先卖后买，买完就得提示")
        self.assertIn("净亏", second)

    def test_再买一次又提示(self):
        """第二段：又买一次 ⇒ **再提示一次**（每次操作都追踪，不是只在回合末算一次）。"""
        w = _world()
        w.buy("秦", GOOD, 50)
        _ok, sell_msg = w.sell("秦", GOOD, 50)
        ok, buy_msg = w.buy("秦", GOOD, 50)
        self.assertTrue(ok, buy_msg)
        self.assertIn("市场空转", buy_msg, "第三笔也得提示")
        self.assertIn("买 100", buy_msg, "该报**当时的累计**：买 100")
        self.assertIn("卖 50", buy_msg)
        self.assertEqual(_loss(buy_msg), int(round(w.churn_loss(w.churn["秦"][GOOD])[1])),
                         "回执里的数字该与账本一致（每笔都按当时的累计重算）")
        self.assertEqual(w.churn_loss(w.churn["秦"][GOOD])[0], 50,
                         "多买的那 50 是真实仓位，不算进倒手量")

    def test_损失就是那一进一出少掉的钱(self):
        """损失 = min(买,卖) ×(买均价 − 卖均价)，拿引擎记的成交账现算对一遍。"""
        w = _world()
        w.buy("秦", GOOD, 50)
        _ok, msg = w.sell("秦", GOOD, 50)
        t = w.churn["秦"][GOOD]
        c, loss = w.churn_loss(t)
        self.assertEqual(c, 50)
        avg_buy, avg_sell = t["buy_gold"] / t["buy_n"], t["sell_gold"] / t["sell_n"]
        self.assertAlmostEqual(loss, 50 * (avg_buy - avg_sell), places=6)
        self.assertGreater(loss, 0, "倒手必亏：买价含 +半价差、卖价含 −半价差")
        self.assertEqual(_loss(msg), int(round(loss)), "回执里的数字该与账本一致")

    def test_只算重叠的那部分(self):
        """买 10 卖 4：倒手的是 4，剩下 6 是真实仓位（不该算进空转）。"""
        w = _world()
        w.buy("秦", GOOD, 10)
        _ok, msg = w.sell("秦", GOOD, 4)
        self.assertEqual(w.churn_loss(w.churn["秦"][GOOD])[0], 4)
        self.assertIn("重叠的 4 单位", msg)

    def test_亏多少与买卖价差同量级(self):
        """同一批货倒手的亏损 ≈ 两趟价差（10% 上下）+ 自己推价的冲击 —— 数量级不许错。"""
        w = _world()
        w.buy("秦", GOOD, 50)
        _ok, msg = w.sell("秦", GOOD, 50)
        loss = _loss(msg)
        unit = mp.MARKET[GOOD]
        self.assertGreater(loss, 50 * unit * mp.MARKET_SPREAD * 0.5, "至少得亏掉大半趟价差")
        self.assertLess(loss, 50 * unit * mp.MARKET_SPREAD * 2, "也不该离谱到两倍价差以上")


class TestNoFalsePositives(unittest.TestCase):
    def test_只买或只卖不提示(self):
        w = _world()
        self.assertNotIn("空转", w.buy("秦", GOOD, 30)[1])
        self.assertNotIn("空转", w.buy("秦", GOOD, 30)[1])
        self.assertNotIn("空转", w.sell("楚", GOOD, 30)[1])
        self.assertNotIn("空转", w.sell("楚", GOOD, 30)[1])

    def test_不同商品各算各的(self):
        w = _world()
        w.nations["秦"].res["木头"] = 100
        w.buy("秦", GOOD, 20)
        self.assertNotIn("空转", w.sell("秦", "木头", 20)[1], "买矿卖木＝两笔正经生意，不是倒手")
        self.assertEqual(w.churn_brief("秦"), "")

    def test_跨回合分批不提示(self):
        """★ 「上回合卖、这回合买」**不是**空转——跨回合分批恰恰是省钱的正确做法
        （见《经济学手册》第四节）：空转只在**同一个回合内**算。"""
        w = _world()
        w.sell("秦", GOOD, 50)
        w.begin_turn()
        w.resolve_turn()                       # 过了一个回合边界
        self.assertEqual(w.churn, {}, "回合末该清空追踪")
        _ok, msg = w.buy("秦", GOOD, 50)
        self.assertNotIn("空转", msg, "跨回合了，那笔卖单不该再算进来")

    def test_别国的倒手不算在我头上(self):
        w = _world()
        w.buy("秦", GOOD, 20)
        w.sell("楚", GOOD, 20)
        _ok, msg = w.buy("秦", GOOD, 20)
        self.assertNotIn("空转", msg, "楚的卖单不构成秦的空转")
        self.assertEqual(w.churn_brief("秦"), "")


class TestSurfaced(unittest.TestCase):
    """提示不止出现在回执里：纪事、回合摘要、市场面板三处都能看见。"""

    def test_进纪事(self):
        w = _world()
        w.buy("秦", GOOD, 20)
        w.sell("秦", GOOD, 20)
        self.assertTrue(any("市场空转" in e for e in w.events_for("秦", limit=5)),
                        "该在本国近讯里留一条（回合末还能回看）")
        self.assertFalse(any("市场空转" in e for e in w.events_for("楚", limit=5)),
                         "空转是自己的账，不该播给全世界")

    def test_进回合结算摘要(self):
        w = _world()
        w.buy("秦", GOOD, 20)
        w.sell("秦", GOOD, 20)
        w.begin_turn()
        w.resolve_turn()
        self.assertIn("空转", w.econ_summary["秦"])
        self.assertIn("亏", w.econ_summary["秦"])

    def test_进市场面板(self):
        w = _world()
        self.assertNotIn("空转", mp_ai._fmt_market(w, "秦"))
        w.buy("秦", GOOD, 20)
        w.sell("秦", GOOD, 20)
        panel = mp_ai._fmt_market(w, "秦")
        self.assertIn("空转", panel)
        self.assertIn("经济手册", panel, "该指条明路：为什么不能倒手")

    def test_赚钱也如实报(self):
        """市价被外人砸下来时的"先卖后买"可能真赚了——那就如实说赚，不许硬写成亏。

        （自己跟自己倒手**必亏**：买价含 +半价差、卖价含 −半价差，两趟都是你付。
        只有**别人**在两笔之间把价推走，才可能出现"卖得贵、买得便宜"。）"""
        w = _world()
        w.sell("秦", GOOD, 50)                 # 先卖（当时价高）
        w.prices[GOOD] = w.prices[GOOD] * 0.4  # 外人砸盘：价掉下来（模拟别国抛售）
        _ok, msg = w.buy("秦", GOOD, 50)       # 再买回来反而便宜
        self.assertIn("空转", msg, "不管赚亏，倒手这个动作本身就该提示")
        self.assertIn("赚", msg)
        self.assertNotIn("净亏", msg)


def _loss(msg: str) -> int:
    """从提示里抠出损失数（回执格式：`净亏 13 金` / `反倒赚了 5 金`）。"""
    import re
    m = re.search(r"(?:净亏|赚了) (\d+) 金", msg)
    assert m, f"提示里没有金额：{msg}"
    return int(m.group(1))


if __name__ == "__main__":
    unittest.main()
