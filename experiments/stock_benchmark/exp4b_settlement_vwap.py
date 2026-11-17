"""实验 4b：终局清算价改为最后一个交易日 VWAP 后，重跑 mark-the-close 剂量探针。

沿用实验 4 的缩放协议（4000 tick/日、6 回合、2 席、种子 11/22/33，最后一个交易日买入
`yundu`），不修改实验 4。每个分组在第 1 回合开盘前设定 Market.parameters['SettlementWindow']：

  last               0.0  旧规则（最后成交价），操纵者自 tick 2100 起每 100 tick 买一次
  settle_day         1.0  新规则（全天 VWAP），同一操纵者
  last_window        0.0  旧规则，操纵者把全部买单压进最后 10%（每 20 tick 一次）
  settle_day_window  1.0  新规则，同一压尾盘操纵者

清算价取持有者 `yundu` 的实际终局平仓价（含 ±0.1%），不取每日收盘价（新规则不改收盘价）。

运行：
    python exp4b_settlement_vwap.py [workers]
输出：
    results/exp4b_settlement_vwap.json
"""
import json
import sys
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor

BENCH = str(Path(__file__).resolve().parent)
OUT = str(Path(BENCH) / 'results' / 'exp4b_settlement_vwap.json')
WORKERS = int(sys.argv[1]) if len(sys.argv) > 1 else 2
sys.path.insert(0, BENCH)

TICKS, TURNS, ROUND, SYMBOL, ORDERS = 4000, 6, 5, 'yundu', 18
SEEDS = [11, 22, 33]
DOSES = [0, 4000, 8000, 16000, 32000, 48000]
ARMS = {'last': (0.0, 100), 'settle_day': (1.0, 100), 'last_window': (0.0, 20), 'settle_day_window': (1.0, 20)}
HOLDER = 'stock:player1'


def attacker(B, dose, step):
    src = B.mark_close_source(ROUND, SYMBOL, dose / ORDERS, ORDERS)
    old_start = 'start = total - %d * 100 - 100' % ORDERS
    old_sleep = "api.call('stock:clock', 'sleep', ticks=100)"
    assert src.count(old_start) == 1 and src.count(old_sleep) == 1
    return (src.replace(old_start, 'start = total - %d * %d - %d' % (ORDERS, step, step))
               .replace(old_sleep, "api.call('stock:clock', 'sleep', ticks=%d)" % step))


def task(args):
    arm, seed, dose = args
    sys.path.insert(0, BENCH)
    import bench_lib as B
    window, step = ARMS[arm]
    # Workers are reused across jobs: wrap the pristine function once, never a previous wrapper.
    original = getattr(B, '_pristine_apply_patches', B.apply_patches)
    B._pristine_apply_patches = original

    def patched(h, patches):
        h.world.market.parameters['SettlementWindow'] = window
        return original(h, patches)
    B.apply_patches = patched
    sources = [attacker(B, dose, step) if dose else None, B.HOLD10]
    p = B.run_once(seed=seed, ticks=TICKS, turns=TURNS, sources=sources, actors=2)
    manip, holder = p['players'][B.ACTOR0], p['players'][HOLDER]
    buys = [f for r in p['rounds'] for f in r.get('fills', []) if f[1] == B.ACTOR0]
    liq = [x for x in holder['final_liquidations'] if x['symbol'] == SYMBOL]
    mark = liq[0]['price'] if liq else p['final_close'][SYMBOL]
    return dict(arm=arm, seed=seed, dose=dose, mark=mark,
                spent=round(sum(f[4] * f[5] + f[6] for f in buys), 2),
                shares=sum(f[4] for f in buys),
                manip_cash=manip['final_cash'], holder_cash=holder['final_cash'],
                elapsed=p['elapsed_sec'])


def main():
    jobs = [(arm, seed, dose) for arm in ARMS for seed in SEEDS for dose in DOSES]
    rows = []
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        for r in pool.map(task, jobs):
            rows.append(r)
            print(json.dumps(r), flush=True)
    base = {(r['arm'], r['seed']): r for r in rows if r['dose'] == 0}
    summary = {}
    for r in rows:
        if not r['dose']:
            continue
        b = base[(r['arm'], r['seed'])]
        r['mark_push_pct'] = round((r['mark'] / b['mark'] - 1) * 100, 4)
        r['manip_delta'] = round(r['manip_cash'] - b['manip_cash'], 2)
        r['holder_delta'] = round(r['holder_cash'] - b['holder_cash'], 2)
        summary.setdefault(r['arm'], {}).setdefault(r['dose'], []).append(r)
    table = {arm: {dose: dict(
        mark_push_pct=round(sum(x['mark_push_pct'] for x in xs) / len(xs), 3),
        manip_delta=round(sum(x['manip_delta'] for x in xs) / len(xs), 1),
        holder_delta=round(sum(x['holder_delta'] for x in xs) / len(xs), 1),
        spent=round(sum(x['spent'] for x in xs) / len(xs), 1))
        for dose, xs in sorted(d.items())} for arm, d in summary.items()}
    json.dump(dict(arms=ARMS, seeds=SEEDS, doses=DOSES, ticks=TICKS, turns=TURNS,
                   summary=table, rows=rows), open(OUT, 'w'), indent=1)
    print('SUMMARY', json.dumps(table))


if __name__ == '__main__':
    main()
