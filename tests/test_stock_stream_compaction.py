import json
import unittest
from unittest.mock import patch

from mods.stock.compact_provider import StockOpenAICompat
from tests.test_llm_provider import _cfg, _FakeResp


class StreamingCompactionTests(unittest.TestCase):
    def test_estimates_body_and_keeps_finish_reason(self):
        chunks = [
            {'choices': [{'delta': {'reasoning_content': '思考'*30}}]},
            {'choices': [{'delta': {'content': '摘要'*10}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'length'}]},
            {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 80}},
        ]
        lines = [line for chunk in chunks for line in ('data: '+json.dumps(chunk, ensure_ascii=False)+'\n', '\n')]
        captured = {}
        def post(url, headers, body, timeout):
            captured.update(body)
            return _FakeResp(lines=lines)
        with patch('mods.stock.compact_provider._post_json', side_effect=post):
            text, stats = StockOpenAICompat(_cfg()).complete_text_stream([], _cfg(), tools=[])
        self.assertEqual(text, '摘要'*10)
        self.assertEqual(stats['body_tokens_estimate'], 20)
        self.assertEqual(stats['stop_reason'], 'length')
        self.assertEqual(captured['thinking'], {'type': 'disabled'})
        self.assertTrue(captured['stream'])
        self.assertNotIn('tools', captured)
