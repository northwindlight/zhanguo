# seat policy: equal-weight buy & hold of every listed symbol on the first trading day.
# Runs inside the stock script sandbox (no imports, no host Python).
def on_tick():
    c = api.call('stock:clock', 'snapshot')
    if not c['trading_day'] or state.get('done'):
        api.call('stock:clock', 'sleep', ticks=5000)
        return
    rows = api.call('stock:market', 'quotes')
    book = api.call('stock:portfolio', 'holdings')
    live = [r for r in rows if r['price'] > 0]
    if live:
        target = int(book['available_cash'] * 0.95 / len(live))
        for r in live:
            ladder = r['depth']['asks']
            avail = 0
            if ladder:
                best = ladder[0][0]
                for lvl in ladder:
                    if lvl[0] <= best * 1.02:
                        avail = avail + int(lvl[1])
            qty = min(int(target / (r['price'] * 1.03)), avail)
            if qty > 0:
                api.call('stock:market', 'buy', symbol=r['symbol'], quantity=qty)
    state['done'] = 1
    api.call('stock:clock', 'sleep', ticks=5000)
