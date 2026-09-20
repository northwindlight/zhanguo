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


class TestPanel(unittest.TestCase):
    """底部固定状态区（用户 2026-09-20「这些弄个固定位置」）：内容、折行、擦除行数。

    不碰真终端：把 stdout 换成 StringIO、手工把 `_active` 置真（真终端里由输入线程置）。
    """

    def setUp(self):
        import io
        self.buf = io.StringIO()
        self._old = sys.stdout
        sys.stdout = self.buf
        self.addCleanup(lambda: setattr(sys, "stdout", self._old))

    def test_head_and_notes_in_box(self):
        c = Console(panel_rows=3, panel_notes=2)
        c._active = True
        c.set_panel(head="🧠 秦 上下文: 窗口262.1k·预算194.6k", note="⚠ 第1次调用失败")
        out = self.buf.getvalue()
        self.assertIn("+---", out)
        self.assertIn("🧠 秦 上下文", out)
        self.assertIn("⚠ 第1次调用失败", out)
        self.assertEqual(c._footer_n, len(c._footer_lines()), "画了几行要记准（擦除按它走）")

    def test_only_recent_notes_kept(self):
        c = Console(panel_rows=3, panel_notes=2)
        c._active = True
        for i in (1, 2, 3):
            c.set_panel(note=f"通知{i}")
        self.assertEqual(c._notes, ["通知2", "通知3"], "状态区只留最近几条")

    def test_clear_panel_removes_box(self):
        c = Console(panel_rows=3, panel_notes=2)
        c._active = True
        c.set_panel(head="🧠 秦 上下文: …")
        self.assertTrue(c._footer_lines())
        c.clear_panel()
        self.assertEqual(c._footer_lines(), [], "清空后盒子不该再占屏")
        self.assertEqual(c._footer_n, 0)

    def test_box_frame_is_ascii_and_even(self):
        """★ 方框必须**各行等宽、边框只用 ASCII**（用户 2026-09-20：「汉字两个格子宽」）。

        制表符 `│─╭╮` 的东亚宽度是 **A（Ambiguous）**——`dw()` 只把 W/F 算两格，
        在"ambiguous 也算两格"的终端里它们真占两格 ⇒ 方框当场错位/折行。
        `+ - |` 是 Na（Narrow），哪里都是一格；汉字内容两格由 `dw()` 计入。
        """
        c = Console(panel_rows=3, panel_notes=2)
        c.set_panel(head="🧠 秦 上下文: 窗口262.1k·预算194.6k（74%）",
                    note="⚠ 秦 第1次调用失败(APITimeoutError)，2s 后重试")
        lines = c._footer_lines()
        widths = {dw(l) for l in lines}
        self.assertEqual(len(widths), 1, f"方框各行不等宽：{[dw(l) for l in lines]}")
        self.assertEqual(lines[0], lines[-1], "上下边框该一样长")
        self.assertTrue(all(ch in "+-" for ch in lines[0]), f"上边框不是纯 ASCII：{lines[0]}")
        for ln in lines[1:-1]:
            self.assertEqual((ln[0], ln[-1]), ("|", "|"), f"侧边框不是纯 ASCII：{ln}")

    def test_long_head_wraps_not_truncated(self):
        """★ 上下文计划那种长行**折行不截断**（用户 2026-09-20：「不截断」）。"""
        c = Console(panel_rows=3, panel_notes=2)
        head = "🧠 秦 上下文: " + "·".join(f"第{i}段数据{i * 7}k" for i in range(12))
        c.set_panel(head=head)
        rows = c._panel_content()
        self.assertGreater(len(rows), 1, "这么长的行该折成多行")
        self.assertEqual("".join(rows).rstrip(), head, "折行不许丢字")

    def test_write_shifts_footer_not_breaks_it(self):
        """写日志＝先擦页脚、写日志、再画回来；`_footer_n` 始终等于真画的行数。"""
        c = Console(panel_rows=3, panel_notes=2)
        c._active = True
        c.set_panel(head="🧠 秦 上下文: …", note="⚠ 重试")
        n = c._footer_n
        c.write("一条日志")
        self.assertEqual(c._footer_n, n, "写日志后方框的行数应保持不变")
        self.assertIn("一条日志", self.buf.getvalue())


if __name__ == "__main__":
    unittest.main()
