# -*- coding: utf-8 -*-
"""终端体验层测试：Markdown→ANSI 渲染、汉字宽度、命令台行编辑（不碰真实终端）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import console  # noqa: E402
from console import Console, dw, pad, render_md  # noqa: E402


class TestWidth(unittest.TestCase):
    def test_cjk_counts_two(self):
        self.assertEqual(dw("abc"), 3)
        self.assertEqual(dw("秦楚"), 4)
        self.assertEqual(dw("秦a"), 3)
        self.assertEqual(dw("，"), 2)          # 全角标点

    def test_pad_aligns(self):
        self.assertEqual(dw(pad("秦", 5)), 5)
        self.assertEqual(dw(pad("秦", 5, "right")), 5)
        self.assertTrue(pad("秦", 5, "right").startswith(" "))


class TestRenderMd(unittest.TestCase):
    def test_bold_and_code(self):
        self.assertEqual(render_md("**粗**"), "\033[1m粗\033[0m")
        self.assertEqual(render_md("`x`"), "\033[36mx\033[0m")

    def test_heading_list_quote_rule(self):
        self.assertNotIn("#", render_md("## 战报"))
        self.assertIn("\033[1m战报\033[0m", render_md("## 战报"))
        self.assertEqual(render_md("- 第一条"), "· 第一条")
        self.assertEqual(render_md("1. 第一条"), "· 第一条")
        self.assertTrue(render_md("> 引文").endswith("引文\033[0m"))
        self.assertIn("─", render_md("---"))

    def test_unclosed_markers_kept(self):
        """未闭合的 ** 不吞字、原样保留。"""
        self.assertEqual(render_md("**没闭合"), "**没闭合")

    def test_disabled_returns_raw(self):
        raw = "**粗** - 列表"
        self.assertEqual(render_md(raw, enabled=False), raw)

    def test_plain_text_untouched(self):
        s = "楚王：①定界已允，②装备之账须正一句。"
        self.assertEqual(render_md(s), s)


class TestLineEditor(unittest.TestCase):
    """直接喂按键给 _feed（_active=False 时不画终端）。"""

    def _c(self, **kw):
        return Console(**kw)

    def _type(self, c, s):
        """逐字符喂（真实终端就是一次一个字符，转义序列也不例外）。"""
        for ch in s:
            c._feed(ch)

    def test_cjk_backspace_deletes_whole_char(self):
        c = self._c()
        self._type(c, "秦楚")
        self.assertEqual(c._buf, ["秦", "楚"])
        c._feed("\x7f")                        # 退格
        self.assertEqual(c._buf, ["秦"])       # 整个汉字没了（不是半格）

    def test_arrows_home_end_delete(self):
        c = self._c()
        self._type(c, "abc")
        self._type(c, "\x1b[D")                      # ←
        c._feed("X")
        self.assertEqual("".join(c._buf), "abXc")
        self._type(c, "\x1b[H")                      # Home
        c._feed("Z")
        self.assertEqual("".join(c._buf), "ZabXc")
        self._type(c, "\x1b[F")                      # End
        self._type(c, "\x1b[3~")                     # Delete（行尾无内容，不变）
        self.assertEqual("".join(c._buf), "ZabXc")
        self._type(c, "\x1b[D")
        self._type(c, "\x1b[3~")                     # Delete 掉一个字符
        self.assertEqual("".join(c._buf), "ZabX")

    def test_enter_submits_to_queue(self):
        c = self._c()
        self._type(c, "add 匈奴")
        c._feed("\r")
        self.assertEqual(c.queue.get_nowait(), "add 匈奴")
        self.assertEqual(c._buf, [])

    def test_blank_line_ignored(self):
        c = self._c()
        c._feed("\r")
        self.assertTrue(c.queue.empty())

    def test_history_up_down(self):
        c = self._c()
        self._type(c, "cheat 秦 金100")
        c._feed("\r")
        self._type(c, "add 匈奴")
        c._feed("\r")
        self._type(c, "\x1b[A")                      # ↑ 上一条
        self.assertEqual("".join(c._buf), "add 匈奴")
        self._type(c, "\x1b[A")
        self.assertEqual("".join(c._buf), "cheat 秦 金100")
        self._type(c, "\x1b[B")                      # ↓ 回到较新
        self.assertEqual("".join(c._buf), "add 匈奴")

    def test_multiline_send_until_end(self):
        c = self._c(is_multiline_start=lambda s: s.startswith("send ") and len(s.split()) == 2)
        self._type(c, "send 秦")
        c._feed("\r")
        self.assertTrue(c._ml)                 # 进入多行模式
        self._type(c, "第一行")
        c._feed("\r")
        self._type(c, "第二行")
        c._feed("\r")
        self._type(c, "END")
        c._feed("\r")
        self.assertFalse(c._ml)
        self.assertEqual(c.queue.get_nowait(), "send 秦\n第一行\n第二行")

    def test_ctrl_u_clears_line(self):
        c = self._c()
        self._type(c, "abc")
        c._feed("\x15")
        self.assertEqual(c._buf, [])


if __name__ == "__main__":
    unittest.main()
