# -*- coding: utf-8 -*-
"""存档契约测试：版本拒载、往返字节级相等、部分资源字典构造。全合成数据，不碰真实档。

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


def _mk() -> mp.World:
    w = mp.World(size=16, seed=5, nations=["秦", "楚"])
    w.begin_turn()
    w.resolve_turn()
    return w


class TestVersionGate(unittest.TestCase):
    def _save_file(self, d):
        p = Path(d) / "s.json"
        _mk().save(p)
        return p

    def test_missing_or_bad_version_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._save_file(d)
            data = json.loads(p.read_text(encoding="utf-8"))
            # ① 无版本号＝一切旧档：拒
            data.pop("version")
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(mp.SaveFormatError):
                mp.World.load(p)
            # ② 版本号不对（未来的迁移时代）：拒
            data["version"] = mp.SAVE_VERSION + 99
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(mp.SaveFormatError):
                mp.World.load(p)

    def test_missing_top_level_key_rejected(self):
        """SAVE_KEYS 里任何一键缺失（截断/手改坏）→ 拒载而不是带病续局。"""
        with tempfile.TemporaryDirectory() as d:
            p = self._save_file(d)
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("armies")
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(mp.SaveFormatError):
                mp.World.load(p)

    def test_save_key_set_matches_contract(self):
        """正常存档必须与 SAVE_KEYS 严丝合缝（save 侧的契约断言不触发即证明）。"""
        with tempfile.TemporaryDirectory() as d:
            p = self._save_file(d)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(set(data), set(mp.SAVE_KEYS))


class TestRoundTrip(unittest.TestCase):
    def test_save_load_save_is_byte_identical(self):
        """load 是无损搬运工：还原后的世界再存档，与原件字节级一致。
        任何"load 丢字段/类型漂移"（frozenset/元组/float-vs-int）都会在这里现形——
        这类漂移正是历史上'幽灵地块'级事故的温床。"""
        with tempfile.TemporaryDirectory() as d:
            p1 = Path(d) / "a.json"
            p2 = Path(d) / "b.json"
            w = _mk()
            w.save(p1)
            mp.World.load(p1).save(p2)
            self.assertEqual(p1.read_bytes(), p2.read_bytes())

    def test_resume_mid_game_equals_straight_run(self):
        """复现性底线：跑 4 回合 → 存 → 读 → 续 2 回合，与一口气跑 6 回合完全一致
        （引擎 rng 随档恢复；动作流 rng 由循环自己持有、天然连续）。"""
        import random
        import expand_rule_v9

        def turn(w, rng):
            w.begin_turn()
            for n in w.alive():
                expand_rule_v9.expand_rule_turn_v9(w, n, rng, max_actions=12)
            w.resolve_turn()

        rng_a = random.Random(7)
        straight = mp.World(size=16, seed=11, nations=["秦", "楚"])
        for _ in range(6):
            turn(straight, rng_a)

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "mid.json"
            rng_b = random.Random(7)
            resumed = mp.World(size=16, seed=11, nations=["秦", "楚"])
            for _ in range(4):
                turn(resumed, rng_b)
            resumed.save(p)
            resumed = mp.World.load(p)
            for _ in range(2):
                turn(resumed, rng_b)
            self.assertEqual(_dump(straight), _dump(resumed))


def _dump(w) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.json"
        w.save(p)
        return p.read_text(encoding="utf-8")


class TestPartialRes(unittest.TestCase):
    def test_partial_res_dict_gets_full_start_keys(self):
        w = mp.World(size=16, seed=5, nations=["秦", "楚"], res={"秦": {"黄金": 100}})
        self.assertEqual(w.res("秦", "黄金"), 100)
        self.assertEqual(w.res("秦", "木头"), mp.START_RES["木头"])   # 缺的键补齐
        w.begin_turn()
        w.resolve_turn()                                              # 旧 bug：首结算 KeyError
