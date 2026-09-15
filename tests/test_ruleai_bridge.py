# -*- coding: utf-8 -*-
"""`rl/ruleai_bridge.py`：规则 AI 的视野（HORIZON）与编组状态。

## 为什么值得一组专门的测试（2026-09-15）

RL 侧四处（`rl/bc.py` ×2、`rl/compare.py`、两个探针）都写着

    sys.modules[fn.__module__].HORIZON = n

这行**对 v10 管用、对 v11/v12 是空操作**（v10 是单文件；v11/v12 是包，
`fn.__module__` 是 `<代>.entry`，而经济层读的是 `<代>.economy.HORIZON`）。
后果：**v11/v12 的视野全程是缺省 200**，而 v10 真的被设成了「回合 + 20」
⇒ 500 回合那一轮两边的规划窗口差 2.6 倍，一度被读成"v11/v12 长局塌到 25%"。

`ruleai/v11/__init__.py` 的模块说明**开头就写了这条警告**并提供 `set_horizon()`
—— 所以本组测试的**决定性判据**是：
★**"照旧写法给入口模块赋值"必须改不动权威视野**（`test_old_style_write_is_invisible`）。
只测"设完之后等于目标"是不够的 —— 旧写法在 v10 上也能过。
"""
from __future__ import annotations

import sys
import unittest

import rule_ai

from rl.ruleai_bridge import (clear_state, horizon_of, set_horizon,
                              _owning_pkg, _horizon_sites)


class RuleAIBridgeTest(unittest.TestCase):
    VERSIONS = tuple(rule_ai.versions())
    # ★"有没有视野旋钮"是**代的性质**，不是测试的假设：v4/v5/v6/ai 没有 HORIZON
    #   （v6 还是 `collect_episode` 的缺省老师）⇒ 对它们必须"无操作、不报错"。
    HAS_KNOB = tuple(v for v in VERSIONS if horizon_of(v) is not None)
    NO_KNOB = tuple(v for v in VERSIONS if horizon_of(v) is None)

    def setUp(self):
        # ★视野是**模块级全局量**，串味会污染别的测试（2026-09-14 那次
        #   "山地测试"莫名失败就是被上一个测试留下的 HORIZON 搞的）⇒ 全部存回。
        self._saved = {}
        for v in self.VERSIONS:
            try:
                self._saved[v] = horizon_of(v)
            except Exception:
                pass
        self.addCleanup(self._restore)

    def _restore(self):
        for v, h in self._saved.items():
            if h is not None:
                set_horizon(v, h)

    # ------------------------------------------------------------ 基本
    def test_knob_versions_are_covered(self):
        """量的是**这一代有没有旋钮**，不是"测试记得的几个版本"。"""
        for v in self.HAS_KNOB:
            with self.subTest(version=v):
                self.assertIsInstance(horizon_of(v), int)
        for must in (rule_ai.DEFAULT_RULE_AI, "v10", "v11", "v12"):
            with self.subTest(must_have_knob=must):
                self.assertIn(must, self.HAS_KNOB)

    def test_set_then_read_back(self):
        """有旋钮的代都能设上，且**回读**（权威副本）等于目标。"""
        for v in self.HAS_KNOB:
            for target in (90, 220, 520):
                with self.subTest(version=v, target=target):
                    self.assertEqual(set_horizon(v, target), target)
                    self.assertEqual(horizon_of(v), target)

    def test_no_knob_versions_are_noop_not_error(self):
        """★没有旋钮的代 = **无操作**，不能炸。

        `collect_episode` 的缺省老师是 v6（没有 HORIZON）—— 若这里 raise，
        `--teacher v6` 这条本来能跑的路会当场崩。
        """
        for v in self.NO_KNOB:
            with self.subTest(version=v):
                self.assertIsNone(set_horizon(v, 520))
                self.assertIsNone(horizon_of(v))

    # ------------------------------------------------------------ ★决定性
    def test_old_style_write_is_invisible(self):
        """**旧写法必须被识破** —— 这是本文件存在的理由。

        `sys.modules[fn.__module__].HORIZON = n` 对包版本只是给 `entry` 造了个
        新变量；权威副本（经济层的）一个字没动。若哪天有人把桥拆了退回旧写法，
        这条测试会当场失败，而不是等到 500 回合的数出来才发现。
        """
        pkg_versions = []
        for v in self.VERSIONS:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            if pkg is None:
                continue                       # 单文件版（v10 一类）不适用
            pkg_versions.append(v)
            with self.subTest(version=v):
                set_horizon(v, 220)
                entry = sys.modules[fn.__module__]
                entry.HORIZON = 999            # ← 旧写法的效果
                self.assertEqual(
                    horizon_of(v), 220,
                    f"{v}：给入口模块赋值竟然改动了权威视野 —— 桥的判据失效了")
        self.assertTrue(pkg_versions, "没有一个包版本，这条测试没意义（注册表变了？）")

    def test_single_file_version_has_no_owning_pkg(self):
        """单文件版**不该**被认成包 —— 认错了就会去读 `ruleai.HORIZON`（不存在）。"""
        for v in self.VERSIONS:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            if pkg is not None:
                self.assertNotEqual(pkg.__name__, "ruleai",
                                    f"{v}: 爬到顶层 ruleai 了，它不是'这一代的包'")

    def test_sites_are_scoped_to_one_generation(self):
        """候选存储点不许跨代 —— 早先按顶层 `ruleai` 扫，v11 的候选里混进了 `ruleai.v10`。"""
        for v in self.VERSIONS:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            scope = pkg.__name__ if pkg is not None else fn.__module__
            names = [n for _d, n, _m in _horizon_sites(scope)]
            for n in names:
                with self.subTest(version=v, site=n):
                    self.assertTrue(n == scope or n.startswith(scope + "."),
                                    f"{v} 的存储点 {n} 不在 {scope} 之内（跨代串味）")

    # ------------------------------------------------------------ 编组状态
    def test_clear_state_reports_whether_it_did_something(self):
        """有模块内存的代返回 True，没有的返回 False（v10 无状态，正常）。"""
        for v in self.VERSIONS:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            has_state = pkg is not None and hasattr(pkg, "grouping")
            with self.subTest(version=v):
                self.assertEqual(clear_state(v), has_state)

    def test_clear_state_actually_empties_the_grouping(self):
        """★真清空，不只是"调过了" —— 编组泄漏是 2026-09-15 找到的第二个共犯。"""
        import ruleai.v11.grouping as g
        self.addCleanup(g.clear)
        g._STATE.setdefault("秦", {})[1] = (3, 4)
        self.assertTrue(clear_state("v11"))
        self.assertEqual(g.targets_of("秦"), {})


if __name__ == "__main__":
    unittest.main()
