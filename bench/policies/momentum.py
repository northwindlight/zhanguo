# seat policy: momentum — once per trading day, buy the strongest mover of the session so far
# and sell the weakest holding. Deliberately cheap: one quotes call per day, single pass, no sort
# (the sandbox meters steps, so a chatty policy would blow the per-callback budget).
def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day']:
        api.call('stock:clock', 'sleep', ticks=5000)
        return
    day = c['round']
    if state.get('day') == day:
        api.call('stock:clock', 'sleep', ticks=5000)
        return
    state['day'] = day
    rows = api.call('stock:market', 'quotes')
    prev = state.get('prev', {})
    best = None
    worst = None
    next_prev = {}
    for r in rows:
        sym = r['symbol']
        px = r['price']
        next_prev[sym] = px
        old = prev.get(sym)
        if not old or old <= 0:
            continue
        chg = px / old - 1
        if best is None or chg > best[0]:
            best = (chg, sym, px)
        if worst is None or chg < worst[0]:
            worst = (chg, sym)
    state['prev'] = next_prev
    book = api.call('stock:portfolio', 'holdings')
    cash = book['available_cash']
    if best is not None and best[0] > 0.01 and cash > 5000:
        want = int(cash * 0.2 / (best[2] * 1.03))
        if want > 0:
            api.call('stock:market', 'buy', symbol=best[1], quantity=want)
    if worst is not None and worst[0] < -0.01:
        held = 0
        for p in book['positions']:
            if p['symbol'] == worst[1]:
                held = p['quantity']
        if held > 0:
            api.call('stock:market', 'sell', symbol=worst[1], quantity=held)
    api.call('stock:clock', 'sleep', ticks=5000)
