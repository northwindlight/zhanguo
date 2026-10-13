# -*- coding: utf-8 -*-
"""结算测试：按「总消费」（累计建造+征兵+军费，市价折金）排名，不再做加权总分。
全合成存档，不碰真实 mp_save.json。

★ 2026-10-08 用户：「**结算厅要结算所有国家**」——旧口径只请 `save["nations"]`
（存活者）入场，而成绩单里**亡国是照样上榜的**：一局打到终局，厅里常常只剩冠军一位，
另外七位君主的整局得失、终局表态、互相指认全都没有下文。现在座次＝全体国家，
亡国之君**凭自己的记忆入场**（引擎侧的对偶：亡国不再清记忆，`World.load` 也不许筛掉）。
见 `TestHallSeatsEveryone` / `TestDeathKeepsMemory`。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mp  # noqa: E402
import settlement  # noqa: E402


def _save(spend: dict | None = None) -> dict:
    """两国合成存档：甲有农场+金矿，乙有矿场。spend 缺省=空（模拟旧档）。"""
    tiles = {
        "0,0": {"owner": "甲", "buildings": {"农场": 2, "黄金矿场": 6}},
        "1,0": {"owner": "甲", "buildings": {}},
        "0,1": {"owner": "乙", "buildings": {"矿场": 1}},
    }
    return {
        "turn": 12,
        "order": ["甲", "乙"],
        "nations": {"甲": {}, "乙": {}},
        "tiles": tiles,
        "armies": [
            {"owner": "甲", "type": "步", "hp": 100, "x": 0, "y": 0},
            {"owner": "乙", "type": "骑", "hp": 50, "x": 0, "y": 1},
        ],
        "spend": spend if spend is not None else {},
    }


class TestSpendTotal(unittest.TestCase):
    def test_total_is_sum_of_three(self):
        save = _save({"甲": {"build": 1000, "recruit": 200, "supply": 300},
                      "乙": {"build": 10, "recruit": 0, "supply": 5}})
        r = settlement.settle(save)["甲"]
        self.assertEqual(r["spend_total"], 1500)
        self.assertEqual(r["spend"], {"build": 1000.0, "recruit": 200.0, "supply": 300.0})

    def test_missing_spend_field_is_zero(self):
        """旧档没有 spend 字段 → 总消费 0，不崩。"""
        r = settlement.settle(_save())["甲"]
        self.assertEqual(r["spend_total"], 0.0)
        board = settlement.scoreboard_text(_save(), settlement.settle(_save()))
        self.assertIn("本档没有消费记录", board)

    def test_rank_by_spend_not_by_dimensions(self):
        """军力/领土更小的国家，只要花得多就排前面。"""
        save = _save({"甲": {"build": 5000, "recruit": 0, "supply": 0},
                      "乙": {"build": 1, "recruit": 0, "supply": 0}})
        res = settlement.settle(save)
        self.assertGreater(res["甲"]["land"], res["乙"]["land"])   # 甲本来就大
        rank = sorted(res, key=lambda n: -res[n]["spend_total"])
        self.assertEqual(rank[0], "甲")
        board = settlement.scoreboard_text(save, res)
        self.assertLess(board.index("甲"), board.index("乙"))


class TestDeadNations(unittest.TestCase):
    """已亡国照样上榜：按累计消费排名，四维现状记 0。"""

    def _save_with_dead(self) -> dict:
        save = _save({"甲": {"build": 100, "recruit": 0, "supply": 0},
                      "丙": {"build": 9000, "recruit": 500, "supply": 500}})
        save["order"] = ["甲", "乙", "丙"]
        save["nations"] = {"甲": {}, "乙": {}}          # 丙 已亡（不在 nations 里）
        return save

    def test_dead_nation_ranked_by_spend(self):
        save = self._save_with_dead()
        res = settlement.settle(save)
        self.assertFalse(res["丙"]["alive"])
        self.assertTrue(res["甲"]["alive"])
        self.assertEqual(res["丙"]["spend_total"], 10000)
        self.assertEqual(res["丙"]["land"], 0)          # 亡国四维归零
        self.assertEqual(res["丙"]["asset"], 0)
        board = settlement.scoreboard_text(save, res)
        self.assertLess(board.index("丙"), board.index("甲"))   # 消费最高 → 榜首
        self.assertIn("（亡）", board)
        self.assertIn("参与到底也算数", board)


class TestScoreboardText(unittest.TestCase):
    def test_board_columns_and_order(self):
        save = _save({"甲": {"build": 300, "recruit": 50, "supply": 70},
                      "乙": {"build": 900, "recruit": 0, "supply": 0}})
        res = settlement.settle(save)
        board = settlement.scoreboard_text(save, res)
        for col in ("总消费", "建造", "征兵", "军费", "GDP", "军力", "领土", "资产"):
            self.assertIn(col, board)
        self.assertIn("按总消费排名", board)
        self.assertIn("支出法 GDP", board)                 # 口径说明
        self.assertNotIn("总分", board)                    # 加权总分已取消
        self.assertLess(board.index("乙"), board.index("甲"))   # 乙花得多 → 排前面


class TestHallSeatsEveryone(unittest.TestCase):
    """★★ 结算厅的**座次**＝全体国家（含已亡国），按入场顺序。

    用户 2026-10-08：「结算厅要结算所有国家」——他看到的现场是：一局打到只剩一国，
    厅里只坐了那一个活着的。而这不是打分问题（成绩单早就含亡国），是**入座名单**
    只取了 `save["nations"]`。
    """

    def _save(self) -> dict:
        save = _save({"甲": {"build": 100, "recruit": 0, "supply": 0},
                      "丙": {"build": 9000, "recruit": 500, "supply": 500}})
        save["order"] = ["甲", "乙", "丙"]
        save["nations"] = {"甲": {}, "乙": {}}          # 丙 已亡（不在 nations 里）
        save["summaries"] = {"丙": [{"turn": 3, "text": "丙的第三回合小结：筑城备粮。"}]}
        save["summary_blocks"] = {"丙": [{"from": 1, "to": 2, "text": "开局扩建成型", "turn": 3}]}
        save["long_memory"] = {"丙": "丙的长期记忆：与甲有盟约，魏 欠我一笔。"}
        save["polity"] = {"丙": "normal"}
        return save

    def test_roster_has_the_dead(self):
        self.assertEqual(settlement.hall_roster(self._save()), ["甲", "乙", "丙"],
                         "亡国的 丙 没在座次里")

    def test_dead_king_walks_in_with_its_memory(self):
        """★ 入座不是"给个空名字"：他带着自己的记忆来（小结/长期记忆都进上下文）。"""
        ctx = settlement._game_context(self._save(), "丙", 5)
        blob = json.dumps(ctx, ensure_ascii=False)
        self.assertIn("丙的第三回合小结", blob, "亡国之君到厅里已经失忆了")
        self.assertIn("丙的长期记忆", blob, "长期记忆没带上")

    def test_dead_king_without_memory_still_walks_in(self):
        """**阴性对照**：旧档（亡国时记忆被清掉的年代）也不能崩——记忆没了，座位还在。"""
        save = self._save()
        for k in ("summaries", "summary_blocks", "long_memory", "polity"):
            save[k] = {}
        ctx = settlement._game_context(save, "丙", 5)
        self.assertTrue(ctx, "重建出的上下文是空的")
        self.assertEqual(ctx[0]["role"], "system")

    def test_the_hall_actually_invites_the_dead(self):
        """端到端：跑完 2 轮，**亡国的 丙 必须发言**。

        ★ 打桩打在 `settlement._llm_client`（结算厅唯一一处建连）——不碰 openai、
        不走网络。**断言语义也要卡死**：`（发言失败，缺席）` 那行同样含「·丙】」，
        只查国名会让"根本没连上"也判绿（第一版就是这么假绿的，还顺手烧了两分钟）。
        """
        from types import SimpleNamespace as NS
        calls: list[str] = []

        def fake_client(nc):
            class _C:
                @staticmethod
                def create(**_kw):
                    calls.append("x")
                    return NS(choices=[NS(message=NS(content="我认了。"))])
            return (NS(chat=NS(completions=_C())), nc.get("model"), 0.7, 8192, {})

        cfg = {"nations": [{"name": n, "base_url": "http://x", "api_key": "k", "model": "m"}
                           for n in ("甲", "乙", "丙")]}
        orig, settlement._llm_client = settlement._llm_client, fake_client
        try:
            lines = settlement.run_chat(self._save(), settlement.settle(self._save()),
                                        cfg, 2, lambda _s: None)
        finally:
            settlement._llm_client = orig
        self.assertEqual(len(calls), 6, f"3 国 × 2 轮该有 6 次发言，实际 {len(calls)}：{lines}")
        for who in ("甲", "乙", "丙"):
            self.assertTrue(any(f"·{who}】我认了。" in ln for ln in lines), f"{who} 没发言：{lines}")


class TestDeathKeepsMemory(unittest.TestCase):
    """引擎侧的对偶：**亡国不清记忆**（否则结算厅请到的是一屋子失忆的君主）。"""

    def _world(self):
        w = mp.World(size=16, seed=3, nations=["秦", "燕"])
        w.summaries["燕"] = [{"turn": 1, "text": "燕的纪事"}]
        w.summary_blocks["燕"] = [{"from": 1, "to": 1, "text": "阶段块", "turn": 2}]
        w.long_memory["燕"] = "长期记忆"
        w.turn_memory["燕"] = [{"turn": 1, "messages": [{"role": "user", "content": "x"}]}]
        w.polity["燕"] = "huns"
        return w

    def _kill(self, w, name):
        for (x, y) in list(w.own_tiles(name)):
            w.tiles[(x, y)]["buildings"]["市政厅"] = 0
        return w._eliminate_if_dead(name)

    def test_memory_survives_death(self):
        w = self._world()
        self.assertTrue(self._kill(w, "燕"))
        self.assertIn("燕", w.summaries, "亡国的纪事被清了——结算厅请到的是失忆的君主")
        self.assertIn("燕", w.summary_blocks)
        self.assertIn("燕", w.long_memory)
        self.assertEqual(w.polity.get("燕"), "huns", "身份（匈奴口吻）被清了")
        self.assertNotIn("燕", w.turn_memory, "逐字缓冲区是**在局**状态，死了不该留（存档最大的一块）")
        self.assertNotIn("燕", w.plans, "国策是在局状态，清了")

    def test_save_load_roundtrip_keeps_it(self):
        """★ 存盘再读回**不能失忆**（两边口径不一致的话，这个 bug 只在重启后现形）。"""
        w = self._world()
        self.assertTrue(self._kill(w, "燕"))
        with tempfile.TemporaryDirectory() as d:
            p = str(Path(d) / "s.json")
            w.save(p)
            w2 = mp.World.load(p)
        self.assertIn("燕", w2.summaries)
        self.assertIn("燕", w2.summary_blocks)
        self.assertIn("燕", w2.long_memory)
        self.assertEqual(w2.polity.get("燕"), "huns")
        self.assertNotIn("燕", w2.turn_memory)


if __name__ == "__main__":
    unittest.main()
