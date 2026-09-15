# -*- coding: utf-8 -*-
"""`rl/ruleai_bridge.py`：规则 AI 的**模块级旋钮**与编组状态。

## 为什么需要这个桥（2026-09-15 的事故）

规则 AI 的策略常量住在**各自的模块**里，而"住在哪"随重构会变：
v10 是单文件，v11/v12 是包（`fn.__module__` 是 `<代>.entry`，值却住在 `<代>.economy`）。
RL 侧原先写 `sys.modules[fn.__module__].HORIZON = n` ⇒ **对 v11/v12 是空操作**，
它们全程按缺省 200 规划而 v10 真的被设上了（500 回合那轮口径差 2.6 倍，
一度被误读成"v11/v12 长局塌到 25%"）。

★当晚用户直接**把视野那个旋钮删了**（「v10 起，不设默认视野，恒等于回合数加 20」）
—— 视野现在读 `world.max_turns + 20`，没有接口可设错。**能被设错的旋钮，不如没有旋钮。**
所以本文件现在测的是**留下来的那个通用旋钮**（`MIL_SHARE` 这类）与编组清理。

★决定性判据不是"设完等于目标"（那在 v10 上也能过），而是
**"给入口模块赋值必须改不动权威副本"**（`test_old_style_write_is_invisible`）。
"""
from __future__ import annotations

import sys
import unittest

import rule_ai

from rl.ruleai_bridge import (clear_state, set_knob,
                              _owning_pkg, _knob_sites)

# ★旋钮名放**模块级**：类体里的推导式看不到类体局部名（
#   `HAS_KNOB = tuple(... KNOB)` 会 NameError，这是 Python 的作用域规矩）。
KNOB = "MIL_SHARE"


def _scope(v: str) -> str:
    """该代的扫描作用域：包版本用包名，单文件版本用模块名。"""
    _name, fn = rule_ai.resolve(v)
    pkg = _owning_pkg(fn)
    return pkg.__name__ if pkg is not None else fn.__module__


class RuleAIBridgeTest(unittest.TestCase):
    VERSIONS = tuple(rule_ai.versions())
    # ★"有没有某个旋钮"是**代的性质**，不是测试的假设
    HAS_KNOB = tuple(v for v in VERSIONS
                     if _knob_sites(_scope(v), KNOB))

    def setUp(self):
        self._saved = {}
        for v in self.VERSIONS:
            sites = _knob_sites(_scope(v), KNOB)
            if sites:
                self._saved[v] = sites[0][2].__dict__[KNOB]
        self.addCleanup(self._restore)

    def _restore(self):
        for v, val in self._saved.items():
            set_knob(v, KNOB, val)

    # ------------------------------------------------------------ 包/单文件分辨
    def test_single_file_version_has_no_owning_pkg(self):
        """单文件版**不该**被认成包 —— 认错了就会去读 `ruleai.<knob>`（不存在）。"""
        for v in self.VERSIONS:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            if pkg is not None:
                self.assertNotEqual(pkg.__name__, "ruleai",
                                    f"{v}: 爬到顶层 ruleai 了，它不是'这一代的包'")

    def test_sites_are_scoped_to_one_generation(self):
        """候选存储点不许跨代 —— 早先按顶层 `ruleai` 扫，v11 的候选里混进了 `ruleai.v10`。"""
        for v in self.VERSIONS:
            scope = _scope(v)
            for _d, n, _m in _knob_sites(scope, KNOB):
                with self.subTest(version=v, site=n):
                    self.assertTrue(n == scope or n.startswith(scope + "."),
                                    f"{v} 的存储点 {n} 不在 {scope} 之内（跨代串味）")

    def test_knob_authority_is_the_deepest_module(self):
        """包版本的权威副本在**经济层**，不是入口模块。

        钉住它：若哪天有人把 `MIL_SHARE` 赋给 `entry`，权威值不变、探针会量出
        一组"没生效"的数。`set_knob` 的回读就是防这个的。
        """
        for v in self.HAS_KNOB:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            if pkg is None:
                continue
            with self.subTest(version=v):
                self.assertEqual(_knob_sites(pkg.__name__, KNOB)[0][1],
                                 f"{pkg.__name__}.economy",
                                 f"{v} 的 {KNOB} 权威副本应当是经济层")

    # ------------------------------------------------------------ 设旋钮
    def test_set_knob_round_trips(self):
        for v in self.HAS_KNOB:
            old = _knob_sites(_scope(v), KNOB)[0][2].__dict__[KNOB]
            with self.subTest(version=v):
                self.assertEqual(set_knob(v, KNOB, 0.45), 0.45)
                self.assertEqual(
                    _knob_sites(_scope(v), KNOB)[0][2].__dict__[KNOB], 0.45)
                set_knob(v, KNOB, old)

    def test_set_knob_missing_raises_when_required(self):
        for v in self.VERSIONS:
            if v in self.HAS_KNOB:
                continue
            with self.subTest(version=v):
                with self.assertRaises(RuntimeError):
                    set_knob(v, KNOB, 0.3)
                self.assertIsNone(set_knob(v, KNOB, 0.3, required=False))

    def test_old_style_write_is_invisible(self):
        """★**决定性**：给入口模块赋值必须**改不动权威副本**。

        这正是当初那个 bug 的形状 —— `sys.modules[fn.__module__].MIL_SHARE = x`
        对包版本只是给 `entry` 造了个新变量。若哪天有人把 `set_knob` 拆了退回旧写法，
        这条会当场失败，而不是等到一组"没生效"的实验数出来才发现。
        """
        pkgs = []
        for v in self.HAS_KNOB:
            _name, fn = rule_ai.resolve(v)
            pkg = _owning_pkg(fn)
            if pkg is None:
                continue
            pkgs.append(v)
            with self.subTest(version=v):
                set_knob(v, KNOB, 0.3)
                entry = sys.modules[fn.__module__]
                entry.__dict__[KNOB] = 999          # ← 旧写法的效果
                auth = _knob_sites(pkg.__name__, KNOB)[0][2].__dict__[KNOB]
                self.assertEqual(auth, 0.3,
                                 f"{v}：给入口模块赋值竟然改动了权威副本")
        self.assertTrue(pkgs, "没有一个包版本，这条测试没意义（注册表变了？）")

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
