"""Display vocabulary must be accepted as action vocabulary at the tool boundary."""
import json
import unittest

from tests.test_world_domains import TestWorldDomains, Q, C


class ToolArgumentNormalization(unittest.TestCase):
    def world(self):
        t = TestWorldDomains(); w = t.world()
        t.site(w, 'scenario:qc', Q, 1, 1); t.site(w, 'scenario:cc', C, 6, 6)
        return t, w

    @staticmethod
    def own_label(player):
        row = next(r for r in player.observe()['facts']['view'] if r['owner'] == Q)
        x, y = row['position']
        return row['name'], f"{row['name']}({x}, {y})", f"{row['name']}（{x}，{y}）", (x, y)

    def test_mixed_and_fullwidth_place_labels(self):
        t, w = self.world(); p = w.runtime.player(w, Q)
        name, label, fullwidth, (x, y) = self.own_label(p)
        for ref in (label, fullwidth, f'{name} {x} {y}', f'{x} {y}', f'({x}, {y})'):
            receipt = p.submit('query', {'panel': 'tile', 'place': ref})
            self.assertTrue(receipt['ok'], (ref, receipt.get('message')))
            self.assertEqual(tuple(receipt['data']['position']), (x, y))

    def test_nation_display_name_targets(self):
        t, w = self.world(); p = w.runtime.player(w, Q)
        for target in ('楚', '楚（scenario:chu）', 'scenario:chu', 'nation/scenario:chu'):
            receipt = p.submit('intelligence', {'action': 'send_letter', 'target': target, 'text': '你好'})
            self.assertTrue(receipt['ok'], (target, receipt.get('message')))

    def test_content_aliases_and_chinese_building(self):
        t, w = self.world(); p = w.runtime.player(w, Q)
        self.assertTrue(p.submit('economy', {'action': 'buy', 'resource': 'supplies', 'quantity': 1})['ok'])
        self.assertTrue(p.submit('economy', {'action': 'buy', 'resource': '装', 'quantity': 1})['ok'])
        name, label, _, _ = self.own_label(p)
        self.assertTrue(p.submit('build', {'kind': '农场', 'site': label})['ok'])
        catalog = p.submit('query', {'panel': 'building_catalog', 'kind': '补给厂'})
        self.assertTrue(catalog['ok'], catalog.get('message'))
        self.assertEqual([row['id'] for row in catalog['data']], ['native:supply-factory'])

    def test_query_cross_panel_limit_and_unknown(self):
        t, w = self.world(); p = w.runtime.player(w, Q)
        foreign = p.submit('query', {'panel': 'foreign', 'limit': 1})
        self.assertTrue(foreign['ok'], foreign.get('message'))
        self.assertTrue(len(foreign['data']['nations']) <= 1)
        bad = p.submit('query', {'panel': 'foreign', 'bogus': 1})
        self.assertFalse(bad['ok'])
        self.assertIn('未知参数', bad.get('message', ''))

    def test_quote_panel_is_readonly_and_consistent(self):
        t, w = self.world(); p = w.runtime.player(w, Q)
        name, label, _, _ = self.own_label(p)
        before = json.dumps(w.runtime.save_mod_state(w), sort_keys=True)
        for args in ({'panel': 'quote', 'action': 'build', 'kind': '农场', 'site': label},
                     {'panel': 'quote', 'action': 'recruit', 'kind': '民兵'},
                     {'panel': 'quote', 'action': 'letter', 'target': '楚', 'text_length': 2},
                     {'panel': 'quote', 'action': 'borrow'}):
            receipt = p.submit('query', args)
            self.assertTrue(receipt['ok'], (args, receipt.get('message')))
            self.assertIn('action', receipt['data'])
        self.assertEqual(before, json.dumps(w.runtime.save_mod_state(w), sort_keys=True))
