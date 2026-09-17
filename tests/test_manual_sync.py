# -*- coding: utf-8 -*-
"""《游戏说明书》的 AUTO 数值块必须与 `balance.py` 现算结果一致。

为什么有这个守卫：旧版 README 背数字，当年靠 `test_readme_sync` 盯着；2026-09-15 起 README
改成「不背数值」（数值只住 `balance.py`），那份守卫随之退役。现在背数字的是人类侧说明书
[`docs/游戏说明书.md`](../docs/游戏说明书.md)，守卫就搬到这里 —— 校验逻辑仍是同一份：
[`docs/sync_manual.py`](../docs/sync_manual.py) 的 `--check`（`render(doc) == doc`）。

发现失败时的修法：**不要手改文档里的数字**，跑一次::

    python3 docs/sync_manual.py

让它从 `balance.py` 现算重写；若它自己抛 `SyncError`（如新增了建筑 kind / 新工具），
按报错提示在同步器里补一段文案。
"""

import pathlib
import re
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "docs"))

import sync_manual  # noqa: E402


class TestManualSync(unittest.TestCase):
    def setUp(self):
        self.doc = sync_manual.DOC_PATH.read_text(encoding="utf-8")

    def test_auto_blocks_match_balance(self):
        """文档现状 == 从 balance.py 现算的结果（漂移则报出 diff）。"""
        new = sync_manual.render(self.doc)
        if new != self.doc:
            import difflib
            diff = "".join(difflib.unified_diff(
                self.doc.splitlines(True), new.splitlines(True),
                fromfile="docs/游戏说明书.md", tofile="balance.py 现算"))
            self.fail("说明书里的 AUTO 块已与 balance.py 脱钩 —— "
                      "跑 `python3 docs/sync_manual.py` 刷新：\n" + diff)

    def test_prose_has_no_bare_numbers(self):
        """AUTO 块外的正文不写数字（数字一律住在块里，才不会悄悄过期）。"""
        hits = sync_manual.lint_prose(self.doc)
        self.assertEqual(hits, [], "正文里出现了游离数字（挪进 AUTO 块，或按需标注 lint-ok）：\n"
                                   + "\n".join(hits))

    def test_checker_has_teeth(self):
        """校验器真的会发现漂移（改了块内容 → 现算结果与文档不再相等）。"""
        mutated = re.sub(
            r"(<!-- AUTO:start BEGIN[^\n]*-->\n).*?(\n<!-- AUTO:start END -->)",
            r"\1\n（手改过的假内容）\2", self.doc, flags=re.DOTALL)
        self.assertNotEqual(mutated, self.doc)
        self.assertNotEqual(sync_manual.render(mutated), mutated)

    def test_every_block_present_and_known(self):
        """文档里的 AUTO 标记集合 == 同步器认识的块集合（不留孤儿标记）。"""
        found = set(re.findall(r"<!-- AUTO:(\w+) BEGIN", self.doc))
        self.assertEqual(found, set(sync_manual.BLOCKS))


if __name__ == "__main__":
    unittest.main()
