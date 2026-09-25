# -*- coding: utf-8 -*-
"""训练日志里那条网络统计的**契约**：`ent` 必须配 `log K` 一起印。

★★ 为什么值得一条守卫（2026-09-25 实测撞的）：

    **均匀分布的熵 = `log K`**，而 `K`（候选数）**是变的**
    —— 实测 8×8 上 K 中位 **51**、范围 **4~128** ⇒ `log K` 在 **1.4~4.9** 之间。

    那天我在日志里看到 `L1: ent=+0.435`，差点报"策略塌缩"。**但我判不了**：
    若那一局 L1 扮的国家只剩一两支军，`K` 就只有 4~6 ⇒ `log K ≈ 1.4~1.8`，
    那 `ent=0.435` 只是"偏窄"。而**当时日志里根本没有 `log K`**。

    ⇒ 判据只能是 **`ent / log K`**（1.0 = 纯均匀、接近 0 = 塌缩）。
      ⇒ 日志必须把它印出来，否则**这个数没法读**，而"没法读的数会被误读**。

★ 钉三件事：
  ① 有 `ent` 时必须同时有 `logK` 与 `e/K`；
  ② `e/K` 的值要**算对**（拿已知的 ent 与 logK 对一遍）；
  ③ `logK` 缺失（`nan`）时**不许崩**（那时 `e/K` 标成 `nan`，别印一个假的数）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rl.train import _fmt                              # noqa: E402

NAN = float("nan")


class TestLogLineCarriesLogK(unittest.TestCase):
    def test_ent_comes_with_logk_and_ratio(self):
        s = _fmt({"pg": 0.1, "vf": 0.002, "ent": 0.435}, 3.93)
        self.assertIn("ent=", s)
        self.assertIn("logK=", s, f"有 ent 却没有 logK ⇒ 这个数没法读：{s}")
        self.assertIn("e/K=", s, f"没有 e/K ⇒ 判据不在一行里：{s}")

    def test_ratio_is_computed_right(self):
        """★ `e/K` 必须**真是** `ent / log K`（不是印了个看着像的数）。"""
        s = _fmt({"ent": 1.5}, 3.0)
        self.assertIn("e/K=0.50", s, f"e/K 算错了：{s}")
        s2 = _fmt({"ent": 0.435}, 3.93)
        self.assertIn("e/K=0.11", s2, f"e/K 算错了：{s2}")

    def test_missing_logk_does_not_crash_or_lie(self):
        """★ 没有 `logK` 时**不许崩、也不许印一个假的比值**。"""
        s = _fmt({"ent": 0.435}, NAN)
        self.assertIn("e/K=nan", s, f"logK 缺失时印了假比值：{s}")
        self.assertIn("ent=", s)

    def test_no_ent_no_noise(self):
        """★ 反向对照：没有 `ent` 时不该硬塞一个 `logK`。"""
        s = _fmt({"pg": 0.1, "vf": 0.0}, 3.9)
        self.assertNotIn("logK", s)
        self.assertIn("pg=", s)

    def test_empty_is_a_dash(self):
        self.assertEqual(_fmt({}), "—")


if __name__ == "__main__":
    unittest.main()