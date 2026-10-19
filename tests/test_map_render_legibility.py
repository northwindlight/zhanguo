"""Map legend and guard hint must describe what the model actually receives."""
import io
import json
import unittest
from pathlib import Path

from PIL import Image

from presentation.maps import _army_label, render_snapshot


class MapRenderLegibility(unittest.TestCase):
    def test_army_label(self):
        self.assertEqual(_army_label([{'kind': 'native:infantry'}], {'native:infantry': 'I'}), 'I1')

    def test_default_map_is_larger_than_960(self):
        tiles = []
        for y in range(24):
            for x in range(24):
                tiles.append({'position': [x, y], 'owner': None, 'terrain': 'native:plain',
                              'buildings': [], 'name': ''})
        state = {'version': 'zhanguo.ui.map/1', 'tiles': tiles, 'armies': [],
                 'glyphs': {'native:plain': 'p'}, 'role_kinds': {}}
        png = render_snapshot(state)
        size = Image.open(io.BytesIO(png)).size
        self.assertGreaterEqual(size[0], 1400)

    def test_legend_and_guard_hint_are_corrective(self):
        root = Path(__file__).resolve().parents[1]
        zh = json.loads((root / 'presentation/locales/zh-CN.json').read_text(encoding='utf-8'))
        tools = json.loads((root / 'mods/native/tools/locales/zh-CN.json').read_text(encoding='utf-8'))
        self.assertIn('野N', zh['map.legend'])
        self.assertIn('I步兵', zh['map.legend'])
        self.assertIn('{places}', tools['tool.army.guard_hint'])
        self.assertNotIn('位置见文字地图', tools['tool.army.guard_hint'])
        self.assertNotIn('可拓荒', tools['tool.frontier.head'])
        self.assertNotIn('可拓荒', tools['tool.frontier.frontier'])
