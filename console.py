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
        c.close()                  # 恢复终端
    提交的命令进 `c.queue`（队列），由主循环在回合边界取。
    """

    def __init__(self, prompt: str = "❯ ", on_interrupt=None,
                 is_multiline_start=None, history_size: int = 100):
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

    # ---- 输出（线程安全：先让开输入行，写完再画回来）
    def write(self, text: str, render: bool = True) -> None:
        if not text:
            return
        if render and self._md:
            text = render_md(text)
        with self._lock:
            if self._active:
                sys.stdout.write("\r\033[K")
            sys.stdout.write(text + "\n")
            sys.stdout.flush()
            if self._active:
                self._redraw()

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
            s = line.rstrip("\n")
            if s.strip():
                self._submit_text(s)

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
                sys.stdout.write("\r\033[K" + self.prompt + line + "\n")
                sys.stdout.flush()
        if self._ml:                            # 多行模式：攒到 END
            if line.strip().upper() == "END":
                self._ml = False
                self._submit_text("\n".join(self._ml_buf))
                self._ml_buf = []
            else:
                self._ml_buf.append(line)
        elif line.strip():
            self._history.append(line)
            del self._history[:-self._hist_size]
            self._hist_idx = len(self._history)
            if self._is_ml_start and self._is_ml_start(line.strip()):
                self._ml = True
                self._ml_buf = [line.strip()]
            else:
                self._submit_text(line.strip())
        self._redraw()

    def _submit_text(self, text: str) -> None:
        self.queue.put(text)

    def _interrupt(self) -> None:
        if self._on_interrupt:
            self._on_interrupt()
        else:
            self.close()

    # ---- 重画
    def _redraw(self) -> None:
        if not self._active:
            return
        prompt = "… " if self._ml else self.prompt
        line = prompt + "".join(self._buf)
        tail = "".join(self._buf[self._pos:])
        with self._lock:
            out = "\r\033[K" + line
            if tail:
                out += f"\033[{dw(tail)}D"
            sys.stdout.write(out)
            sys.stdout.flush()
