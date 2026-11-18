"""自修订轨迹：从 stock 对局审计里还原每席的脚本版本，并把交易槽失败与当回合修订对应起来。

只读 research/game-records/ 下已归档的审计压缩包，不调用模型、不改存档。

运行：
    python experiments/self_revision/analyze.py
输出：
    experiments/self_revision/results/self_revision.json

口径：
  * 修订回合：该席当回合保存了任一交易槽（1～4，不含研究槽）的新版本，且通过自动检查。
  * 失败日：交易日收盘回执里任一交易槽状态不是 effective。休市日一律不计——Hydrogen 局的
    引擎版本在休市日仍会运行交易槽（0585416 已修复），那些失败不是模型的问题。
  * 诱发失败：失败日当回合有修订，且上一个交易日没有失败（跳过休市日往前找）。
  * 版本：按 write 全文与 patch（old 恰好出现一次时替换）重放；检查失败的源码引擎仍会保存，
    重放时照样更新当前源码，但不计为有效版本。无法重放的 patch 单列。
  * 去重：H、F 两局的审计压缩包里有同一 id 的重复记录（内容完全相同），按 id 只保留一条，
    否则重复的 write 会被误算成「与上一版逐字节相同」。
  * 编辑方式：只统计通过自动检查的 write / patch。
  * 失败分类：算力（单次回调计算量超限、共享算力池用满）、结构（语法树/源码超限）、
    市场（无对手方报价、持仓或现金不足）、其余为逻辑错误。
"""
import collections
import json
import re
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RECORDS = ROOT / 'research' / 'game-records'
OUT = Path(__file__).resolve().parent / 'results' / 'self_revision.json'
GAMES = {
    'H': 'stock-gemini-hydrogen-20261113',
    'F': 'stock-gemini38-flash-20261114',
    'Y': 'stock-hy4flashfree-20261117',
    'R1': 'stock-hy4flashfree-20261118-r1',
    'R2': 'stock-hy4flashfree-20261118-r2',
}


def closed(period):
    return period % 7 in (6, 0)


def classify(message):
    if '计算量超过' in message or '共享算力池已用满' in message:
        return 'budget'
    if '语法树节点' in message or ('源码' in message and '超限' in message):
        return 'structure'
    if '对手方没有报价' in message or '持仓不足' in message or '现金不足' in message:
        return 'market'
    return 'logic'


def load(game_dir):
    records = {}
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(game_dir / 'session.json.audit.tar.gz') as tar:
            tar.extractall(tmp, filter='data')
        for path in sorted(Path(tmp).glob('session.json.audit/*/*.json')):
            for r in json.loads(path.read_text(encoding='utf-8'))['records']:
                if records.setdefault(r['id'], r) != r:
                    raise ValueError('审计 id %s 有两条内容不同的记录' % r['id'])
    return sorted(records.values(), key=lambda r: r['id'])


def analyze(records):
    seats = collections.defaultdict(lambda: dict(
        revised=set(), wealth={}, fail={}, kinds=collections.Counter(), style=collections.Counter(),
        research_runs=0))
    current, history = {}, collections.defaultdict(list)
    for r in records:
        actor, action, receipt = r['actor'], r['action'], r['receipt']
        seat = seats[actor]
        if action == 'script':
            args = r['arguments'] if isinstance(r['arguments'], dict) else {}  # 参数格式错误的调用已被拒绝
            slot = str(args.get('slot', '1'))
            if args.get('action') == 'run' and slot == 'research':
                seat['research_runs'] += 1
                continue
            if slot == 'research' or args.get('action') not in ('write', 'patch') or not receipt['ok']:
                continue
            valid = ((receipt.get('data') or {}).get('check') or {}).get('valid') is not False
            if valid:
                seat['style'][args['action']] += 1
            key = (actor, slot)
            if args['action'] == 'write':
                source = args['source']
            else:
                base = current.get(key, '')
                if base.count(args['old']) != 1:
                    seat['kinds']['unreconstructable'] += 1
                    continue
                source = base.replace(args['old'], args['new'], 1)
            current[key] = source
            if not valid:
                seat['kinds']['rejected'] += 1
                continue
            seat['revised'].add(receipt['period'])
            versions = history[key]
            if versions and source == versions[-1]:
                seat['kinds']['identical'] += 1
            elif source in versions:
                seat['kinds']['revert'] += 1
            else:
                seat['kinds']['new'] += 1
            versions.append(source)
        elif action == 'settlement':
            data = receipt['data']
            period = data['round']
            seat['wealth'][period] = data['detail'].get('wealth')
            failures = []
            if not closed(period):
                for row in data['detail'].get('slots', []):
                    if row['status'] != 'effective':
                        m = re.search('槽' + row['slot'] + r' \w+，[^（]*（([^）]{0,80})', data['text'])
                        failures.append(classify(m.group(1) if m else data['text']))
            seat['fail'][period] = failures

    per_seat, pooled = {}, dict(rev=[0, 0, []], other=[0, 0, []])
    classes, kinds, style = collections.Counter(), collections.Counter(), collections.Counter()
    induced = failed_days = research = 0
    for actor in sorted(seats):
        seat = seats[actor]
        rounds = sorted(seat['wealth'])
        previous = 100000.0
        previous_failed = False
        for period in rounds:
            change = seat['wealth'][period] - previous
            previous = seat['wealth'][period]
            if closed(period):
                continue
            group = pooled['rev' if period in seat['revised'] else 'other']
            group[0] += 1
            group[1] += bool(seat['fail'][period])
            group[2].append(change)
            if seat['fail'][period]:
                failed_days += 1
                if period in seat['revised'] and not previous_failed:
                    induced += 1
            previous_failed = bool(seat['fail'][period])
        for period in rounds:
            classes.update(seat['fail'][period])
        kinds.update(seat['kinds'])
        style.update(seat['style'])
        research += seat['research_runs']
        per_seat[actor] = dict(rounds_revised=len(seat['revised']), versions=dict(seat['kinds']),
                               edit_style=dict(seat['style']), research_runs=seat['research_runs'],
                               failed_trading_days=sum(1 for p in rounds if seat['fail'][p]),
                               final_settled_wealth=seat['wealth'][rounds[-1]])

    def rate(group):
        days, failed, changes = group
        return dict(trading_days=days, failure_rate=round(failed / days, 4) if days else None,
                    mean_wealth_change=round(sum(changes) / days, 1) if days else None)

    revised = [s['rounds_revised'] for s in per_seat.values()]
    total_failures = sum(classes.values())
    return dict(
        rounds_revised_range=[min(revised), max(revised)],
        versions=dict(kinds), edit_style=dict(style), research_runs=research,
        revised_days=rate(pooled['rev']), other_days=rate(pooled['other']),
        failed_trading_days=failed_days, induced_failures=induced,
        failure_classes=dict(classes),
        budget_share=round(classes['budget'] / total_failures, 4) if total_failures else None,
        seats=per_seat)


def main():
    report = {}
    for label, name in GAMES.items():
        report[label] = dict(record=name, **analyze(load(RECORDS / name)))
        g = report[label]
        print('%s %s: revised rounds %s, failure rate revised %.1f%% vs other %.1f%%, '
              'induced %d/%d, budget share %.0f%%, versions %s, style %s, research %d' % (
                  label, name, g['rounds_revised_range'], 100 * g['revised_days']['failure_rate'],
                  100 * g['other_days']['failure_rate'], g['induced_failures'], g['failed_trading_days'],
                  100 * g['budget_share'], g['versions'], g['edit_style'], g['research_runs']))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    print('written', OUT)


if __name__ == '__main__':
    main()
