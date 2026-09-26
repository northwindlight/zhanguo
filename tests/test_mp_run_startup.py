# -*- coding: utf-8 -*-
"""`mp_run.py` 的**启动路径**守卫：真跑一回合，不许崩。

为什么要有这条（2026-09-19 真栽过）：
    世界央行那段 `emit(...)` 写在了 `emit` **定义之前**（它是 `run()` 里的嵌套函数），
    World 层的冒烟测试全绿、`tests/` 全绿，而用户 `./start.sh` 一跑就是
    `UnboundLocalError: cannot access local variable 'emit'`。
    ⇒ **库里没有任何测试跑过 `run()` 本身** —— 启动顺序、配置接线、开关这类东西全在盲区里。

做法：用**无 key 的配置**（两道都走规则 AI 代打，不碰网络、不烧 token），
     `--new --turns 1` 在子进程里真跑一回合，断言退出码为 0、且开关按配置生效。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _cfg(d: Path, bank: bool, **extra) -> Path:
    """最小可用配置：无 base_url/api_key ⇒ 规则 AI 代打（离线、秒级）。

    `extra` 用来逐条加开关（如 `opening_guide=False`）——**不加就是"配置里没写这个键"**，
    正好用来区分"没写"与"写了 false"。
    """
    p = d / "cfg.json"
    p.write_text(json.dumps({
        "map_size": 16, "seed": 7, "max_turns": 1, "rule_ai": "v10",
        "save": str(d / "s.json"), "journal": str(d / "j.md"),
        "nations": [{"name": "秦"}, {"name": "楚"}],
        "world_bank": bank,
        **extra,
    }, ensure_ascii=False), encoding="utf-8")
    return p


def _run(cfg: Path, d: Path, turns: int = 1, *, new: bool = True,
         stdin: str | None = None) -> subprocess.CompletedProcess:
    """在**临时目录**里跑（cwd=d）：存档/日志/`mp_map.txt` 全落在临时目录，不污染工作区。

    脚本走绝对路径——`mp_run.py` 的 import 靠 sys.path[0]（= 脚本所在目录），所以换 cwd 无碍。
    `new=False` 就是续局（读同一份存档）。
    """
    return subprocess.run(
        [sys.executable, str(ROOT / "mp_run.py"), "--config", str(cfg),
         *(["--new"] if new else []), "--turns", str(turns)],
        cwd=d, capture_output=True, text=True, timeout=300, input=stdin)


class TestMpRunStartup(unittest.TestCase):
    def test_starts_up_at_all(self):
        """★ 跑得起来（守 UnboundLocalError 这类"启动顺序"崩溃）。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=False), d)
            self.assertEqual(r.returncode, 0, f"启动即崩：\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
            self.assertIn("终局", r.stdout, "没跑完一局")

    def test_bank_is_enabled_by_config(self):
        """配置开了 ⇒ 启动时打开、并播一句；存档里也应是开的。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=True), d)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertIn("世界央行", r.stdout, "配置开关没接线")
            self.assertTrue(json.loads((d / "s.json").read_text(encoding="utf-8"))["bank"]["on"],
                            "配置 world_bank=true 应该让存档里的银行是开的")

    def test_bank_stays_off_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=False), d)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertFalse(json.loads((d / "s.json").read_text(encoding="utf-8"))["bank"]["on"])

    def test_bank_cannot_be_turned_off_midgame(self):
        """★「只能在配置文件开、不能关」：先开着跑一局，再把配置改成 false 续局 ⇒ 银行仍开。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertEqual(_run(_cfg(d, bank=True), d).returncode, 0)
            # 同一份存档，配置改成关 —— 续局（不带 --new）
            cfg_off = _cfg(d, bank=False)
            r = _run(cfg_off, d, turns=2, new=False)      # 续局（**不带 --new**）
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertNotIn("新开一局", r.stdout, "该走续局，不该重开")
            self.assertTrue(json.loads((d / "s.json").read_text(encoding="utf-8"))["bank"]["on"],
                            "配置说 false 也不能把已经开着的银行关掉")

    def test_opening_guide_defaults_on(self):
        """配置里不写 `opening_guide` ⇒ 开局指南照挂（默认开）。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertEqual(_run(_cfg(d, bank=False), d).returncode, 0)
            self.assertIs(json.loads((d / "s.json").read_text(encoding="utf-8"))["opening_guide"],
                          True)

    def test_opening_guide_config_can_only_turn_it_off(self):
        """★ 配置**只许关、不许强开**（与央行正好相反）——这是"有指南 vs 无指南"的对照臂。

        为什么必须在这里跑真启动：开关的接线点在 `mp_run.run()`（不是 `make_world`），
        因为剧本局的开局存档是 `scenarios/eight_nations.py` 自己 `build()` 出来的，
        `--new` 走不到那条路 —— 写在 `make_world` 里会让 `opening_guide: false`
        对剧本局**静默失效**。
        """
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            self.assertEqual(_run(_cfg(d, bank=False, opening_guide=False), d).returncode, 0)
            self.assertIs(json.loads((d / "s.json").read_text(encoding="utf-8"))["opening_guide"],
                          False, "配置说关就该关（新建的 World 默认是 True）")
            # 同一份存档，配置改回 true —— 续局（不带 --new）
            r = _run(_cfg(d, bank=False, opening_guide=True), d, turns=2, new=False)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            self.assertNotIn("新开一局", r.stdout, "该走续局，不该重开")
            self.assertIs(json.loads((d / "s.json").read_text(encoding="utf-8"))["opening_guide"],
                          False, "配置说 true 也不能把已经关掉的指南强开回来")


class TestObserverCommands(unittest.TestCase):
    """★ 观察者命令台（从 stdin 喂命令）—— 与"启动路径"同一个盲区，之前一条测试都没有。

    `say`/`公告`：**全世界**广播（走 `World.broadcast`，`seen` 记全体，不受视野过滤）——
    这是**通用机制**，跟开不开世界央行无关（用户 2026-09-19：「直接说公告就行了，
    因为有的局没有央行」）。
    `rate`：改储蓄利率并触发自动播报（开行才有）。
    """

    def test_say_broadcasts_to_everyone(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=True), d, turns=2,
                     stdin="say 世界央行：为抑制过度储蓄，本次下调至 -8%\n")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("已向全世界广播", r.stdout)
            hist = json.loads((d / "s.json").read_text(encoding="utf-8"))["history"]
            ann = [h for h in hist if "📢" in h["text"]]
            self.assertTrue(ann, "say 没写进纪事")
            self.assertIn("为抑制过度储蓄", ann[-1]["text"], "正文要原样带上")
            self.assertEqual(sorted(ann[-1]["seen"]), sorted(["秦", "楚"]),
                             "广播该给全体（不是按视野过滤）")

    def test_announcement_works_without_the_bank(self):
        """★ 公告是通用机制：**没开央行**的局也该能用 say 向全世界喊话。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=False), d, turns=2, stdin="公告 今年大旱，粮价必涨\n")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("已向全世界广播", r.stdout, "关行的局也该能发公告")
            hist = json.loads((d / "s.json").read_text(encoding="utf-8"))["history"]
            self.assertTrue(any("今年大旱" in h["text"] for h in hist))

    def test_announcement_handles_multiline_paste(self):
        """★ **多段文字**：先敲 `公告` 再粘正文、以 END 收尾 —— 与 `send` 同一套 Console 机制。

        （用户 2026-09-19：「是 send，send 正确处理了多段文字问题，而 say 没有」：
        漏的是 `Console(is_multiline_start=…)` 那个谓词——`say` 从没进过多行模式。）
        """
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=False), d, turns=2,
                     stdin="公告\n世界央行公告：\n为抑制过度储蓄，本次下调至 -8%。\nEND\n")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            hist = json.loads((d / "s.json").read_text(encoding="utf-8"))["history"]
            ann = [h["text"] for h in hist if "📢" in h["text"]]
            self.assertTrue(ann, f"多行公告没进纪事：{r.stdout[-400:]}")
            body = ann[-1]
            self.assertIn("世界央行公告：", body)
            self.assertIn("为抑制过度储蓄，本次下调至 -8%。", body)
            self.assertIn("\n", body, f"换行该原样保留（两段要分开）：{body!r}")

    def test_announcement_from_file(self):
        """`公告 @文件` 与 `send 国家 @文件` 同款（正文从文件读，多行原样）。"""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            f = d / "notice.txt"
            f.write_text("第一条：利率下调。\n第二条：贷款照旧。", encoding="utf-8")
            r = _run(_cfg(d, bank=False), d, turns=2, stdin=f"公告 @{f}\n")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            hist = json.loads((d / "s.json").read_text(encoding="utf-8"))["history"]
            ann = [h["text"] for h in hist if "📢" in h["text"]]
            self.assertTrue(ann, "从文件发的公告没进纪事")
            self.assertIn("第二条：贷款照旧。", ann[-1])
            self.assertIn("\n", ann[-1], "文件里的换行要保留")

    def test_rate_command_sets_rate_and_announces(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=True), d, turns=2, stdin="rate -8\n")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("为了减少紧缩，下调了", r.stdout, "自动播报没触发")
            bk = json.loads((d / "s.json").read_text(encoding="utf-8"))["bank"]
            self.assertAlmostEqual(bk["rate"], -0.08, places=6)


class TestEmitWiring(unittest.TestCase):
    """★ 接线守卫：`run_openai_turn` 的播报通道（emit）必须真的被接上。

    2026-09-19 查出：`mp_run.py` 调的是 `run_openai_turn(world, name, ncfg,
    max_steps=…)`——**从没传过 `emit=`**（`emit` 参数自 8dc7f7f 就在，
    `git log -S"emit=emit" -- mp_run.py` 零命中）。函数里所有播报都写成 `if emit:`
    ⇒ 整条通道是死的：上下文计划🧠 / 下滑🧠 / 压缩记忆🧠 / 压缩失败⚠ / 重试⚠ /
    思考💭 / 宣告🗣 全部静默。真实后果：**压缩一直在跑**（存档 5 国共 27 条
    summary_blocks + long_memory），但看海台与 mp_journal.md 里一次都没出现过
    ——「为什么从没见过压缩」的真凶就是这个。

    洞在**调用方**：库层测试传不传 emit 都绿，只有源码断言守得住。配套
    `test_turn_loop.test_emit_channel_reports_slide_and_compact` 守"接上了响不响"。
    """

    def test_call_site_passes_notice_channel(self):
        """★ 通知通道（🧠/⚠/🛑）必须接上——现在接的是 `panel_note`
        （底部固定状态区，用户 2026-09-20「这些弄个固定位置」；非 tty 时它自己退回日志流）。"""
        src = (ROOT / "mp_run.py").read_text(encoding="utf-8")
        i = src.index("run_openai_turn(")
        seg = src[i:i + 1200].replace(" ", "").replace("\n", "")
        self.assertIn("emit=partial(panel_note", seg,
                      "run_openai_turn 的通知通道没接上：压缩/下滑/重试/故障会重新变成"
                      "静默的死通道（2026-09-19 那个洞的形状）")
        self.assertIn("head=partial(panel_note", seg,
                      "常驻状态行（上下文计划）没接上：状态区第 1 行会一直空着")

    def test_call_site_passes_on_call(self):
        """★ 接线守卫：**每次调用回显一次**（用户 2026-09-20）也得真接上。

        与 emit 同一个洞的形状：库层（`test_turn_loop`）只守"接上了响不响"，
        调用点这一半只有源码断言守得住。丢了 on_call ⇒ 看海台又变成
        「整国回合结束才一次性砸下来」。
        """
        src = (ROOT / "mp_run.py").read_text(encoding="utf-8")
        i = src.index("run_openai_turn(")
        seg = src[i:i + 900].replace(" ", "").replace("\n", "")
        self.assertIn("on_call=partial(echo_call", seg,
                      "run_openai_turn 的**逐次调用回显**没接上（`on_call=` 丢了）："
                      "动作会重新攒到回合末才回显")


class TestPanelNote(unittest.TestCase):
    """通知行的去向：**日志文件一定全量记**，终端上则分「有状态区 / 没状态区」两条路。"""

    def _journal(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        return Path(td.name) / "j.md"

    def test_falls_back_to_log_when_no_panel(self):
        """没有真终端（管道/重定向）⇒ 退回日志流：这些行在那边**必须还能看见**。"""
        import io
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import mp_run
        p = self._journal()
        buf = io.StringIO()
        old, mp_run.CONSOLE = mp_run.CONSOLE, None
        self.addCleanup(lambda: setattr(mp_run, "CONSOLE", old))
        _stdout, sys.stdout = sys.stdout, buf
        self.addCleanup(lambda: setattr(sys, "stdout", _stdout))
        mp_run.panel_note(p, "⚠ 秦 第1次调用失败(APITimeoutError)")
        self.assertIn("第1次调用失败", buf.getvalue(), "没面板时该退回日志流")
        self.assertIn("第1次调用失败", p.read_text(encoding="utf-8"), "日志文件必须全量记")

    def test_goes_to_panel_and_still_journals(self):
        """有状态区 ⇒ 进面板（终端上**不再**重复打一遍），日志文件照旧全量。"""
        import io
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import mp_run

        class _FakeConsole:
            panel_on = True

            def __init__(self):
                self.calls = []

            def set_panel(self, head=None, note=None):
                self.calls.append((head, note))

        p = self._journal()
        fake = _FakeConsole()
        old, mp_run.CONSOLE = mp_run.CONSOLE, fake
        self.addCleanup(lambda: setattr(mp_run, "CONSOLE", old))
        buf = io.StringIO()
        _stdout, sys.stdout = sys.stdout, buf
        self.addCleanup(lambda: setattr(sys, "stdout", _stdout))
        mp_run.panel_note(p, "🧠 秦 上下文: 窗口262k·预算195k", head=True)
        mp_run.panel_note(p, "⚠ 秦 第1次调用失败")
        self.assertEqual(fake.calls, [("🧠 秦 上下文: 窗口262k·预算195k", None),
                                      (None, "⚠ 秦 第1次调用失败")],
                         "常驻行走 head、通知走 note")
        self.assertEqual(buf.getvalue(), "", "进了面板就不该再往日志流重复打一遍")
        text = p.read_text(encoding="utf-8")
        self.assertIn("上下文: 窗口262k", text)
        self.assertIn("第1次调用失败", text)


class TestEchoCall(unittest.TestCase):
    """`echo_call` 本身的契约：抬头 + 纪事原文，且**不重打**（游标只前进）。"""

    def _world(self):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import mp
        w = mp.World(size=16, seed=7, nations=["秦", "楚"])
        w.turn = 3
        return w

    def test_echo_call_writes_header_and_consumes_history(self):
        import io
        w = self._world()
        import mp_run
        _stdout, sys.stdout = sys.stdout, io.StringIO()   # 回显会打到终端：测试里噤声
        self.addCleanup(lambda: setattr(sys, "stdout", _stdout))
        w.action("秦", "build", "tile=5 5 building=农场", "✅ 动工 农场")
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "j.md"
            out: list[str] = []
            mp_run.echo_call(w, out, p, "秦", 0)
            self.assertEqual(out, [], "回显该当场刷走（不留缓冲）")
            text = p.read_text(encoding="utf-8")
            self.assertIn("◈ 秦 第 1 次调用", text, "缺逐次调用的抬头")
            self.assertIn("◇ build", text, "动作没刷出来")
            # 第二次调用：只打新的那条，旧动作不许重打（纪事游标已前进）
            w.action("秦", "query", "panel=all", "（面板）")
            mp_run.echo_call(w, out, p, "秦", 1)
            text = p.read_text(encoding="utf-8")
            self.assertIn("◈ 秦 第 2 次调用", text)
            self.assertIn("◇ query", text)
            self.assertEqual(text.count("◇ build"), 1, "旧动作被重打了（游标没前进）")


if __name__ == "__main__":
    unittest.main()
