# 策略动物园：四个风格互斥的基准策略，用于检验引擎的策略区分度。
# 全部只用公开信息（quotes/history/holdings），兼容沙盒限制：
# 不用 lambda、推导式和 set.add；只用循环、字典和元组比较。
#
# 用回放客户端把它们放进同一个市场（键为回合号）：
#   {"id":"stock:reversion","client":"replay","scripts":{
#     "1": open('strategy_zoo.py').read().split('# ---')[1], ...}}
# 或参考 probe_review.py --causal-controls 的对照写法。

# --- news：跟最新公开消息（含财报），利好买入、利空清仓 ---
def on_tick():
    calendar = api.call('stock:clock', 'snapshot')
    if not calendar['trading_day']:
        return
    quotes = api.call('stock:market', 'quotes')
    for row in quotes:
        if not row['news']:
            continue
        event = row['news'][-1]
        key = row['symbol']
        seen = str(event['round']) + ':' + str(event['tick'])
        if state.get(key) == seen:
            continue
        state[key] = seen
        book = api.call('stock:portfolio', 'holdings')
        held = {}
        for p in book['positions']:
            held[p['symbol']] = p['available_quantity']
        if event['cashflow_change_pct'] < 0 and held.get(key, 0) > 0:
            api.call('stock:market', 'sell', symbol=key, quantity=held[key])
        elif event['cashflow_change_pct'] > 0 and not held.get(key, 0):
            quantity = min(60, int(book['available_cash'] * 0.05 / (row['price'] * 1.05)))
            if quantity > 0:
                api.call('stock:market', 'buy', symbol=key, quantity=quantity)
    api.call('stock:clock', 'sleep', ticks=200)

# --- rotate：持有日内涨跌幅最大/最小的三只，定期轮动（反向把 < 换成 > 即均值回归版）---
def on_tick():
    calendar = api.call('stock:clock', 'snapshot')
    if not calendar['trading_day']:
        return
    rows = api.call('stock:market', 'quotes')
    chg = {}
    price = {}
    for r in rows:
        chg[r['symbol']] = r['change_pct']
        price[r['symbol']] = r['price']
    symbols = []
    for r in rows:
        symbols.append(r['symbol'])
    want = []
    for i in range(3):
        best = None
        for sym in symbols:
            if sym in want:
                continue
            if best is None or chg[sym] > chg[best]:
                best = sym
        want.append(best)
    held = {}
    for p in api.call('stock:portfolio', 'holdings')['positions']:
        held[p['symbol']] = p['available_quantity']
    for sym in held:
        if held[sym] > 0 and sym not in want:
            api.call('stock:market', 'sell', symbol=sym, quantity=held[sym])
    cash = api.call('stock:portfolio', 'holdings')['available_cash']
    for sym in want:
        if sym not in held and cash > price[sym] * 40:
            quantity = int(cash * 0.3 / (price[sym] * 1.02))
            if quantity > 0:
                api.call('stock:market', 'buy', symbol=sym, quantity=quantity)
                cash = cash - quantity * price[sym] * 1.02
    api.call('stock:clock', 'sleep', ticks=1500)

# --- hold：开盘等权买入并持有，纯市场贝塔 ---
def on_tick():
    calendar = api.call('stock:clock', 'snapshot')
    if not calendar['trading_day']:
        return
    if state.get('done'):
        return
    rows = api.call('stock:market', 'quotes')
    cash = api.call('stock:portfolio', 'holdings')['available_cash']
    for r in rows:
        quantity = int(cash / 10 / (r['price'] * 1.02))
        if quantity > 0:
            api.call('stock:market', 'buy', symbol=r['symbol'], quantity=quantity)
    state['done'] = True
    api.call('stock:clock', 'sleep', ticks=5000)
