"""实验 3：机制干预（制度反事实）。

3a 佣金：Commission=0 vs 0.001，同一高频往返（churn）脚本。
   命题：往返本身是零和转移；佣金是系统抽水。
   * 佣金 0 时，玩家亏的钱应≈背景做市/清算账户赚的钱（零和），系统费用为 0；
   * 佣金 0.1% 时，玩家每笔往返多付 2×0.1% 名义额，亏损应在 0 与 0.1% 之间按换手放大；
   * 同 seed 下两套参数成交序列完全相同（qty 固定 50），差额可精确归因为佣金。

3b 盘口深度：DepthShares=100（默认）vs 20，同一「第 1 回合收盘 10 只各买 200 股」脚本。
   命题：深度下降 → 成交率下降、成交均价对盘口的滑点上升、终局现金差异被可执行性放大。

协议（缩放，只作机制方向）：session_ticks=2000、max_turns=10（churn，7 个交易日）/
max_turns=8（sweep，5 个交易日）。种子：11/22/33/44/55 配对。

运行：
    python exp3_mechanism.py
输出：
    results/exp3_mechanism.json
"""
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp3_mechanism.json'
SEEDS = [11, 22, 33, 44, 55]
CHURN_TICKS, CHURN_TURNS = 2000, 10
SWEEP_TICKS, SWEEP_TURNS = 2000, 8


def task(args):
    experiment, label, seed, ticks, turns, source, patches = args
    payload = B.run_once(seed=seed, ticks=ticks, turns=turns, sources=[source],
                         patches=patches, keep_background=(experiment == 'churn'))
    return experiment, label, seed, patches, payload


def _paired_flows(payload):
    """按 execution_id 把同笔成交的两条腿（玩家腿/背景腿）配对，核对逐笔零和。"""
    legs = {}
    for round_row in payload['rounds']:
        for fill in round_row.get('fills', []):
            legs.setdefault(fill[0].rsplit(':', 1)[0], []).append(fill)
    background = set(payload['background']['actors'])
    player_gross = player_fees = counterparty_gross = 0.0
    pairs = 0
    for trade_id, group in legs.items():
        player_legs = [f for f in group if f[1] == B.ACTOR0]
        bg_legs = [f for f in group if f[1] in background]
        if len(player_legs) != 1 or len(bg_legs) != 1:
            continue
        pairs += 1
        for leg in player_legs:
            amount = leg[4] * leg[5]
            player_gross += -amount if leg[2] in ('buy', 'cover') else amount
            player_fees += leg[6]
        for leg in bg_legs:
            amount = leg[4] * leg[5]
            counterparty_gross += -amount if leg[2] in ('buy', 'cover') else amount
    return dict(pairs=pairs, player_gross_flow=round(player_gross, 2),
                player_fees=round(player_fees, 2),
                counterparty_gross_flow=round(counterparty_gross, 2),
                gross_zero_sum_residual=round(player_gross + counterparty_gross, 2))


def churn_row(label, seed, payload):
    player = payload['players'][B.ACTOR0]
    background = payload['background']
    delta = round(player['final_cash'] - 100000.0, 2)
    flows = _paired_flows(payload)
    cash_flow_residual = round(delta - (flows['player_gross_flow'] - flows['player_fees']), 2)
    return dict(
        label=label, seed=seed,
        commission=payload['patches'].get('commission'),
        final_cash=player['final_cash'], player_delta=delta,
        counterparty_pairs=flows['pairs'],
        player_gross_flow=flows['player_gross_flow'],
        counterparty_gross_flow=flows['counterparty_gross_flow'],
        gross_zero_sum_residual=flows['gross_zero_sum_residual'],
        cash_flow_residual=cash_flow_residual,
        background_wealth_change=background['wealth_change'],
        fees_collected=background['fees_collected'],
        buy_fills=player['buy_fills'], sell_fills=player['sell_fills'],
        bought_quantity=player['bought_quantity'], sold_quantity=player['sold_quantity'],
        bought_amount=player['bought_amount'], sold_amount=player['sold_amount'],
        fill_fees=player['fill_fees'], final_liq_fees=player['final_liq_fees'],
        explicit_fees=round(player['fill_fees'] + player['final_liq_fees'], 2),
        turnover=round(player['bought_amount'] + player['sold_amount'], 2),
        round_trips=player['sell_fills'],
        pnl_per_round_trip=round(delta / player['sell_fills'], 4)
        if player['sell_fills'] else None,
        fills_signature=[[f[0], f[2], f[3], f[4], f[5], f[7]]
                         for r in payload['rounds'] for f in r.get('fills', [])
                         if f[1] == B.ACTOR0],
    )


def sweep_row(label, seed, payload):
    player = payload['players'][B.ACTOR0]
    pre = (player['script_state'] or {}).get('pre', {})
    buys = {}
    for round_row in payload['rounds']:
        for fill in round_row.get('fills', []):
            if fill[1] != B.ACTOR0 or fill[4] <= 0:
                continue
            symbol = fill[3]
            row = buys.setdefault(symbol, dict(qty=0, notional=0.0))
            row['qty'] += fill[4]
            row['notional'] += fill[4] * fill[5]
    per_symbol = []
    for symbol in sorted(pre):
        row = buys.get(symbol, dict(qty=0, notional=0.0))
        best_ask = pre[symbol][0]
        avg = row['notional'] / row['qty'] if row['qty'] else None
        per_symbol.append(dict(
            symbol=symbol, pre_best_ask=best_ask, pre_depth_volume=pre[symbol][1],
            target=200, filled=row['qty'],
            fill_rate=round(row['qty'] / 200.0, 4),
            avg_fill_price=None if avg is None else round(avg, 4),
            slippage_vs_pre_ask_pct=None if avg is None else round((avg / best_ask - 1) * 100, 4),
        ))
    filled = sum(r['filled'] for r in per_symbol)
    weighted_slip = 0.0
    slip_weight = 0.0
    for r in per_symbol:
        if r['avg_fill_price'] is None:
            continue
        weight = r['filled'] * r['pre_best_ask']
        weighted_slip += r['slippage_vs_pre_ask_pct'] * weight
        slip_weight += weight
    return dict(
        label=label, seed=seed, depth_shares=payload['patches'].get('depth_shares'),
        final_cash=player['final_cash'],
        return_pct=round(player['final_cash'] / 1000 - 100, 4),
        target_quantity=200 * len(per_symbol), filled_quantity=filled,
        fill_rate=round(filled / float(200 * len(per_symbol)), 4),
        weighted_slippage_pct=round(weighted_slip / slip_weight, 4) if slip_weight else None,
        buy_fills=player['buy_fills'],
        bought_amount=player['bought_amount'],
        explicit_fees=round(player['fill_fees'] + player['final_liq_fees'], 2),
        per_symbol=per_symbol,
    )


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tasks = []
    for seed in SEEDS:
        for rate in (0.0, 0.001):
            tasks.append(('churn', 'commission_%g' % rate, seed, CHURN_TICKS, CHURN_TURNS,
                          B.CHURN, {'commission': rate}))
        for depth in (100, 20):
            tasks.append(('sweep', 'depth_%d' % depth, seed, SWEEP_TICKS, SWEEP_TURNS,
                          B.SWEEP, {'depth_shares': depth}))
    print('[exp3] %d games' % len(tasks))
    rows = {'churn': [], 'sweep': []}
    elapsed = {}
    with ProcessPoolExecutor(max_workers=6) as pool:
        for experiment, label, seed, patches, payload in pool.map(task, tasks):
            if experiment == 'churn':
                rows['churn'].append(churn_row(label, seed, payload))
            else:
                rows['sweep'].append(sweep_row(label, seed, payload))
            elapsed[(experiment, label, seed)] = payload['elapsed_sec']
            print('  done', experiment, label, seed, 'elapsed', payload['elapsed_sec'])

    def group(rows_list, key):
        out = {}
        for row in rows_list:
            out.setdefault(row[key], []).append(row)
        return out

    churn_group = group(rows['churn'], 'label')
    churn_summary = {}
    for label, group_rows in churn_group.items():
        churn_summary[label] = dict(
            n=len(group_rows),
            commission=group_rows[0]['commission'],
            final_cash=B._stats([r['final_cash'] for r in group_rows]),
            player_delta=B._stats([r['player_delta'] for r in group_rows]),
            counterparty_pairs=B._stats([r['counterparty_pairs'] for r in group_rows]),
            player_gross_flow=B._stats([r['player_gross_flow'] for r in group_rows]),
            counterparty_gross_flow=B._stats([r['counterparty_gross_flow'] for r in group_rows]),
            gross_zero_sum_residual=B._stats([r['gross_zero_sum_residual'] for r in group_rows]),
            cash_flow_residual=B._stats([r['cash_flow_residual'] for r in group_rows]),
            background_wealth_change_mtm=B._stats(
                [r['background_wealth_change'] for r in group_rows]),
            fees_collected=B._stats([r['fees_collected'] for r in group_rows]),
            round_trips=B._stats([r['round_trips'] for r in group_rows]),
            turnover=B._stats([r['turnover'] for r in group_rows]),
            explicit_fees=B._stats([r['explicit_fees'] for r in group_rows]),
            pnl_per_round_trip=B._stats([r['pnl_per_round_trip'] for r in group_rows]),
        )
    commission_delta = []
    zero = {r['seed']: r for r in churn_group.get('commission_0', [])}
    for row in churn_group.get('commission_0.001', []):
        base = zero.get(row['seed'])
        if base:
            commission_delta.append(dict(
                seed=row['seed'],
                delta_final_cash=round(row['final_cash'] - base['final_cash'], 2),
                delta_fees=round(row['explicit_fees'] - base['explicit_fees'], 2),
                turnover=row['turnover'],
                fills_identical_ex_fee=row['fills_signature'] == base['fills_signature']))

    sweep_group = group(rows['sweep'], 'label')
    sweep_summary = {}
    for label, group_rows in sweep_group.items():
        sweep_summary[label] = dict(
            n=len(group_rows), depth_shares=group_rows[0]['depth_shares'],
            final_cash=B._stats([r['final_cash'] for r in group_rows]),
            return_pct=B._stats([r['return_pct'] for r in group_rows]),
            fill_rate=B._stats([r['fill_rate'] for r in group_rows]),
            filled_quantity=B._stats([r['filled_quantity'] for r in group_rows]),
            weighted_slippage_pct=B._stats([r['weighted_slippage_pct'] for r in group_rows]),
            buy_fills=B._stats([r['buy_fills'] for r in group_rows]),
            explicit_fees=B._stats([r['explicit_fees'] for r in group_rows]),
        )
    depth_delta = []
    deep = {r['seed']: r for r in sweep_group.get('depth_100', [])}
    for row in sweep_group.get('depth_20', []):
        base = deep.get(row['seed'])
        if base:
            depth_delta.append(dict(
                seed=row['seed'],
                delta_final_cash=round(row['final_cash'] - base['final_cash'], 2),
                delta_fill_rate=round(row['fill_rate'] - base['fill_rate'], 4),
                delta_slippage_pct=round(row['weighted_slippage_pct'] - base['weighted_slippage_pct'], 4)
                if row['weighted_slippage_pct'] is not None and base['weighted_slippage_pct'] is not None
                else None))

    report = dict(
        protocol=dict(engine='mods.stock.toml (PAMS 0.3.0)',
                      seeds=SEEDS,
                      churn=dict(ticks=CHURN_TICKS, turns=CHURN_TURNS, trading_days=7,
                                 strategy='每回调买 50 股 shanhai，下一回调清仓；sleep 100'),
                      sweep=dict(ticks=SWEEP_TICKS, turns=SWEEP_TURNS, trading_days=5,
                                 strategy='第 1 回合收盘附近对 10 只初始股各下 200 股市价买，持有到终局'),
                      patch_note='制度参数在 round 1 开盘前注入 Market（commission/DepthShares），'
                                 '不修改仓库 mods/；同 seed 下其余市场参数完全相同'),
        churn=dict(summary=churn_summary, rows=rows['churn'], commission_delta=commission_delta),
        sweep=dict(summary=sweep_summary, rows=rows['sweep'], depth_delta=depth_delta),
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[exp3] churn summary', json.dumps(churn_summary, ensure_ascii=False))
    print('[exp3] sweep summary', json.dumps(sweep_summary, ensure_ascii=False))
    print('[exp3] written', OUT)


if __name__ == '__main__':
    main()
