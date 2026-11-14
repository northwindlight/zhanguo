"""Native SDK wire tests use a local HTTP server; no Google account is needed."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from google.genai import types
from clients import context
from clients.gemini import GeminiNative, contents, endpoint, generation_config
from clients.provider import EndpointError, make_backend


def cfg(**kw):
    return {'provider': 'gemini', 'base_url': 'http://127.0.0.1:1', 'api_key': 'test-key',
            'model': 'gemini-test', 'max_tokens': 2048, 'reasoning_effort': 'high',
            'api_retries': 1, 'api_retry_wait': 0, **kw}


class GeminiTests(unittest.TestCase):
    def server(self, responses):
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                response = responses.pop(0)
                if isinstance(response, tuple):
                    status, body = response
                    raw = json.dumps(body).encode()
                    self.send_response(status); self.send_header('Content-Type', 'application/json')
                else:
                    raw = ''.join('data: '+json.dumps(chunk)+'\n\n' for chunk in response).encode()
                    self.send_response(200); self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def cleanup(): server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(cleanup)
        return cfg(base_url=f'http://127.0.0.1:{server.server_port}'), requests

    def test_sdk_wire_parallel_tools_signature_and_result_continuation(self):
        first = [{'candidates': [{'index': 0, 'content': {'role': 'model', 'parts': [
            {'text': 'Need market', 'thought': True},
            {'functionCall': {'id': 'a', 'name': 'query', 'args': {'panel': 'market'}}, 'thoughtSignature': 'c2ln'},
            {'functionCall': {'id': 'b', 'name': 'query', 'args': {'panel': 'army'}}},
        ]}}]}, {'candidates': [{'finishReason': 'STOP'}], 'usageMetadata': {
            'promptTokenCount': 100, 'cachedContentTokenCount': 80, 'candidatesTokenCount': 20, 'thoughtsTokenCount': 5}}]
        second = [{'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}]
        config, requests = self.server([first, second])
        tools = [{'type': 'function', 'function': {'name': 'query', 'parameters': {'type': 'object', 'properties': {'panel': {'type': 'string'}}}}}]
        messages = [{'role': 'system', 'content': 'rules'}, {'role': 'user', 'content': 'Query both'}]
        backend = make_backend(config)
        events = []
        result, stats = backend.chat_turn(messages, tools, config, on_delta=lambda kind, text: events.append((kind, text)))
        self.assertEqual(len(result['tool_calls']), 2)
        self.assertEqual((stats['hit'], stats['miss'], stats['reason_tokens'], stats['out_tokens']), (80, 20, 5, 25))
        self.assertIn(('thinking', 'Need market'), events)
        replay = json.loads(json.dumps(result))  # actual save/load representation
        messages += [{'role': 'assistant', **replay}, {'role': 'tool', 'tool_call_id': 'a', 'content': '{"ok":true}'},
                     {'role': 'tool', 'tool_call_id': 'b', 'content': 'Army result'}]
        reply, _ = backend.chat_turn(messages, tools, config)
        self.assertEqual(reply['content'], 'OK')
        path, headers, wire = requests[1]
        self.assertTrue(path.startswith('/v1beta/models/gemini-test:streamGenerateContent'))
        self.assertEqual(headers.get('x-goog-api-key') or headers.get('X-Goog-Api-Key'), 'test-key')
        native = wire['contents'][-2]['parts']
        self.assertEqual(native[1]['thoughtSignature'], 'c2ln')
        self.assertNotIn('thoughtSignature', native[2])
        self.assertEqual([p['functionResponse']['id'] for p in wire['contents'][-1]['parts']], ['a', 'b'])
        self.assertEqual(wire['tools'][0]['functionDeclarations'][0]['parameters_json_schema'], tools[0]['function']['parameters'])

    def test_legacy_history_keeps_native_functions_and_literal_migration_sentinel(self):
        messages = [{'role': 'assistant', 'tool_calls': [{'id': 'old', 'function': {'name': 'query', 'arguments': '{"panel":"market"}'}}]},
                    {'role': 'tool', 'tool_call_id': 'old', 'content': 'result'}]
        _, converted = contents(messages, cfg())
        self.assertEqual(converted[0].parts[0].model_dump(mode='json')['thought_signature'], 'skip_thought_signature_validator')
        self.assertEqual(converted[1].parts[0].function_response.name, 'query')

    def test_endpoint_version_is_not_duplicated(self):
        self.assertEqual(endpoint(cfg(base_url='https://example.test/proxy/v1beta/')), ('https://example.test/proxy', 'v1beta'))

    def test_legacy_transcript_is_opt_in_and_never_rewrites_native_parts(self):
        messages = []
        for i in range(4):
            messages += [{'role': 'assistant', 'tool_calls': [{'id': str(i), 'function': {'name': 'query', 'arguments': '{"panel":"market"}'}}]},
                         {'role': 'tool', 'tool_call_id': str(i), 'content': f'完整结果{i}'}]
        messages += [{'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'native', 'function': {'name': 'query', 'arguments': '{}'}}],
                      context.REPLAY_KEY: {'provider': 'gemini', 'model': 'gemini-test', 'parts': [
                          {'function_call': {'id': 'native', 'name': 'query', 'args': {}}, 'thought_signature': 'c2ln'}]}},
                     {'role': 'tool', 'tool_call_id': 'native', 'content': 'new'}]
        original = json.dumps(messages)
        _, normal = contents(messages, cfg())
        self.assertIsNotNone(normal[0].parts[0].function_call)
        _, converted = contents(messages, cfg(gemini_legacy_tool_batches=2))
        self.assertIn('历史工具调用 0 query', converted[0].parts[0].text)
        self.assertIn('完整结果0', converted[1].parts[0].text)
        self.assertIsNotNone(converted[4].parts[0].function_call)
        self.assertEqual(converted[-2].parts[0].thought_signature, b'sig')
        self.assertEqual(json.dumps(messages), original)

    def test_malformed_empty_and_incomplete_streams_are_errors(self):
        for chunks in ([{'candidates': [{'finishReason': 'MALFORMED_FUNCTION_CALL'}]}],
                       [{'candidates': [{'finishReason': 'STOP'}]}],
                       [{'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'partial'}]}}]}]):
            config, _ = self.server([chunks])
            with self.assertRaises(EndpointError) as got:
                GeminiNative(config).chat_turn([{'role': 'user', 'content': 'hi'}], [], config)
            self.assertEqual(got.exception.status_code, 502)

    def test_http_retry_and_fatal_errors_follow_game_policy(self):
        good = [{'candidates': [{'content': {'role': 'model', 'parts': [{'text': 'OK'}]}, 'finishReason': 'STOP'}]}]
        config, requests = self.server([(429, {'error': {'code': 429, 'message': 'quota', 'status': 'RESOURCE_EXHAUSTED'}}), good])
        config['api_retries'] = 2
        result, _ = GeminiNative(config).chat_turn([{'role': 'user', 'content': 'hi'}], [], config)
        self.assertEqual(result['content'], 'OK'); self.assertEqual(len(requests), 2)
        config, requests = self.server([(400, {'error': {'code': 400, 'message': 'bad schema', 'status': 'INVALID_ARGUMENT'}})])
        config['api_retries'] = 3
        with self.assertRaises(EndpointError): GeminiNative(config).chat_turn([{'role': 'user', 'content': 'hi'}], [], config)
        self.assertEqual(len(requests), 1)

    def test_context_shrink_preserves_function_signatures_and_does_not_merge_native_parts(self):
        message = {'role': 'assistant', 'content': '', 'reasoning_content': 'summary', 'tool_calls': [],
                   context.REPLAY_KEY: {'provider': 'gemini', 'model': 'm', 'parts': [
                       {'text': 'summary', 'thought': True}, {'text': 'reply', 'thought_signature': 'c2ln'}]}}
        shrunk = context._shrink_messages([message], keep_reasoning=False, tool_trim=0)[0]
        self.assertEqual(shrunk[context.REPLAY_KEY]['parts'], [{'text': 'reply', 'thought_signature': 'c2ln'}])
        self.assertEqual(len(context.merge_same_role([shrunk, {'role': 'assistant', 'content': 'next'}])), 2)
        self.assertEqual(len(message[context.REPLAY_KEY]['parts']), 2)

    def test_compression_disables_thinking_and_tool_execution_but_retains_schema(self):
        tools = [{'function': {'name': 'query', 'parameters': {'type': 'object'}}}]
        config = generation_config('rules', tools, cfg(), compact=True, max_tokens=500)
        self.assertEqual(config.thinking_config.thinking_budget, 0)
        self.assertEqual(config.tool_config.function_calling_config.mode, 'NONE')
        self.assertTrue(config.automatic_function_calling.disable)
        self.assertEqual(config.max_output_tokens, 500)


if __name__ == '__main__': unittest.main()
