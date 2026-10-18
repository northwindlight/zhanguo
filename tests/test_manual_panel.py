"""Hand-written manuals are reachable on demand, never joined into rules."""
import unittest

from host import GameHost


class TestManualPanel(unittest.TestCase):
    def host(self):
        cfg = {'map_size': 10, 'seed': 5,
               'nations': [{'id': 's:q', 'name': '秦', 'client': 'rule'},
                           {'id': 's:c', 'name': '楚', 'client': 'rule'}]}
        return GameHost.new(cfg)

    def test_manual_panel_reads_documents_without_mutation(self):
        host = self.host()
        player = host.player('s:q')
        before = host.to_save()
        data = player.query('manual', {'doc': 'basics'})
        self.assertEqual(data['id'], 'basics')
        self.assertIn('开局', data['body'])
        via_query = player.query('query', {'panel': 'manual', 'doc': 'manual'})
        self.assertIn('游戏说明书', via_query['title'])
        self.assertEqual(host.to_save(), before)

    def test_manual_panel_rejects_unknown_document(self):
        player = self.host().player('s:q')
        for bad in ('../etc/passwd', 'nope', ''):
            with self.assertRaises(ValueError):
                player.query('manual', {'doc': bad})

    def test_manuals_are_not_rule_sections(self):
        player = self.host().player('s:q')
        titles = [row['title'] for row in player.rules('all')]
        self.assertNotIn('基础指南', titles)
        self.assertNotIn('游戏说明书', titles)


if __name__ == '__main__':
    unittest.main()
