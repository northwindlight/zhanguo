import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request
import urllib.error

import argparse
parser = argparse.ArgumentParser(description="Measure client-visible SSE timing without publishing credentials or response text")
parser.add_argument('--config', required=True, help='Private JSON with a targets list; keep outside version control')
parser.add_argument('--output', required=True)
parser.add_argument('--prompt-file', required=True)
parser.add_argument('--repeats', type=int, default=3)
parser.add_argument('--max-tokens', type=int, default=16384)
parser.add_argument('--modes', nargs='+', choices=['no_thinking', 'game_effort'], default=['game_effort'])
args = parser.parse_args()
if not 1 <= args.repeats <= 3:
    parser.error('repeats must be between 1 and 3')
CONFIG = Path(args.config)
OUT = Path(args.output)
PROMPT = Path(args.prompt_file).read_text().strip()
specs = [(c['label'], c) for c in json.loads(CONFIG.read_text())['targets']]
results = {'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
           'prompt': PROMPT, 'repeats_per_mode': args.repeats,
           'design': 'Same prompt per target; temperature=0.3; no game history or tools; concurrent across targets, sequential within each target; client-visible SSE arrival timing.', 'max_tokens': args.max_tokens,
           'requests': []}

def run(spec, mode, repeat):
    label, cfg = spec
    anth = cfg['provider'] == 'anthropic'
    url = cfg['base_url'].rstrip('/') + ('/v1/messages' if anth else '/chat/completions')
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream'}
    if anth:
        headers.update({'x-api-key': cfg['api_key'], 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + cfg['api_key']
    body = {'model': cfg['model'], 'messages': [{'role': 'user', 'content': PROMPT}],
            'max_tokens': args.max_tokens, 'temperature': 0.3, 'stream': True,
            'thinking': {'type': 'disabled' if mode == 'no_thinking' else 'enabled'}}
    if mode != 'no_thinking':
        if anth:
            body['output_config'] = {'effort': cfg['reasoning_effort']}
        else:
            body['reasoning_effort'] = cfg['reasoning_effort']
    if not anth:
        body['stream_options'] = {'include_usage': True}
    row = {'model': label, 'mode': mode, 'repeat': repeat,
           'requested_effort': None if mode == 'no_thinking' else cfg['reasoning_effort'],
           'started_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
           'chunks': [], 'usage': {}, 'finish_reason': None}
    t0 = time.perf_counter()
    text, reason = [], []
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=180) as resp:
            row['headers_s'] = time.perf_counter() - t0
            for raw in resp:
                now = time.perf_counter() - t0
                if not raw.startswith(b'data:'):
                    continue
                data = raw[5:].strip()
                if data == b'[DONE]':
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get('error'):
                    row['error'] = {'type': 'stream_error'}
                    break
                fragments = []
                if anth:
                    typ = obj.get('type')
                    if typ == 'message_start':
                        row['usage'].update((obj.get('message') or {}).get('usage') or {})
                    elif typ == 'message_delta':
                        row['usage'].update(obj.get('usage') or {})
                        row['finish_reason'] = (obj.get('delta') or {}).get('stop_reason')
                    elif typ == 'content_block_delta':
                        delta = obj.get('delta') or {}
                        if delta.get('text'):
                            fragments.append(('text', delta['text']))
                        if delta.get('thinking'):
                            fragments.append(('reason', delta['thinking']))
                else:
                    if obj.get('usage'):
                        row['usage'].update(obj['usage'])
                    for choice in obj.get('choices') or []:
                        if choice.get('finish_reason'):
                            row['finish_reason'] = choice['finish_reason']
                        delta = choice.get('delta') or {}
                        for key, kind in [('content', 'text'), ('reasoning_content', 'reason')]:
                            if delta.get(key):
                                fragments.append((kind, delta[key]))
                for kind, fragment in fragments:
                    (text if kind == 'text' else reason).append(fragment)
                    row['chunks'].append({'t_s': now, 'kind': kind, 'chars': len(fragment)})
        row['wall_s'] = time.perf_counter() - t0
    except Exception as exc:
        row['wall_s'] = time.perf_counter() - t0
        row['error'] = {'type': type(exc).__name__}
        if isinstance(exc, urllib.error.HTTPError):
            row['error']['http_status'] = exc.code
    row['usage'].pop('credits', None)
    answer, thought = ''.join(text), ''.join(reason)
    row.update({'text_chars': len(answer), 'reason_chars': len(thought),
                'text_sha256': hashlib.sha256(answer.encode()).hexdigest(),
                'line_count': len(answer.strip().splitlines())})
    for kind in ['text', 'reason']:
        chunks = [c for c in row['chunks'] if c['kind'] == kind]
        if chunks:
            row[f'{kind}_first_s'] = chunks[0]['t_s']
            row[f'{kind}_last_s'] = chunks[-1]['t_s']
            span = chunks[-1]['t_s'] - chunks[0]['t_s']
            row[f'{kind}_span_s'] = span
            row[f'{kind}_chunks'] = len(chunks)
            # Exclude first chunk from the numerator: it was generated before timing starts.
            row[f'{kind}_chars_per_s'] = (sum(c['chars'] for c in chunks[1:]) / span) if span > 0 else None
    return row

def worker(spec):
    rows = []
    for mode in args.modes:
        for repeat in range(1, args.repeats + 1):
            row = run(spec, mode, repeat)
            rows.append(row)
            small = {k: v for k, v in row.items() if k not in ['chunks', 'text_sha256']}
            print(json.dumps(small, ensure_ascii=False), flush=True)
    return rows

OUT.parent.mkdir(parents=True, exist_ok=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
    for rows in pool.map(worker, specs):
        results['requests'].extend(rows)
results['finished_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
print('SAVED ' + str(OUT), flush=True)
