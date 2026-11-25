"""English catalogs must not silently fall back to Chinese.

`Translator` merges the selected language over zh-CN, so a key missing from
en-US (or left in Chinese) reaches English model players as Chinese text.
These checks are static and offline: no engine world, no model calls.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from presentation.console import ConsoleUI  # noqa: E402
from presentation.i18n import Translator  # noqa: E402
from presentation.tips import TIPS, TIPS_EN, random_tip  # noqa: E402

CJK = re.compile(r'[一-鿿]')
LOCALES = ROOT / 'presentation/locales'
# The map legend names the literal on-map glyph for barbarian guards.
CJK_ALLOWED = {'map.legend'}


def load(name):
    return json.loads((LOCALES / name).read_text(encoding='utf-8'))


class CatalogCoverage(unittest.TestCase):
    def test_english_covers_every_chinese_key(self):
        zh, en = load('zh-CN.json'), load('en-US.json')
        self.assertEqual(sorted(set(zh) - set(en)), [])

    def test_english_values_are_translated(self):
        en = load('en-US.json')
        left = sorted(k for k, v in en.items() if k not in CJK_ALLOWED and CJK.search(v))
        self.assertEqual(left, [])

    def test_english_catalog_loads_with_matching_placeholders(self):
        Translator('en-US')  # raises on placeholder drift


class TipsCoverage(unittest.TestCase):
    def test_english_tips_align_with_chinese(self):
        self.assertEqual(len(TIPS_EN), len(TIPS))
        self.assertFalse([t for t in TIPS_EN if CJK.search(t)])

    def test_same_key_picks_the_same_tip_index(self):
        for key in ('qin:3', 'chu:12:x', ''):
            self.assertEqual(TIPS.index(random_tip(key)), TIPS_EN.index(random_tip(key, 'en-US')))


class EnglishReceipts(unittest.TestCase):
    def setUp(self):
        self.en = ConsoleUI({'language': 'en-US'})
        self.zh = ConsoleUI({'language': 'zh-CN'})

    def assertEnglish(self, text):
        self.assertFalse(CJK.search(text), text)

    def test_step_notice(self):
        receipt = {'ok': True, 'data': None, 'message': '', 'steps': {'used': 21, 'limit': 30}}
        self.assertEnglish(self.en.receipt_body('end_turn', {}, receipt, 'qin'))
        self.assertIn('本回合已用 21/30 次模型调用（还剩 9 次）',
                      self.zh.receipt_body('end_turn', {}, receipt, 'qin'))

    def test_building_slot_failure(self):
        data = {'reason': 'building_slots', 'position': [2, 3], 'kind': 'native:farm', 'block': 'slots',
                'occupied': 4, 'capacity': 4, 'existing': ['native:farm'], 'resources': {'native:food': 2}}
        self.assertEnglish(self.en.building_slots(data))
        self.assertIn('本格建筑位已满：4/4', self.zh.building_slots(data))

    def test_resource_shortage_and_money(self):
        receipt = {'ok': False, 'message': '', 'data': {'reason': 'insufficient_resource',
                   'resource': 'native:gold', 'required': 30, 'available': 10}}
        self.assertEnglish(self.en.receipt_body('buy', {}, receipt, 'qin'))
        self.assertEnglish(self.en.money(12) + self.en.money(None) + self.en.resources({'native:wood': 3}, '+'))
        self.assertEqual(self.zh.money(12), '12金')
        self.assertEqual(self.zh.resources({'native:wood': 3}, '+'), '+3木')

    def test_memory_and_relation(self):
        receipt = {'ok': True, 'message': '', 'data': [{'period': 4, 'context': 'plan'}]}
        self.assertEnglish(self.en.receipt_body('memory_search', {}, receipt, 'qin'))
        self.assertEqual(self.en.relation('war'), 'war')
        self.assertEqual(self.zh.relation('war'), '交战')


if __name__ == '__main__':
    unittest.main()
