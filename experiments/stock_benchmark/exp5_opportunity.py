"""实验 5：环境健康检查（sanity）—— 机会总量、策略条件稳定性、多空容量。

⚠️ 定位（2026-11-16 方向修正后）：本脚本的跨种子统计**只作为环境健康检查**，
不用来证明「不同种子/单局结果可直接比较」。可比性由 `exp5b_league.py` 的联赛/Elo
动态验证回答。这里的数字回答的是「这个引擎的环境本身是否健康」，例如：
* 种子间「绝对值总波动」是否大致稳定（机会预算是否被随机性大幅改变）；
* storm 波动状态/事件跳空能否解释一部分波动；
* 三个不同风格脚本的跨种子离散度（远大于环境机会的离散度说明风格方差来自策略）；
* 默认借券容量 5000 股对日内策略是否 binding、持续做空时谁先卡住。

协议 S：session_ticks=4000、max_turns=20（15 个交易日），与 exp2 一致；缩放口径同 README。

运行：
    python exp5_opportunity.py
输出：
    results/exp5_opportunity.json
"""
import json
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp5_opportunity.json'
TICKS = 4000
TURNS = 20
SEEDS = [11, 22, 33, 44, 55, 66, 77, 88, 99, 110,
         121, 132, 143, 154, 165, 176, 187, 198, 209, 220]
CAPACITY_SEEDS = [11, 22, 33, 44, 55]
CAPACITY_SYMBOL = 'hongfan'      # 便宜、流动性尚可：借券 5000 股与现金/保证金容量可比
REGIME_TRANSITIONS = {'calm': ('storm', .30), 'storm': ('calm', .55)}


# ---------------------------------------------------------------------------
# storm 状态重算（与 mods/stock/dynamics.py:_regime_for 同逻辑）
# ---------------------------------------------------------------------------

def regime_series(seed, rounds):
    state = 'calm'
    series = []
    for step in range(1, rounds + 1):
        roll = random.Random('%s|regime|%s' % (seed, step)).random()
        target, probability = REGIME_TRANSITIONS[state]
        if roll < probability:
            state = target
        series.append(state)
    return series


# ---------------------------------------------------------------------------
# 单局执行（ProcessPool 任务）
# ---------------------------------------------------------------------------

def _flows(rounds, actor, liquidations):
    long_pnl = short_pnl = 0.0
    for round_row in rounds:
        for fill in round_row.get('fills', []):
            if fill[1] != actor:
                continue
            amount, fee, side = fill[4] * fill[5], fill[6], fill[2]
            if side in ('buy', 'sell'):
                long_pnl += (-amount - fee) if side == 'buy' else (amount - fee)
            else:
                short_pnl += (amount - fee) if side == 'short' else (-amount - fee)
    for row in liquidations or []:
        amount, fee = row['amount'], row['fee']
        if row['side'] == 'sell':
            long_pnl += amount - fee
        elif row['side'] == 'cover':
            short_pnl += -amount - fee
    return round(long_pnl, 2), round(short_pnl, 2)


def task(spec):
    kind, seed, source, patches, opts = spec
    patches = dict(patches or {})
    opts = dict(opts or {})
    payload = B.run_once(seed=seed, ticks=TICKS, turns=TURNS, sources=[source],
                         patches=patches, keep_news=opts.get('news', False),
                         keep_regime=True)
    player = payload['players'][B.ACTOR0]
    long_pnl, short_pnl = _flows(payload['rounds'], B.ACTOR0,
                                 player['final_liquidations'])
    out = dict(kind=kind, seed=seed, patches=patches,
               final_cash=player['final_cash'],
               return_pct=round(player['final_cash'] / 1000 - 100, 4),
               fills=player['fills_count'],
               buy_fills=player['buy_fills'], sell_fills=player['sell_fills'],
               bought_quantity=player['bought_quantity'],
               sold_quantity=player['sold_quantity'],
               final_liquidations=[dict(symbol=x['symbol'], side=x['side'],
                                        quantity=x['quantity'], price=x['price'])
                                   for x in player['final_liquidations']],
               long_pnl=long_pnl, short_pnl=short_pnl,
               fees=round(player['fill_fees'] + player['final_liq_fees'], 2),
               script_state=player['script_state'],
               elapsed_sec=payload['elapsed_sec'])
    if opts.get('rounds') or opts.get('news'):
        out['rounds'] = [dict(round=row['round'], trading_day=row['trading_day'],
                              regime=row.get('regime'), close=row['close'])
                         for row in payload['rounds']]
    if opts.get('news'):
        out['news'] = payload['news']
        out['counterfactual'] = payload['counterfactual_equal_weight']
    return out


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------

def _stats(values):
    values = [v for v in values if v is not None]
    n = len(values)
    mean = sum(values) / n
    sd = (sum((v - mean) ** 2 for v in values) / n) ** .5
    return dict(n=n, mean=round(mean, 6), median=round(sorted(values)[n // 2], 6),
                sd=round(sd, 6), cv=round(sd / mean, 6) if mean else None,
                min=round(min(values), 6), max=round(max(values), 6))


def pearson(xs, ys):
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    cov = sum((x - mx) * (y - my) for x, y in pairs)
    vx = sum((x - mx) ** 2 for x, _ in pairs) ** .5
    vy = sum((y - my) ** 2 for _, y in pairs) ** .5
    return round(cov / (vx * vy), 4) if vx and vy else None


def opportunity_metrics(run):
    rounds = run['rounds']
    trading = [row for row in rounds if row['trading_day']]
    symbols = sorted(trading[0]['close'])
    returns = []
    per_symbol = {symbol: [] for symbol in symbols}
    for index in range(1, len(trading)):
        previous, current = trading[index - 1]['close'], trading[index]['close']
        for symbol in symbols:
            value = current[symbol] / previous[symbol] - 1
            returns.append(value)
            per_symbol[symbol].append(abs(value))
    sum_abs = sum(abs(value) for value in returns)
    # 事件跳空：市场快讯在引擎里按股票逐条写入 news（jump 已按 beta×sens 加权），
    # 所以对全部 news 条目求 Σ|jump| 等价于「按 10 只初始股加权后的市场冲击」。
    news = run['news'] or []
    by_kind = {}
    total_jump = 0.0
    for item in news:
        jump = float(item.get('cashflow_change_pct') or 0.0) / 100.0
        kind = item.get('kind') or 'unknown'
        by_kind[kind] = by_kind.get(kind, 0.0) + abs(jump)
        total_jump += abs(jump)
    engine_regimes = {row['round']: row.get('regime') for row in trading}
    expected = regime_series(run['seed'], TURNS - 1)
    storm_all = sum(1 for state in expected if state == 'storm')
    storm_trading = sum(1 for step, state in enumerate(expected, 1)
                        if state == 'storm' and step in engine_regimes)
    mismatch = [step for step, state in enumerate(expected, 1)
                if step in engine_regimes and engine_regimes[step] != state]
    return dict(
        seed=run['seed'],
        trading_days=len(trading),
        n_daily_returns=len(returns),
        sum_abs_X=round(sum_abs, 6),
        mean_abs_X=round(sum_abs / len(returns), 6),
        sum_abs_X_per_day=round(sum_abs / (len(trading) - 1), 6),
        mean_abs_per_symbol={symbol: round(sum(values) / len(values), 6)
                             for symbol, values in per_symbol.items()},
        counterfactual_X=run['counterfactual'],
        sum_abs_jump=round(total_jump, 6),
        sum_abs_jump_pct=round(total_jump * 100, 4),
        sum_abs_jump_by_kind={kind: round(value * 100, 4)
                              for kind, value in sorted(by_kind.items())},
        news_count=len(news),
        news_count_by_kind={kind: sum(1 for item in news if item.get('kind') == kind)
                            for kind in sorted({item.get('kind') for item in news})},
        storm_days_all_rounds=storm_all,
        storm_days_trading=storm_trading,
        regime_engine_mismatch_rounds=mismatch,
    )


def summarize(rows):
    metrics = ['sum_abs_X', 'mean_abs_X', 'sum_abs_X_per_day', 'counterfactual_X',
               'sum_abs_jump', 'sum_abs_jump_pct', 'storm_days_trading',
               'storm_days_all_rounds', 'news_count']
    out = {metric: _stats([row[metric] for row in rows]) for metric in metrics}
    out['abs_counterfactual_X'] = _stats([abs(row['counterfactual_X']) for row in rows])
    out['correlations'] = dict(
        sum_abs_vs_storm=pearson([row['sum_abs_X'] for row in rows],
                                 [row['storm_days_trading'] for row in rows]),
        counterfactual_vs_storm=pearson([row['counterfactual_X'] for row in rows],
                                        [row['storm_days_trading'] for row in rows]),
        sum_abs_vs_jump=pearson([row['sum_abs_X'] for row in rows],
                                [row['sum_abs_jump'] for row in rows]),
        abs_counterfactual_vs_sum_abs=pearson([abs(row['counterfactual_X']) for row in rows],
                                              [row['sum_abs_X'] for row in rows]),
        sum_abs_vs_news_count=pearson([row['sum_abs_X'] for row in rows],
                                      [row['news_count'] for row in rows]),
    )
    return out


def strategy_summary(rows):
    groups = {}
    for run in rows:
        groups.setdefault(run['kind'], []).append(run)
    out = {}
    for kind, group in groups.items():
        returns = [run['return_pct'] for run in group]
        finals = [run['final_cash'] for run in group]
        out[kind] = dict(
            n=len(group),
            final_cash=_stats(finals),
            return_pct=_stats(returns),
            positive_seeds=sum(1 for value in returns if value > 0),
            long_pnl=_stats([run['long_pnl'] for run in group]),
            short_pnl=_stats([run['short_pnl'] for run in group]),
            fills=_stats([run['fills'] for run in group]),
            fees=_stats([run['fees'] for run in group]),
            long_ok=_stats([(run['script_state'] or {}).get('long_ok', 0)
                            for run in group]),
            short_ok=_stats([(run['script_state'] or {}).get('short_ok', 0)
                             for run in group]),
            long_rej=_stats([(run['script_state'] or {}).get('long_rej', 0)
                             for run in group]),
            short_rej=_stats([(run['script_state'] or {}).get('short_rej', 0)
                              for run in group]),
            rows=[dict(seed=run['seed'], final_cash=run['final_cash'],
                       return_pct=run['return_pct'], long_pnl=run['long_pnl'],
                       short_pnl=run['short_pnl'], fills=run['fills'],
                       script_state=run['script_state']) for run in group],
        )
    return out


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    news_source = B.news_sym_source(notional=15000, cap=5)
    specs = []
    for seed in SEEDS:
        specs.append(('cash_opportunity', seed, None, {}, dict(news=True)))
    for seed in SEEDS:
        specs.append(('news', seed, news_source, {}, {}))
        specs.append(('momentum', seed, B.trend_source(False, 10000), {}, {}))
        specs.append(('reversal', seed, B.trend_source(True, 10000), {}, {}))
    for seed in CAPACITY_SEEDS:
        specs.append(('news_borrow_20k', seed, news_source, {'borrow_shares': 20000}, {}))
    for seed in CAPACITY_SEEDS:
        specs.append(('accum_long', seed, B.accum_source(CAPACITY_SYMBOL, short=False),
                      {}, {}))
        specs.append(('accum_short_5k', seed, B.accum_source(CAPACITY_SYMBOL, short=True),
                      {}, {}))
        specs.append(('accum_short_20k', seed, B.accum_source(CAPACITY_SYMBOL, short=True),
                      {'borrow_shares': 20000}, {}))
    print('[exp5] %d games (ticks=%d, turns=%d)' % (len(specs), TICKS, TURNS))
    runs = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        for run in pool.map(task, specs):
            runs.append(run)
            print('  done', run['kind'], run['seed'], 'cash', round(run['final_cash'], 2),
                  'elapsed', run['elapsed_sec'])

    cash_runs = [run for run in runs if run['kind'] == 'cash_opportunity']
    opportunity_rows = [opportunity_metrics(run) for run in cash_runs]
    opportunity = dict(
        definition=dict(
            sum_abs_X='10 只初始股 × 14 个逐日收盘收益的 Σ|r|（15 个交易日的 14 个日间收益）',
            mean_abs_X='Σ|r| / 140，即每只股票每日平均绝对收益',
            counterfactual_X='10 只初始股等权、第 1 回合收盘到终局收盘的收益（不含费用）',
            sum_abs_jump='全部已公布新闻的 Σ|cashflow_change_pct|；市场快讯在引擎里按股票逐条写入'
                         '（jump 已含 beta×sens 加权），因此自然按 10 只初始股加权',
            storm_days_trading='按 dynamics._regime_for 的 seed|regime|step 链重算，'
                               '统计实际交易日里处于 storm 的天数'),
        rows=opportunity_rows,
        summary=summarize(opportunity_rows),
    )

    strategies = strategy_summary([run for run in runs
                                   if run['kind'] in ('news', 'momentum', 'reversal')])

    capacity_runs = [run for run in runs if run['kind'].startswith(('news_borrow', 'accum'))]
    default_news = {run['seed']: run for run in runs if run['kind'] == 'news'}
    news_borrow_rows = []
    for run in capacity_runs:
        if run['kind'] != 'news_borrow_20k':
            continue
        base = default_news.get(run['seed'])
        news_borrow_rows.append(dict(
            seed=run['seed'],
            default_final_cash=base['final_cash'] if base else None,
            borrow20k_final_cash=run['final_cash'],
            delta_final_cash=round(run['final_cash'] - base['final_cash'], 2) if base else None,
            default_script_state=base['script_state'] if base else None,
            borrow20k_script_state=run['script_state']))
    accum = {}
    for kind in ('accum_long', 'accum_short_5k', 'accum_short_20k'):
        group = [run for run in capacity_runs if run['kind'] == kind]
        source = B.accum_source(CAPACITY_SYMBOL, short=kind.startswith('accum_short'))
        is_short = kind.startswith('accum_short')
        accum[kind] = dict(
            n=len(group),
            direction='short' if is_short else 'long',
            source_note='同一脚本（%s %s），只有 BorrowShares 不同'
                        % (CAPACITY_SYMBOL, 'accum_short' if is_short else 'accum_long'),
            ordered_shares=_stats([(run['script_state'] or {}).get('shares', 0)
                                   for run in group]),
            filled_shares=_stats([run['sold_quantity'] if is_short else run['bought_quantity']
                                  for run in group]),
            end_position_shares=_stats([sum(row['quantity'] for row in run['final_liquidations'])
                                        for run in group]),
            fills=_stats([(run['script_state'] or {}).get('fills', 0) for run in group]),
            rejects=_stats([(run['script_state'] or {}).get('rej', 0) for run in group]),
            final_cash=_stats([run['final_cash'] for run in group]),
            rows=[dict(seed=run['seed'], patches=run['patches'],
                       script_state=run['script_state'],
                       bought_quantity=run['bought_quantity'],
                       sold_quantity=run['sold_quantity'],
                       final_liquidations=run['final_liquidations'],
                       final_cash=run['final_cash']) for run in group])
    capacity = dict(
        protocol_note='symbol=%s；accum 每 100 tick 吃一次盘口、每次 ≤200 股、不主动平仓，'
                      '用于找出借券/保证金/现金哪个先 binding' % CAPACITY_SYMBOL,
        news_borrow_compare=dict(rows=news_borrow_rows,
                                 note='news_sym 单笔名义额 15000、每日最多 5 笔、收盘前平仓；'
                                      '因盘口深度限制实际每笔仅数十~数百股，'
                                      '预期 5000 股借券容量不 binding'),
        accum=accum,
    )

    report = dict(
        position='本文件的跨种子统计只作环境健康检查（sanity），'
                 '不作为「不同种子/单局可比」的证明；可比性见 exp5b_league.py',
        protocol=dict(engine='mods.stock.toml (PAMS 0.3.0)',
                      ticks=TICKS, turns=TURNS, trading_days=15,
                      seeds=SEEDS, capacity_seeds=CAPACITY_SEEDS,
                      scale='session_ticks 缩到 1/5（默认 20000）+ 回合缩到 1/3；'
                            '日频方差不变但日内做市重报次数只有默认的 1/5，只作机制方向'),
        opportunity=opportunity,
        strategies=strategies,
        capacity=capacity,
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[exp5] opportunity CV',
          json.dumps({k: v['cv'] for k, v in opportunity['summary'].items()
                      if isinstance(v, dict) and 'cv' in v}, ensure_ascii=False))
    print('[exp5] written', OUT)


if __name__ == '__main__':
    main()
