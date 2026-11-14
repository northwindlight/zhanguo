"""Google Gen AI SDK transport; tool execution remains owned by the game loop."""
from __future__ import annotations

import json
import time
import uuid
from copy import deepcopy
from urllib.parse import urlsplit

from clients.context import REPLAY_KEY, est_tokens
from clients.provider import (EndpointError, _map_message, _retry_stream,
                              _stream_notify, hard_timeout, stream_timeout)


def _sdk():
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise ValueError('Gemini 接入需要 google-genai；请在虚拟环境运行 pip install -r requirements-gemini.txt') from exc
    return genai, types


def endpoint(cfg):
    """The SDK appends the API version, including for a local compatible server."""
    base = str(cfg['base_url']).rstrip('/')
    version = str(cfg.get('gemini_api_version') or 'v1beta')
    tail = urlsplit(base).path.rsplit('/', 1)[-1]
    if tail in ('v1', 'v1beta', 'v1alpha'):
        base, version = base.rsplit('/', 1)
    return base, version


def contents(messages, cfg):
    """Keep native response Parts verbatim, and map legacy call/result pairs by ID."""
    _, types = _sdk()
    system, output, names = [], [], {}
    for stored in messages:
        message = _map_message(stored, vision=cfg.get('vision', False), renderer=cfg.get('_ui_expand_message'))
        role = message.get('role')
        if role == 'system':
            system.append(str(message.get('content') or ''))
            continue
        parts = []
        if role == 'assistant':
            native = message.get(REPLAY_KEY) or {}
            if native.get('provider') == 'gemini' and native.get('model') == cfg['model'] and native.get('parts'):
                parts = [types.Part.model_validate(p) for p in native['parts']]
                for part in parts:
                    if part.function_call and part.function_call.id:
                        names[part.function_call.id] = part.function_call.name
            else:
                if message.get('content'):
                    parts.append(types.Part(text=str(message['content'])))
                for call in message.get('tool_calls') or []:
                    function = call['function']
                    arguments = json.loads(function.get('arguments') or '{}')
                    if not isinstance(arguments, dict):
                        raise ValueError('Gemini function arguments must be an object')
                    names[call['id']] = function['name']
                    parts.append(types.Part(
                        function_call=types.FunctionCall(id=call['id'], name=function['name'], args=arguments),
                        # Documented migration sentinel, never a replacement for a
                        # signature returned by this native transport.
                        thought_signature='skip_thought_signature_validator'))
            target_role = 'model'
        elif role == 'tool':
            ident = message.get('tool_call_id')
            if ident not in names:
                raise ValueError('Gemini tool result has no matching function call')
            raw = message.get('content') or ''
            try:
                result = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError:
                result = raw
            if not isinstance(result, dict): result = {'result': result}
            parts = [types.Part(function_response=types.FunctionResponse(id=ident, name=names[ident], response=result))]
            target_role = 'user'
        elif role == 'user':
            blocks = message.get('content')
            if not isinstance(blocks, list): blocks = [{'type': 'text', 'text': str(blocks or '')}]
            for block in blocks:
                if block['type'] == 'text':
                    if block.get('text'): parts.append(types.Part(text=block['text']))
                elif block['type'] == 'image_url':
                    url = block['image_url']['url']
                    if url.startswith('data:'):
                        import base64
                        header, data = url.split(',', 1)
                        parts.append(types.Part.from_bytes(data=base64.b64decode(data, validate=True), mime_type=header[5:].split(';')[0]))
                    else:
                        raise ValueError('Gemini 图片须使用 data URI；不将普通网页地址伪装为 Files API 文件')
                else:
                    raise ValueError('Unsupported Gemini user content block')
            target_role = 'user'
        else:
            raise ValueError(f'Unsupported Gemini message role: {role!r}')
        if not parts: continue
        # Parallel function responses must form one user Content in call order.
        if output and output[-1].role == target_role:
            output[-1].parts.extend(parts)
        else:
            output.append(types.Content(role=target_role, parts=parts))
    return '\n\n'.join(system), output


def generation_config(system, tools, cfg, *, compact=False, max_tokens=None):
    _, types = _sdk()
    data = {
        'system_instruction': system or None,
        'max_output_tokens': int(max_tokens if max_tokens is not None else cfg.get('max_tokens', 16384)),
        'automatic_function_calling': types.AutomaticFunctionCallingConfig(disable=True),
        'candidate_count': 1,
        'automatic_continuation': False,
    }
    if cfg.get('temperature') is not None: data['temperature'] = cfg['temperature']
    if tools:
        data['tools'] = [types.Tool(function_declarations=[types.FunctionDeclaration(
            name=t['function']['name'], description=t['function'].get('description'),
            parameters_json_schema=deepcopy(t['function'].get('parameters') or {'type': 'object'})) for t in tools])]
        data['tool_config'] = types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode='NONE' if compact else 'AUTO'))
    thinking = str(cfg.get('thinking') or 'enabled').lower()
    thought = {'include_thoughts': not compact and thinking != 'disabled'}
    if compact or thinking == 'disabled': thought['thinking_budget'] = 0
    elif cfg.get('gemini_thinking_budget') is not None: thought['thinking_budget'] = cfg['gemini_thinking_budget']
    elif cfg.get('reasoning_effort'): thought['thinking_level'] = str(cfg['reasoning_effort']).upper()
    data['thinking_config'] = types.ThinkingConfig(**thought)
    return types.GenerateContentConfig(**data)


def _usage(usage, stats, text, thought, calls):
    if usage is None:
        stats.update(usage_reported=False, estimated=True, hit=0, miss=0,
                     reason_tokens=est_tokens(thought), out_tokens=est_tokens(text)+est_tokens(thought)+sum(est_tokens(c['function']['arguments']) for c in calls))
        return
    prompt = int(usage.prompt_token_count or 0)
    hit = int(usage.cached_content_token_count or 0)
    reasoning = int(usage.thoughts_token_count or 0)
    stats.update(usage_reported=True, hit=hit, miss=max(0, prompt-hit),
                 reason_tokens=reasoning, out_tokens=int(usage.candidates_token_count or 0)+reasoning)


class GeminiNative:
    def __init__(self, cfg):
        self.cfg = cfg
        _sdk()  # fail at startup, not halfway through a nation's turn

    def _client(self, cfg, timeout):
        genai, types = _sdk()
        base, version = endpoint(cfg)
        return genai.Client(api_key=cfg['api_key'], http_options=types.HttpOptions(
            base_url=base, api_version=version, timeout=int(timeout*1000),
            client_args={'trust_env': False} if urlsplit(base).hostname in ('localhost', '127.0.0.1', '::1') else {},
            retry_options=types.HttpRetryOptions(attempts=1)))

    def chat_turn(self, messages, tools, cfg, on_retry=None, on_delta=None):
        return _retry_stream(self._stream_once, messages, tools, cfg, on_retry, on_delta)

    def _stream_once(self, messages, tools, cfg, on_delta=None):
        _, types = _sdk()
        system, history = contents(messages, cfg)
        config = generation_config(system, tools, cfg, compact=bool(cfg.get('_gemini_compact')))
        ttft, gap = float(cfg.get('api_ttft_timeout', 180)), float(cfg.get('api_chunk_timeout', 60))
        start = last = time.monotonic()
        first = None
        parts, text, thought, calls = [], [], [], []
        usage, finish, terminal = None, None, False
        stats = {'maxgap': 0.0}
        try:
            with self._client(cfg, ttft) as client, stream_timeout(ttft, gap) as clock:
                stream = client.models.generate_content_stream(model=cfg['model'], contents=history, config=config)
                try:
                    for chunk in stream:
                        clock.kick()
                        now = time.monotonic()
                        stats['maxgap'] = max(stats['maxgap'], now-last)
                        last = now
                        if chunk.usage_metadata is not None: usage = chunk.usage_metadata
                        if chunk.prompt_feedback and chunk.prompt_feedback.block_reason:
                            raise EndpointError(f'Gemini prompt blocked: {chunk.prompt_feedback.block_reason}', status_code=400)
                        for candidate in chunk.candidates or []:
                            if candidate.index not in (None, 0): continue
                            if candidate.finish_reason:
                                finish = str(getattr(candidate.finish_reason, 'value', candidate.finish_reason))
                                terminal = True
                            for part in candidate.content.parts if candidate.content else []:
                                if part.text:
                                    if first is None: first = now
                                    (thought if part.thought else text).append(part.text)
                                    _stream_notify(on_delta, 'thinking' if part.thought else 'body', part.text)
                                if part.function_call:
                                    if first is None: first = now
                                    call = part.function_call
                                    if call.partial_args or call.will_continue:
                                        raise EndpointError('Gemini returned an incomplete function call', status_code=502)
                                    if not call.name or not isinstance(call.args, dict):
                                        raise EndpointError('Gemini returned an invalid function call', status_code=502)
                                    ident = call.id or 'gemini_'+uuid.uuid4().hex
                                    part = part.model_copy(deep=True)
                                    part.function_call.id = ident
                                    calls.append({'id': ident, 'type': 'function', 'function': {
                                        'name': call.name, 'arguments': json.dumps(call.args, ensure_ascii=False)}})
                                parts.append(part.model_dump(mode='json', exclude_none=True))
                finally:
                    close = getattr(stream, 'close', None)
                    if close: close()
        except EndpointError:
            raise
        except Exception as exc:
            # SDK error classes expose numeric code; translate to the existing
            # retry policy (429/5xx retry, authentication/parameter errors stop).
            code = getattr(exc, 'code', None)
            if isinstance(code, int): raise EndpointError(f'Gemini HTTP {code}: {exc}', status_code=code) from exc
            if isinstance(exc, (TimeoutError, OSError)): raise
            if isinstance(exc, ValueError):
                raise EndpointError(f'Gemini response decode error: {exc}', status_code=502) from exc
            if type(exc).__module__.startswith(('httpx', 'httpcore')):
                raise EndpointError(f'Gemini transport error: {exc}', status_code=502) from exc
            raise
        if not terminal:
            raise EndpointError('Gemini stream ended without a finish reason', status_code=502)
        if finish not in ('STOP', 'MAX_TOKENS'):
            status = 400 if finish in ('SAFETY', 'RECITATION', 'BLOCKLIST', 'PROHIBITED_CONTENT', 'SPII') else 502
            raise EndpointError(f'Gemini abnormal finish: {finish}', status_code=status)
        if not calls and not ''.join(text).strip():
            raise EndpointError(f'Gemini empty response (finish={finish})', status_code=502)
        if len({c['id'] for c in calls}) != len(calls):
            raise EndpointError('Gemini returned duplicate function call IDs', status_code=502)
        msg = {'content': ''.join(text), 'reasoning_content': ''.join(thought),
               REPLAY_KEY: {'provider': 'gemini', 'model': cfg['model'], 'parts': parts}}
        if calls: msg['tool_calls'] = calls
        stats.update(wall=time.monotonic()-start, stream=max(0.0, last-(first or last)),
                     stop_reason='max_tokens' if finish == 'MAX_TOKENS' else 'tool_use' if calls else 'stop')
        if first is not None: stats['first'] = first-start
        _usage(usage, stats, msg['content'], msg['reasoning_content'], calls)
        return msg, stats

    def complete_text(self, messages, cfg, max_tokens=None, tools=None):
        # Reuse the streamed parser and its finish/empty checks; the SDK still
        # owns HTTP/SSE. Compression may fail without corrupting the game state.
        compact = {**cfg, 'thinking': 'disabled', 'max_tokens': int(max_tokens if max_tokens is not None else cfg.get('ctx_compact_tokens', 1500))}
        compact.pop('reasoning_effort', None)
        compact['_gemini_compact'] = True
        limit = float(cfg.get('api_timeout', 900))
        compact.update(api_ttft_timeout=limit, api_chunk_timeout=limit)
        with hard_timeout(limit, 'Gemini 记忆压缩超时'):
            msg, stats = self._stream_once(messages, tools, compact)
        return msg['content'], stats
