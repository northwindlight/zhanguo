# -*- coding: utf-8 -*-
"""《战争手册》（`docs/战争手册.md`）必须与局内那份提示**逐字一致**。

为什么有这个守卫：这本书不是"另写一份给人看的讲义"，而是**局内 AI 读的那一份原文**
（`mp_ai.war_manual()`）——而且它比《经济学手册》还紧一层：**只要在交战，每回合都被
强行挂进 system prompt**（`World.at_war` 为真时，见 `mp_ai.system_prompt`）。人类侧文档
要是与它分家，就会出现"书里写的"和"AI 打仗时真信的"不是一回事。

顺带把两条**容易踩、且改了不报错**的硬约束钉在这里：

1. **正文里不许出现随回合/随局势变化的内容** —— 它进的是 system 前缀（`ctx.py` 开头那条
   "system 必须逐字节稳定"）。写成"第 N 回合"、市价、国名、当前兵力，就是**每回合**整段
   前缀连同 replay 一起失效。所以这里钉死"同一个函数调两次逐字相同"。
2. **正文里不许出现任何对手数据** —— 手册只讲机制与自己的账；对手的国库/产出/军队数
   只能走 `spy` / `buy_report` / 视野。用户 2026-10-06 明令：那是情报，不能白送。

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

# 2026-10-06 定稿时是 8 节；加节要连这个数一起改（防"悄悄少了一节"）。
WANT_SECTIONS = 8

# 情报边界：这些是**数据字段**名，不是机制名词（"军费"是机制，故不在列）。
FORBIDDEN_LEAKS = ("国库", "总资产", "GDP", "产出", "储备", "库存")


def _body_lines(doc: str) -> list[str]:
    """AUTO 块内的行（去空行、去 `## ` 分节前缀）——即"书里那本书"的原始行。"""
    m = sync_manual.block_re("war").search(doc)
    assert m, "文档里找不到 AUTO:war 块"
    out = []
    for ln in m.group("body").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out.append(ln[3:] if ln.startswith("## ") else ln)
    return out


class TestWarManualSync(unittest.TestCase):
    def setUp(self):
        self.doc = sync_manual.WAR_DOC_PATH.read_text(encoding="utf-8")

    def test_auto_block_matches_source(self):
        """文档现状 == 从 mp_ai.war_manual() 现算的结果（漂移则报出 diff）。"""
        new = sync_manual.render(self.doc, sync_manual.WAR_BLOCKS)
        if new != self.doc:
            diff = "".join(difflib.unified_diff(
                self.doc.splitlines(True), new.splitlines(True),
                fromfile="docs/战争手册.md", tofile="war_manual() 现算"))
            self.fail("《战争手册》已与局内提示脱钩 —— "
                      "跑 `python3 docs/sync_manual.py` 刷新：\n" + diff)

    def test_body_is_verbatim_from_prompt(self):
        """块内正文逐行等于局内提示原文（只允许 `## ` 分节与去缩进）。"""
        import mp_ai
        src = [ln.strip() for ln in mp_ai.war_manual().splitlines() if ln.strip()]
        self.assertEqual(_body_lines(self.doc), src,
                         "《战争手册》正文与 mp_ai.war_manual() 不再逐字对应 —— "
                         "要改手册请改 war_manual()，文档随刷新走")

    def test_sections_are_rendered_as_headings(self):
        """每个小节都提成了 `##` 小标题（否则 Markdown 里会与正文并成一坨）。"""
        m = sync_manual.block_re("war").search(self.doc)
        heads = re.findall(r"^## (.+)$", m.group("body"), flags=re.MULTILINE)
        self.assertEqual(len(heads), WANT_SECTIONS, f"小节数不是 {WANT_SECTIONS}：{heads}")
        for h in heads:
            self.assertRegex(h, sync_manual._SECTION_RE)

    def test_prompt_is_byte_stable(self):
        """★ 它进 system 前缀 ⇒ 同一次会话里调两次必须逐字节相同。

        写进"第 N 回合"、市价、当前兵力任何一样，都会让**每回合**的前缀缓存整体失效
        （不只是窗口到期那一次）。
        """
        import mp_ai
        self.assertEqual(mp_ai.war_manual(), mp_ai.war_manual())

    def test_no_opponent_data(self):
        """★ 情报边界：手册不含对手的数据字段（用户 2026-10-06 明令不可白送情报）。"""
        import mp_ai
        body = mp_ai.war_manual()
        hits = [w for w in FORBIDDEN_LEAKS if w in body]
        self.assertEqual(hits, [], f"《战争手册》正文出现了对手数据字段：{hits}")


if __name__ == "__main__":
    unittest.main()
