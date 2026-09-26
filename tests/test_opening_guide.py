# -*- coding: utf-8 -*-
"""《开局指南》的**挂载行为**：开局前若干回合在、之后不在、开关能关、存档带着走。

为什么单独钉一组：它是**引擎级带教**（不是某一国的 `extra_prompt`），挂载点有三个容易
静默失效的地方——

- **窗口**：`world.turn < EXTRA_PROMPT_TURNS`。写反成 `<=` 就多挂一回合；
  挂载条件里 world 前一步取了兜底值，就会在结算厅那类 shim 世界上炸（详见下面的 shim 用例）。
- **开关**：`World.opening_guide` 进存档，配置只能**关**不能强开（`mp_run` 的启动接线，
  在本文件的子进程用例里跑真启动验证）。
- **不串门**：它**不许**写进 `extra_prompt`（八国剧本的常驻之志正占着那个字段，
  两者混一起会把 CHARTER 挤掉或改写）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp  # noqa: E402
import mp_ai  # noqa: E402
from balance import EXTRA_PROMPT_TURNS  # noqa: E402


def _world(size: int = 16, seed: int = 7) -> mp.World:
    return mp.World(size=size, seed=seed, nations=["秦", "楚"], max_turns=30)


class TestOpeningGuideWindow(unittest.TestCase):
    def test_开局前有_到期后没有(self):
        w = _world()
        self.assertIn("开局指南", mp_ai.system_prompt(w, "秦"),
                      "开局（第 0 回合）就该挂上")
        w.turn = EXTRA_PROMPT_TURNS - 1
        self.assertIn("开局指南", mp_ai.system_prompt(w, "秦"), "窗口内最后一回合仍在")
        w.turn = EXTRA_PROMPT_TURNS
        self.assertNotIn("开局指南", mp_ai.system_prompt(w, "秦"),
                         "过了窗口就该消失（边界是 turn < EXTRA_PROMPT_TURNS）")

    def test_开关关掉就没有(self):
        w = _world()
        w.opening_guide = False
        self.assertNotIn("开局指南", mp_ai.system_prompt(w, "秦"))

    def test_不写进extra_prompt(self):
        """它走自己的字段——`extra_prompt` 是"一国一条密谕"，别去占那一格。"""
        w = _world()
        self.assertEqual(w.extra_prompt, {})
        self.assertIn("开局指南", mp_ai.system_prompt(w, "秦"))

    def test_八国剧本的常驻之志不受影响(self):
        """剧本自带 CHARTER（占着 extra_prompt）——两份提示必须同时在场、互不覆盖。"""
        from scenarios import eight_nations as S
        w = S.build()
        self.assertTrue(w.opening_guide)
        for n in S.NATIONS:
            self.assertEqual(w.extra_prompt[n]["text"], S.CHARTER, "之志被动过了")
        text = mp_ai.system_prompt(w, S.ZHOU)
        self.assertIn("开局指南", text)
        self.assertIn("六王毕", text)

    def test_shim世界不炸(self):
        """结算厅那类 shim（SimpleNamespace）少字段时**不许**抛异常。

        `system_prompt` 由 `build_context` 在**回合中途**调用：真炸了就是一个打了一半的
        回合（比启动即崩更难查）。结算厅自己也显式给了 `opening_guide=False`。
        """
        shim = SimpleNamespace(turn=0, polity={}, extra_prompt={},
                               alive=lambda: ["秦"], opening_guide=False)
        self.assertNotIn("开局指南", mp_ai.system_prompt(shim, "秦"))
        bare = SimpleNamespace(turn=0, polity={}, extra_prompt={}, alive=lambda: ["秦"])
        self.assertNotIn("开局指南", mp_ai.system_prompt(bare, "秦"),
                         "没有这个字段时按「不挂」处理（getattr 兜底，不是抛异常）")


class TestOpeningGuideSave(unittest.TestCase):
    def test_存档默认登记在追加字段表里(self):
        """★ 追加式存档政策：加了字段就要在 SAVE_DEFAULTS 登记，老档才不会拒载。"""
        self.assertIn("opening_guide", mp.SAVE_DEFAULTS)
        self.assertIn("opening_guide", mp.SAVE_KEYS)

    def test_存档往返保住开关(self):
        for val in (True, False):
            w = _world()
            w.opening_guide = val
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "s.json"
                w.save(p)
                self.assertIs(json.loads(p.read_text(encoding="utf-8"))["opening_guide"], val)
                self.assertIs(mp.World.load(p).opening_guide, val)

    def test_老档缺字段按默认补齐(self):
        """把字段删掉模拟更老的档：按 SAVE_DEFAULTS 补齐，不许拒载。"""
        w = _world()
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            w.save(p)
            data = json.loads(p.read_text(encoding="utf-8"))
            data.pop("opening_guide")
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            self.assertTrue(mp.World.load(p).opening_guide)


if __name__ == "__main__":
    unittest.main()
