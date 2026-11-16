# seat policy: cash only (inaction baseline). Runs inside the stock script sandbox.
def on_tick():
    api.call('stock:clock', 'sleep', ticks=5000)
