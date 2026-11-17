"""真实对局的记录重估：同一批已入账消费，分别按当期估值 S 与基准价 A 加总。

只读取 Qwen／DeepSeek 120 边界局公开审计包中的 native_economy 历史分段，
不解压到仓库、不载入存档、不推进游戏或调用模型。审计分段覆盖第 0—99 期；
第 100—119 期的事实仍在 session.json 内，本程序不涉及。基准价取生成该局的
提交 280296c 中市场声明的 BasePrice，金为计价物。
"""
from collections import Counter, defaultdict
from hashlib import sha256
import json
from math import isclose
from pathlib import Path
import subprocess
import tarfile
import tomllib

ROOT = Path(__file__).resolve().parents[2]
GAME = ROOT / 'research/game-records/eight-nations-dq-120'
AUDIT = GAME / 'session.json.audit.tar.gz'
GAME_COMMIT = '280296c'
CONSUMPTION = ('recruit', 'military', 'administration')
NAMES = dict(zip(('qin', 'wei', 'han', 'zhao', 'yan', 'qi', 'chu', 'zhou'), '秦魏韩赵燕齐楚周'))
# 仓库已有对照表 comparisons/dq-vs-hydrogen-0-33-20261114 的第 33 期八国累计消费
KNOWN_CUMULATIVE_33 = 10135.75


def base_prices():
    text = subprocess.check_output(['git', 'show', f'{GAME_COMMIT}:mods/native/market/mod.toml'],
                                   cwd=ROOT, text=True)
    prices = {row['Resource']: float(row['BasePrice']) for row in tomllib.loads(text)['Listings']}
    prices['native:gold'] = 1.0
    return prices


def read_history():
    facts, turns, files = {}, {}, []
    with tarfile.open(AUDIT) as archive:
        for member in sorted(archive.getmembers(), key=lambda m: m.name):
            if '/native_economy/' not in member.name or not member.isfile():
                continue
            history = json.load(archive.extractfile(member))
            assert history['format'] == 'zhanguo.domain-history/1' and history['module'] == 'native:economy'
            files.append(member.name.rsplit('/', 1)[1])
            for entry in history['records']:
                row = entry['value']
                key = row['fact']['id'] if entry['kind'] == 'fact' else (row['period'], row['owner'])
                target = facts if entry['kind'] == 'fact' else turns
                assert target.get(key, row) == row, ('Conflicting history', key)
                target[key] = row
    return facts, turns, files


def valuation_prices(facts):
    """从单一非金资源的事实反推每期估值价，并检查同期同资源只有一个价。"""
    prices = defaultdict(set)
    for row in facts.values():
        fact = row['fact']
        for side in ('inputs', 'outputs'):
            other = [(r, q) for r, q in fact[side] if r != 'native:gold']
            gold = sum(q for r, q in fact[side] if r == 'native:gold')
            if len(other) == 1 and other[0][1] > 0:
                prices[(fact['period'], other[0][0])].add(round((row[side]['known'] - gold) / other[0][1], 9))
            elif not other and fact[side]:
                assert isclose(row[side]['known'], gold, abs_tol=1e-9), ('Gold is not numeraire', fact['id'])
    assert all(len(v) == 1 for v in prices.values()), 'More than one valuation price in a period'
    return {key: next(iter(v)) for key, v in prices.items()}


def main():
    base = base_prices()
    facts, turns, files = read_history()
    periods = sorted({row['fact']['period'] for row in facts.values()})
    unpriced = Counter(f"{row['fact']['category']}:{side}" for row in facts.values()
                       for side in ('inputs', 'outputs') if row[side]['unpriced'])
    unpriced.update(f'turn:{key}' for row in turns.values() for key, value in row.items()
                    if isinstance(value, dict) and value.get('unpriced'))

    # 完整性：每份期末报表的 spending 等于该国该期四类事实估值之和。
    spent = defaultdict(float)
    for row in facts.values():
        fact = row['fact']
        if fact['category'] in (*CONSUMPTION, 'construction'):
            spent[(fact['period'], fact['owner'])] += row['inputs']['known']
    for key, turn in turns.items():
        assert isclose(spent.pop(key, 0.0), turn['spending']['known'], abs_tol=1e-7), ('Report mismatch', key)
    orphan = {f'{p}:{o}': v for (p, o), v in spent.items() if v}

    prices = valuation_prices(facts)
    S, A = defaultdict(float), defaultdict(float)
    by_category = defaultdict(lambda: defaultdict(float))
    fees = defaultdict(lambda: defaultdict(float))
    quantities = defaultdict(lambda: defaultdict(float))
    cumulative_33 = 0.0
    for row in facts.values():
        fact = row['fact']
        if fact['category'] not in CONSUMPTION or not fact['owner'].startswith('scenario:'):
            continue
        nation = NAMES[fact['owner'].split(':')[1]]
        value = row['inputs']['known']
        S[nation] += value
        A[nation] += sum(base[r] * q for r, q in fact['inputs'])
        by_category[nation][fact['category']] += value
        if fact['category'] == 'administration':
            fees[nation][fact['id'].split('/')[0].split(':')[1]] += value
        for resource, quantity in fact['inputs']:
            quantities[nation][resource.split(':')[1]] += quantity
        if fact['period'] <= 33:
            cumulative_33 += value
    assert isclose(cumulative_33, KNOWN_CUMULATIVE_33, abs_tol=0.005), cumulative_33

    def rank(values):
        return {n: i + 1 for i, n in enumerate(sorted(values, key=values.get, reverse=True))}

    rank_s, rank_a = rank(S), rank(A)
    rank_core = rank({n: S[n] - by_category[n]['administration'] for n in S})
    nations = [{'nation': n, 'S_current_price': S[n], 'A_base_price': A[n], 'price_contribution': S[n] - A[n],
                'price_contribution_share_of_A': S[n] / A[n] - 1, 'rank_S': rank_s[n], 'rank_A': rank_a[n],
                'S_without_administration': S[n] - by_category[n]['administration'],
                'rank_S_without_administration': rank_core[n],
                'category_values': dict(by_category[n]), 'administration_fees': dict(fees[n]),
                'administration_share_of_S': by_category[n]['administration'] / S[n],
                'quantities': dict(quantities[n])}
               for n in sorted(S, key=S.get, reverse=True)]
    paths = {r.split(':')[1]: {p: prices[(p, r)] for p in periods if (p, r) in prices}
             for r in ('native:supply', 'native:food', 'native:equipment')}
    result = {
        'kind': 'recorded_consumption_revaluation',
        'game': str(GAME.relative_to(ROOT)), 'game_commit': GAME_COMMIT,
        'audit_sha256': sha256(AUDIT.read_bytes()).hexdigest(), 'audit_files': files,
        'periods': [periods[0], periods[-1]], 'facts': len(facts), 'turn_reports': len(turns),
        'base_prices': base, 'unpriced_records': dict(unpriced),
        'reports_match_facts': True, 'facts_without_report': orphan,
        'cumulative_consumption_through_33': cumulative_33,
        'totals': {'S': sum(S.values()), 'A': sum(A.values())},
        'rank_changes': sorted(n for n in S if rank_s[n] != rank_a[n]),
        'nations': nations,
        'valuation_price_ranges': {r: [min(v.values()), max(v.values())] for r, v in paths.items()},
        'valuation_prices': paths,
    }
    target = Path(__file__).with_name('record_revaluation_results.json')
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(f"periods {result['periods']}, facts {len(facts)}, reports {len(turns)}, unpriced {dict(unpriced)}")
    print('国 | S | A | S/A−1 | 名次S | 名次A | 行政占S | 去行政名次')
    for row in nations:
        print(f"{row['nation']} | {row['S_current_price']:.1f} | {row['A_base_price']:.1f} | "
              f"{row['price_contribution_share_of_A']:+.1%} | {row['rank_S']} | {row['rank_A']} | "
              f"{row['administration_share_of_S']:.1%} | {row['rank_S_without_administration']}")
    print('totals', result['totals'], 'price ranges', result['valuation_price_ranges'])


if __name__ == '__main__':
    main()
