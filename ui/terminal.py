# -*- coding: utf-8 -*-
"""终端体验层：Markdown→ANSI 渲染 + 汉字宽度感知的命令台。

- `render_md()`：把 AI 写的 markdown 转成终端可读的 ANSI（**粗体**、`代码`、# 标题、- 列表…）。
  只在**终端**用；写进 mp_journal.md 的仍是原始 markdown（那是 .md 文件，本来就该是原文）。
- `Console`：带提示符的命令台。日志随时可能刷屏，所以输出前先清掉当前输入行、输出后重画
  ——光标位置与汉字宽度都自己算（**汉字占 2 格，退格删整个字符而不是一格**）。
  POSIX 用 termios 的 cbreak（保留 OPOST，\n 仍能正常换行、Ctrl-C 仍走 SIGINT），
  Windows 用 msvcrt；不是 tty（管道/重定向）时自动降级为普通 readline，不画提示符。

依赖只有标准库。
"""
from __future__ import annotations

import codecs
import os
import queue
import sys
import threading
import unicodedata

# ───────────────────────── 宽度（汉字占 2 格） ─────────────────────────
def dw(s: str) -> int:
    """终端显示宽度：CJK/全角字符按 2 计。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _csi_final(ch: str) -> bool:
    """CSI 序列的终止字节（@~ 区间，不是只有 '@' 和 '~' 两个字面量）。"""
    return "@" <= ch <= "~"


def pad(s: str, width: int, align: str = "left") -> str:
    s = str(s)
    gap = max(0, width - dw(s))
    return " " * gap + s if align == "right" else s + " " * gap


def _wrap(s: str, width: int) -> list[str]:
    """按**显示宽度**折行（汉字 2 格）。**不截断**——状态区宁可多占一行：
    上下文计划那种行截掉尾巴就只剩半句（用户 2026-09-20 口径：不截断）。"""
    rows: list[str] = []
    cur, w = "", 0
    for ch in str(s):
        cw = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if cur and w + cw > width:
            rows.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    rows.append(cur)
    return rows


def _clip(s: str, width: int) -> str:
    """按显示宽度截断（超出部分丢弃）——只在状态区**封顶**时才用（见 `_panel_content`）。"""
    out, w = "", 0
    for ch in str(s):
        cw = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
        if w + cw > width:
            break
        out += ch
        w += cw
    return out


# ───────────────────────── Markdown → ANSI ─────────────────────────
_B, _D, _C, _R = "\033[1m", "\033[2m", "\033[36m", "\033[0m"


def render_md(text: str, enabled: bool = True) -> str:
    """把 markdown 转成终端可读样式。enabled=False（非 tty/管道）时原样返回。

    只认 AI 真正常用的几种：**粗体**、`行内代码`、# 标题、- / 1. 列表、> 引用、--- 分隔线。
    斜体不处理（`*` 在中文正文里太容易误伤）。
    """
    if not enabled or not text:
        return text
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]
        if stripped.startswith("#"):                       # # 标题 → 粗体
            out.append(f"{indent}{_B}{stripped.lstrip('#').strip()}{_R}")
            continue
        if stripped[:4] in ("--- ", "*** ") or stripped in ("---", "***", "___"):
            out.append(f"{indent}{_D}{'─' * 40}{_R}")      # 分隔线
            continue
        if stripped.startswith("> "):                      # 引用 → 暗淡
            out.append(f"{indent}{_D}▏{stripped[2:]}{_R}")
            continue
        body = stripped
        # 列表：- / * / + 或 1. / 1)
        for mark in ("- ", "* ", "+ "):
            if body.startswith(mark):
                body = "· " + body[2:]
                break
        else:
            i = 0
            while i < len(body) and body[i].isdigit():
                i += 1
            if 0 < i < len(body) - 1 and body[i] in ".)" and body[i + 1] == " ":
                body = "· " + body[i + 2:]
        out.append(indent + _inline(body))
    return "\n".join(out)


def _inline(s: str) -> str:
    """行内样式：**粗体** 与 `代码`。未闭合的标记原样保留，不吞字。"""
    out, i = [], 0
    while i < len(s):
        if s.startswith("**", i):
            j = s.find("**", i + 2)
            if j > 0:
                out.append(_B + s[i + 2:j] + _R)
                i = j + 2
                continue
        if s[i] == "`":
            j = s.find("`", i + 1)
            if j > 0:
                out.append(_C + s[i + 1:j] + _R)
                i = j + 1
                continue
        out.append(s[i])
        i += 1
    return "".join(out)


# ───────────────────────── 命令台 ─────────────────────────
class Console:
    """输入行与日志共用终端：输出前清行、输出后重画提示符。

    用法：
        c = Console(on_line=..., is_multiline_start=lambda s: s.startswith("send ") and len(s.split())==2)
        c.start()
        c.write("日志一行")        # 线程安全，自动让开输入行
        c.set_panel(head="🧠 秦 上下文: …", note="⚠ 第1次调用失败")
        c.clear_panel()
        c.close()                  # 恢复终端
    提交的命令进 `c.queue`（队列），由主循环在回合边界取。

    **底部固定状态区**（`set_panel`，用户 2026-09-20「这些弄个固定位置」）：
    终端最底几行钉着一个方框——第 1 行常驻（上下文计划，按宽度折行不截断），
    其后是最近的通知（⚠ 重试 / 🧠 下滑·压缩 / 🛑 故障）。日志照旧往上滚，
    写日志前先把这个页脚擦掉、写完再画回来（`_erase_footer`/`_draw_footer`），
    所以方框看着是钉住的。**只画在真终端上**（`panel_on`），非 tty 时调用方
    得把行退回日志流，否则这些行就彻底看不见了。
    """

    def __init__(self, prompt: str = "❯ ", on_interrupt=None,
                 is_multiline_start=None, history_size: int = 100,
                 panel_rows: int = 3, panel_notes: int = 2):
        self.prompt = prompt
        self.queue: queue.Queue[str] = queue.Queue()
        self._on_interrupt = on_interrupt
        self._is_ml_start = is_multiline_start
        self._buf: list[str] = []
        self._pos = 0
        self._esc = ""                 # 转义序列累积
        self._ml = False               # 多行模式（send 正文）
        self._ml_buf: list[str] = []
        self._history: list[str] = []
        self._hist_idx = 0
        self._hist_size = history_size
        self._lock = threading.RLock()
        self._active = False           # 是否已画出提示符
        self._closed = False
        self._tty = sys.stdin.isatty() and sys.stdout.isatty()
        self._md = sys.stdout.isatty()
        self._old_term = None
        self._thread: threading.Thread | None = None
        # 固定状态区：`_head` 常驻行，`_notes` 最近通知（FIFO，只留 panel_notes 条）；
        # `_footer_n` = **屏幕上正画着几行页脚**（不含输入行）——擦除必须按这个数走，
        # 否则折行/清空之后会擦错行（把日志一起吃掉）。
        self._head = ""
        self._notes: list[str] = []
        self._panel_rows = panel_rows
        self._panel_notes = panel_notes
        self._panel_max = panel_rows + 2      # 折行最多允许多占 2 行，再多就截断
        self._footer_n = 0

    @property
    def panel_on(self) -> bool:
        """固定状态区是否**真的在画**（真终端 + 输入线程已接管）。
        不是它 ⇒ 调用方必须把行写回日志流（管道/重定向里也能看见）。"""
        return self._tty and self._active

    # ---- 固定状态区
    def set_panel(self, head: str | None = None, note: str | None = None) -> None:
        """更新底部固定状态区：`head` 换常驻行（如上下文计划），`note` 追加一条通知。"""
        with self._lock:
            if head is not None:
                self._head = head
            if note is not None:
                self._notes.append(note)
                del self._notes[:-self._panel_notes]
            if self._active:
                self._refresh_footer()

    def clear_panel(self) -> None:
        """清空固定状态区（回合之间/终局）：盒子连同占位一起消失。"""
        with self._lock:
            self._head, self._notes = "", []
            if self._active:
                self._refresh_footer()

    def _term_size(self) -> tuple[int, int]:
        import shutil
        try:
            sz = shutil.get_terminal_size((100, 24))
            return max(20, sz.columns - 1), max(4, sz.lines)
        except Exception:
            return 99, 24

    def _term_width(self) -> int:
        """画方框用的宽度。**减 1**：正好写满最后一格会触发终端的"待折行"状态。"""
        return self._term_size()[0]

    def _panel_content(self) -> list[str]:
        """状态区**内容**行：常驻行在前，其后是最近通知（不足则补空行，盒子高度稳）。"""
        avail = max(16, self._term_width() - 4)     # 「│ 」+「 │」各占 2 格
        rows: list[str] = _wrap(self._head, avail) if self._head else [""]
        for n in self._notes:
            rows += _wrap(n, avail)
        while len(rows) < 1 + self._panel_notes:
            rows.append("")
        # 封顶：按配置，且**不许超过屏幕高度的 1/3**（矮终端里 5 行页脚会把日志挤没）
        cap = min(self._panel_max, max(1 + self._panel_notes, self._term_size()[1] // 3))
        if len(rows) > cap:                         # 只在这时才截断
            rows = rows[:cap]
            rows[-1] = _clip(rows[-1], max(4, avail - 1)) + "…"
        return rows

    def _footer_lines(self) -> list[str]:
        """页脚要画的**全部行**（状态方框；不含光标所在的输入行）。内容空 ⇒ 不占屏。

        ★ 边框**用 ASCII**（`+ - |`），不用 `╭─╮│` 那套制表符：制表符的东亚宽度是
        **A（Ambiguous）**——在把 ambiguous 算两格的终端（中日韩环境的常见设置，
        `dw()` 只按 W/F 算两格）里它会变成两个格子宽，方框当场错位/折行。
        `+ - |` 是 Na（Narrow），哪里都是一格。汉字内容本身两格，已由 `dw()` 计入。
        """
        if not self._head and not self._notes:
            return []
        w = self._term_width()
        inner = max(1, w - 4)
        out = ["+" + "-" * (w - 2) + "+"]
        for r in self._panel_content():
            out.append("| " + pad(r, inner) + " |")
        out.append("+" + "-" * (w - 2) + "+")
        return out

    def _refresh_footer(self) -> None:
        """重画整个页脚（先擦旧的，再画新的）。调用方须持锁。"""
        sys.stdout.write(self._erase_footer())
        self._draw_footer()

    # ---- 输出（线程安全：先让开输入行与状态区，写完再画回来）
    def write(self, text: str, render: bool = True) -> None:
        if not text:
            return
        if render and self._md:
            text = render_md(text)
        with self._lock:
            if self._active:
                sys.stdout.write(self._erase_footer())
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
            if self._active:
                self._draw_footer()

    def start(self) -> "Console":
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._closed = True
        if self._old_term:
            import termios
            fd, old = self._old_term
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except Exception:
                pass
            self._old_term = None
        self._active = False
        if self._md:
            with self._lock:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()

    # ---- 输入
    def _reader(self) -> None:
        try:
            if not self._tty:
                raise RuntimeError("not a tty")
            if os.name == "nt":
                self._read_windows()
            else:
                self._read_posix()
        except Exception:
            self._read_plain()

    def _read_plain(self) -> None:
        """非 tty / 终端初始化失败：退回普通逐行读取（无提示符、无重画）。"""
        self._active = False
        while not self._closed:
            line = sys.stdin.readline()
            if not line:
                return
            self._handle_line(line.rstrip("\n"))    # ★ 与 tty 那支共用同一套（含多行 END）

    def _read_posix(self) -> None:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        self._old_term = (fd, old)
        tty.setcbreak(fd)              # 关 ICANON/ECHO，保留 ISIG（Ctrl-C 仍发 SIGINT）与 OPOST
        self._active = True
        self._redraw()
        dec = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while not self._closed:
                b = os.read(fd, 1)
                if not b:
                    break
                ch = dec.decode(b)
                if not ch:
                    continue
                if not self._feed(ch):
                    break
        finally:
            if self._old_term:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                self._old_term = None
            self._active = False

    def _read_windows(self) -> None:
        import msvcrt
        self._active = True
        self._redraw()
        while not self._closed:
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):        # 特殊键：前缀 + 扫描码
                code = msvcrt.getwch()
                ch = {"H": "\x1b[A", "P": "\x1b[B", "M": "\x1b[C", "K": "\x1b[D"}.get(code, "")
                if not ch:
                    continue
            if not self._feed(ch):
                break
        self._active = False

    # ---- 按键处理
    def _feed(self, ch: str) -> bool:
        """返回 False = 结束读取线程。"""
        if self._esc or ch == "\x1b":
            return self._feed_esc(ch)
        if ch in ("\r", "\n"):
            self._accept()
        elif ch == "\x7f" or ch == "\b":
            self._backspace()
        elif ch == "\x03":                      # Ctrl-C（Windows 分支会走到这）
            self._interrupt()
            return False
        elif ch == "\x04":                      # Ctrl-D
            return False
        elif ch == "\x01":                      # Ctrl-A
            self._pos = 0
            self._redraw()
        elif ch == "\x05":                      # Ctrl-E
            self._pos = len(self._buf)
            self._redraw()
        elif ch == "\x15":                      # Ctrl-U 清行
            self._buf, self._pos = [], 0
            self._redraw()
        elif ch == "\t":
            pass
        elif ch >= " ":                         # 可打印（含汉字）
            self._buf.insert(self._pos, ch)
            self._pos += 1
            self._redraw()
        return True

    def _feed_esc(self, ch: str) -> bool:
        """CSI 序列：\\x1b[ A/B/C/D/H/F、\\x1b[3~ 等。"""
        if ch == "\x1b":
            self._esc = "\x1b"
            return True
        self._esc += ch
        if len(self._esc) == 2 and ch not in "[O":
            self._esc = ""                      # 单字节转义（Alt-x），忽略
            return True
        if self._esc.startswith(("\x1b[", "\x1bO")) and (
                len(self._esc) < 3 or not _csi_final(self._esc[-1])):
            return True                         # 序列未结束
        seq = self._esc
        self._esc = ""
        if seq == "\x1b[A":
            self._history_move(-1)
        elif seq == "\x1b[B":
            self._history_move(1)
        elif seq == "\x1b[C":
            if self._pos < len(self._buf):
                self._pos += 1
                self._redraw()
        elif seq == "\x1b[D":
            if self._pos > 0:
                self._pos -= 1
                self._redraw()
        elif seq in ("\x1b[H", "\x1b[1~", "\x1bOH"):
            self._pos = 0
            self._redraw()
        elif seq in ("\x1b[F", "\x1b[4~", "\x1bOF"):
            self._pos = len(self._buf)
            self._redraw()
        elif seq == "\x1b[3~":                  # Delete
            if self._pos < len(self._buf):
                del self._buf[self._pos]
                self._redraw()
        return True

    def _backspace(self) -> None:
        if self._pos > 0:
            del self._buf[self._pos - 1]        # 删整个字符（汉字 2 格也一次删掉）
            self._pos -= 1
            self._redraw()

    def _history_move(self, d: int) -> None:
        if not self._history:
            return
        self._hist_idx = max(0, min(len(self._history), self._hist_idx + d))
        self._buf = list(self._history[self._hist_idx]) if self._hist_idx < len(self._history) else []
        self._pos = len(self._buf)
        self._redraw()

    def _accept(self) -> None:
        line = "".join(self._buf)
        self._buf, self._pos = [], 0
        if self._active:
            with self._lock:
                # ★ 回车这一行**连同状态方框一起让位**：敲下去的命令本身像日志行一样进滚动区
                #   （跟 shell 一个观感），页脚随即画回来。若只写一行再 `_redraw`，输入行会被
                #   顶到方框**下方**——"页脚紧贴输入行上方"这个不变量一破，下次 `_erase_footer`
                #   就擦错行（把日志吃掉）。
                sys.stdout.write(self._erase_footer() + self.prompt + line + "\n")
                sys.stdout.flush()
                self._draw_footer()
        self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        """一行输入的**统一处理**（tty 与非 tty 两条读取路径共用）：

        ① 多行模式下攒到 `END` 才提交（正文原样带换行）；
        ② 否则命中 `is_multiline_start` ⇒ 进多行模式；
        ③ 其余直接提交。

        ★ 2026-09-19 修：这套原先只写在 `_accept()`（tty 那支）里，而 `_read_plain()`（非 tty）
        **逐行直投** ⇒ 管道/重定向输入时多行完全不生效（用户报「send 能处理多段、say 不能」，
        修的时候才发现非 tty 那一支连 `send` 也不处理）。
        """
        if self._ml:                            # 多行模式：攒到 END
            if line.strip().upper() == "END":
                self._ml = False
                self._submit_text("\n".join(self._ml_buf))
                self._ml_buf = []
            else:
                self._ml_buf.append(line)
            return
        if not line.strip():
            return
        self._history.append(line)
        del self._history[:-self._hist_size]
        self._hist_idx = len(self._history)
        if self._is_ml_start and self._is_ml_start(line.strip()):
            self._ml = True
            self._ml_buf = [line.strip()]
        else:
            self._submit_text(line.strip())

    def _submit_text(self, text: str) -> None:
        self.queue.put(text)

    def _interrupt(self) -> None:
        if self._on_interrupt:
            self._on_interrupt()
        else:
            self.close()

    # ---- 重画
    def _prompt_render(self) -> str:
        """输入行的渲染（含光标定位）：光标停在 `_pos` 那一格。"""
        prompt = "… " if self._ml else self.prompt
        line = prompt + "".join(self._buf)
        tail = "".join(self._buf[self._pos:])
        out = "\r\033[K" + line
        if tail:
            out += f"\033[{dw(tail)}D"
        return out

    def _erase_footer(self) -> str:
        """擦掉屏幕上当前的页脚（状态方框 + 输入行），光标停在页脚**顶行**。
        只擦**正画着的那几行**（`_footer_n`）——多擦一行就会把日志吃掉。"""
        out = "\r\033[K"                       # 光标在输入行：先擦输入行
        for _ in range(self._footer_n):
            out += "\033[1A\r\033[K"           # 再逐行上移擦掉状态方框
        return out

    def _draw_footer(self) -> None:
        """画出页脚（状态方框 + 输入行），光标停在输入行。"""
        lines = self._footer_lines()
        self._footer_n = len(lines)
        sys.stdout.write("".join(ln + "\n" for ln in lines) + self._prompt_render())
        sys.stdout.flush()

    def _redraw(self) -> None:
        """按键后的重画：**只重画输入行**（状态方框没变，别整个重画——会闪）。"""
        if not self._active:
            return
        with self._lock:
            sys.stdout.write(self._prompt_render())
            sys.stdout.flush()
