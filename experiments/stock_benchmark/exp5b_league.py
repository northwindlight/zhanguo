"""实验 5b：联赛 / Elo 动态验证（零模型，协议 S）。

设计动机：动态博弈系统的「可比性」不应由静态的环境统计（种子间机会总量的 CV）来证明，
而应由**联赛**来验证——多风格选手在大量随机种子、随机阵容的对局里互相排名，
看排序是否稳定、是否随牛熊环境变化。

协议：session_ticks=4000、max_turns=20（15 个交易日），4 席一局；
选手池 5 个确定性脚本（可重复抽取、允许同风格多席）：
    mom         ma5/ma20 趋势，多空
    rev         与 mom 反向（均值回归），多空
    news        事件后顺消息方向做多/做空，持有到当日收盘
    cash        空仓不交易
    short_heavy 第 1 个交易日等权做空 10 只初始股，持有到终局

每局换种子；记录每席终局现金/名次/多空腿盈亏，以及该局的买入持有反事实（判牛熊）。

Elo：初始 1000、K=32，按「同局内风格两两比较」更新——
  每种风格在该局的得分 = 该风格各席终局现金的均值；两种风格都出现时，
  较高者得 1 分、较低者 0 分、相等 0.5；expected = 1/(1+10^((Rb-Ra)/400))。
  同一风格在同局出现多席时先取均值再比较（避免同风格内耗）。
Bootstrap CI：对局做有放回重采样 1000 次，每次从头跑同一 Elo 规则，取 2.5%/97.5% 分位。

运行：
    python exp5b_league.py            # 48 局
输出：
    results/exp5b_league.json
"""
import json
import math
import random
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import bench_lib as B

OUT = Path(__file__).resolve().parent / 'results' / 'exp5b_league.json'
TICKS = 4000
TURNS = 20
GAMES = 48
SEAT_COUNT = 4
BASE_SEED = 20261116
ELO_BASE = 1000.0
ELO_K = 32.0
BOOTSTRAP = 1000
STYLES = ['mom', 'rev', 'news', 'cash', 'short_heavy']
STRATEGY_SOURCE = {
    'mom': B.trend_source(inverted=False, notional=10000),
    'rev': B.trend_source(inverted=True, notional=10000),
    'news': B.news_sym_source(notional=15000, cap=5),
    'cash': None,
    'short_heavy': B.SHORT_HEAVY,
}


# ---------------------------------------------------------------------------
# 调度与单局执行
# ---------------------------------------------------------------------------

def build_schedule(games=None):
    games = GAMES if games is None else games
    rng = random.Random(BASE_SEED)
    schedule = []
    for index in range(games):
        schedule.append(dict(game=index + 1, seed=rng.randrange(100000, 999999),
                             seats=[rng.choice(STYLES) for _ in range(SEAT_COUNT)]))
    return schedule


def seat_flows(rounds, actor, liquidations):
    """把一个席位的成交拆成多头腿/空头腿的现金盈亏（不含终局 0.1% 折价）。"""
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


def task(game):
    sources = [STRATEGY_SOURCE[style] for style in game['seats']]
    payload = B.run_once(seed=game['seed'], ticks=TICKS, turns=TURNS,
                         sources=sources, actors=SEAT_COUNT)
    seats = []
    for seat, style in enumerate(game['seats']):
        actor = 'stock:player%d' % seat
        player = payload['players'][actor]
        long_pnl, short_pnl = seat_flows(payload['rounds'], actor,
                                         player['final_liquidations'])
        seats.append(dict(seat=seat, actor=actor, style=style,
                          final_cash=player['final_cash'],
                          rank=player['final_rank'],
                          return_pct=round(player['final_cash'] / 1000 - 100, 4),
                          fills=player['fills_count'],
                          buy_fills=player['buy_fills'], sell_fills=player['sell_fills'],
                          long_pnl=long_pnl, short_pnl=short_pnl,
                          fees=round(player['fill_fees'] + player['final_liq_fees'], 2),
                          script_state=player['script_state']))
    trading = [row for row in payload['rounds'] if row['trading_day']]
    entry_close, final_close = trading[0]['close'], trading[-1]['close']
    symbols = sorted(entry_close)
    counterfactual = sum(final_close[s] / entry_close[s] - 1 for s in symbols) / len(symbols)
    return dict(game=game['game'], seed=game['seed'], seats=seats,
                counterfactual=round(counterfactual, 6),
                elapsed_sec=payload['elapsed_sec'])


# ---------------------------------------------------------------------------
# 联赛统计
# ---------------------------------------------------------------------------

def style_means(game):
    """每局每种风格的平均终局现金（同风格多席取均值）。"""
    groups = {}
    for seat in game['seats']:
        groups.setdefault(seat['style'], []).append(seat['final_cash'])
    return {style: sum(values) / len(values) for style, values in groups.items()}


def pairwise_stats(games):
    matrix = {a: {b: dict(games=0, wins=0, losses=0, ties=0)
                  for b in STYLES if b != a} for a in STYLES}
    for game in games:
        means = style_means(game)
        for a in STYLES:
            for b in STYLES:
                if a == b or a not in means or b not in means:
                    continue
                row = matrix[a][b]
                row['games'] += 1
                if means[a] > means[b] + 1e-9:
                    row['wins'] += 1
                elif means[a] < means[b] - 1e-9:
                    row['losses'] += 1
                else:
                    row['ties'] += 1
    for a in STYLES:
        for b in STYLES:
            if a == b:
                continue
            row = matrix[a][b]
            row['win_rate'] = round(row['wins'] / row['games'], 4) if row['games'] else None
    return matrix


def elo_run(games, k=ELO_K, base=ELO_BASE):
    ratings = {style: base for style in STYLES}
    history = []
    for game in games:
        means = style_means(game)
        present = [style for style in STYLES if style in means]
        deltas = {}
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                a, b = present[i], present[j]
                expected_a = 1.0 / (1.0 + 10 ** ((ratings[b] - ratings[a]) / 400.0))
                if means[a] > means[b] + 1e-9:
                    score_a = 1.0
                elif means[a] < means[b] - 1e-9:
                    score_a = 0.0
                else:
                    score_a = 0.5
                change = k * (score_a - expected_a)
                deltas[a] = deltas.get(a, 0.0) + change
                deltas[b] = deltas.get(b, 0.0) - change
        for style, change in deltas.items():
            ratings[style] += change
        history.append({style: round(value, 3) for style, value in ratings.items()})
    return ratings, history


def elo_bootstrap(games, reps=BOOTSTRAP):
    rng = random.Random(BASE_SEED + 1)
    samples = {style: [] for style in STYLES}
    n = len(games)
    for _ in range(reps):
        resample = [games[rng.randrange(n)] for _ in range(n)]
        ratings, _ = elo_run(resample)
        for style in STYLES:
            samples[style].append(ratings[style])
    summary = {}
    for style in STYLES:
        values = sorted(samples[style])
        summary[style] = dict(
            mean=round(sum(values) / len(values), 2),
            p2_5=round(values[int(0.025 * (len(values) - 1))], 2),
            p97_5=round(values[int(0.975 * (len(values) - 1))], 2))
    return summary


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        average = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = average
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 3:
        return None
    rx, ry = _rank(xs), _rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** .5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** .5
    return round(cov / (vx * vy), 4) if vx and vy else None


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    vx = sum((x - mx) ** 2 for x in xs) ** .5
    vy = sum((y - my) ** 2 for y in ys) ** .5
    return round(cov / (vx * vy), 4) if vx and vy else None


def style_returns(game):
    groups = {}
    for seat in game['seats']:
        groups.setdefault(seat['style'], []).append(seat['return_pct'])
    return {style: sum(values) / len(values) for style, values in groups.items()}


def stability(games):
    half = len(games) // 2
    first, second = games[:half], games[half:]
    first_rank = {}
    second_rank = {}
    for part, target in ((first, first_rank), (second, second_rank)):
        totals = {style: [] for style in STYLES}
        for game in part:
            for style, value in style_returns(game).items():
                totals[style].append(value)
        for style, values in totals.items():
            if values:
                target[style] = round(sum(values) / len(values), 4)
    common = [style for style in STYLES if style in first_rank and style in second_rank]
    return dict(first_half_mean_return=first_rank, second_half_mean_return=second_rank,
                spearman_rank=spearman([first_rank[s] for s in common],
                                       [second_rank[s] for s in common]),
                styles=common)


def regime_breakdown(games):
    groups = dict(bull=[], bear=[], flat=[])
    for game in games:
        cf = game['counterfactual']
        if cf > 0.005:
            groups['bull'].append(game)
        elif cf < -0.005:
            groups['bear'].append(game)
        else:
            groups['flat'].append(game)
    out = {}
    for regime, subset in groups.items():
        block = dict(games=len(subset),
                     mean_counterfactual=round(sum(g['counterfactual'] for g in subset)
                                               / len(subset), 6) if subset else None,
                     styles={})
        for style in STYLES:
            returns, wins, beats, ranks, long_pnl, short_pnl, seats = [], 0, 0, [], [], [], 0
            for game in subset:
                means = style_means(game)
                if style not in means:
                    continue
                seats += 1
                returns.append(style_returns(game)[style])
                other = [v for s, v in means.items() if s != style]
                if other and means[style] > sum(other) / len(other):
                    wins += 1
                if other:
                    beats += 1
                rank = [seat['rank'] for seat in game['seats'] if seat['style'] == style]
                ranks.extend(rank)
                for seat in game['seats']:
                    if seat['style'] == style:
                        long_pnl.append(seat['long_pnl'])
                        short_pnl.append(seat['short_pnl'])
            block['styles'][style] = dict(
                games=seats,
                mean_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
                win_rate_vs_field=round(wins / beats, 4) if beats else None,
                mean_rank=round(sum(ranks) / len(ranks), 3) if ranks else None,
                mean_long_pnl=round(sum(long_pnl) / len(long_pnl), 2) if long_pnl else None,
                mean_short_pnl=round(sum(short_pnl) / len(short_pnl), 2) if short_pnl else None,
            )
        out[regime] = block
    return out


def environment(games):
    totals = [sum(seat['final_cash'] for seat in game['seats']) for game in games]
    mean_total = sum(totals) / len(totals)
    sd_total = (sum((v - mean_total) ** 2 for v in totals) / len(totals)) ** .5
    cash_seats = [seat for game in games for seat in game['seats'] if seat['style'] == 'cash']
    cash_wins = 0
    cash_ranks = []
    for game in games:
        means = style_means(game)
        if 'cash' not in means:
            continue
        cash_ranks.extend(seat['rank'] for seat in game['seats'] if seat['style'] == 'cash')
        other = [v for style, v in means.items() if style != 'cash']
        if other and means['cash'] > sum(other) / len(other):
            cash_wins += 1
    judged = [game for game in games if 'cash' in style_means(game)]
    return dict(
        total_cash=dict(mean=round(mean_total, 2), sd=round(sd_total, 2),
                        min=round(min(totals), 2), max=round(max(totals), 2)),
        cash_seat_win_rate_vs_field=round(cash_wins / len(judged), 4) if judged else None,
        cash_seat_games=len(judged),
        cash_seat_mean_rank=round(sum(cash_ranks) / len(cash_ranks), 3) if cash_ranks else None,
    )


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    schedule = build_schedule()
    print('[exp5b] league: %d games x %d seats x (ticks=%d, turns=%d)'
          % (len(schedule), SEAT_COUNT, TICKS, TURNS))
    played = []
    with ProcessPoolExecutor(max_workers=8) as pool:
        for game in pool.map(task, schedule):
            played.append(game)
            print('  game %d seed %d seats %s elapsed %ss' %
                  (game['game'], game['seed'], [s['style'] for s in game['seats']],
                   game['elapsed_sec']))
    played.sort(key=lambda row: row['game'])

    ratings, history = elo_run(played)
    final_elo = {style: round(value, 2) for style, value in ratings.items()}
    bootstrap = elo_bootstrap(played)
    convergence = {}
    for block_end in range(5, len(played) + 1, 5):
        prefix_ratings, _ = elo_run(played[:block_end])
        convergence[str(block_end)] = {style: round(prefix_ratings[style], 2)
                                       for style in STYLES}
    matrix = pairwise_stats(played)
    regime = regime_breakdown(played)

    overall = {}
    for style in STYLES:
        returns = []
        ranks = []
        long_pnl = []
        short_pnl = []
        wins = beats = 0
        for game in played:
            means = style_means(game)
            if style not in means:
                continue
            returns.append(style_returns(game)[style])
            ranks.extend(seat['rank'] for seat in game['seats'] if seat['style'] == style)
            for seat in game['seats']:
                if seat['style'] == style:
                    long_pnl.append(seat['long_pnl'])
                    short_pnl.append(seat['short_pnl'])
            other = [v for s, v in means.items() if s != style]
            if other:
                beats += 1
                if means[style] > sum(other) / len(other):
                    wins += 1
        overall[style] = dict(
            seat_games=len(returns),
            mean_return_pct=round(sum(returns) / len(returns), 4) if returns else None,
            sd_return_pct=round((sum((v - sum(returns) / len(returns)) ** 2
                                     for v in returns) / len(returns)) ** .5, 4) if returns else None,
            mean_rank=round(sum(ranks) / len(ranks), 3) if ranks else None,
            win_rate_vs_field=round(wins / beats, 4) if beats else None,
            mean_long_pnl=round(sum(long_pnl) / len(long_pnl), 2) if long_pnl else None,
            mean_short_pnl=round(sum(short_pnl) / len(short_pnl), 2) if short_pnl else None,
            elo=final_elo[style],
        )

    bull_win = {style: regime['bull']['styles'][style]['win_rate_vs_field'] for style in STYLES}
    bear_win = {style: regime['bear']['styles'][style]['win_rate_vs_field'] for style in STYLES}
    common = [style for style in STYLES if bull_win[style] is not None and bear_win[style] is not None]
    report = dict(
        protocol=dict(engine='mods.stock.toml (PAMS 0.3.0)',
                      ticks=TICKS, turns=TURNS, trading_days=15,
                      seat_count=SEAT_COUNT, games=len(played),
                      base_seed=BASE_SEED,
                      style_pool=STYLES,
                      schedule=schedule,
                      script_player_note='全部选手是确定性沙盒脚本，不是 LLM；'
                                         '本实验只验证联赛/排序机制，不构成模型能力排名',
                      elo_rule='初始 1000、K=32；同局内不同风格按平均终局现金两两比较，'
                               '较高得 1/较低得 0/相等 0.5；同风格多席先取均值',
                      bootstrap='有放回重采样对局 1000 次，重跑同一 Elo 规则'),
        final_elo=final_elo,
        elo_bootstrap=bootstrap,
        elo_convergence=convergence,
        elo_history=history,
        pairwise=matrix,
        stability=stability(played),
        regime=regime,
        bull_bear_win_rate={
            'bull': bull_win, 'bear': bear_win,
            'style_order_bull': sorted(STYLES, key=lambda s: (bull_win[s] is None, -(bull_win[s] or 0))),
            'style_order_bear': sorted(STYLES, key=lambda s: (bear_win[s] is None, -(bear_win[s] or 0))),
            'cross_regime_spearman': spearman([bull_win[s] for s in common],
                                              [bear_win[s] for s in common]),
        },
        environment=environment(played),
        style_overall=overall,
        games=played,
    )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('[exp5b] final Elo', json.dumps(final_elo, ensure_ascii=False))
    print('[exp5b] written', OUT)


if __name__ == '__main__':
    main()
