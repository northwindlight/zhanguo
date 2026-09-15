# -*- coding: utf-8 -*-
"""规则 AI 注册表守卫：**版本由配置选，代码里不许出现版本号**。

病根：`dummy_turn` 曾经写死 `from expand_rule_v9 import expand_rule_turn_v9` ——
"哪一版当基线"长进了代码结构里，换基线要改引擎 + 改文档 + 改测试三处。
现在版本名只出现在两处：配置里的字符串、`rule_ai._SOURCES` 的表。本测试钉住这条：

  1. 注册表自洽：缺省版本在表里、`None`/空串落到缺省、未知版本**当场报错**（不静默退回）；
  2. 每一版都能真跑起来（同一签名：3 回合冒烟，各自出一串动作）——
     注册表写了却 `import` 不到 / 函数名抄错，这里立刻红；
  3. **引擎侧源码里没有版本号**（`mp_ai.py` / `mp_run.py` 不许出现 `expand_rule_v`）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import rule_ai  # noqa: E402

# 引擎侧（实现这场游戏的那几个文件）不许出现版本号
ENGINE_SRC = ("mp_ai.py", "mp_run.py", "mp.py", "game.py", "settlement.py", "spend_rules.py")


class TestRegistry(unittest.TestCase):
    def test_default_is_registered(self):
        self.assertIn(rule_ai.DEFAULT_RULE_AI, rule_ai.versions(),
                      "缺省版本不在注册表里——换基线时漏改 _SOURCES")

    def test_resolve_default(self):
        for given in (None, "", "  "):
            with self.subTest(given=given):
                ver, fn = rule_ai.resolve(given)
                self.assertEqual(ver, rule_ai.DEFAULT_RULE_AI)
                self.assertTrue(callable(fn))

    def test_resolve_named(self):
        for ver in rule_ai.versions():
            with self.subTest(ver=ver):
                got, fn = rule_ai.resolve(ver)
                self.assertEqual(got, ver)
                self.assertTrue(callable(fn), f"{ver} 解析出来的不是可调用对象")

    def test_case_insensitive(self):
        self.assertEqual(rule_ai.resolve("V10")[0], "v10")

    def test_unknown_version_raises(self):
        """配置写错了要**当场报错**：静默退回缺省 = 实验记录里的"v9"其实是 v10。"""
        with self.assertRaises(ValueError) as cm:
            rule_ai.resolve("v99")
        msg = str(cm.exception)
        for ver in rule_ai.versions():
            self.assertIn(ver, msg, f"报错信息里没列出可用版本 {ver}")


class TestEveryVersionRuns(unittest.TestCase):
    """每一版都真跑 3 回合（同签名），注册表与实际文件不许对不上。"""

    TURNS = 3

    def _drive(self, fn) -> int:
        import random
        w = mp.World(size=10, seed=3, nations=["秦"])
        rng = random.Random(0)
        n = 0
        for _ in range(self.TURNS):
            w.begin_turn()
            n += len(fn(w, "秦", rng, max_actions=8))
            w.resolve_turn()
        return n

    def test_all_versions_produce_actions(self):
        for ver in rule_ai.versions():
            with self.subTest(ver=ver):
                _, fn = rule_ai.resolve(ver)
                self.assertGreater(self._drive(fn), 0,
                                   f"{ver} 三回合一个动作都没出（签名/实现对不上？）")

    def test_versions_differ(self):
        """不同版本不是同一个函数（注册表抄错行的典型症状：两行指向同一模块）。"""
        seen = {}
        for ver in rule_ai.versions():
            _, fn = rule_ai.resolve(ver)
            key = (fn.__module__, fn.__name__)
            self.assertNotIn(key, seen, f"{ver} 与 {seen.get(key)} 指向同一个函数")
            seen[key] = ver


class TestNoHardcodedVersion(unittest.TestCase):
    """引擎侧源码里不许出现版本号——换基线只改配置。"""

    # 「钉死某一版」才算硬编码：`expand_rule_v*`（通配提法）在注释里合法
    PINNED = re.compile(r"expand_rule_(turn_)?v\d")

    def test_engine_has_no_version_literal(self):
        for fname in ENGINE_SRC:
            src = (ROOT / fname).read_text(encoding="utf-8")
            hit = self.PINNED.search(src)
            self.assertIsNone(hit, f"{fname} 钉死了某个版本的规则 AI（{hit.group() if hit else ''}）——"
                                   f"版本该走 rule_ai.resolve（配置项 rule_ai）")

    def test_dummy_turn_takes_version(self):
        """dummy_turn 必须收 `rule_ai` 形参（否则配置传不进去）。"""
        import inspect
        import mp_ai
        params = inspect.signature(mp_ai.dummy_turn).parameters
        self.assertIn("rule_ai", params, "dummy_turn 没有 rule_ai 形参，配置传不到")

    def test_run_reads_config_key(self):
        """mp_run 要真把配置项读出来（顶层 + 逐国覆盖各一处），并在**开局前**验一遍。"""
        src = (ROOT / "mp_run.py").read_text(encoding="utf-8")
        self.assertIn('cfg.get("rule_ai"', src, "mp_run 没读顶层 rule_ai")
        self.assertIn('ncfg.get("rule_ai"', src, "mp_run 没读逐国覆盖的 rule_ai")
        self.assertIn("rule_ai_registry.resolve(rule_ai)", src,
                      "mp_run 没在开局前验配置——版本写错要等跑到无 key 国家才炸")


class TestActionBudgetUnlimited(unittest.TestCase):
    """★看海口径的**动作上限已删**（用户 2026-09-15：「给我全删了」）。

    原来引擎一路把缺省给到 **12**（`mp_ai.dummy_turn` 的形参缺省 + `mp_run` 的
    `ncfg.get("max_actions", 12)`），规则 AI 每回合发到 12 个就被截断。
    实测（`experiments/probe_teacher_actions.py`，4 图 × 200 回合）：
    **v10 有 8.6% 的回合被截顶**，v11 0.9%、v12 0.6%。

    ★本组测的是**机制**而不是"某次跑出来的观测"：观测会因地图/种子而变，
      机制不会。判据 = **引擎往下传的那个数**（在截断发生的那一层量）。
    """

    def test_signature_default_is_unlimited(self):
        import inspect
        import mp_ai
        d = inspect.signature(mp_ai.dummy_turn).parameters["max_actions"].default
        self.assertEqual(d, rule_ai.UNLIMITED_ACTIONS,
                         "dummy_turn 的动作额度缺省又被卡住了——看海会重新截断在 12")

    def test_dummy_turn_hands_unlimited_down(self):
        """★决定性：`dummy_turn` 往规则 AI 传下去的那个数必须是"无上限"。

        截断发生在**规则 AI 内部**（各代入口的 `if len(acts) >= max_actions: break`），
        所以只能在这一层量 —— 拿一个探针函数替掉真的规则 AI，看它收到什么。
        """
        import random
        import mp_ai
        got = {}

        def spy(world, name, rng=None, max_actions=None, **_kw):
            got["max_actions"] = max_actions
            return []

        orig = rule_ai.resolve
        rule_ai.resolve = lambda *a, **k: ("v10", spy)
        try:
            w = mp.World(size=8, seed=1, nations=["秦"])
            mp_ai.dummy_turn(w, "秦", random.Random(0))
        finally:
            rule_ai.resolve = orig
        self.assertEqual(
            got.get("max_actions"), rule_ai.UNLIMITED_ACTIONS,
            f"dummy_turn 把 {got.get('max_actions')} 传给了规则 AI（应当无上限）")

    def test_run_fallback_is_unlimited(self):
        """`mp_run` 的兜底也不许再写死 12（配置里显式写 `max_actions` 仍然有效）。"""
        src = (ROOT / "mp_run.py").read_text(encoding="utf-8")
        m = re.search(r'max_actions=ncfg\.get\(\s*"max_actions"\s*,\s*([^)]+?)\s*\)', src)
        self.assertIsNotNone(m, "mp_run 里找不到 max_actions 的兜底写法（改结构了？）")
        self.assertIn("UNLIMITED_ACTIONS", m.group(1),
                      f"mp_run 的 max_actions 兜底是 {m.group(1)} —— 又卡上限了")