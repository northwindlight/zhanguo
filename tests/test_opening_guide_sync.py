# -*- coding: utf-8 -*-
"""《开局指南》（`docs/开局指南.md`）必须与局内那份提示**逐字一致**。

为什么有这个守卫：这本书同样不是"另写一份给人看的带教"，而是**局内 AI 读的那一份原文**
（`mp_ai.opening_guide()`）——而且它比《经济学手册》还要紧一层：开局前若干回合它是被
**强行挂进每一国 system prompt** 的，人类侧文档要是与它分家，就会出现"书里写的"和
"AI 开局真信的"不是一回事。

顺带把两条**容易踩的硬约束**钉在这里（它们都是"改了不报错、只在别处炸"那种）：

1. **正文里不许出现 `世界央行` / `buy_report`** —— `rules_text(world, "")` 无主题时会把
   全部章节（含本指南）拼起来，而 `tests/test_bank.py` 断言"央行关着时全文里没有这两个词"。
   一句话里顺口提一句"央行"，坏掉的是银行那组测试。
2. **正文里不许出现随回合变化的内容** —— 它进的是 system 前缀（`ctx.py` 开头那条
   "system 必须逐字节稳定"）。写成"第 N 回合"、价格、国名，就是**每回合**整段前缀连同
   replay 一起失效，而不只是窗口到期那一次。所以这里钉死"同一个函数调两次逐字相同"。

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


def _body_lines(doc: str) -> list[str]:
    """AUTO 块内的行（去空行、去 `## ` 分节前缀）——即"书里那本书"的原始行。"""
    m = sync_manual.block_re("guide").search(doc)
    assert m, "文档里找不到 AUTO:guide 块"
    out = []
    for ln in m.group("body").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out.append(ln[3:] if ln.startswith("## ") else ln)
    return out


class TestOpeningGuideSync(unittest.TestCase):
    def setUp(self):
        self.doc = sync_manual.GUIDE_DOC_PATH.read_text(encoding="utf-8")

    def test_auto_block_matches_source(self):
        """文档现状 == 从 mp_ai.opening_guide() 现算的结果（漂移则报出 diff）。"""
        new = sync_manual.render(self.doc, sync_manual.GUIDE_BLOCKS)
        if new != self.doc:
            diff = "".join(difflib.unified_diff(
                self.doc.splitlines(True), new.splitlines(True),
                fromfile="docs/开局指南.md", tofile="opening_guide() 现算"))
            self.fail("《开局指南》已与局内提示脱钩 —— "
                      "跑 `python3 docs/sync_manual.py` 刷新：\n" + diff)

    def test_body_is_verbatim_from_prompt(self):
        """块内正文逐行等于局内提示原文（只允许 `## ` 分节与去缩进）。"""
        import mp_ai
        src = [ln.strip() for ln in mp_ai.opening_guide().splitlines() if ln.strip()]
        self.assertEqual(_body_lines(self.doc), src,
                         "《开局指南》正文与 mp_ai.opening_guide() 不再逐字对应 —— "
                         "要改指南请改 opening_guide()，文档随刷新走")

    def test_sections_are_rendered_as_headings(self):
        """六个小节都提成了 `##` 小标题（否则 Markdown 里会与正文并成一坨）。"""
        m = sync_manual.block_re("guide").search(self.doc)
        heads = re.findall(r"^## (.+)$", m.group("body"), flags=re.MULTILINE)
        self.assertEqual(len(heads), 6, f"小节数不是 6：{heads}")
        for h in heads:
            self.assertRegex(h, sync_manual._SECTION_RE)

    def test_prose_has_no_bare_numbers(self):
        """AUTO 块外的正文不写数字（数字一律住在块里，才不会悄悄过期）。"""
        hits = sync_manual.lint_prose(self.doc, sync_manual.GUIDE_BLOCKS)
        self.assertEqual(hits, [], "正文里出现了游离数字（挪进 AUTO 块，或按需标注 lint-ok）：\n"
                                   + "\n".join(hits))

    def test_checker_has_teeth(self):
        """校验器真的会发现漂移（改了块内容 → 现算结果与文档不再相等）。"""
        mutated = re.sub(
            r"(<!-- AUTO:guide BEGIN[^\n]*-->\n).*?(\n<!-- AUTO:guide END -->)",
            r"\1\n（手改过的假内容）\2", self.doc, flags=re.DOTALL)
        self.assertNotEqual(mutated, self.doc)
        self.assertNotEqual(sync_manual.render(mutated, sync_manual.GUIDE_BLOCKS), mutated)

    def test_block_present_and_known(self):
        """文档里的 AUTO 标记集合 == 同步器为它登记的块集合（不留孤儿标记）。"""
        found = set(re.findall(r"<!-- AUTO:(\w+) BEGIN", self.doc))
        self.assertEqual(found, set(sync_manual.GUIDE_BLOCKS))

    def test_doc_registered_with_syncer(self):
        """这份文档已在 DOCS 里登记（否则 `python3 docs/sync_manual.py` 会漏掉它）。"""
        self.assertIn(sync_manual.GUIDE_DOC_PATH, sync_manual.DOCS)
        self.assertIs(sync_manual.DOCS[sync_manual.GUIDE_DOC_PATH], sync_manual.GUIDE_BLOCKS)


class TestOpeningGuidePromptSafety(unittest.TestCase):
    """这是**局内提示**，不是普通文档：三条硬约束钉在这里（见模块头）。"""

    def setUp(self):
        import mp_ai
        self.text = mp_ai.opening_guide()

    def test_不含央行口径的词(self):
        """不许提 `世界央行` / `buy_report`：`rules_text(w,"")` 全文会被 test_bank 断言。"""
        self.assertNotIn("世界央行", self.text)
        self.assertNotIn("buy_report", self.text)

    def test_逐字节稳定(self):
        """同一个函数调两次必须逐字相同（进 system 前缀，随回合变字＝每回合缓存全失效）。"""
        import mp_ai
        self.assertEqual(self.text, mp_ai.opening_guide())

    def test_签名零参数(self):
        """不许吃 world/name——那样迟早有人把"第 N 回合""国名""价格"写进去。"""
        import inspect

        import mp_ai
        self.assertEqual(list(inspect.signature(mp_ai.opening_guide).parameters), [])

    def test_挂进rules且能按主题取回(self):
        """过期后靠 `rules(开局指南)` 取回：章节表里要有它，主题词要认得。"""
        import mp_ai
        labels = [label for label, _ in mp_ai._help_sections()]
        self.assertIn("开局指南", labels)
        self.assertIn("开局指南", self.text)


if __name__ == "__main__":
    unittest.main()
