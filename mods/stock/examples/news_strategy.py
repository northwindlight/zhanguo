# Automatic trading slot; only public, already released news is used.
def on_tick():
    calendar = api.call('stock:clock', 'snapshot')
    if not calendar['trading_day']:
        return
    quotes = api.call('stock:market', 'quotes')
    book = api.call('stock:portfolio', 'holdings')
    held = {}
    for row in book['positions']:
        held[row['symbol']] = row['available_quantity']
    cash = book['available_cash']
    for row in quotes:
        if not row['news']:
            continue
        event = row['news'][-1]
        key = row['symbol']
        seen = str(event['round']) + ':' + str(event['tick'])
        if state.get(key) == seen:
            continue
        state[key] = seen
        if event['cashflow_change_pct'] < 0 and held.get(key, 0) > 0:
            api.call('stock:market', 'sell', symbol=key, quantity=held[key])
        elif event['cashflow_change_pct'] > 0 and not held.get(key, 0):
            quantity = min(50, int(cash * 0.03 / (row['price'] * 1.03)))
            if quantity > 0:
                api.call('stock:market', 'buy', symbol=key, quantity=quantity)
    api.call('stock:clock', 'sleep', ticks=100)
