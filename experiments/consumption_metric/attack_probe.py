"""E1 攻击性验证（现行引擎）。历史版本见 `717a12f:experiments/spend_metric_probes.py`。

与历史的区别（引用数字时必须一并说明）：
  · 引擎为模块化重写后的现行树，指标字段 `spend_total` → `TurnReport.cumulative_spending`
  · 基线由历史 v6 换成现行 v11plus（走同一 Player 契约）
  · 开局用发行版真实起点（5 格 + 市政厅；金 1500 / 粮 20 / 木 60 / 矿 10 / 装备 5 / 补给 10）
  · 攻击策略按现行动作词汇重写（build/recruit/buy/sell），不再调用旧 World API

    .venv/bin/python experiments/consumption_metric/attack_probe.py --seeds 0,1 --turns 300
    # 分片并行：--seeds 一个值一次，多进程跑
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics as st
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from engine.runtime import Runtime                                    # noqa: E402
from host import GameHost                                             # noqa: E402

from experiments.consumption_metric.attack_clients import POLICIES    # noqa: E402

ENTRY = 'experiments.consumption_metric.attack_clients:AttackClient'
CONFIGURATION = 'experiments.consumption_metric.attack_clients:normalize_config'
BASELINE = 'v11plus'          # 现役构建（丢了原版经济层，见 attack_notes.md §1c）
STRATEGIES = (BASELINE,) + tuple(POLICIES)
OWNER = 'scenario:qin'
PATCHED = tuple(POLICIES)     # 需要额外注册客户端种类的策略


def attack_runtime():
    """在发行版内存副本里补两个客户端种类，不改任何随仓文件。"""
    runtime = Runtime.load(ROOT / 'mods.toml')
    extra = {'attack': {'Entry': ENTRY, 'Configuration': CONFIGURATION}}
    factories = runtime.distribution.get('ClientFactories')
    try:
        factories.update(extra)
        return runtime
    except TypeError:                      # 只读映射：整体重建
        merged = {**runtime.distribution,
                  'ClientFactories': {**dict(factories or {}), **extra}}
        return Runtime(list(runtime.units.values()), merged)


def nation_cfg(strategy):
    if strategy == BASELINE:
        return {'id': OWNER, 'name': '秦', 'client': 'rule', 'rule_ai': BASELINE}
    return {'id': OWNER, 'name': '秦', 'client': 'attack', 'attack': strategy}


def run(strategy, seed, size, turns):
    cfg = {'mods': 'mods.toml', 'map_size': size, 'seed': seed, 'nations': [nation_cfg(strategy)]}
    runtime = attack_runtime() if strategy in PATCHED else None
    host = GameHost.new(cfg, runtime=runtime) if runtime is not None else GameHost.new(cfg)
    started = time.perf_counter()
    for _ in range(turns):
        host.cycle()
    turns_rows = host.world.economy.turns(OWNER)
    last = turns_rows[-1]

    def cumulative(attr):
        total, missing = 0.0, False
        for row in turns_rows:
            money = getattr(row, attr)
            missing = missing or not money.complete
            total += money.known
        return total, missing

    construction, c_missing = cumulative('investment')
    recruitment, r_missing = cumulative('recruitment')
    military, mil_missing = cumulative('military')
    complete = last.cumulative_spending.complete and not (c_missing or r_missing or mil_missing)
    spending = last.cumulative_spending.known
    return {'strategy': strategy, 'seed': seed, 'size': size, 'turns': turns,
            'elapsed_seconds': time.perf_counter() - started,
            'cumulative_spending': spending,
            'construction': construction,
            'recruitment': recruitment,
            'military': military,
            'administration': spending - construction - recruitment - military,
            'complete_valuation': complete,
            'liabilities': last.liabilities,
            'land': len(host.world.territory.owned(OWNER))}


def report(rows):
    by = {}
    for row in rows:
        by.setdefault(row['strategy'], []).append(row)
    base = sorted(r['cumulative_spending'] for r in by.get(BASELINE, []))
    base_median = st.median(base) if base else None
    print(f"\n{'策略':<14}{'消费·中位':>12}{'各 seed(k)':<34}{'建造':>9}{'征兵':>8}"
          f"{'军费':>8}{'行政':>8}{'领土':>7}{'相对基线':>10}")
    for strategy in STRATEGIES:
        group = by.get(strategy, [])
        if not group:
            continue
        values = sorted(r['cumulative_spending'] for r in group)
        median = st.median(values)
        rel = f'{median / base_median:>9.1%}' if base_median else ''
        seeds = ' '.join(f'{v / 1000:.1f}' for v in values)
        mean = lambda key: st.mean(r[key] for r in group)            # noqa: E731
        print(f"{strategy:<14}{median:>12,.0f}  {seeds:<32}{mean('construction'):>9,.0f}"
              f"{mean('recruitment'):>8,.0f}{mean('military'):>8,.0f}"
              f"{mean('administration'):>8,.0f}{mean('land'):>7.1f}{rel}")
    if base:
        worst = min(base)
        print(f"\n判据（绕开误差棒）：诚实基线最差的一次 {worst:,.0f}")
        for strategy in STRATEGIES:
            if strategy == BASELINE or strategy not in by:
                continue
            best = max(r['cumulative_spending'] for r in by[strategy])
            print(f"  {strategy:<14} 最好一次 {best:>10,.0f}  = 基线最差一次的 {best / worst:>6.1%}")


def main():
    ap = argparse.ArgumentParser(description='E1 攻击性验证（现行引擎）')
    ap.add_argument('--seeds', default='0')
    ap.add_argument('--strategies', default=','.join(STRATEGIES))
    ap.add_argument('--size', type=int, default=16)
    ap.add_argument('--turns', type=int, default=300)
    ap.add_argument('--out', default=str(HERE / 'attack_results_e1.json'))
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(',')]
    strategies = [s for s in args.strategies.split(',') if s]
    rows = []
    for strategy in strategies:
        for seed in seeds:
            row = run(strategy, seed, args.size, args.turns)
            rows.append(row)
            print(json.dumps({k: (round(v, 2) if isinstance(v, float) else v)
                              for k, v in row.items() if k != 'turns'}, ensure_ascii=False), flush=True)
    path = Path(args.out)
    existing = json.loads(path.read_text(encoding='utf-8'))['rows'] if path.exists() else []
    keep = [r for r in existing if not any(r['strategy'] == n['strategy'] and r['seed'] == n['seed']
                                          and r['turns'] == n['turns'] for n in rows)]
    path.write_text(json.dumps({'size': args.size, 'turns': args.turns, 'rows': keep + rows},
                               ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report(keep + rows)
    print(f'\nWrote {path}')


if __name__ == '__main__':
    main()
