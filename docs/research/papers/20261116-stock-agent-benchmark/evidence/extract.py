"""Recompute the paper's evidence from the two public stock saves.

Read-only: never modifies the saves. Standard library only.

Usage:
    python extract.py [--hydrogen PATH] [--flash PATH] [--out DIR]

Defaults point at the public records under research/game-records/ and write
JSON/CSV next to this script.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import statistics
import tarfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # docs/research/papers/<paper>/evidence -> repo root
DEFAULTS = {
    'hydrogen': ROOT / 'research/game-records/stock-gemini-hydrogen-20261113/session.json.gz',
    'flash': ROOT / 'research/game-records/stock-gemini38-flash-20261114/session.json.gz',
}
SEATS = [('stock:alpha', '量化甲'), ('stock:beta', '量化乙'),
         ('stock:gamma', '量化丙'), ('stock:delta', '量化丁')]
SEAT_NAMES = dict(SEATS)
INITIAL_CASH = 100000.0
INITIAL_STOCKS = ['shanhai', 'qingzhou', 'yundu', 'changfeng', 'beiyuan',
                  'hongfan', 'zhongli', 'lanhai', 'jinhui', 'taiping']
PASS = []


def check(label, ok, detail=''):
    PASS.append(dict(label=label, ok=bool(ok), detail=detail))
    print(('PASS  ' if ok else 'FAIL  ') + label + (f'  [{detail}]' if detail else ''))


def load(save: Path):
    raw = save.read_bytes()
    data = json.loads(gzip.decompress(raw))
    audit = {row['id']: row for row in data['host'].get('audit', [])}
    audit_path = save.with_name(save.name[:-3] + '.audit.tar.gz' if save.name.endswith('.gz')
                                else save.name + '.audit.tar.gz')
    if audit_path.exists():
        with tarfile.open(audit_path, 'r:gz') as tar:
            for member in tar.getmembers():
                if not member.isfile() or not member.name.endswith('.json'):
                    continue
                for row in json.loads(tar.extractfile(member).read())['records']:
                    previous = audit.get(row['id'])
                    if previous is not None and previous != row:
                        raise SystemExit(f'conflicting audit id {row["id"]} in {member.name}')
                    audit[row['id']] = row
    return dict(path=str(save), raw=raw, sha256=hashlib.sha256(raw).hexdigest(),
                json_sha256=hashlib.sha256(gzip.decompress(raw)).hexdigest(), data=data, audit=audit)


def drawdown(values, initial=INITIAL_CASH):
    peak, worst = initial, 0.0
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    return round(worst, 4)


def receipt_data(row):
    data = (row.get('receipt') or {}).get('data')
    return data if isinstance(data, dict) else {}


def seat_metrics(game):
    accounts = game['data']['modules']['stock:portfolio']['accounts']
    rows = []
    for actor, name in SEATS:
        acc = accounts[actor]
        equity = [e['wealth'] for e in acc['equity']]
        identity = (INITIAL_CASH + acc['realized_pnl'] - acc['compute_expenses']
                    - acc['intelligence_expenses'] - acc['borrow_fees'] - acc['interest_paid'])
        rows.append(dict(
            actor=actor, name=name, final_cash=acc['final_cash'], rank=acc.get('final_rank'),
            return_pct=round((acc['final_cash'] / INITIAL_CASH - 1) * 100, 4),
            realized_pnl=acc['realized_pnl'], compute_expenses=acc['compute_expenses'],
            intelligence_expenses=acc['intelligence_expenses'],
            borrow_fees=acc['borrow_fees'], interest_paid=acc['interest_paid'], debt=acc['debt'],
            max_drawdown_pct=drawdown(equity + [acc['final_cash']]),
            peak_wealth=max(equity),
            peak_round=max(acc['equity'], key=lambda e: e['wealth'])['round'],
            rounds_below_initial=sum(1 for value in equity if value < INITIAL_CASH),
            completed_rounds=len(equity),
            final_close_count=len(acc.get('final_liquidations') or []),
            identity_value=round(identity, 4),
            identity_ok=abs(identity - acc['final_cash']) < 0.005,
            equity=[{'round': e['round'], 'wealth': e['wealth']} for e in acc['equity']],
        ))
    return rows


def trading_activity(game):
    """Counts that mirror experiments/stock_compare.py, recomputed from the audit."""
    audit = game['audit']
    accounts = game['data']['modules']['stock:portfolio']['accounts']
    out = {}
    for actor, name in SEATS:
        own = [row for row in audit.values() if row.get('actor') == actor]
        calls = [row for row in own if row['action'] not in
                 {'buy', 'sell', 'short', 'cover', 'cancel', 'stock_fill',
                  'stock_final_close', 'settlement'}]
        edits = [row for row in own if row['action'] == 'script'
                 and row['arguments'].get('action') in {'write', 'patch'}]
        bad_checks = [row for row in edits if (receipt_data(row).get('check') or {}).get('valid') is False]
        research = [row for row in own if row['action'] == 'script'
                    and row['arguments'].get('action') == 'run'
                    and row['arguments'].get('slot', '1') == 'research']
        settlements = [row for row in own if row['action'] == 'settlement']
        trading = [row for row in settlements if (row['receipt']['period'] - 1) % 7 < 5]
        slots = [slot for row in trading for slot in row['receipt']['data'].get('detail', {}).get('slots', [])]
        out[name] = dict(
            tool_actions=len(calls),
            rejected_tool_actions=sum(row['receipt'].get('ok') is False for row in calls),
            script_edits=len(edits), failed_auto_checks=len(bad_checks),
            research_runs=len(research),
            research_failures=sum(receipt_data(row).get('status') == 'failed'
                                  or row['receipt'].get('ok') is False for row in research),
            trading_slot_runs=len(slots),
            trading_slot_failures=sum(slot['status'] == 'failed' for slot in slots),
        )
    out['__total__'] = {key: sum(out[name][key] for _, name in SEATS)
                        for key in out[SEATS[0][1]]}
    return out


def cash_flows(game):
    """Per-seat/per-symbol cash flows: audit fills plus round-60 final closes."""
    audit, accounts = game['audit'], game['data']['modules']['stock:portfolio']['accounts']
    flows = {name: defaultdict(float) for _, name in SEATS}
    sides = {name: defaultdict(int) for _, name in SEATS}
    turnover = defaultdict(float)
    fees = defaultdict(float)
    for row in audit.values():
        if row.get('action') != 'stock_fill':
            continue
        name = SEAT_NAMES.get(row.get('actor'))
        if name is None:
            continue
        body = receipt_data(row)
        symbol, side = body.get('symbol'), body.get('side')
        amount, fee = float(body.get('amount') or 0), float(body.get('fee') or 0)
        sides[name][side] += 1
        turnover[name] += amount
        fees[name] += fee
        flows[name][symbol] += {'buy': -amount - fee, 'sell': amount - fee,
                                'short': amount - fee, 'cover': -amount - fee}[side]
    for actor, name in SEATS:
        for close in accounts[actor].get('final_liquidations') or []:
            symbol, side = close.get('symbol'), close.get('side')
            amount, fee = float(close.get('amount') or 0), float(close.get('fee') or 0)
            sides[name]['final_' + side] += 1
            turnover[name] += amount
            fees[name] += fee
            flows[name][symbol] += (amount - fee) if side == 'sell' else (-amount - fee)
    for actor, name in SEATS:
        total = sum(flows[name].values())
        realized = accounts[actor]['realized_pnl']
        check(f'{name} 成交现金流合计 = realized_pnl', abs(total - realized) < 0.02,
              f'{total:.2f} vs {realized:.2f}')
    return flows, sides, turnover, fees


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hydrogen', type=Path, default=DEFAULTS['hydrogen'])
    ap.add_argument('--flash', type=Path, default=DEFAULTS['flash'])
    ap.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    args = ap.parse_args()
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    games = {'hydrogen': load(args.hydrogen), 'flash': load(args.flash)}

    report = {'saves': {tag: dict(path=game['path'], gz_sha256=game['sha256'],
                                     json_sha256=game['json_sha256'])
                        for tag, game in games.items()},
              'seats': {}, 'behaviour': {}, 'attribution': {}, 'engine': {}}

    for tag, game in games.items():
        report['seats'][tag] = seat_metrics(game)
        for row in report['seats'][tag]:
            check(f'{tag}/{row["name"]} 会计恒等式', row['identity_ok'], f'{row["identity_value"]:.2f}')
        report['behaviour'][tag] = trading_activity(game)

        # engine-version facts: rest-day slot executions and the compute price sheet
        settlements = [row for row in game['audit'].values() if row.get('action') == 'settlement']
        slots_of = lambda row: (row['receipt'].get('data') or {}).get('detail', {}).get('slots', [])
        rest = [row for row in settlements if (row['receipt']['period'] - 1) % 7 >= 5]
        trade = [row for row in settlements if (row['receipt']['period'] - 1) % 7 < 5]
        paid = {'pool': set(), 'slot': set(), 'research': set()}
        for row in game['audit'].values():
            action = row.get('action')
            body = (row.get('receipt') or {}).get('data')
            if not isinstance(body, dict):
                continue
            if action == 'compute':
                kind = (row.get('arguments') or {}).get('kind')
                if kind in paid and body.get('paid'):
                    paid[kind].add(round(float(body['paid']), 2))
            elif action == 'script' and (row.get('arguments') or {}).get('action') == 'run':
                if body.get('billed'):
                    paid['research'].add(round(float(body['billed']), 2))
        report['engine'][tag] = dict(
            trading_day_slot_runs=sum(len(slots_of(row)) for row in trade),
            rest_day_slot_runs=sum(len(slots_of(row)) for row in rest),
            rest_day_seats=len(rest),
            paid_pool=sorted(paid['pool']), paid_slot=sorted(paid['slot']),
            research_fee=sorted(paid['research']))

    for tag in games:
        engine = report['engine'][tag]
        print(f"{tag}: trading-day slots {engine['trading_day_slot_runs']}, "
              f"rest-day slots {engine['rest_day_slot_runs']} over {engine['rest_day_seats']} rest-day rows, "
              f"pool prices {engine['paid_pool']}, slot prices {engine['paid_slot']}, research {engine['research_fee']}")
    check('休市日席位槽执行：Hydrogen 64 次 / Flash 0 次',
          report['engine']['hydrogen']['rest_day_slot_runs'] == 64
          and report['engine']['flash']['rest_day_slot_runs'] == 0)
    price_ok = all(set(engine['paid_pool']) <= {120, 240, 480}
                   and set(engine['paid_slot']) <= {80, 160, 320}
                   and set(engine['research_fee']) <= {200}
                   for engine in report['engine'].values())
    check('两局算力/槽/研究价格均为 0585416 版价目（120/240/480、80/160/320、200）', price_ok)

    expected = {'hydrogen': '2345b9fd75b08c468d6f076f5aad990d53160bf0793502398faadf236ba4595c',
                'flash': '04e6d461c6c49b4c6629e1cae93ad1af42f1c9bf67bec373aad634bde3e862f0'}
    hashes_ok = all(report['saves'][tag]['json_sha256'] == digest for tag, digest in expected.items())
    check('两局解压后 session.json 的 SHA-256 与发布记录一致', hashes_ok)

    stocks_meta = {tag: {symbol: {key: row.get(key) for key in
                        ('anchor', 'vol', 'beta', 'converge', 'drift', 'sens', 'liq', 'spread', 'report_offset')}
                        for symbol, row in game['data']['modules']['stock:market']['stocks'].items()}
                  for tag, game in games.items()}
    check('14 只股票静态参数两局逐字段一致', stocks_meta['hydrogen'] == stocks_meta['flash'],
          f"{len(stocks_meta['hydrogen'])} symbols")

    # attribution of the per-seat average gap
    average = {tag: {key: statistics.fmean(row[key] for row in report['seats'][tag])
                     for key in ('final_cash', 'realized_pnl', 'compute_expenses',
                                 'intelligence_expenses', 'borrow_fees', 'interest_paid',
                                 'max_drawdown_pct')}
               for tag in games}
    items = [('realized_pnl', '交易净盈亏', 1), ('compute_expenses', '算力支出', -1),
             ('intelligence_expenses', '情报支出', -1), ('borrow_fees', '借券费', -1),
             ('interest_paid', '利息', -1)]
    decomposition, total = [], 0.0
    for key, label, sign in items:
        value = sign * (average['hydrogen'][key] - average['flash'][key])
        decomposition.append(dict(key=key, label=label, per_seat_delta=round(value, 2)))
        total += value
    cash_delta = average['hydrogen']['final_cash'] - average['flash']['final_cash']
    report['attribution'] = dict(
        per_seat_average_hydrogen={k: round(v, 2) for k, v in average['hydrogen'].items()},
        per_seat_average_flash={k: round(v, 2) for k, v in average['flash'].items()},
        items=decomposition, cash_delta=round(cash_delta, 2), items_sum=round(total, 2),
        identity_ok=abs(total - cash_delta) < 0.02)
    check('差异分解合计 = 每席平均现金差', report['attribution']['identity_ok'],
          f'{total:.2f} vs {cash_delta:.2f}')

    # per-symbol cash flows
    flows = {}
    for tag, game in games.items():
        per_seat_flows, sides, turnover, fees = cash_flows(game)
        flows[tag] = (per_seat_flows, sides, turnover, fees)
        report['behaviour'][tag].update(
            sides={name: dict(sides[name]) for _, name in SEATS},
            turnover_by_seat={name: round(turnover[name], 2) for _, name in SEATS},
            fees_by_seat={name: round(fees[name], 2) for _, name in SEATS},
            turnover_total=round(sum(turnover.values()), 2),
            fees_total=round(sum(fees.values()), 2))
    symbols = sorted(set().union(*[set(flows[tag][0][name]) for tag in flows for _, name in SEATS]),
                     key=lambda s: -abs(sum(flows['hydrogen'][0][name].get(s, 0.0)
                                            for _, name in SEATS)
                                        - sum(flows['flash'][0][name].get(s, 0.0)
                                              for _, name in SEATS)))
    rows = []
    for symbol in symbols:
        row = {'symbol': symbol}
        for tag in flows:
            row[f'{tag}_total'] = round(sum(flows[tag][0][name].get(symbol, 0.0) for _, name in SEATS), 2)
            for _, name in SEATS:
                row[f'{tag}_{name}'] = round(flows[tag][0][name].get(symbol, 0.0), 2)
        row['diff'] = round(row['hydrogen_total'] - row['flash_total'], 2)
        rows.append(row)
    with open(out / 'per-symbol-cashflow.csv', 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    # public news alignment + price-path comparison
    news = {tag: {(n['round'], n['tick'], n['symbol'], n['kind']): n
                  for n in game['data']['modules']['stock:market']['news']}
            for tag, game in games.items()}
    shared = set(news['hydrogen']) & set(news['flash'])
    fields = ('headline', 'cashflow_change_pct', 'effect')
    same = {field: sum(1 for key in shared
                       if news['hydrogen'][key].get(field) == news['flash'][key].get(field))
            for field in fields}
    report['news'] = dict(counts={tag: len(news[tag]) for tag in news},
                          shared=len(shared), only_hydrogen=len(set(news['hydrogen']) - shared),
                          only_flash=len(set(news['flash']) - shared),
                          fields_identical=same,
                          round_window={tag: [min(key[0] for key in news[tag]),
                                              max(key[0] for key in news[tag])] for tag in news})
    check('公开消息逐条对齐', len(shared) == 160 and not (set(news['hydrogen']) ^ set(news['flash'])),
          f'shared={len(shared)}, fields={same}')

    history = {tag: game['data']['modules']['stock:market']['history'] for tag, game in games.items()}
    WARMUP = 20  # leading anchor values used to warm up moving averages

    def closes_by_round(values):
        """history = 20 anchor values + one close per round since listing."""
        close = values[WARMUP:]
        return dict(zip(range(80 - len(values), 80 - len(values) + len(close)), close))

    per_symbol, baseline = {}, {}
    for symbol in history['hydrogen']:
        series = {tag: closes_by_round(history[tag][symbol]) for tag in history}
        common = sorted(set(series['hydrogen']) & set(series['flash']))
        daily = {}
        for tag in series:
            row = [series[tag][r] for r in common]
            daily[tag] = [row[i + 1] / row[i] - 1 for i in range(len(row) - 1) if row[i] > 0]
        n = min(len(daily['hydrogen']), len(daily['flash']))
        a, b = daily['hydrogen'][:n], daily['flash'][:n]
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
        va = math.sqrt(sum((x - ma) ** 2 for x in a))
        vb = math.sqrt(sum((y - mb) ** 2 for y in b))
        per_symbol[symbol] = dict(
            corr=round(cov / (va * vb), 4) if va and vb else None,
            first_round=min(series['hydrogen']),
            return_hydrogen_pct=round((series['hydrogen'][59] / series['hydrogen'][min(series['hydrogen'])] - 1) * 100, 2),
            return_flash_pct=round((series['flash'][59] / series['flash'][min(series['flash'])] - 1) * 100, 2))
    for tag in history:
        baseline[tag] = round(statistics.fmean(
            closes_by_round(history[tag][s])[59] / closes_by_round(history[tag][s])[1] - 1
            for s in INITIAL_STOCKS) * 100, 2)
    report['price_paths'] = dict(
        per_symbol=per_symbol, buy_and_hold_equal_weight_pct=baseline,
        median_corr=round(statistics.median(v['corr'] for v in per_symbol.values()), 4))
    check('被动等权持有基准接近', abs(baseline['hydrogen'] - baseline['flash']) < 2.0, str(baseline))

    (out / 'seats.json').write_text(json.dumps(report['seats'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'attribution.json').write_text(json.dumps(report['attribution'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'behaviour.json').write_text(json.dumps(report['behaviour'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'news-and-prices.json').write_text(
        json.dumps({'news': report['news'], 'price_paths': report['price_paths']},
                   ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'checks.json').write_text(json.dumps(PASS, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'engine.json').write_text(json.dumps(report['engine'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'saves.json').write_text(json.dumps(report['saves'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not all(item['ok'] for item in PASS):
        raise SystemExit(f'{sum(not item["ok"] for item in PASS)} check(s) failed')
    print(f'\n{len(PASS)} checks passed; outputs written to {out}')


if __name__ == '__main__':
    main()
