# -*- coding: utf-8 -*-
"""世界央行（2026-09-19）：开关 / 储蓄结息 / 贷款 / 广播 / 面板 / 存档。

用户口径：
· 「加世界央行，允许贷款，他们的默认现金就是储蓄，我可以设定利率，甚至负利率，
   如果为负利率，每个回合强制扣钱，贷款利率等于储蓄利率+3%，贷款利率也可以为负」
· 「贷款可以约定时间，每个回合自动计算应还额，最高贷款 1000 金币，最多贷款 10 回合，
   到期强制扣钱，可以扣成负的」
· 追问：① 单笔 1000、**还清前不能再借**；② 播报 = **变动幅度 + 现值**；③ 储蓄**扣不到负**。
· 补充：「贷款算外交，要 10 块钱」；「匈奴也可以借」；「世界银行是一个配置开关，默认不开启」。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import mp_ai  # noqa: E402


def _world(nations=("秦", "楚"), on=True):
    w = mp.World(size=16, seed=3, nations=list(nations))
    w.bank["on"] = on
    return w


class TestSwitch(unittest.TestCase):
    """**默认关**：关着就一分钱不动、一个字不占（面板/工具/规则里都不出现）。"""

    def test_off_by_default(self):
        w = mp.World(size=16, seed=3, nations=["秦"])
        self.assertFalse(w.bank_on())
        self.assertEqual(w.bank["rate"], 0.0)
        self.assertEqual(w.bank["loans"], {})

    def test_off_blocks_everything(self):
        w = _world(on=False)
        self.assertIn("没开世界央行", w.bank_set_rate(0.05)[1])
        self.assertIn("没开世界央行", w.bank_loan("秦", 500, 3)[1])
        w.resolve_turn()
        self.assertEqual(w.res("秦", "黄金"), 1500, "关着不该动钱")

    def test_off_is_invisible_to_the_model(self):
        w = _world(on=False)
        st = mp_ai.full_state(w, "秦")
        self.assertNotIn("【央行】", st)
        self.assertFalse(any(s["function"]["name"] == "loan" for s in mp_ai.tool_schemas(w, "秦")))
        self.assertNotIn("世界央行", mp_ai.rules_text(w, ""))

    def test_on_shows_up_everywhere(self):
        w = _world(on=True)
        self.assertIn("【央行】", mp_ai.full_state(w, "秦"))
        self.assertTrue(any(s["function"]["name"] == "loan" for s in mp_ai.tool_schemas(w, "秦")))
        self.assertIn("世界央行", mp_ai.rules_text(w, ""))
        self.assertIn("世界央行", mp_ai.rules_text(w, "央行"))

    def test_broadcast_reaches_everyone(self):
        """世界公告与"按视野过滤的纪事"不同：没坐标也要**全世界**都收到。"""
        w = _world()
        w.bank_set_rate(0.05)
        for n in ("秦", "楚"):
            self.assertTrue(any("世界央行公告" in e for e in w.events_for(n, limit=3)), n)

    def test_broadcast_does_not_backfill_vision(self):
        """★ 回归守卫：`broadcast` 不能把「有快照就只认快照」这条规矩改坏
        （夺地后旧战报不得回溯显形）。"""
        w = _world()
        w.log("旧战报", phase="战报", nation="秦", x=3, y=3)   # 楚当时看不见
        w.tiles[(3, 3)] = w._new_tile(3, 3, "楚")              # 楚后来占下
        self.assertFalse(any("旧战报" in e for e in w.events_for("楚")), "旧战报回溯显形了")


class TestRate(unittest.TestCase):
    def test_up_broadcast_text(self):
        w = _world()
        ok, msg = w.bank_set_rate(0.05)
        self.assertTrue(ok)
        self.assertIn("为了抑制通货膨胀，上调了 5.0% 利率（现为 +5.0%）", msg)

    def test_down_broadcast_text_with_delta(self):
        """下调 = 减少紧缩；x 是**变动幅度**，括号里给现值（用户选的 A 方案）。"""
        w = _world()
        w.bank_set_rate(0.05)
        _ok, msg = w.bank_set_rate(-0.03)
        self.assertIn("为了减少紧缩，下调了 8.0% 利率（现为 -3.0%）", msg)

    def test_same_rate_makes_no_noise(self):
        w = _world()
        w.bank_set_rate(0.05)
        ok, msg = w.bank_set_rate(0.05)
        self.assertFalse(ok)
        self.assertIn("没变", msg)

    def test_clamped_to_band(self):
        w = _world()
        ok, msg = w.bank_set_rate(9.99)
        self.assertTrue(ok)
        self.assertIn("已夹到", msg)
        self.assertAlmostEqual(w.bank_rate(), mp.BANK_RATE_MAX)

    def test_loan_rate_is_savings_plus_spread(self):
        w = _world()
        w.bank_set_rate(0.05)
        self.assertAlmostEqual(w.bank_loan_rate(), 0.05 + mp.BANK_SPREAD)
        w.bank_set_rate(-0.2)
        self.assertAlmostEqual(w.bank_loan_rate(), -0.2 + mp.BANK_SPREAD, places=6)
        self.assertLess(w.bank_loan_rate(), 0, "负储蓄利率下贷款利率也可以为负")


class TestSavings(unittest.TestCase):
    """"国库现金默认就是储蓄"：每回合自动结息，不用存。"""

    def test_positive_rate_pays(self):
        w = _world()
        w.bank_set_rate(0.05)
        g0 = w.res("秦", "黄金")
        w.resolve_turn()
        self.assertEqual(w.res("秦", "黄金"), g0 + int(g0 * 0.05))

    def test_negative_rate_charges(self):
        w = _world()
        w.bank_set_rate(-0.1)
        g0 = w.res("秦", "黄金")
        w.resolve_turn()
        self.assertEqual(w.res("秦", "黄金"), g0 + int(g0 * -0.1))

    def test_negative_rate_never_goes_below_zero(self):
        """★ 储蓄**扣不到负**（用户：「利率是根据储蓄相关的，怎么可能扣成负呢」）。"""
        w = _world()
        w.bank_set_rate(-0.5)
        w.nations["秦"].res["黄金"] = 1
        w.resolve_turn()
        self.assertGreaterEqual(w.res("秦", "黄金"), 0)

    def test_zero_rate_is_noop(self):
        w = _world()
        g0 = w.res("秦", "黄金")
        w.resolve_turn()
        self.assertEqual(w.res("秦", "黄金"), g0)


class TestLoan(unittest.TestCase):
    def _borrow(self, w, amount=500, turns=3):
        w.bank_loan("秦", amount, turns)
        return w.bank["loans"]["秦"]

    def test_limits(self):
        w = _world()
        self.assertIn("上限", w.bank_loan("秦", mp.BANK_LOAN_MAX + 1, 3)[1])
        self.assertIn("期限", w.bank_loan("秦", 100, mp.BANK_LOAN_MAX_TURNS + 1)[1])
        self.assertIn("正整数", w.bank_loan("秦", 0, 3)[1])
        self.assertIn("正整数", w.bank_loan("秦", -5, 3)[1])

    def test_one_at_a_time_until_repaid(self):
        """★ 单笔 1000、**还清前不能再借**（用户口径）。"""
        w = _world()
        ok, _ = w.bank_loan("秦", 500, 3)
        self.assertTrue(ok)
        ok, msg = w.bank_loan("秦", 100, 1)
        self.assertFalse(ok)
        self.assertIn("还清前不能再借", msg)
        for _ in range(4):                      # 跑满到期
            w.resolve_turn()
        self.assertNotIn("秦", w.bank["loans"])
        self.assertTrue(w.bank_loan("秦", 100, 1)[0], "还清后又可以借了")

    def test_cash_arrives_immediately(self):
        w = _world()
        g0 = w.res("秦", "黄金")
        w.bank_loan("秦", 500, 3)
        self.assertEqual(w.res("秦", "黄金"), g0 + 500)

    def test_accrual_and_forced_deduction(self):
        """每回合计息（应还额自动算），到期**强制扣款**。"""
        w = _world()
        w.bank_set_rate(0.05)                   # 贷款利率 = 5% + BANK_SPREAD
        lr = 0.05 + mp.BANK_SPREAD
        ln = self._borrow(w, 800, 3)
        self.assertEqual(ln["due"], 800)
        w.resolve_turn()
        self.assertEqual(w.bank["loans"]["秦"]["due"], int(round(800 * (1 + lr))))
        self.assertEqual(w.bank["loans"]["秦"]["turns_left"], 2)
        g_before = w.res("秦", "黄金")
        w.resolve_turn()
        w.resolve_turn()                        # 到期这一下
        self.assertNotIn("秦", w.bank["loans"])
        self.assertLess(w.res("秦", "黄金"), g_before, "到期应被强制扣款")

    def test_maturity_can_drive_gold_negative(self):
        """★ 到期强制扣款**可以扣成负的**（用户明确要求；储蓄才不许扣负）。"""
        w = _world()
        self._borrow(w, 1000, 1)
        w.nations["秦"].res["黄金"] = 10        # 先把钱花光
        w.resolve_turn()
        self.assertNotIn("秦", w.bank["loans"])
        self.assertLess(w.res("秦", "黄金"), 0, f"该扣成负的，实际 {w.res('秦', '黄金')}")

    def test_negative_loan_rate_shrinks_debt(self):
        """贷款利率为负 ⇒ 欠款每回合**缩水**。"""
        w = _world()
        w.bank_set_rate(-0.2)                   # 贷款率 = -17%
        ln = self._borrow(w, 1000, 2)
        w.resolve_turn()
        self.assertLess(w.bank["loans"]["秦"]["due"], ln["principal"])

    def test_per_nation_independent(self):
        w = _world()
        self.assertTrue(w.bank_loan("秦", 500, 3)[0])
        self.assertTrue(w.bank_loan("楚", 500, 3)[0], "各国额度独立，不该互相挡住")
        self.assertEqual(set(w.bank["loans"]), {"秦", "楚"})


class TestToolLayer(unittest.TestCase):
    def test_loan_tool_charges_diplo_fee(self):
        """★「贷款算外交，要 10 块钱」：走外交费（成功才扣）。"""
        w = _world()
        g0 = w.res("秦", "黄金")
        mp_ai._exec(w, "秦", "loan", {"amount": 500, "turns": 3})
        self.assertEqual(w.res("秦", "黄金"), g0 + 500 - mp.DIPLO_COST)

    def test_failed_loan_costs_nothing(self):
        w = _world()
        g0 = w.res("秦", "黄金")
        out = mp_ai._exec(w, "秦", "loan", {"amount": 99999, "turns": 3})
        self.assertIn("上限", out)
        self.assertEqual(w.res("秦", "黄金"), g0, "失败不烧金")

    def test_bad_args_do_not_crash(self):
        w = _world()
        out = mp_ai._exec(w, "秦", "loan", {"amount": "很多", "turns": None})
        self.assertIn("正整数", out)

    def test_huns_can_borrow(self):
        """★「匈奴也可以借」——贷款不是国与国的外交，不进 HUNS_BLOCKED。"""
        w = _world(nations=("秦", "林胡"))
        w.apply_polity("林胡", "huns")
        out = mp_ai._exec(w, "林胡", "loan", {"amount": 300, "turns": 2})
        self.assertNotIn("匈奴不搞", out)
        self.assertIn("已到账", out)
        self.assertIn("林胡", w.bank["loans"])

    def test_schema_description_has_no_live_rate(self):
        """★ 工具表在请求最前面：描述里**不能嵌当前利率**，否则一改利率前缀缓存全废。"""
        w = _world()
        sch = [s for s in mp_ai.tool_schemas(w, "秦")
               if s["function"]["name"] == "loan"][0]["function"]
        w.bank_set_rate(0.09)
        sch2 = [s for s in mp_ai.tool_schemas(w, "秦")
                if s["function"]["name"] == "loan"][0]["function"]
        self.assertEqual(sch["description"], sch2["description"], "利率变了工具表却跟着变")
        self.assertIn("面板", sch["description"], "该把现值指到面板去")


class TestSwitchIsOneWay(unittest.TestCase):
    """★ 「银行只能在配置文件开、不能关，可以中途加」（用户 2026-09-19）。

    `bank_enable()` 因此是**单向**的：只允许 false → true；配置说 false 也不会关掉已开的局。
    """

    def test_enable_from_off(self):
        w = _world(on=False)
        self.assertTrue(w.bank_enable(), "关着时该能打开（含中途开）")
        self.assertTrue(w.bank_on())
        self.assertFalse(w.bank_enable(), "已经开着 ⇒ 这次不算打开")

    def test_cannot_be_turned_off(self):
        w = _world(on=True)
        self.assertFalse(w.bank_enable())
        self.assertTrue(w.bank_on(), "没有「关」这个动作——开了就一直在")

    def test_midgame_enable_works_with_existing_save(self):
        """中途开：先跑几回合（关着），再打开，然后正常结息。"""
        w = _world(on=False)
        w.resolve_turn()
        g0 = w.res("秦", "黄金")
        w.bank_enable()
        w.bank_set_rate(0.1)
        w.resolve_turn()
        self.assertEqual(w.res("秦", "黄金"), g0 + int(g0 * 0.1))


class TestLoanSurvivesSave(unittest.TestCase):
    """★ 贷款进存档：中途存/读之后，计息与**到期强制扣款**必须接着算。

    （用户 2026-09-19：「贷款进存档，防止神秘 bug」——一笔没还的贷款如果在读档后
    凭空消失或算错，就是最难查的那种账。）
    """

    def test_resume_mid_loan(self):
        w = _world()
        w.bank_loan("秦", 600, 3)
        w.resolve_turn()                       # 计息一次（此刻利率 0 ⇒ 欠款 = 600×(1+利差)）
        due_at_save = w.bank["loans"]["秦"]["due"]
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        self.assertIn("秦", w2.bank["loans"], "读档后贷款不该消失")
        self.assertEqual(w2.bank["loans"]["秦"]["turns_left"], 2)
        self.assertEqual(w2.bank["loans"]["秦"]["due"], due_at_save)
        w2.resolve_turn()                      # 续跑：继续计息
        self.assertEqual(w2.bank["loans"]["秦"]["due"],
                         int(round(due_at_save * (1 + mp.BANK_SPREAD))))
        g0 = w2.res("秦", "黄金")
        w2.resolve_turn()                      # 到期这一下
        self.assertNotIn("秦", w2.bank["loans"])
        self.assertLess(w2.res("秦", "黄金"), g0, "续档后到期仍要强制扣款")


class TestPanelAndSave(unittest.TestCase):
    def test_panel_shows_rate_and_debt(self):
        w = _world()
        w.bank_set_rate(0.05)
        w.bank_loan("秦", 500, 3)
        st = mp_ai.full_state(w, "秦")
        self.assertIn("储蓄利率 +5.0%", st)
        self.assertIn(f"贷款利率 {(0.05 + mp.BANK_SPREAD):+.1%}", st)
        self.assertIn("你欠央行 500 金", st)
        self.assertIn("还剩 3 回合到期", st)

    def test_panel_warns_on_negative_rate(self):
        w = _world()
        w.bank_set_rate(-0.1)
        self.assertIn("缩水", mp_ai.full_state(w, "秦"))

    def test_save_load_roundtrip(self):
        w = _world()
        w.bank_set_rate(0.04)
        w.bank_loan("秦", 700, 5)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            w2 = mp.World.load(p)
        self.assertTrue(w2.bank_on())
        self.assertAlmostEqual(w2.bank_rate(), 0.04)
        self.assertEqual(w2.bank["loans"]["秦"]["due"], 700)
        self.assertEqual(w2.bank["loans"]["秦"]["turns_left"], 5)

    def test_v3_save_still_loads_with_bank_off(self):
        """★ **追加式存档**（2026-09-19 用户：「这个档允许追加，而不是重开，只加银行不能删
        硬件，就是为了增量更新存档」）：老档缺 `bank` 键 ⇒ 按默认补齐（银行关着），
        **不用重开**；再存一次就把新键带上（增量更新）。"""
        self.assertEqual(mp.SAVE_VERSION, 4)
        self.assertIn("bank", mp.SAVE_DEFAULTS)
        w = _world()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("bank")                      # 模拟 v3 档
            data["version"] = 3
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            w2 = mp.World.load(p)                 # 应能开，不该拒载
            self.assertFalse(w2.bank_on())
            w2.save(p)                            # 再存 ⇒ 新键补上
            self.assertIn("bank", json.loads(p.read_text(encoding="utf-8")))


def _world_with_report(nations=("秦", "楚"), on=True):
    """跑到第 10 回合末 ⇒ 第 11 回合开局，各国各有第一期报表（`report_turn`=11）。"""
    w = mp.World(size=24, seed=3, nations=list(nations))
    w.bank["on"] = on
    for _ in range(10):
        w.begin_turn()
        w.resolve_turn()
    return w


class TestSellReport(unittest.TestCase):
    """★ 「找央行买他国经济报表，20 金一次」（用户 2026-09-19）。

    口径：卖的是**已公布的公开账**的明细（每期报表生成时全世界都收到过一行公告），
    所以与 spy（偷当下的国库/储备/每块地建设）不冲突；买不到**一律不收钱**。
    """

    def test_buy_charges_and_lands_in_intel(self):
        w = _world_with_report()
        g0 = w.res("秦", "黄金")
        out = mp_ai.execute(w, "秦", "buy_report", {"to": "楚"})
        self.assertIn("✅ 已买下 楚 第 11 回合的经济报表", out)
        self.assertIn("【经济报表 · 报表回合 11", out, "该给全文，不是一行摘要")
        self.assertEqual(w.res("秦", "黄金"), g0 - mp.BUY_REPORT_COST, "一次该扣 20 金")
        self.assertEqual(len(w.econ_intel["秦"]), 1)
        self.assertTrue(w.econ_intel["秦"][0]["text"].startswith("🏦【央行售出】楚 第 11 回合经济报表"),
                        "买来的报表该带来源标记进情报库")
        self.assertIn("央行售出", mp_ai.execute(w, "秦", "query", {"panel": "spy"}),
                      "query panel=spy 该能重看买来的表")
        self.assertTrue(any("从世界央行买下 楚 第 11 回合的经济报表" in e
                            for e in w.events_for("秦", limit=5)), "该记一条纪事")
        self.assertFalse(any("央行" in e for e in w.events_for("楚", limit=5)),
                         "买谁的表是买家自己的事，不该让卖主收到通知")

    def test_turn_selects_period(self):
        w = _world_with_report()
        for _ in range(10):                       # 再跑 10 回合 ⇒ 第 21 回合那期也出来了
            w.begin_turn()
            w.resolve_turn()
        out = mp_ai.execute(w, "秦", "buy_report", {"to": "楚", "turn": 11})
        self.assertIn("报表回合 11", out)
        out = mp_ai.execute(w, "秦", "buy_report", {"to": "楚", "turn": 21})
        self.assertIn("报表回合 21", out)
        out = mp_ai.execute(w, "秦", "buy_report", {"to": "楚", "turn": 99})
        self.assertIn("没有第 99 回合的报表", out)
        self.assertIn("已出的期", out, "该把可买的期列出来")

    def test_repeat_buying_same_period_pays_each_time(self):
        """20 金**一次**：同一期反复买就反复收费（不是买断制）。"""
        w = _world_with_report()
        g0 = w.res("秦", "黄金")
        mp_ai.execute(w, "秦", "buy_report", {"to": "楚"})
        mp_ai.execute(w, "秦", "buy_report", {"to": "楚"})
        self.assertEqual(w.res("秦", "黄金"), g0 - 2 * mp.BUY_REPORT_COST)
        self.assertEqual(len(w.econ_intel["秦"]), 2)

    def test_nothing_is_charged_when_it_cannot_be_sold(self):
        """★ 买不到不收钱：没报表 / 买自己 / 该期不存在 / 国库不足，钱一分不动。"""
        w = _world_with_report()
        g0 = w.res("秦", "黄金")

        w2 = mp.World(size=24, seed=3, nations=["秦", "楚"])   # 第 3 回合：一期报表都还没结过
        w2.bank["on"] = True
        w2.turn = 3
        self.assertIn("还没有任何经济报表",
                      mp_ai.execute(w2, "秦", "buy_report", {"to": "楚"}))
        self.assertFalse(w2.econ_intel.get("秦"))

        self.assertIn("买自己国家的报表不用花钱",
                      mp_ai.execute(w, "秦", "buy_report", {"to": "秦"}))
        self.assertIn("没有这个国家",
                      mp_ai.execute(w, "秦", "buy_report", {"to": "齐"}))
        w.add_res("秦", "黄金", -(w.res("秦", "黄金") - 5))     # 国库只剩 5
        self.assertIn("国库不足", mp_ai.execute(w, "秦", "buy_report", {"to": "楚"}))
        self.assertEqual(w.res("秦", "黄金"), 5, "被拒的购买不该扣钱")
        self.assertFalse(w.econ_intel.get("秦"))
        self.assertEqual(w.res("楚", "黄金"), 1500, "钱只是不扣，不该转给卖主")

    def test_needs_the_bank(self):
        """开行才有这工具（关着则工具表里一个字都不出现）。"""
        w = _world_with_report(on=False)
        names = [s["function"]["name"] for s in mp_ai.tool_schemas(w, "秦")]
        self.assertNotIn("buy_report", names)
        self.assertNotIn("buy_report", mp_ai.rules_text(w, ""))
        self.assertNotIn("buy_report", mp_ai.full_state(w, "秦"))
        self.assertIn("没开世界央行", mp_ai.execute(w, "秦", "buy_report", {"to": "楚"}))
        w.bank["on"] = True
        names = [s["function"]["name"] for s in mp_ai.tool_schemas(w, "秦")]
        self.assertIn("buy_report", names)
        self.assertIn("buy_report", mp_ai.rules_text(w, "央行"))
        self.assertIn("buy_report", mp_ai.full_state(w, "秦"), "面板该指条明路")

    def test_huns_can_buy_too(self):
        """★ 银行工具与政体无关（手册：「匈奴也能借」）——匈奴看得见【央行】就得拿得到工具。

        此前 `tool_schemas` 只在**无政体**那条路上挂银行工具，匈奴走 `_SCHEMA_CACHE`
        那条路 ⇒ 面板写着"你可以借一笔"、工具表里却连 `loan` 都没有（看得见摸不着）。
        """
        w = _world_with_report(nations=("秦", "林胡"))
        w.apply_polity("林胡", "huns")
        names = [s["function"]["name"] for s in mp_ai.tool_schemas(w, "林胡")]
        self.assertIn("loan", names, "匈奴该拿得到借款工具")
        self.assertIn("buy_report", names, "匈奴该拿得到买报表工具")
        out = mp_ai._exec(w, "林胡", "buy_report", {"to": "秦"})
        self.assertNotIn("匈奴不搞", out)
        self.assertIn("✅ 已买下 秦", out)


if __name__ == "__main__":
    unittest.main()
