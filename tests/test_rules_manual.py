# -*- coding: utf-8 -*-
"""局内 `rules` 的**结构守护**：数据源换成了 doc 目录那本《游戏说明书》。

2026-10-06 用户口径：「别再用旧总则，换成规则说明书（doc 目录的）」「整套手写规则段退役」
「尽量不要给全文」「剔掉上帝视角章」。

★ 为什么需要这组守卫：说明书里只有 **26% 是 `sync_manual.py` 现算的 AUTO 块**
（有 `--check` 守漂移），剩下 **74% 是散文，没有任何校验**。它一旦当了局内规则的数据源，
那 74% 就变成"玩家读到的规则，但没人守"。这组测试守住的是**结构与边界**
（哪些章不能给、正文里不许出现什么、读不到文件不能崩）——内容正确性仍归作者。
"""

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import mp_ai  # noqa: E402


class TestManualSections(unittest.TestCase):
    def test_读得到且非空(self):
        secs = mp_ai.manual_sections()
        self.assertIsNotNone(secs, "读不到 docs/游戏说明书.md —— 局内 rules 会退化成只剩讲义")
        self.assertGreaterEqual(len(secs), 8, f"章数太少：{[t for t, _ in secs]}")
        for title, body in secs:
            self.assertTrue(title.strip(), "章标题不该为空")
            self.assertGreater(len(body), 50, f"「{title}」正文太短，像是被清理规则吃掉了")

    def test_上帝视角章不进局内(self):
        """政体参数 / 终局结算 / 延伸阅读 / 观察者命令 —— 一律不给局内 AI。"""
        titles = [t for t, _ in mp_ai.manual_sections()]
        for bad in ("政体", "终局结算", "延伸阅读", "看海台", "世界央行"):
            self.assertFalse(any(bad in t for t in titles),
                             f"「{bad}」不该出现在局内可见章节里：{titles}")

    def test_正文不含外链与内部符号(self):
        """剔章之外还要剔**章内的开发注记**：外链、`.py` 路径、`模块.函数`、引擎类名。

        ★ 注意别把**工具名**当注记：`break_defense` / `offer_peace` 这种带下划线的标识符
        是玩家能调的命令（命令表里就有），必须留——所以这里只禁"明显是引擎内部"的形状。
        """
        pat = re.compile(r"\]\([^)]*\.md\)|\.py\b|[A-Z][a-z]+\.[a-z_]+|`_|mp_ai|settlement\b")
        for title, body in mp_ai.manual_sections():
            hit = pat.search(body)
            self.assertIsNone(hit, f"「{title}」正文里还剩开发注记：{hit.group(0) if hit else ''}")


class TestRulesText(unittest.TestCase):
    def setUp(self):
        import mp
        self.w = mp.World(size=20, seed=7, nations=["秦", "楚"])

    def test_认不出主题就回目录_不倒全文(self):
        out = mp_ai.rules_text(self.w, "")
        self.assertTrue(out.startswith("【规则书目录】"), out[:80])
        self.assertLess(len(out), 1500, "目录不该膨胀成全文")
        # 反例守卫：目录里不该夹带某一章的全部正文
        self.assertNotIn("| 建筑 | 造价（金）", out)

    def test_命中主题只回那一章(self):
        out = mp_ai.rules_text(self.w, "建筑")
        self.assertTrue(out.startswith("【"), out[:40])
        self.assertIn("造价", out)
        self.assertNotIn("【五、外交】", out, "只该回命中的那一章")

    def test_旧词有别名(self):
        """旧手写版的节名（总览 / 建筑与造价 / 信箱…）不该变成"查不到"。"""
        for old in ("总览", "信箱", "情报", "命令"):
            out = mp_ai.rules_text(self.w, old)
            self.assertFalse(out.startswith("【规则书目录】"), f"「{old}」没被别名接住")

    def test_裁军类主题有别名(self):
        """★ 实测 AI 反复问的就是这些词（8 国日志里 topic=「解散 裁军 复员 遣散」出现过多次），
        从前一个都不在别名表里 ⇒ 一律兜底成**全书**，AI 据此把「裁军无机制」写进了国策。
        现在这些词必须落到含遣散条款的【军队与战斗】。"""
        for word in ("遣散", "裁军", "复员", "解散军队"):
            out = mp_ai.rules_text(self.w, word)
            self.assertFalse(out.startswith("【规则书目录】"), f"「{word}」没被别名接住")
            self.assertIn("遣散", out, f"「{word}」该落到写着遣散条款的【军队与战斗】")

    def test_讲义仍可查(self):
        for name in ("经济手册", "基础指南", "战争手册", "战略手册"):
            self.assertIn(name, mp_ai.rules_text(self.w, name))

    def test_说明书读不到时不崩(self):
        """★ 这是 `mp_ai.py` 第一处运行时读盘 ⇒ 读不到**绝不能**把回合带崩。"""
        import mp
        w = mp.World(size=20, seed=7, nations=["秦", "楚"])
        saved = mp_ai.MANUAL_PATH
        mp_ai.MANUAL_PATH = pathlib.Path("/nonexistent/游戏说明书.md")
        try:
            out = mp_ai.rules_text(w, "")
            self.assertIn("讲义", out)              # 降级到只剩讲义，且**说清了原因**
            self.assertIn("战争手册", mp_ai.rules_text(w, "战争手册"))
        finally:
            mp_ai.MANUAL_PATH = saved


if __name__ == "__main__":
    unittest.main()
