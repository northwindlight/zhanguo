"""Recount visible action receipts; validate successful trades/buildings against save.

Read-only for journal.md and state.json.gz. Writes report-count-audit.json.
"""
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent
names = dict(zip(['qin','wei','han','zhao','yan','qi','chu','zhou'],'秦魏韩赵燕齐楚周'))
resources = dict(zip(['wood','food','ore','oil','equipment','supply'],['木头','粮食','矿石','石油','装备','补给']))
counts, trades, buildings = (defaultdict(Counter) for _ in range(3))
calls, seconds = Counter(), Counter()
history_left, news, current = 0, False, None
excluded = Counter()
for number, line in enumerate((ROOT/'journal.md').open(),1):
    heading = re.match(r'^### 第 (\d+) 回合 · (\S+) 行动', line)
    if heading:
        current = (int(heading[1]),heading[2])
    if line.startswith(('◈','⏳','### 第 ')):
        assert not history_left, ('incomplete replay', number,history_left)
        history_left, news = 0, False
    call = re.match(r'^◈ (\S+) 第 \d+ 次调用',line)
    if call:
        calls[call[1]] += 1
    duration = re.match(r'^✓ (\S+) 行动结束，用时 ([\d.]+) 秒',line)
    if duration:
        seconds[duration[1]] += float(duration[2])
    if history_left:
        if re.match(r'^\[\d+·',line):
            history_left -= 1
        if re.match(r'^\[\d+·行动\]',line):
            excluded['recent_events_action_rows'] += 1
        continue
    if news:
        if re.match(r'^\[\d+·行动\]',line):
            excluded['news_action_rows'] += 1
        continue
    match = re.match(r'^\[(\d+)·行动\] (\S+) ◇ (\S+)',line)
    if not match:
        continue
    turn,country,action = match.groups()
    assert current == (int(turn),country), ('out of context action',number)
    counts[country]['actions'] += 1
    counts[country][action] += 1
    if '❌' in line:
        counts[country]['failed'] += 1
    build = re.match(r'^\[\d+·行动\] \S+ ◇ 建造 (\S+).*?→ 动工.*?−(\d+)金 −(\d+)木',line)
    if build:
        counts[country]['build_success'] += 1
        counts[country]['build_gold'] += int(build[2])
        counts[country]['build_wood'] += int(build[3])
        buildings[country][build[1]] += 1
    trade = re.match(r'^\[\d+·行动\] (\S+) ◇ (售出|购入) (\S+)×(\d+).*?实[收付] (\d+)',line)
    if trade:
        _,side,resource,quantity,gold = trade.groups()
        trades[country][side+resource] += int(quantity)
        trades[country][side+'金'] += int(gold)
        trades[country][side+resource+'金'] += int(gold)
    if action == '近期事件':
        total = re.search(r'共 (\d+) 条',line)
        assert total, number
        history_left = int(total[1])
    if action == 'query' and 'panel=news' in line:
        news = True

assert all(counts[c]['结束回合'] == 51 for c in names.values())
snapshot = json.loads(gzip.decompress((ROOT/'state.json.gz').read_bytes()))
saved_trades = defaultdict(Counter)
for row in snapshot['modules']['native:market']['trades']:
    country = names[row['owner'].split(':')[1]]
    side = '购入' if row['side']=='buy' else '售出'
    resource = resources[row['resource'].split(':')[1]]
    saved_trades[country][side+resource] += row['quantity']
    saved_trades[country][side+'金'] += row['gold']
    saved_trades[country][side+resource+'金'] += row['gold']
assert dict(trades) == dict(saved_trades), 'journal and saved market trades disagree'
site_owners = {r['id']:r['owner'] for r in snapshot['modules']['native:buildings']['sites']}
saved_building_counts = Counter(names[site_owners[r['site']].split(':')[1]]
                                for r in snapshot['modules']['native:buildings']['instances'])
assert all(saved_building_counts[c] == counts[c]['build_success']+1 for c in names.values())
result = {'schema':'game-visible-action-audit/1','journal_sha256':hashlib.sha256((ROOT/'journal.md').read_bytes()).hexdigest(),
          'method':'Consume the exact reported number of replayed events, including multiline plans; exclude news response bodies until next call; count rejected trades as attempts but never as executed quantities.',
          'counts':counts,'buildings_started':buildings,'successful_trades':trades,
          'calls':calls,'action_wall_seconds':seconds,'excluded':excluded,
          'checks':{'successful_trades_equal_saved_market':True,'builds_plus_initial_equal_saved_instances':True,
                    'end_turn_receipts_per_country':51,'saved_building_instances':sum(saved_building_counts.values()),
                    'saved_market_trade_rows':len(snapshot['modules']['native:market']['trades'])}}
(ROOT/'report-count-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'actions':sum(c['actions'] for c in counts.values()),'failed':sum(c['failed'] for c in counts.values()),'builds':sum(c['build_success'] for c in counts.values()),'excluded':excluded,'checks':result['checks']},ensure_ascii=False))
