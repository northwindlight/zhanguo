"""实验 4：结算价（last executed price）可操纵性探针。

机制：终局清算 `portfolio.finalize` 用 `market.price_of(symbol)`，
而 SESSION 期间的 price_of 是 PAMS 的「最后成交价」；收盘 `market.settle`
把 close 固定成这个最后成交价。因此终局清算现金 = 现金 − 贷款 + 仓位 ×
（最后成交价 × (1∓0.1%) − 佣金）。问题：一个席位能否在最后一个交易日
用很少的钱把 mark 推高，从而改变自己、以及同场「持有不动」席位的清算现金？

协议（缩放，只作机制探针）：session_ticks=4000、max_turns=6（5 个交易日，
第 6 回合只清算）。两个席位：
  * A（操纵者）：前 4 个回合不动作；第 5 回合从 tick≈2100 起，每 100 tick
    用市价单买入 `yundu`（流通性最差之一，liq=0.5），共 18 笔，每笔预算
    dose/18；另一组对照不写脚本。
  * B（持有者）：第 1 回合收盘附近等权买入 10 只初始股，之后不动。
剂量：总预算 {4000, 8000, 16000, 32000, 48000}；种子 {11, 22, 33}。

运行：
    python exp4_mark_close.py
输出：
    results/exp4_mark_close.json
"""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp4_mark_close.json'
TICKS = 4000
TURNS = 6
MANIP_ROUND = 5          # max_turns=6 → 第 5 回合是最后一个交易日
SYMBOL = 'yundu'
ORDERS = 18
SEEDS = [11, 22, 33]
DOSES = [4000, 8000, 16000, 32000, 48000]
HOLDER = 'stock:player1'


def task(args):
    seed, dose = args
    manip = B.mark_close_source(MANIP_ROUND, SYMBOL, dose / ORDERS if dose else 0, ORDERS)
    sources = [manip if dose else None, B.HOLD10]
    payload = B.run_once(seed=seed, ticks=TICKS, turns=TURNS, sources=sources, actors=2)
    return seed, dose, payload


def row(seed, dose, payload):
    manip = payload['players'][B.ACTOR0]
    holder = payload['players'][HOLDER]
    mark = payload['final_close'].get(SYMBOL)
    holder_liq = {x['symbol']: x for x in holder['final_liquidations']}
    held_yundu = holder_liq.get(SYMBOL, {}).get('quantity', 0)
    buys = [f for r in payload['rounds'] for f in r.get('fills', []) if f[1] == B.ACTOR0]
    spent = sum(f[4] * f[5] for f in buys) + sum(f[6] for f in buys)
    shares = sum(f[4] for f in buys)
    other_end = {s: p for s, p in payload['final_close'].items() if s != SYMBOL}
    return dict(
        seed=seed, dose=dose, mark=mark,
        manip_final_cash=manip['final_cash'],
        manip_bought_shares=shares,
        manip_spent=round(spent, 2),
        manip_bought_amount=manip['bought_amount'],
        manip_fees=round(manip['fill_fees'] + manip['final_liq_fees'], 2),
        manip_bought_yundu=sum(f[4] for f in buys if f[3] == SYMBOL),
        manip_avg_cost=round(manip['bought_amount'] / shares, 4) if shares else None,
        holder_final_cash=holder['final_cash'],
        holder_yundu_shares=held_yundu,
        holder_other_closes=other_end,
    )


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tasks = [(seed, dose) for seed in SEEDS for dose in [0] + DOSES]
    print('[exp4] %d games (ticks=%d, turns=%d)' % (len(tasks), TICKS, TURNS))
    runs = {}
    with ProcessPoolExecutor(max_workers=6) as pool:
        for seed, dose, payload in pool.map(task, tasks):
            runs[(seed, dose)] = payload
            print('  done seed', seed, 'dose', dose, 'elapsed', payload['elapsed_sec'])

    rows = []
    response = []
    for seed in SEEDS:
        control = runs[(seed, 0)]
        base_mark = control['final_close'][SYMBOL]
        base_manip = control['players'][B.ACTOR0]['final_cash']
        base_holder = control['players'][HOLDER]['final_cash']
        holder_liq = {x['symbol']: x for x in
                      control['players'][HOLDER]['final_liquidations']}
        held_yundu = holder_liq.get(SYMBOL, {}).get('quantity', 0)
        base_other = {s: p for s, p in control['final_close'].items() if s != SYMBOL}
        weird = False
        for dose in DOSES:
            payload = runs[(seed, dose)]
            r = row(seed, dose, payload)
            push = round((r['mark'] / base_mark - 1) * 100, 4)
            r.update(
                mark_push_pct=push,
                manip_delta=round(r['manip_final_cash'] - base_manip, 2),
                holder_delta=round(r['holder_final_cash'] - base_holder, 2),
                holder_predicted_delta=round(
                    held_yundu * (r['mark'] - base_mark) * 0.999 * 0.999, 2),
                holder_yundu_mark_gain=round(held_yundu * (r['mark'] - base_mark), 2),
                other_mark_max_abs_change_pct=round(max(
                    [abs(r['holder_other_closes'][s] / base_other[s] - 1) * 100
                     for s in base_other if s in r['holder_other_closes']] or [0.0]), 4),
                cost_per_mark_point=round(r['manip_spent'] / push, 2) if push else None,
            )
            # 前 4 个交易日的收盘必须逐字段一致，否则说明有别的渠道被扰动
            early_control = [(x['round'], x['close']) for x in control['rounds'] if x['round'] <= 4]
            early_treat = [(x['round'], x['close']) for x in payload['rounds'] if x['round'] <= 4]
            r['early_rounds_identical'] = early_control == early_treat
            weird = weird or not r['early_rounds_identical']
            rows.append(r)
            response.append(dict(seed=seed, dose=dose,
                                 mark_push_pct=r['mark_push_pct'],
                                 manip_spent=r['manip_spent'],
                                 manip_bought_shares=r['manip_bought_shares'],
                                 manip_delta=r['manip_delta'],
                                 holder_delta=r['holder_delta'],
                                 holder_yundu_mark_gain=r['holder_yundu_mark_gain'],
                                 cost_per_mark_point=r['cost_per_mark_point']))
        for r in rows:
            if r['seed'] == seed:
                r['held_yundu_control'] = held_yundu
                r['control_mark'] = base_mark

    def group(key):
        out = {}
        for r in rows:
            out.setdefault(r[key], []).append(r)
        return out

    by_dose = group('dose')
    dose_summary = {}
    for dose, group_rows in sorted(by_dose.items()):
        dose_summary[str(dose)] = dict(
            dose=dose, n=len(group_rows),
            mark_push_pct=B._stats([r['mark_push_pct'] for r in group_rows]),
            manip_spent=B._stats([r['manip_spent'] for r in group_rows]),
            manip_bought_shares=B._stats([r['manip_bought_shares'] for r in group_rows]),
            manip_delta=B._stats([r['manip_delta'] for r in group_rows]),
            holder_delta=B._stats([r['holder_delta'] for r in group_rows]),
            holder_yundu_mark_gain=B._stats([r['holder_yundu_mark_gain'] for r in group_rows]),
            holder_predicted_delta=B._stats([r['holder_predicted_delta'] for r in group_rows]),
            cost_per_mark_point=B._stats([r['cost_per_mark_point'] for r in group_rows]),
            other_mark_max_abs_change_pct=B._stats(
                [r['other_mark_max_abs_change_pct'] for r in group_rows]),
            early_rounds_identical=all(r['early_rounds_identical'] for r in group_rows),
        )
    report = dict(
        protocol=dict(engine='mods.stock.toml (PAMS 0.3.0)', ticks=TICKS, turns=TURNS,
                      manip_round=MANIP_ROUND, symbol=SYMBOL, orders=ORDERS,
                      seeds=SEEDS, doses=DOSES,
                      note='缩放协议：2000→4000 tick/日、60→6 回合；只作结算价可操纵性的方向与量级探针'),
        control=dict(mark={str(seed): runs[(seed, 0)]['final_close'][SYMBOL] for seed in SEEDS},
                     manip_final_cash={str(seed): runs[(seed, 0)]['players'][B.ACTOR0]['final_cash']
                                      for seed in SEEDS},
                     holder_final_cash={str(seed): runs[(seed, 0)]['players'][HOLDER]['final_cash']
                                        for seed in SEEDS}),
        dose_summary=dose_summary, response=response, rows=rows,
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[exp4] dose summary', json.dumps(dose_summary, ensure_ascii=False))
    print('[exp4] written', OUT)


if __name__ == '__main__':
    main()
