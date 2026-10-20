"""Guard: a tool description must not exist in two diverging copies.

`mods/native/tools/locales/zh-CN.json` can override the inline description at
render time. Wherever a catalog key exists it must equal the code value, so the
two copies cannot drift silently (``end_turn`` had drifted once).
"""
import json
from pathlib import Path
import unittest

from engine.runtime import Runtime

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'mods/native/tools/locales/zh-CN.json'


class TestToolDescriptions(unittest.TestCase):
    def test_catalog_descriptions_match_code(self):
        catalog = json.loads(CATALOG.read_text(encoding='utf-8'))
        runtime = Runtime.load(ROOT / 'mods.toml')
        covered = 0
        for key, tool in runtime.tools.items():
            if key != tool.name:
                continue
            value = catalog.get(f'tools.{tool.name}.description')
            if value is None:
                continue
            covered += 1
            self.assertEqual(value, tool.description, tool.name)
        self.assertGreater(covered, 0)


if __name__ == '__main__':
    unittest.main()
