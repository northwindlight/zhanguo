"""UI text is stored once: duplicated keys resolve to the tool catalog."""
import json
from pathlib import Path
import unittest

from presentation.i18n import ALIASES, Translator

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'mods/native/tools/locales/zh-CN.json'


class TestLocaleAliases(unittest.TestCase):
    def test_alias_resolves_to_tool_catalog_text(self):
        tool = json.loads(TOOLS.read_text(encoding='utf-8'))
        text = Translator('zh-CN')
        checked = 0
        for alias, target in ALIASES.items():
            if target not in tool:
                continue
            self.assertEqual(text.messages[alias], tool[target], alias)
            checked += 1
        self.assertGreater(checked, 50)

    def test_duplicated_ui_values_are_gone(self):
        ui = json.loads((ROOT / 'presentation/locales/zh-CN.json').read_text(encoding='utf-8'))
        tool = json.loads(TOOLS.read_text(encoding='utf-8'))
        shared = set(ui.values()) & set(tool.values())
        self.assertEqual(shared, set())
        self.assertTrue(set(ALIASES) <= set(ui) | set(tool) | set(ALIASES))


if __name__ == '__main__':
    unittest.main()
