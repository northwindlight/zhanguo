"""Stock finale uses recorded rankings and keeps each player's replay private."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from host import GameHost
from settlement import load_save, run_chat, scoreboard_text, settle
from tests.test_stock_game import config


class StockFinaleTest(unittest.TestCase):
    def test_stock_finished_ranking_private_context_and_no_save_mutation(self):
        cfg = config(actors=2)
        cfg['nations'][0]['client'] = 'llm'
        cfg['nations'][1]['client'] = 'llm'
        for entry in cfg['nations']:
            entry.update(model='fake', base_url='http://fake', api_key='fake')
        h = GameHost.new(cfg)
        a, b = 'stock:player0', 'stock:player1'
        h.world.scripts.write(a, "print('private alpha marker')")
        h.world.scripts.write(b, "print('private beta marker')")
        h.world.portfolio.accounts[a]['cash'] = 100100.0
        h.world.portfolio.accounts[b]['cash'] = 100200.0
        h.session.final_settlement()
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / 'session.json'
            h.save(save)
            before = hashlib.sha256(save.read_bytes()).digest()
            data, world = load_save(save)
            rows = settle(data, world)
            self.assertEqual([r['actor'] for r in rows], [b, a])
            board = scoreboard_text(rows)
            self.assertIn('100200.00', board)
            self.assertNotIn('消费（金）', board)
            seen = {}
            class Speaker:
                def complete_text(self, messages, item, tools):
                    self_tools = tools
                    seen[item['id']] = (json.dumps(messages, ensure_ascii=False), self_tools)
                    return '我的复盘。', {'stop_reason':'end_turn'}
            transcript = run_chat(data, rows, cfg, 1, log=lambda _: None,
                                  backend_factory=lambda _: Speaker())
            self.assertEqual(len(transcript), 2)
            self.assertIn('private alpha marker', seen[a][0])
            self.assertNotIn('private beta marker', seen[a][0])
            self.assertIn('private beta marker', seen[b][0])
            self.assertNotIn('private alpha marker', seen[b][0])
            self.assertTrue(all(entry[1] == [] for entry in seen.values()))
            continuation = {}
            class ContinuingSpeaker:
                def complete_text(self, messages, item, tools):
                    continuation[item['id']] = messages
                    return '第二轮复盘。', {}
            prior = ['【第1轮·量化1】先说的发言。', '【第1轮·量化0】后说的发言。']
            result = run_chat(data, rows, cfg, 2, log=lambda _:None,
                              backend_factory=lambda _:ContinuingSpeaker(),
                              prior_transcript=prior, start_round=2, prior_rounds=1)
            self.assertEqual(len(result), 4)
            self.assertIn('先说的发言。', json.dumps(continuation[b], ensure_ascii=False))
            self.assertIn('后说的发言。', json.dumps(continuation[a], ensure_ascii=False))
            self.assertTrue(any(m['role'] == 'assistant' and m['content'] == '后说的发言。'
                                for m in continuation[a]))
            self.assertTrue(any(m['role'] == 'user' and '第2/2轮' in m['content']
                                for m in continuation[a]))
            self.assertEqual(hashlib.sha256(save.read_bytes()).digest(), before)

    def test_stock_unfinished_save_has_no_final_scoreboard(self):
        h = GameHost.new(config())
        with tempfile.TemporaryDirectory() as directory:
            save = Path(directory) / 'session.json'
            h.save(save)
            data, world = load_save(save)
            with self.assertRaisesRegex(ValueError, '尚未完成最终清算'):
                settle(data, world)


if __name__ == '__main__':
    unittest.main()
