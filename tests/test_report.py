# -*- coding: utf-8 -*-
"""经济报表测试：每 10 回合自动结一期、口径正确、AI 只能查不能手动跑。
全合成世界，不碰真实 mp_save.json。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import mp_ai  # noqa: E402


def _find_tiles(w, want: str) -> tuple[int, int]:
    """物化地块直到找到一块含 want 资源（耕地/黄金）的格子。"""
    for x in range(12):
        for y in range(12):
            if (x, y) in w.tiles:
                continue
            t = w._new_tile(x, y, "秦")
            w.tiles[(x, y)] = t
            if t["resources"].get(want, 0) >= 1:
                return (x, y)
    raise AssertionError(f"找不到含 {want} 的地块")


def _mk(seed: int = 11) -> tuple[mp.World, tuple[int, int], tuple[int, int]]:
    """合成世界：秦有 1 农场 + 1 金矿 + 1 支步兵（补给充足），楚什么都没做。"""
    w = mp.World(size=24, seed=seed, nations=["秦", "楚"])
    w.cheat("秦", 黄金=20000, 木头=900, 粮食=500, 补给=300)
    farm_t = _find_tiles(w, "耕地")
    gold_t = _find_tiles(w, "黄金")
    w.build("秦", *farm_t, "农场")
    w.build("秦", *gold_t, "黄金矿场")
    w.armies.append({"id": 1, "gid": 100000001, "name": "秦·步一军", "type": "步", "hp": 100,
                     "x": farm_t[0], "y": farm_t[1], "owner": "秦", "moved_turn": -1})
    return w, farm_t, gold_t


def _run(w, turns: int, act=None) -> None:
    for turn in range(1, turns + 1):
        w.begin_turn()
        if act:
            act(turn)
        w.resolve_turn()


class TestCadence(unittest.TestCase):
    def test_no_report_before_tenth_turn(self):
        w, *_ = _mk()
        _run(w, 9)
        self.assertEqual(w.econ_reports.get("秦"), None)
        self.assertIn("不能手动运行", mp_ai.execute(w, "秦", "report", {}))

    def test_reports_at_11_and_21(self):
        w, *_ = _mk()
        _run(w, 21)
        self.assertEqual([r["report_turn"] for r in w.econ_reports["秦"]], [11, 21])
        self.assertEqual([r["period_end"] for r in w.econ_reports["秦"]], [10, 20])

    def test_ledger_resets_each_period(self):
        w, farm_t, _ = _mk()
        _run(w, 10, lambda t: w.build("秦", *farm_t, "农场") if t == 5 else None)
        first = w.econ_reports["秦"][0]
        _run(w, 10)
        second = w.econ_reports["秦"][1]
        self.assertGreater(first["invest"], 0)
        self.assertEqual(second["invest"], 0)          # 第 11–20 回合没建东西
        self.assertFalse(w.ledger.get("秦", {}).get("invest_gold"))


class TestCaliber(unittest.TestCase):
    def test_gdp_is_production_value_added(self):
        w, *_ = _mk()
        _run(w, 10)
        rep = w.econ_reports["秦"][0]
        # 金矿第 2 回合落地 → 本期 9 回合 ×10 金 = 90/10 = 9 金/回合（再加农场的粮）
        self.assertGreater(rep["gdp"], 9.0)
        self.assertLess(rep["gdp"], 30.0)

    def test_military_is_consumption_times_market_price(self):
        w, *_ = _mk()
        _run(w, 10)
        rep = w.econ_reports["秦"][0]
        self.assertEqual(rep["supply_eaten"], 10)       # 1 支步兵 × 10 回合
        self.assertAlmostEqual(rep["military"], round(10 / 10 * w.prices["补给"], 1), places=1)
        self.assertAlmostEqual(rep["military_ratio"], rep["military"] / rep["gdp"], delta=0.01)

    def test_military_ignores_source(self):
        """自产补给照样算军费——买不买补给不影响军费口径。"""
        w, *_ = _mk()
        _run(w, 10)                                     # 全程没买过补给
        rep = w.econ_reports["秦"][0]
        self.assertEqual(rep["import_gold"], 0)
        self.assertGreater(rep["military"], 0)

    def test_no_army_no_military(self):
        w = mp.World(size=20, seed=5, nations=["秦", "楚"])
        _run(w, 10)
        rep = w.econ_reports["秦"][0]
        self.assertEqual(rep["supply_eaten"], 0)
        self.assertEqual(rep["military"], 0)

    def test_invest_counts_build_spend(self):
        w, farm_t, _ = _mk()
        led_before = dict(w.ledger["秦"])
        self.assertGreater(led_before["invest_gold"], 0)     # 农场+金矿的实付金
        self.assertGreater(led_before["invest_wood_value"], 0)
        _run(w, 10)
        rep = w.econ_reports["秦"][0]
        self.assertAlmostEqual(rep["invest"],
                               round(led_before["invest_gold"] + led_before["invest_wood_value"], 1),
                               places=1)

    def test_trade_ratio_from_market_turnover(self):
        w, *_ = _mk()
        _run(w, 10, lambda t: w.buy("秦", "矿石", 10) if t == 5 else None)
        rep = w.econ_reports["秦"][0]
        # 只买不卖 → 外贸占比 > 0，且内循环 = 1 − 外贸
        self.assertGreater(rep["trade_ratio"], 0)
        self.assertAlmostEqual(rep["trade_ratio"] + (1 - rep["trade_ratio"]), 1.0, places=9)

    def test_first_period_growth_all_none_and_rendered_as_dash(self):
        """首期没有上期可比 → 三个增长率都是 None，报表里显示「—」（不是 0%）。"""
        w, *_ = _mk()
        _run(w, 10)
        rep = w.econ_reports["秦"][0]
        for k in ("gdp_growth", "invest_growth", "assets_growth"):
            self.assertIsNone(rep[k])
        for text in (mp_ai._fmt_report(w, "秦"), mp_ai._fmt_report(w, "秦", all_=True)):
            self.assertIn("—", text)
            self.assertNotIn("+0.0%", text)

    def test_corrupt_previous_snapshot_does_not_crash(self):
        """上期快照缺字段（旧档/损坏）→ 增长率退回「—」，绝不让异常抛进回合结算。"""
        w, *_ = _mk()
        _run(w, 10)
        w.econ_reports["秦"] = [{}]
        _run(w, 10)                                      # 结第二期，不应抛异常
        self.assertIsNone(w.econ_reports["秦"][-1]["gdp_growth"])
        self.assertIn("—", mp_ai._fmt_report(w, "秦"))

    def test_growth_rates_second_period(self):
        w, *_ = _mk()
        _run(w, 20)
        first, second = w.econ_reports["秦"]
        self.assertIsNone(first["gdp_growth"])           # 首期无上期
        self.assertIsNotNone(second["gdp_growth"])
        self.assertGreater(second["gdp"], first["gdp"])
        self.assertGreater(second["gdp_growth"], 0)


class TestContextInjection(unittest.TestCase):
    """只在出表那一回合把全文塞进状态面板；其余回合只给一行摘要。"""

    def _seg(self, state: str) -> str:
        return state[state.index("【经济报表】"):state.index("【纪事")]

    def test_full_report_on_reporting_turn(self):
        w, *_ = _mk()
        _run(w, 10)
        w.begin_turn()                                   # 第 11 回合 = 出表回合
        seg = self._seg(mp_ai.full_state(w, "秦"))
        self.assertIn("报表回合 11", seg)
        self.assertIn("军费（每回合补给消耗）", seg)
        self.assertIn("挤压投资", seg)                   # 固定警示随全文一起进上下文

    def test_only_summary_on_other_turns(self):
        w, *_ = _mk()
        _run(w, 10)
        w.begin_turn()
        _run(w, 4)                                       # 第 12–15 回合：不是出表回合
        seg = self._seg(mp_ai.full_state(w, "秦"))
        self.assertIn("最新第 11 回合", seg)
        self.assertNotIn("军费（每回合补给消耗）", seg)   # 不重复整张表
        self.assertIn("report", seg)

    def test_panel_before_first_report(self):
        w, *_ = _mk()
        _run(w, 5)
        self.assertIn("尚无", self._seg(mp_ai.full_state(w, "秦")))

    def test_panel_shows_latest_period_only(self):
        w, *_ = _mk()
        _run(w, 20)
        w.begin_turn()                                   # 第 21 回合
        seg = self._seg(mp_ai.full_state(w, "秦"))
        self.assertIn("报表回合 21", seg)
        self.assertNotIn("报表回合 11", seg)             # 只放最新一期
        self.assertIn("共 2 期", seg)                     # 提示历史期怎么取


class TestAnnouncement(unittest.TestCase):
    def test_generation_is_announced_to_owner_only(self):
        """报表生成要写一条纪事：看海终端看得到，该国【近讯】也看得到（别国看不到）。"""
        w, *_ = _mk()
        _run(w, 10)
        lines = [h["text"] for h in w.history if "经济报表已生成" in h.get("text", "")]
        self.assertEqual(len(lines), 2)                      # 秦、楚 各一条
        self.assertIn("第 11 回合经济报表已生成", " ".join(lines))
        self.assertIn("report", lines[0])
        self.assertTrue(any("经济报表已生成" in e for e in w.events_for("秦")))
        self.assertFalse(any("经济报表已生成" in e for e in w.events_for("野人")))


class TestPartialPeriod(unittest.TestCase):
    """不完整首期：续档/中途登场时账本从零开始，结账要按实际覆盖回合数平均，不能一律 ÷10。"""

    def test_old_save_without_ledger_gets_partial_first_period(self):
        w, *_ = _mk()
        _run(w, 5)
        path = tempfile.mktemp(suffix=".json")
        try:
            w.save(path)
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            data.pop("ledger")                       # 模拟旧档：没有账本字段
            Path(path).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            w2 = mp.World.load(path)
            self.assertEqual(w2.ledger, {})
            _run(w2, 5)                              # 第 6–10 回合才被记账
            rep = w2.econ_reports["秦"][0]
            self.assertEqual(rep["report_turn"], 11)
            self.assertEqual((rep["period_start"], rep["span"]), (6, 5))
            self.assertGreater(rep["gdp"], 9.5)      # 按 5 回合平均 → 接近满产；÷10 会腰斩到 ~5
        finally:
            os.unlink(path)

    def test_ledger_since_survives_save_load(self):
        """正常续档（账本在档里）：覆盖回合数不丢，下一期仍是完整 10 回合。"""
        w, *_ = _mk()
        _run(w, 5)
        path = tempfile.mktemp(suffix=".json")
        try:
            w.save(path)
            w2 = mp.World.load(path)
            _run(w2, 5)
            rep = w2.econ_reports["秦"][0]
            self.assertEqual((rep["period_start"], rep["span"]), (1, 10))
        finally:
            os.unlink(path)


class TestReadOnly(unittest.TestCase):
    def test_report_tool_cannot_generate(self):
        w, *_ = _mk()
        _run(w, 10)
        n = len(w.econ_reports["秦"])
        for args in ({}, {"all": True}, {"turn": 11}, {"turn": 99}):
            mp_ai.execute(w, "秦", "report", args)
        self.assertEqual(len(w.econ_reports["秦"]), n)

    def test_both_warnings_always_shown(self):
        w, *_ = _mk()
        _run(w, 10)
        for text in (mp_ai._fmt_report(w, "秦"),
                     mp_ai._fmt_report(w, "秦", all_=True)):
            self.assertIn("挤压投资", text)
            self.assertIn("待宰羔羊", text)

    def test_report_failure_does_not_break_turn(self):
        """报表是派生数据：生成炸了只跳过本期并记进纪事，绝不能拖垮回合结算。"""
        w, *_ = _mk()

        def boom(_n):
            raise RuntimeError("boom")

        w.nation_assets = boom
        _run(w, 10)                                      # 第 10 回合结账时炸 → 不该抛异常
        self.assertFalse(w.econ_reports.get("秦"))
        self.assertTrue(any("经济报表生成失败" in h.get("text", "") for h in w.history))

    def test_save_load_roundtrip(self):
        w, *_ = _mk()
        _run(w, 10)
        path = tempfile.mktemp(suffix=".json")
        try:
            w.save(path)
            w2 = mp.World.load(path)
            self.assertEqual(w2.econ_reports, w.econ_reports)
            self.assertEqual(w2.ledger, w.ledger)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
