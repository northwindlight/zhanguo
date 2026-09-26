# -*- coding: utf-8 -*-
"""《经济学手册》（`docs/经济学手册.md`）必须与局内那份提示**逐字一致**。

为什么有这个守卫：这本书不是"另写一份给人看的讲义"，而是**局内 AI 读的那一份原文**
（`mp_ai._econ_manual()`）搬到 Markdown 里。一旦有人图省事在 `blk_econ()` 或文档里
直接改字句，局内提示与人类侧文档就分家了 —— 而两边看起来都"没坏"，只有读者会发现
书里写的和 AI 信的规矩不是一回事。

校验分两层：

1. `render(doc, ECON_BLOCKS) == doc` —— 文档现状 == 从 `mp_ai._econ_manual()` 现算
   （与游戏说明书同一条 `--check`）。
2. 块内文字去掉 `## ` 前缀、去掉空行后，**逐行等于** `_econ_manual()` 的行序列
   —— 挡住"分节函数里手写文案"（那种情况下第 1 条仍会绿）。

发现失败时的修法：**不要手改文档**，跑一次::

    python3 docs/sync_manual.py

让它从源头现算重写。
"""

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
    m = sync_manual.block_re("econ").search(doc)
    assert m, "文档里找不到 AUTO:econ 块"
    out = []
    for ln in m.group("body").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        out.append(ln[3:] if ln.startswith("## ") else ln)
    return out


class TestEconManualSync(unittest.TestCase):
    def setUp(self):
        self.doc = sync_manual.ECON_DOC_PATH.read_text(encoding="utf-8")

    def test_auto_block_matches_source(self):
        """文档现状 == 从 mp_ai._econ_manual() 现算的结果（漂移则报出 diff）。"""
        new = sync_manual.render(self.doc, sync_manual.ECON_BLOCKS)
        if new != self.doc:
            import difflib
            diff = "".join(difflib.unified_diff(
                self.doc.splitlines(True), new.splitlines(True),
                fromfile="docs/经济学手册.md", tofile="_econ_manual() 现算"))
            self.fail("《经济学手册》已与局内提示脱钩 —— "
                      "跑 `python3 docs/sync_manual.py` 刷新：\n" + diff)

    def test_body_is_verbatim_from_prompt(self):
        """块内正文逐行等于局内提示原文（只允许 `## ` 分节与去缩进）。"""
        import mp_ai
        src = [ln.strip() for ln in mp_ai._econ_manual().splitlines() if ln.strip()]
        self.assertEqual(_body_lines(self.doc), src,
                         "《经济学手册》正文与 mp_ai._econ_manual() 不再逐字对应 —— "
                         "要改手册请改 _econ_manual()，文档随刷新走")

    def test_sections_are_rendered_as_headings(self):
        """六个小节都提成了 `##` 小标题（否则 Markdown 里会与正文并成一坨）。"""
        m = sync_manual.block_re("econ").search(self.doc)
        heads = re.findall(r"^## (.+)$", m.group("body"), flags=re.MULTILINE)
        self.assertEqual(len(heads), 6, f"小节数不是 6：{heads}")
        for h in heads:
            self.assertRegex(h, sync_manual._SECTION_RE)

    def test_prose_has_no_bare_numbers(self):
        """AUTO 块外的正文不写数字（数字一律住在块里，才不会悄悄过期）。"""
        hits = sync_manual.lint_prose(self.doc, sync_manual.ECON_BLOCKS)
        self.assertEqual(hits, [], "正文里出现了游离数字（挪进 AUTO 块，或按需标注 lint-ok）：\n"
                                   + "\n".join(hits))

    def test_checker_has_teeth(self):
        """校验器真的会发现漂移（改了块内容 → 现算结果与文档不再相等）。"""
        mutated = re.sub(
            r"(<!-- AUTO:econ BEGIN[^\n]*-->\n).*?(\n<!-- AUTO:econ END -->)",
            r"\1\n（手改过的假内容）\2", self.doc, flags=re.DOTALL)
        self.assertNotEqual(mutated, self.doc)
        self.assertNotEqual(sync_manual.render(mutated, sync_manual.ECON_BLOCKS), mutated)

    def test_block_present_and_known(self):
        """文档里的 AUTO 标记集合 == 同步器为它登记的块集合（不留孤儿标记）。"""
        found = set(re.findall(r"<!-- AUTO:(\w+) BEGIN", self.doc))
        self.assertEqual(found, set(sync_manual.ECON_BLOCKS))

    def test_doc_registered_with_syncer(self):
        """这份文档已在 DOCS 里登记（否则 `python3 docs/sync_manual.py` 会漏掉它）。"""
        self.assertIn(sync_manual.ECON_DOC_PATH, sync_manual.DOCS)
        self.assertIs(sync_manual.DOCS[sync_manual.ECON_DOC_PATH], sync_manual.ECON_BLOCKS)


if __name__ == "__main__":
    unittest.main()
