"""Bounded council working records over the actor-filtered, lossless archive.

Working notes are claims made by the reviewer, never engine facts. References
point to immutable archive row offsets, not diplomatic or army identifiers.
"""
from collections import Counter
from copy import deepcopy
import json

from presentation.facts import flatten

MAX_OPEN = 24
MAX_TEXT = 600
MAX_REFERENCES = 8


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def period_of(row):
    period = row.get('period', (row.get('receipt') or {}).get('period'))
    if period is None:
        period = (row.get('state') or {}).get('period')
    return period


def archive_record(row, index):
    """No recursive dialogues, model reasoning, or uncontrolled query copies."""
    result = deepcopy({k: v for k, v in row.items() if k not in ('dialogue', 'messages')})
    result['ref'] = f'row/{index}'
    result['period'] = period_of(row)
    if row.get('kind') == 'council.context':
        result['content'] = [
            {k: deepcopy(v) for k, v in message.items()
             if k in ('role', 'content', 'tool_calls', 'tool_call_id')}
            for message in row.get('messages', [])]
    return result


def current_summary(state, ref='current'):
    """Keep dated decision facts when full snapshots must be paged out."""
    if not state:
        return None
    facts = flatten(state.get('facts', {}))
    actor = state.get('actor')
    units = [u for u in facts.get('army', []) if u.get('hp', 0) > 0 and u.get('owner') != '野人']
    counts = Counter(u['owner'] for u in units)
    tiles = facts.get('view', [])
    visible = Counter(t.get('owner') or 'unowned' for t in tiles)
    econ = facts.get('econ') or {}
    bank = facts.get('bank') or {}
    relation = facts.get('diplomacy') or {}
    intel = {}
    for message in facts.get('intel', []):
        if message.get('kind') != 'spy':
            continue
        payload = message.get('payload') or {}
        source = message.get('source')
        if source not in intel or (message.get('sampled') or 0) >= (intel[source]['sampled'] or 0):
            intel[source] = {'source': source, 'sampled': message.get('sampled'),
                             'unit_counts': payload.get('unit_counts'), 'economy': payload.get('economy')}
    return {
        'actor': actor, 'period': state.get('period'),
        'scope': '本国数据完整；他国土地和军队仅当前可见子集，全国总量未知。可见0不等于全国0；旧谍报只证明采样时情况。',
        'visible_armies': dict(counts), 'visible_tiles': dict(visible),
        'economy': {key: deepcopy(econ[key]) for key in ('stocks', 'latest_turn', 'latest_report', 'latest_flows') if key in econ},
        'bank': {key: deepcopy(value) for key, value in bank.items() if key != 'purchases'},
        'purchased_reports': [{'target': p.get('report', {}).get('owner'), 'bought': p.get('period'),
                               'report_end': p.get('report', {}).get('end')}
                              for p in bank.get('purchases', [])],
        'diplomacy': {key: deepcopy(relation[key]) for key in ('alliances', 'vassals', 'pacts', 'wars', 'truces', 'offers', 'votes', 'pending') if key in relation},
        'latest_spy_per_target': list(intel.values()),
        'details': f'council_read(ref={ref}, section=army/view/econ/bank/diplomacy/intel) 分页读取该快照原文，含旧采样；摘要只列各目标最新谍报。',
    }


def trajectory_overview(rows, start=0):
    """Evidence of actions, spending opportunities and information purchases."""
    periods = {}
    for index, row in enumerate(rows, start):
        period = period_of(row)
        if period is None:
            continue
        bucket = periods.setdefault(period, {'period': period, 'first_ref': f'row/{index}',
                                             'actions': Counter(), 'spy': 0, 'buy_report': 0,
                                             'failures': 0, 'cash_min': None, 'cash_max': None})
        if row.get('kind') == 'action':
            receipt = row.get('receipt') or {}
            args = row.get('arguments') or {}
            action = args.get('action', row.get('action', ''))
            bucket['actions'][str(action)] += 1
            if receipt.get('ok') and action in ('spy', 'buy_report'):
                bucket[action] += 1
            if receipt.get('ok') is False:
                bucket['failures'] += 1
        values = []
        state_bank = flatten((row.get('state') or {}).get('facts', {})).get('bank') or {}
        if isinstance(state_bank.get('cash'), (int, float)):
            values.append(state_bank['cash'])
        for change in row.get('changes', []):
            if change.get('path') == ['facts', 'bank', 'cash'] and isinstance(change.get('set'), (int, float)):
                values.append(change['set'])
        for value in values:
            bucket['cash_min'] = value if bucket['cash_min'] is None else min(bucket['cash_min'], value)
            bucket['cash_max'] = value if bucket['cash_max'] is None else max(bucket['cash_max'], value)
    result = []
    for period in sorted(periods):
        row = periods[period]
        row['actions'] = dict(row['actions'])
        result.append(row)
    return coalesce_overview(result)


def coalesce_overview(rows, limit=24):
    # The interval can be arbitrarily long. Preserve all counts and cash
    # extrema while grouping old periods, rather than growing the prompt.
    result=deepcopy(rows)
    while len(result)>limit:
        merged=[]
        for index in range(0,len(result),2):
            group=result[index:index+2]
            if len(group)==1:
                merged.extend(group)
                continue
            first,last=group
            actions=Counter(first['actions']);actions.update(last['actions'])
            minimum=[r['cash_min'] for r in group if r['cash_min'] is not None]
            maximum=[r['cash_max'] for r in group if r['cash_max'] is not None]
            merged.append({'period':first['period'],'through':last.get('through',last['period']),
                           'first_ref':first['first_ref'],'actions':dict(actions),
                           'spy':sum(r['spy'] for r in group),'buy_report':sum(r['buy_report'] for r in group),
                           'failures':sum(r['failures'] for r in group),
                           'cash_min':min(minimum) if minimum else None,'cash_max':max(maximum) if maximum else None})
        result=merged
    return result


def tracking_view(tracking):
    active = [deepcopy(row) for row in tracking['items'] if row['status'] == 'open']
    closed = [row for row in tracking['items'] if row['status'] != 'open']
    return {'open': active, 'recent_closed': deepcopy(closed[-4:]), 'closed_count': len(closed),
            'scope': '议长的待核事项与承诺记录，不是引擎已证实事实。关闭须说明证据或合理替代；批准不自动关闭。'}


def validate_tracking(tracking):
    if not isinstance(tracking, dict) or set(tracking) != {'next_id', 'items'}:
        raise ValueError('Invalid council tracking')
    if type(tracking['next_id']) is not int or tracking['next_id'] < 1 or not isinstance(tracking['items'], list):
        raise ValueError('Invalid council tracking counters')
    ids = set()
    fields = {'id', 'kind', 'text', 'status', 'created', 'updated', 'due', 'references', 'resolution'}
    for row in tracking['items']:
        if not isinstance(row, dict) or set(row) != fields:
            raise ValueError('Invalid council tracked item')
        if type(row['id']) is not int or not 0 < row['id'] < tracking['next_id'] or row['id'] in ids:
            raise ValueError('Invalid council tracking id')
        ids.add(row['id'])
        if row['kind'] not in ('premise', 'commitment', 'risk', 'issue') or row['status'] not in ('open', 'resolved', 'superseded'):
            raise ValueError('Invalid council tracking status')
        if not isinstance(row['text'], str) or not 0 < len(row['text']) <= MAX_TEXT:
            raise ValueError('Invalid council tracking text')
        if any(type(row[key]) is not int or row[key] < 0 for key in ('created', 'updated')) or row['updated'] < row['created']:
            raise ValueError('Invalid council tracking period')
        if row['due'] is not None and (type(row['due']) is not int or row['due'] < 0):
            raise ValueError('Invalid council tracking due period')
        if not isinstance(row['resolution'], str) or len(row['resolution']) > MAX_TEXT:
            raise ValueError('Invalid council tracking resolution')
        if row['status'] != 'open' and not row['resolution'].strip():
            raise ValueError('Closed council item needs a resolution')
        if not isinstance(row['references'], list) or not 0 < len(row['references']) <= MAX_REFERENCES or any(
                not isinstance(ref, str) or not ref.startswith('row/') or not ref[4:].isdigit()
                for ref in row['references']):
            raise ValueError('Invalid council tracking references')
    if sum(row['status'] == 'open' for row in tracking['items']) > MAX_OPEN:
        raise ValueError('Too many open council items')


def update_tracking(tracking, *, period, kind, text, references, item_id=None,
                    status='open', due=None, resolution=''):
    if item_id is not None and (type(item_id) is not int or item_id < 1):
        raise ValueError('item_id 须为已有事项的正整数编号')
    candidate = deepcopy(tracking)
    previous = next((r for r in candidate['items'] if r['id'] == item_id), None)
    if item_id is not None and previous is None:
        raise ValueError('追踪事项不存在')
    if previous is None:
        item_id = candidate['next_id']
        candidate['next_id'] += 1
    row = {'id': item_id, 'kind': kind, 'text': text.strip() if isinstance(text, str) else text,
           'status': status, 'created': previous['created'] if previous else period, 'updated': period,
           'due': due, 'references': deepcopy(references), 'resolution': resolution}
    if previous:
        candidate['items'][candidate['items'].index(previous)] = row
    else:
        candidate['items'].append(row)
    validate_tracking(candidate)
    tracking.clear()
    tracking.update(candidate)
    return deepcopy(row)
