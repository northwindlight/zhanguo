"""零模型引擎实验公共库：用仓库自带离线 stock 发行版跑确定性策略。

不调用任何 LLM / 网络 / 付费接口：玩家全部使用 client='replay'，
策略源码通过配置里的 scripts 表（键为回合号）注入，由沙盒执行。

约定
----
* 每个实验脚本只写自己的结果 JSON；本库只负责开一局、推进回合、记录账本。
* 「约定协议」的缩放参数（session_ticks / max_turns）由调用方显式传入，
  结果 JSON 里必须原样记录，README 说明它只作机制方向。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # D:\code\zhanguo
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from host import GameHost  # noqa: E402  (必须在 sys.path 注入之后)

ACTOR0 = 'stock:player0'
DEFAULT_SEEDS = [11, 22, 33, 44, 55, 66, 77, 88]


# ---------------------------------------------------------------------------
# 配置与哈希
# ---------------------------------------------------------------------------

def config(seed, turns, sources, actors=1, planning_workers=None):
    nations = []
    for i in range(actors):
        source = sources[i] if i < len(sources) else None
        entry = dict(id='stock:player%d' % i, name='策略%d' % i, client='replay')
        entry['scripts'] = {'1': source} if source is not None else {}
        nations.append(entry)
    cfg = dict(mods='mods.stock.toml', seed=int(seed), max_turns=int(turns), nations=nations)
    if planning_workers is not None:
        cfg['planning_workers'] = int(planning_workers)
    return cfg


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------
# 机制干预：在 round 1 开盘前改世界参数（不落盘、不改 mods/）
# ---------------------------------------------------------------------------

def apply_patches(h, patches):
    """patches: {'commission': float, 'depth_shares': int}。

    Commission 在 Market.__init__ 里被复制成 self.commission，DepthShares 则在
    每次做市报价时从 parameters 现读；两处都改，保证整局语义一致。
    这是「制度反事实」：等价于把发行版参数在开局前换成另一套，而非运行中半途改。
    """
    market = h.world.market
    if 'commission' in patches:
        rate = float(patches['commission'])
        market.commission = rate
        market.parameters['Commission'] = rate
    if 'depth_shares' in patches:
        market.parameters['DepthShares'] = int(patches['depth_shares'])
    return patches


# ---------------------------------------------------------------------------
# 一局
# ---------------------------------------------------------------------------

def _round_row(h, actors, player_set, keep_fills, keep_background):
    market = h.world.market
    clock = h.world.clock
    # h.cycle() 之后 clock.round 已经推进到下一回合；刚跑完的是 round-1。
    # 休市日 market.open_session 不运行，market.round / last_fills 会停在上一个
    # 交易日——所以回合号取 clock.round-1，且只有交易日才记录成交。
    session = clock.round - 1
    trading = ((session - 1) % clock.cycle) < clock.trading_days
    row = dict(round=session, clock_round=clock.round, trading_day=bool(trading),
               close={s: round(p, 4) for s, p in market.close.items()},
               actors={})
    for actor in actors:
        account = h.world.portfolio.accounts[actor]
        book = h.world.portfolio.holdings(actor)
        row['actors'][actor] = dict(
            cash=account['cash'], debt=account['debt'],
            wealth=book['wealth'], positions=len(book['positions']),
            alive=account['alive'],
            equity=(account['equity'][-1]['wealth'] if account['equity'] else None))
    if keep_fills and trading:
        picks = []
        for fill in market.last_fills:
            if not keep_background and fill['actor'] not in player_set:
                continue
            picks.append([fill['execution_id'], fill['actor'], fill['side'], fill['symbol'],
                          fill['quantity'], fill['price'], fill['fee'], fill['tick'],
                          fill['order_id']])
        row['fills'] = picks
        row['fills_hash'] = digest(picks)
    elif keep_fills:
        row['fills'] = []
        row['fills_hash'] = digest([])
    return row


def run_once(*, seed, ticks, turns, sources, actors=1, patches=None,
             planning_workers=None, purchase_pool=False, keep_fills=True,
             keep_background=False, world_hashes=False):
    """跑完一整局（turns-1 个交易日 + 最终清算），返回逐回合账本与终局摘要。

    purchase_pool=True 时，每个行动回合开局前由 ACTOR0 买一档算力池（120/回合），
    用来在确定性测试里覆盖「资源采购动作」。
    """
    patches = dict(patches or {})
    cfg = config(seed, turns, sources, actors=actors, planning_workers=planning_workers)
    started = time.perf_counter()
    h = GameHost.new(cfg)
    h.world.clock.session_ticks = int(ticks)
    apply_patches(h, patches)
    initial_symbols = sorted(h.world.market.stocks)
    background_actors = [a for a, row in h.world.portfolio.accounts.items() if row['background']]
    background_initial_wealth = round(sum(
        h.world.portfolio.holdings(a)['wealth'] for a in background_actors), 2)
    actors_list = ['stock:player%d' % i for i in range(actors)]
    player_set = set(actors_list)
    rounds = []
    world_hash_rows = []
    pool_receipts = []
    while not h.finished():
        if purchase_pool and h.world.clock.can_act(ACTOR0):
            receipt = h.player(ACTOR0).submit('compute', {'action': 'buy', 'kind': 'pool'})
            pool_receipts.append(dict(round=h.clock(), ok=bool(receipt.get('ok'))))
        h.cycle()
        rounds.append(_round_row(h, actors_list, player_set, keep_fills, keep_background))
        if world_hashes:
            world_hash_rows.append(digest(h.runtime.save_mod_state(h.world)))
    elapsed = time.perf_counter() - started

    # 终局：清算现金、名次、费用、成交
    accounts = h.world.portfolio.accounts
    players = {}
    for actor in actors_list:
        account = accounts[actor]
        book = h.world.portfolio.holdings(actor)
        fills = [f for r in rounds for f in r.get('fills', []) if f[1] == actor]
        liq = account.get('final_liquidations') or []
        players[actor] = dict(
            final_cash=account.get('final_cash'),
            final_rank=account.get('final_rank'),
            cash=account.get('cash'), debt=account.get('debt'),
            realized_pnl=account.get('realized_pnl'),
            compute_expenses=account.get('compute_expenses'),
            intelligence_expenses=account.get('intelligence_expenses'),
            borrow_fees=account.get('borrow_fees'),
            interest_paid=account.get('interest_paid'),
            alive=account.get('alive'),
            fills_count=len(fills),
            buy_fills=len([f for f in fills if f[2] in ('buy', 'cover')]),
            sell_fills=len([f for f in fills if f[2] in ('sell', 'short')]),
            bought_quantity=sum(f[4] for f in fills if f[2] in ('buy', 'cover')),
            sold_quantity=sum(f[4] for f in fills if f[2] in ('sell', 'short')),
            bought_amount=round(sum(f[4] * f[5] for f in fills if f[2] in ('buy', 'cover')), 2),
            sold_amount=round(sum(f[4] * f[5] for f in fills if f[2] in ('sell', 'short')), 2),
            fill_fees=round(sum(f[6] for f in fills), 2),
            final_liq_count=len(liq),
            final_liq_fees=round(sum(x['fee'] for x in liq), 2),
            final_liquidations=liq,
            holdings_end=[dict(symbol=p['symbol'], quantity=p['quantity'],
                               price=p['price'], market_value=p['market_value'])
                          for p in book['positions']],
            equity_curve=[dict(round=point['round'], wealth=point['wealth'])
                          for point in account['equity']],
            script_state=h.world.scripts.rows.get(actor, {}).get('1', {}).get('state'),
        )

    final_close = {s: round(p, 4) for s, p in h.world.market.close.items()}
    background_final_wealth = round(sum(
        h.world.portfolio.holdings(a)['wealth'] for a in background_actors if a in accounts), 2)
    entry_close = rounds[0]['close']
    common = [s for s in initial_symbols if s in entry_close and s in final_close and entry_close[s]]
    counterfactual = (sum(final_close[s] / entry_close[s] - 1 for s in common) / len(common)
                      if common else None)
    payload = dict(
        seed=int(seed), ticks=int(ticks), turns=int(turns), actors=actors,
        sources=sources, patches=patches, purchase_pool=bool(purchase_pool),
        planning_workers=planning_workers,
        initial_symbols=initial_symbols, common_symbols=common,
        rounds=rounds, players=players,
        final_close=final_close, entry_close=entry_close,
        counterfactual_equal_weight=counterfactual,
        pool_receipts=pool_receipts,
        elapsed_sec=round(elapsed, 3),
        background=dict(
            actors=background_actors,
            initial_wealth=background_initial_wealth,
            final_wealth=background_final_wealth,
            wealth_change=round(background_final_wealth - background_initial_wealth, 2),
            fees_collected=round(h.world.portfolio.fees_collected, 2),
            resource_fees=round(h.world.portfolio.resource_fees, 2),
            intelligence_fees=round(h.world.portfolio.intelligence_fees, 2),
        ),
    )
    if world_hashes:
        payload['world_hashes'] = world_hash_rows
        payload['final_world_hash'] = world_hash_rows[-1] if world_hash_rows else None
    payload['ledger_hash'] = digest(dict(
        seed=int(seed), ticks=int(ticks), turns=int(turns), rounds=rounds,
        players={a: {k: v for k, v in row.items() if k not in ('script_state',)}
                 for a, row in players.items()},
    ))
    payload['fill_hash'] = digest([f for r in rounds for f in r.get('fills', [])])
    return payload


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------

def _stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    median = (ordered[(n - 1) // 2] + ordered[n // 2]) / 2
    return dict(n=n, mean=round(mean, 4), median=round(median, 4),
                min=round(ordered[0], 4), max=round(ordered[-1], 4),
                stdev=round((sum((v - mean) ** 2 for v in ordered) / n) ** .5, 4))


def end_weighted_counterfactual(game):
    """把期末实际仓位从第 1 回合收盘一直拿到终局收盘的收益（等初始权重口径）。

    对 hold10 这就是论文里的「被动等权持有」引擎版反事实：
    分子/分母都只用终局清算的股数，因此与真实成交价、费用无关。
    """
    liq = game['players'][ACTOR0]['final_liquidations']
    entry, final = game['entry_close'], game['final_close']
    numerator = denominator = 0.0
    for row in liq:
        symbol = row['symbol']
        if symbol not in entry or symbol not in final:
            continue
        numerator += row['quantity'] * final[symbol]
        denominator += row['quantity'] * entry[symbol]
    return (numerator / denominator - 1) if denominator else None


def deployed_return(game):
    """只对实际投出去的钱算收益：终局清算 + 中途卖出净收入 / 建仓现金支出 - 1。"""
    player = game['players'][ACTOR0]
    sell_fees = 0.0
    for round_row in game['rounds']:
        for fill in round_row.get('fills', []):
            if fill[1] == ACTOR0 and fill[2] in ('sell', 'short'):
                sell_fees += fill[6]
    entry_cost = player['bought_amount'] + (player['fill_fees'] - sell_fees)
    proceeds = sum(x['amount'] for x in player['final_liquidations']) - \
        sum(x['fee'] for x in player['final_liquidations'])
    exit_net = player['sold_amount'] - sell_fees + proceeds
    return (exit_net / entry_cost - 1) if entry_cost else None, entry_cost, exit_net


def summarize_baselines(results):
    """按策略汇总。results: [(strategy, game_payload), ...]

    反事实统一取同 seed「空仓不动」局的价格路径（无玩家交易扰动）：
    * counterfactual_pct = 10 只初始股等权、第 1 回合收盘到终局收盘（无费用）；
    这是论文 +31.46%/+32.46% 那一行的引擎内同口径版本。
    """
    by_strategy = {}
    for strategy, game in results:
        by_strategy.setdefault(strategy, []).append(game)
    cash_by_seed = {g['seed']: g for g in by_strategy.get('cash', [])}
    hold_by_seed = {g['seed']: g for g in by_strategy.get('hold10', [])}

    out = dict(protocol=dict(seed_list=sorted({r[1]['seed'] for r in results})),
               strategies={})
    for strategy, games in by_strategy.items():
        rows = []
        for game in games:
            player = game['players'][ACTOR0]
            initial = 100000.0
            final = player['final_cash']
            pure = cash_by_seed.get(game['seed'])
            pure_cf = pure['counterfactual_equal_weight'] if pure else None
            own_cf = game['counterfactual_equal_weight']
            end_cf = end_weighted_counterfactual(game)
            dep_ret, entry_cost, exit_net = deployed_return(game)
            hold = hold_by_seed.get(game['seed'])
            hold_return = (hold['players'][ACTOR0]['final_cash'] / initial - 1) if hold else None
            rows.append(dict(
                seed=game['seed'],
                final_cash=round(final, 2),
                return_pct=round(final / initial - 1, 6),
                counterfactual_pure_pct=None if pure_cf is None else round(pure_cf, 6),
                counterfactual_own_pct=None if own_cf is None else round(own_cf, 6),
                counterfactual_end_weighted_pct=None if end_cf is None else round(end_cf, 6),
                excess_vs_cash_pct=round(final / initial - 1, 6),
                excess_vs_hold_pct=None if hold_return is None else
                round(final / initial - 1 - hold_return, 6),
                excess_vs_pure_cf_pct=None if pure_cf is None else
                round(final / initial - 1 - pure_cf, 6),
                deployed_return_pct=None if dep_ret is None else round(dep_ret, 6),
                deployed_cost_pct=None if dep_ret is None or pure_cf is None else
                round((dep_ret - pure_cf) * 100, 4),
                entry_cost=round(entry_cost, 2),
                fills_count=player['fills_count'],
                fill_fees=round(player['fill_fees'], 2),
                final_liq_fees=round(player['final_liq_fees'], 2),
                explicit_fees=round(player['fill_fees'] + player['final_liq_fees'], 2),
                compute_expenses=round(player['compute_expenses'] or 0.0, 2),
                end_positions=len(player['final_liquidations']),
            ))
        out['strategies'][strategy] = dict(
            n=len(rows),
            final_cash=_stats([r['final_cash'] for r in rows]),
            return_pct=_stats([r['return_pct'] for r in rows]),
            counterfactual_pure_pct=_stats([r['counterfactual_pure_pct'] for r in rows]),
            counterfactual_end_weighted_pct=_stats([r['counterfactual_end_weighted_pct']
                                                    for r in rows]),
            excess_vs_cash_pct=_stats([r['excess_vs_cash_pct'] for r in rows]),
            excess_vs_hold_pct=_stats([r['excess_vs_hold_pct'] for r in rows]),
            excess_vs_pure_cf_pct=_stats([r['excess_vs_pure_cf_pct'] for r in rows]),
            deployed_cost_pct=_stats([r['deployed_cost_pct'] for r in rows]),
            fills=_stats([r['fills_count'] for r in rows]),
            explicit_fees=_stats([r['explicit_fees'] for r in rows]),
            rows=rows,
        )
    return out


# ---------------------------------------------------------------------------
# 沙盒策略源码（Python 子集：无 import / lambda / f-string / set）
# ---------------------------------------------------------------------------

CASH = None

#: 第 1 回合收盘附近等权买入 10 只初始股，之后不动。
#: 最后 1000 tick 分 5 批吃当前盘口（每批 ≤ 2% 保护价内的全部卖量），
#: 尽量把 10 万现金等权投出去；剩余现金留仓。
HOLD10 = '''def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        return
    if c['round'] != 1:
        api.call('stock:clock', 'sleep', ticks=c['slices'])
        return
    total = c['slices']
    t = c['tick']
    step = state.get('step', 0)
    if step == 0:
        if t < total - 1000:
            api.call('stock:clock', 'sleep', ticks=total - 1000 - t)
            return
        rows = api.call('stock:market', 'quotes')
        book = api.call('stock:portfolio', 'holdings')
        cash = book['available_cash']
        target = {}
        for r in rows:
            target[r['symbol']] = int(cash * 0.096 / (r['price'] * 1.03))
        state['target'] = target
        state['step'] = 1
        api.call('stock:clock', 'sleep', ticks=200)
        return
    if step < 6:
        rows = api.call('stock:market', 'quotes')
        book = api.call('stock:portfolio', 'holdings')
        held = {}
        for p in book['positions']:
            held[p['symbol']] = p['quantity']
        target = state.get('target', {})
        for r in rows:
            sym = r['symbol']
            want = target.get(sym, 0) - held.get(sym, 0)
            depth = r['depth']['asks']
            avail = 0
            if depth:
                best = depth[0][0]
                for lvl in depth:
                    if lvl[0] <= best * 1.02:
                        avail = avail + int(lvl[1])
            qty = min(want, avail)
            if qty > 0:
                api.call('stock:market', 'buy', symbol=sym, quantity=qty)
        state['step'] = step + 1
        api.call('stock:clock', 'sleep', ticks=150)
        return
    api.call('stock:clock', 'sleep', ticks=total)
'''

#: 简单均线：ma5 上穿 ma20 建仓（每只最多 10% 现金），下穿清仓。
MA = '''def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        return
    rows = api.call('stock:market', 'quotes')
    book = api.call('stock:portfolio', 'holdings')
    held = {}
    for p in book['positions']:
        held[p['symbol']] = p['quantity']
    cash = book['available_cash']
    for r in rows:
        sym = r['symbol']
        ma5 = r['ma5']
        ma20 = r['ma20']
        if ma5 is None or ma20 is None:
            continue
        qty = held.get(sym, 0)
        if ma5 > ma20 and qty == 0:
            buy = int(cash * 0.10 / (r['price'] * 1.03))
            if buy > 0:
                api.call('stock:market', 'buy', symbol=sym, quantity=buy)
                cash = cash - buy * r['price'] * 1.03
        elif ma5 < ma20 and qty > 0:
            api.call('stock:market', 'sell', symbol=sym, quantity=qty)
    api.call('stock:clock', 'sleep', ticks=500)
'''

#: 可复现噪声（沙盒禁 random，用 LCG）：每次唤醒随机器决定买 5% 现金或清仓一只。
NOISE = '''def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        return
    n = state.get('n', 1)
    n = (n * 1103515245 + 12345) % 2147483648
    state['n'] = n
    rows = api.call('stock:market', 'quotes')
    if not rows:
        return
    idx = n % len(rows)
    sym = rows[idx]['symbol']
    price = rows[idx]['price']
    n2 = (n * 1103515245 + 12345) % 2147483648
    side = n2 % 2
    book = api.call('stock:portfolio', 'holdings')
    held = {}
    for p in book['positions']:
        held[p['symbol']] = p['quantity']
    cash = book['available_cash']
    if side == 0:
        qty = int(cash * 0.05 / (price * 1.03))
        if qty > 0:
            api.call('stock:market', 'buy', symbol=sym, quantity=qty)
    else:
        q = held.get(sym, 0)
        if q > 0:
            api.call('stock:market', 'sell', symbol=sym, quantity=q)
    api.call('stock:clock', 'sleep', ticks=200)
'''

#: 高频往返：一次回调买 50 股，下一次回调清掉同一只（固定 shanhai）。
CHURN = '''def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        return
    rows = api.call('stock:market', 'quotes')
    sym = rows[0]['symbol']
    price = rows[0]['price']
    book = api.call('stock:portfolio', 'holdings')
    held = 0
    for p in book['positions']:
        if p['symbol'] == sym:
            held = p['quantity']
    if held > 0:
        api.call('stock:market', 'sell', symbol=sym, quantity=held)
    else:
        qty = min(50, int(book['available_cash'] * 0.05 / (price * 1.02)))
        if qty > 0:
            api.call('stock:market', 'buy', symbol=sym, quantity=qty)
    api.call('stock:clock', 'sleep', ticks=100)
'''

#: 固定冲击：第 1 回合收盘附近，对每只初始股各下一个 200 股市价买（测深度/成交率）。
SWEEP = '''def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        return
    if c['round'] != 1:
        return
    if state.get('done'):
        return
    if c['tick'] < c['slices'] - 500:
        api.call('stock:clock', 'sleep', ticks=c['slices'] - 500 - c['tick'])
        return
    rows = api.call('stock:market', 'quotes')
    pre = {}
    for r in rows:
        depth = r['depth']['asks']
        top = 0
        volume = 0
        if depth:
            top = depth[0][0]
            for lvl in depth:
                volume = volume + int(lvl[1])
        pre[r['symbol']] = [top, volume]
    state['pre'] = pre
    for r in rows:
        api.call('stock:market', 'buy', symbol=r['symbol'], quantity=200)
    state['done'] = 1
    api.call('stock:clock', 'sleep', ticks=c['slices'])
'''

#: 收盘拉抬（settlement mark manipulation）。占位符 __MANIP_ROUND__ / __SYMBOL__ /
#: __DOSE_PER_ORDER__ / __ORDERS__ 由 mark_close 实验替换。
MARK_CLOSE = '''def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        return
    if c['round'] != __MANIP_ROUND__:
        return
    total = c['slices']
    start = total - __ORDERS__ * 100 - 100
    if c['tick'] < start:
        api.call('stock:clock', 'sleep', ticks=start - c['tick'])
        return
    if state.get('done', 0) >= __ORDERS__:
        return
    rows = api.call('stock:market', 'quotes')
    for r in rows:
        if r['symbol'] == '__SYMBOL__':
            book = api.call('stock:portfolio', 'holdings')
            cash = book['available_cash']
            depth = r['depth']['asks']
            avail = 0
            if depth:
                best = depth[0][0]
                for lvl in depth:
                    if lvl[0] <= best * 1.02:
                        avail = avail + int(lvl[1])
            budget = int(__DOSE_PER_ORDER__)
            if cash < budget:
                budget = int(cash)
            qty = min(avail, int(budget / (r['price'] * 1.03)))
            if qty > 0:
                api.call('stock:market', 'buy', symbol=r['symbol'], quantity=qty)
                state['done'] = state.get('done', 0) + 1
                state['spent_round'] = state.get('spent_round', 0) + qty
    api.call('stock:clock', 'sleep', ticks=100)
'''


def mark_close_source(round_number, symbol, dose_per_order, orders):
    return (MARK_CLOSE.replace('__MANIP_ROUND__', str(round_number))
            .replace('__SYMBOL__', str(symbol))
            .replace('__DOSE_PER_ORDER__', str(int(dose_per_order)))
            .replace('__ORDERS__', str(int(orders))))
