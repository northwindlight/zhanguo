"""实验 1：确定性 / 重放。

问题（至少两条独立轴）：
1) 同 seed + 同脚本序列（含每回合买一档算力池的资源采购动作）跑两遍，
   逐回合账本、成交序列、世界状态哈希是否逐字节相同；
2) planning_workers=1（严格串行）vs 4（并发盘前规划）是否得到同一世界。

运行：
    python exp1_determinism.py
输出：
    results/exp1_determinism.json
"""
import json
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp1_determinism.json'

CASES = [
    dict(name='single_actor_pool_replay', seed=999, ticks=2000, turns=6,
         sources=[B.CHURN], actors=1, purchase_pool=True,
         note='单席位 + 每回合买算力池（资源采购动作）的往返脚本'),
    dict(name='two_actor_mixed_replay', seed=1234, ticks=2000, turns=6,
         sources=[B.CHURN, B.HOLD10], actors=2, purchase_pool=True,
         note='两席位（往返 + 买入持有），每回合 ACTOR0 买算力池'),
]

WORKER_CASE = dict(name='four_actor_worker_parallelism', seed=20261116, ticks=1200, turns=6,
                   sources=[B.CHURN, B.HOLD10, B.MA, B.NOISE], actors=4,
                   purchase_pool=False,
                   note='四席位混合脚本：planning_workers=1 vs 4 的世界一致性')


def canonical(payload):
    """重放比较用的规范化账本（剔除耗时、绝对秒数等非语义字段）。"""
    keep = dict(seed=payload['seed'], ticks=payload['ticks'], turns=payload['turns'],
                sources=payload['sources'], actors=payload['actors'],
                patches=payload['patches'], purchase_pool=payload['purchase_pool'],
                rounds=payload['rounds'],
                players=payload['players'],
                final_close=payload['final_close'])
    return json.dumps(keep, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def compare(label, a, b):
    ca, cb = canonical(a), canonical(b)
    same_ledger = ca == cb
    same_ledger_hash = a['ledger_hash'] == b['ledger_hash']
    same_fill_hash = a['fill_hash'] == b['fill_hash']
    same_world = a.get('final_world_hash') == b.get('final_world_hash')
    first_diff = None
    if not same_ledger:
        for i, (ra, rb) in enumerate(zip(a['rounds'], b['rounds'])):
            if json.dumps(ra, sort_keys=True) != json.dumps(rb, sort_keys=True):
                first_diff = dict(kind='round', index=i, round=ra.get('round'))
                break
        if first_diff is None:
            for actor in a['players']:
                pa = json.dumps(a['players'][actor], sort_keys=True)
                pb = json.dumps(b['players'][actor], sort_keys=True)
                if pa != pb:
                    first_diff = dict(kind='player', actor=actor)
                    break
    return dict(label=label,
                identical_bytes=same_ledger,
                ledger_hash_identical=same_ledger_hash,
                fill_hash_identical=same_fill_hash,
                final_world_hash_identical=same_world,
                ledger_sha256={'run_a': B.digest(ca), 'run_b': B.digest(cb)},
                ledger_hash={'run_a': a['ledger_hash'], 'run_b': b['ledger_hash']},
                fill_hash={'run_a': a['fill_hash'], 'run_b': b['fill_hash']},
                final_cash={'run_a': {k: v['final_cash'] for k, v in a['players'].items()},
                            'run_b': {k: v['final_cash'] for k, v in b['players'].items()}},
                first_diff=first_diff)


def run_case(case, **kwargs):
    return B.run_once(seed=case['seed'], ticks=case['ticks'], turns=case['turns'],
                      sources=case['sources'], actors=case['actors'],
                      purchase_pool=case.get('purchase_pool', False),
                      keep_background=True, world_hashes=True, **kwargs)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report = dict(protocol=dict(engine='mods.stock.toml (PAMS 0.3.0)',
                                source='本地离线 stock 发行版，零模型调用',
                                comparison='规范化 JSON 逐字节相等 + sha256'),
                  cases=[], worker_case=None, all_pass=True)
    for case in CASES:
        print('[exp1] replay', case['name'])
        a = run_case(case)
        b = run_case(case)
        row = compare(case['name'], a, b)
        row.update(seed=case['seed'], ticks=case['ticks'], turns=case['turns'],
                   actors=case['actors'], purchase_pool=case.get('purchase_pool'),
                   pool_purchases_ok=[r['ok'] for r in a['pool_receipts']],
                   elapsed_sec=[a['elapsed_sec'], b['elapsed_sec']],
                   note=case['note'])
        report['cases'].append(row)
        report['all_pass'] = report['all_pass'] and row['identical_bytes'] and row['fill_hash_identical']
        print('   identical:', row['identical_bytes'], 'fill_hash:', row['fill_hash_identical'])

    print('[exp1] workers 1 vs 4', WORKER_CASE['name'])
    serial = run_case(WORKER_CASE, planning_workers=1)
    parallel = run_case(WORKER_CASE, planning_workers=4)
    row = compare('planning_workers_1_vs_4', serial, parallel)
    row.update(seed=WORKER_CASE['seed'], ticks=WORKER_CASE['ticks'], turns=WORKER_CASE['turns'],
               actors=WORKER_CASE['actors'],
               elapsed_sec={'workers_1': serial['elapsed_sec'], 'workers_4': parallel['elapsed_sec']},
               note=WORKER_CASE['note'])
    report['worker_case'] = row
    report['all_pass'] = report['all_pass'] and row['identical_bytes'] and row['fill_hash_identical']
    print('   identical:', row['identical_bytes'], 'fill_hash:', row['fill_hash_identical'])

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[exp1] written', OUT, 'all_pass =', report['all_pass'])


if __name__ == '__main__':
    main()
