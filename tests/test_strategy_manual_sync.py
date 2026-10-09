# -*- coding: utf-8 -*-
"""《战略手册》（`docs/战略手册.md`）必须与局内那份提示**逐字一致**，且**不挂进任何 prompt**。

为什么有这个守卫：这本书不是"另写一份给人看的讲义"，而是**局内 AI 读的那一份原文**
（`mp_ai.strategy_manual()`）。它与另三本的区别只有一条**挂载口径**——用户 2026-10-09
明确选「**完全按需取**」：**不挂进任何 system prompt**，只有 AI 自己 `rules(战略手册)`
才看得到（《战争手册》里挂了一句"推荐阅读"把人引过来）。这条口径很容易被后人"好心"
改成常驻（毕竟它讲得很重要），所以在这里钉死。

另外两条硬约束（与《战争手册》同规，都是**改了不报错**的那种）：

1. **正文里不许出现随回合/随局势变化的内容** —— 它进的是 `rules` 的返回体，而
   `rules` 的目录/正文会被拼进 system 前缀的历史里（`ctx.py` 开头那条"system 必须逐字节
   稳定"）。写成"第 N 回合"、市价、国名、当前兵力，就会制造无谓的缓存失效。
2. **正文里不许出现任何对手数据** —— 手册只讲盘面判断；对手的家底只能走
   `spy` / `buy_report` / 视野。用户 2026-10-06 明令：那是情报，不能白送。

发现失败时的修法：**不要手改文档**，跑一次::

    python3 docs/sync_manual.py

让它从源头现算重写。
"""

import difflib
import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs"))
sys.path.insert(0, str(ROOT))

import sync_manual  # noqa: E402

# 2026-10-09 定稿时是 8 节；加节要连这个数一起改（防"悄悄少了一节"）。
# ★ 同日加第九节「附：孙子兵法摘抄」（用户要求），故 9。
WANT_SECTIONS = 9

# 情报边界：这些是**数据字段**名，不是机制名词（"军费"是机制，故不在列）。
FORBIDDEN_LEAKS = ("国库", "总资产", "GDP", "产出", "储备", "库存")

# ★ 只在手册里出现、不会出现在别处的句子——用来判"它有没有被挂进 prompt"。
FINGERPRINT = "坐山观虎斗"


def _body_lines(doc: str) -> list[str]:
    """AUTO 块内的行（去空行、去 `## ` 分节前缀）——即"书里那本书"的原始行。"""
    m = sync_manual.block_re("strategy").search(doc)
    assert m, "文档里找不到 AUTO:strategy 块"
    out = []
    for ln in m.group("body").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out.append(ln[3:] if ln.startswith("## ") else ln)
    return out


class TestStrategyManualSync(unittest.TestCase):
    def setUp(self):
        self.doc = sync_manual.STRATEGY_DOC_PATH.read_text(encoding="utf-8")

    def test_auto_block_matches_source(self):
        """文档现状 == 从 mp_ai.strategy_manual() 现算的结果（漂移则报出 diff）。"""
        new = sync_manual.render(self.doc, sync_manual.STRATEGY_BLOCKS)
        if new != self.doc:
            diff = "".join(difflib.unified_diff(
                self.doc.splitlines(True), new.splitlines(True),
                fromfile="docs/战略手册.md", tofile="strategy_manual() 现算"))
            self.fail("《战略手册》已与局内提示脱钩 —— "
                      "跑 `python3 docs/sync_manual.py` 刷新：\n" + diff)

    def test_body_is_verbatim_from_prompt(self):
        """块内正文逐行等于局内提示原文（只允许 `## ` 分节与去缩进）。"""
        import mp_ai
        src = [ln.strip() for ln in mp_ai.strategy_manual().splitlines() if ln.strip()]
        self.assertEqual(_body_lines(self.doc), src,
                         "《战略手册》正文与 mp_ai.strategy_manual() 不再逐字对应 —— "
                         "要改手册请改 strategy_manual()，文档随刷新走")

    def test_sections_are_rendered_as_headings(self):
        """每个小节都提成了 `##` 小标题（否则 Markdown 里会与正文并成一坨）。"""
        m = sync_manual.block_re("strategy").search(self.doc)
        heads = re.findall(r"^## (.+)$", m.group("body"), flags=re.MULTILINE)
        self.assertEqual(len(heads), WANT_SECTIONS, f"小节数不是 {WANT_SECTIONS}：{heads}")
        for h in heads:
            self.assertRegex(h, sync_manual._SECTION_RE)

    def test_body_is_byte_stable(self):
        """同一次会话里调两次必须逐字节相同（不许写回合号 / 市价 / 当前兵力）。"""
        import mp_ai
        self.assertEqual(mp_ai.strategy_manual(), mp_ai.strategy_manual())

    def test_no_opponent_data(self):
        """★ 情报边界：手册不含对手的数据字段（用户 2026-10-06 明令不可白送情报）。"""
        import mp_ai
        body = mp_ai.strategy_manual()
        hits = [w for w in FORBIDDEN_LEAKS if w in body]
        self.assertEqual(hits, [], f"《战略手册》正文出现了对手数据字段：{hits}")


class TestStrategyManualMounting(unittest.TestCase):
    """★ 挂载口径：**完全按需取**（用户 2026-10-09）——不许被后人改成常驻。"""

    @staticmethod
    def _world():
        import mp
        return mp.World(size=20, seed=7, nations=["秦", "楚"])

    def test_not_in_system_prompt_at_peace(self):
        import mp_ai
        w = self._world()
        self.assertNotIn(FINGERPRINT, mp_ai.system_prompt(w, "秦"),
                         "《战略手册》被挂进了和平期的 system prompt —— "
                         "用户选的是「完全按需取」，只有 rules(战略手册) 才该返回它")

    def test_not_in_system_prompt_at_war(self):
        """战时也不挂：战争手册挂了（那是它的口径），但战略手册只留一句"推荐阅读"。"""
        import mp_ai
        w = self._world()
        w.declare_war("秦", "楚")
        p = mp_ai.system_prompt(w, "秦")
        self.assertIn("战争手册", p, "战时的《战争手册》该照旧挂上")
        self.assertNotIn(FINGERPRINT, p, "《战略手册》正文不该出现在 system prompt 里")

    def test_war_manual_recommends_it(self):
        """★ 但《战争手册》（战时每回合常驻）里要有一句"推荐阅读"把人引过来。"""
        import mp_ai
        war = mp_ai.war_manual()
        self.assertIn("rules(战略手册)", war, "战争手册里丢了推荐阅读的指路")

    def test_reachable_by_rules_and_aliases(self):
        """`rules(战略手册)` 取得到；AI 会用的那些词也都该被别名接住（不许兜底成全书）。"""
        import mp_ai
        w = self._world()
        for topic in ("战略手册", "均势", "围魏救赵", "坐山观虎斗", "运动战", "僵持"):
            out = mp_ai.rules_text(w, topic)
            self.assertNotEqual(out, "", topic)
            self.assertFalse(out.startswith("【规则书目录】"), f"「{topic}」没被别名接住")
            self.assertIn(FINGERPRINT, out, f"「{topic}」该落到《战略手册》")


if __name__ == "__main__":
    unittest.main()
