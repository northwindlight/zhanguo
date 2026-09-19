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


def _cfg(d: Path, bank: bool) -> Path:
    """最小可用配置：无 base_url/api_key ⇒ 规则 AI 代打（离线、秒级）。"""
    p = d / "cfg.json"
    p.write_text(json.dumps({
        "map_size": 16, "seed": 7, "max_turns": 1, "rule_ai": "v10",
        "save": str(d / "s.json"), "journal": str(d / "j.md"),
        "nations": [{"name": "秦"}, {"name": "楚"}],
        "world_bank": bank,
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

    def test_rate_command_sets_rate_and_announces(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            r = _run(_cfg(d, bank=True), d, turns=2, stdin="rate -8\n")
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            self.assertIn("为了减少紧缩，下调了", r.stdout, "自动播报没触发")
            bk = json.loads((d / "s.json").read_text(encoding="utf-8"))["bank"]
            self.assertAlmostEqual(bk["rate"], -0.08, places=6)


if __name__ == "__main__":
    unittest.main()
