# -*- coding: utf-8 -*-
"""★★ **命令行入口必须能自报家门**（`--help` 不许崩）。

为什么值得一条守卫：`argparse` 对**每一条 help 串**都做 `help % params` 格式化，
所以 help 里出现**裸的 `%`** 就会当场抛
`ValueError: unsupported format character ...`。

★ 而这个坑的形态正是「**工具骗人**」那一类：
  - 报错是**发生在我从不会去调的地方**（炉子从来不传 `--help`）⇒ 一直没人发现；
  - 我是在帮用户查参数时才撞上的（`--help` 直接 traceback）。
  ⇒ 代价不高但很蠢：**写了个参数却没法查它**。

★ 实测踩过的那一处：`--league-retire-rate` 的 help 里写「3 国 33%、**5 国 20%**」——
  那个 `%` 后面跟着中文顿号，argparse 把它当格式符解 ⇒ 崩。
  修法是写 `%%`。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class TestCliHelp(unittest.TestCase):
    def _help(self, mod: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", mod, "--help"],
                              cwd=ROOT, capture_output=True, text=True, timeout=300)

    def test_train_help_works(self):
        r = self._help("rl.train")
        self.assertEqual(r.returncode, 0,
                         f"`python -m rl.train --help` 崩了 —— help 串里多半有裸的 `%`\n"
                         f"{r.stderr[-800:]}")
        for key in ("--max-steps", "--minibatch", "--epochs", "--league-db",
                    "--league-snapshot-every", "--league-cache"):
            self.assertIn(key, r.stdout, f"`{key}` 没出现在 help 里")

    def test_eval_help_works(self):
        r = self._help("rl.eval_fixed")
        self.assertEqual(r.returncode, 0,
                         f"`python -m rl.eval_fixed --help` 崩了\n{r.stderr[-800:]}")


if __name__ == "__main__":
    unittest.main()

class TestEveryFlagReachesTrain(unittest.TestCase):
    """★★ **CLI 里出现的每个选项都必须真的传进 `train()`** —— 用户 2026-09-25。

    ★ 这条是我自己踩出来的：`--memory latent` 加了 argparse 选项、也在 `train()` 里
      实现了，**但忘了在 CLI 那行 `train(...)` 里转发** ⇒ 跑起来日志照打
      「记忆：**关**」、参数全按缺省走，**一声不响**。那正是用户最忌的
      "工具骗人"形状（工具在"本该生效"处静默返回成功）。

    ★ 做法：把 `main()` 的源码里 `train(...)` 那段实参名抓出来，和
      `train()` 的**关键字形参名**比对 ⇒ "加了选项却没接线"当场红。
    """

    def test_all_keyword_params_are_passed(self):
        import ast
        import inspect
        sys.path.insert(0, str(ROOT))
        from rl import train as TR
        src = (ROOT / "rl" / "train.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        call = None
        for node in ast.walk(tree):                 # 找 `main()` 里那个 train(...) 调用
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "train":
                call = node
        self.assertIsNotNone(call, "没找到 CLI 里的 `train(...)` 调用")
        passed = {kw.arg for kw in call.keywords if kw.arg}
        params = set(inspect.signature(TR.train).parameters)
        miss = params - passed - {"log"}            # `log` 由调用方注入，允许不传
        self.assertEqual(miss, set(),
                         f"★ 这些 `train()` 参数**没被 CLI 转发**：{sorted(miss)}\n"
                         "  ⇒ 加了命令行选项却不生效，而且**不报错**")