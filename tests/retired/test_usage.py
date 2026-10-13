# -*- coding: utf-8 -*-
"""全期 token 累计（`World.token_usage`）的单测。

这是**统计**字段，不参与任何结算——所以测试守的是三件事：
  ① 累加口径（hit+miss=输入总量、out/reason 各归各）；
  ② 估的与报的**分得开**（`est_calls` / `real_calls`），否则汇总出来的
     "总用量"是估数与真数的混合物，拿它算钱会错得没边；
  ③ 存档往返：save→load→save 字节级相等，且老档（没有这个键）开得起来。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402


def _mk(nations=("秦", "楚")):
    return mp.World(size=12, seed=5, nations=list(nations))


class TestUsageAccounting(unittest.TestCase):
    def test_counters_start_zero_for_every_nation(self):
        w = _mk()
        for n in w.nations:
            slot = w.token_usage[n]
            for k in ("calls", "prompt", "hit", "miss", "out", "reason",
                      "est_calls", "real_calls"):
                self.assertEqual(slot[k], 0, f"{n}.{k} 应零起步")

    def test_accumulates_reported_call(self):
        w = _mk()
        w.add_usage("秦", {"hit": 800, "miss": 200, "out_tokens": 30,
                           "reason_tokens": 12, "usage_reported": True})
        s = w.token_usage["秦"]
        self.assertEqual(s["calls"], 1)
        self.assertEqual(s["prompt"], 1000, "hit+miss 才是输入总量")
        self.assertEqual((s["hit"], s["miss"]), (800, 200))
        self.assertEqual((s["out"], s["reason"]), (30, 12))
        self.assertEqual((s["real_calls"], s["est_calls"]), (1, 0))

    def test_estimated_kept_separate_from_reported(self):
        """估的那几笔必须能数出来——混进总账就会被当成真数用。"""
        w = _mk()
        w.add_usage("秦", {"hit": 0, "miss": 100, "out_tokens": 5, "usage_reported": False})
        w.add_usage("秦", {"hit": 100, "miss": 0, "out_tokens": 7, "usage_reported": True})
        s = w.token_usage["秦"]
        self.assertEqual(s["calls"], 2)
        self.assertEqual((s["est_calls"], s["real_calls"]), (1, 1))
        tot = w.usage_totals()
        self.assertTrue(tot["estimated"], "掺了估算 ⇒ 汇总要打 estimated")

    def test_unknown_nation_is_ignored_not_created(self):
        """★ 键绝不能被外部字符串撑大：那会让存档契约断言在不相干的回合突然炸。"""
        w = _mk()
        before = set(w.token_usage)
        w.add_usage("glm-5.3-flash", {"hit": 1, "miss": 1, "out_tokens": 1})
        w.add_usage("不存在国", {"hit": 1, "miss": 1, "out_tokens": 1})
        self.assertEqual(set(w.token_usage), before, "不得凭空多出键")
        self.assertEqual(w.token_usage["秦"]["calls"], 0)

    def test_totals_sum_across_nations(self):
        w = _mk()
        w.add_usage("秦", {"hit": 100, "miss": 0, "out_tokens": 10, "usage_reported": True})
        w.add_usage("楚", {"hit": 300, "miss": 100, "out_tokens": 20, "usage_reported": True})
        t = w.usage_totals()
        self.assertEqual(t["prompt"], 500)
        self.assertEqual(t["out"], 30)
        self.assertEqual(t["total"], 530)
        self.assertAlmostEqual(t["hit_rate"], 400 / 500)
        self.assertFalse(t["estimated"])

    def test_hit_rate_none_when_no_input(self):
        """没有输入时命中率是 None（而不是 0）——0% 是"全没命中"，两回事。"""
        w = _mk()
        self.assertIsNone(w.usage_totals()["hit_rate"])


class TestUsagePersistence(unittest.TestCase):
    def test_roundtrip_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as d:
            p1, p2 = Path(d) / "a.json", Path(d) / "b.json"
            w = _mk()
            w.add_usage("秦", {"hit": 700, "miss": 300, "out_tokens": 42,
                               "reason_tokens": 9, "usage_reported": True})
            w.save(p1)
            mp.World.load(p1).save(p2)
            self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_usage_survives_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            w = _mk()
            w.add_usage("楚", {"hit": 5, "miss": 6, "out_tokens": 7, "usage_reported": True})
            w.save(p)
            got = mp.World.load(p).token_usage["楚"]
            self.assertEqual(got["hit"], 5)
            self.assertEqual(got["prompt"], 11)
            self.assertEqual(got["out"], 7)
            self.assertEqual(got["real_calls"], 1)

    def test_old_save_without_field_opens_with_zeros(self):
        """老档缺 token_usage ⇒ 按国家补零起步，**不是**拒载。"""
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "old.json"
            _mk().save(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            del data["token_usage"]                      # 冒充旧档
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            w = mp.World.load(p)
            self.assertEqual(set(w.token_usage), set(w.nations))
            self.assertTrue(all(v["calls"] == 0 for v in w.token_usage.values()))

    def test_save_materialises_every_nation(self):
        """save 必须按国家补齐：否则"存的是 {}、读回来是带键的"会破坏字节级往返。"""
        w = _mk()
        w.token_usage = {}                # 故意掏空
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a.json"
            w.save(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(set(data["token_usage"]), set(w.nations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
