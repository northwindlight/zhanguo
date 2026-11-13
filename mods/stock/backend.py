"""Pinned PAMS 0.3.0 adapter. Only this module uses PAMS private interfaces.

PAMS matches a batch at its clearing price (last matched priced pair), not at
separate resting prices. Brokerage accounting remains owned by stock:portfolio.
"""
import random


class PamsBook:
    version = 'pams/0.3.0'

    @staticmethod
    def require():
        try:
            import pams
            from importlib.metadata import version
            installed = version('pams')
        except ImportError as exc:
            raise ValueError('stock 需要 PAMS：请安装 requirements-stock.txt') from exc
        if installed != '0.3.0':
            raise ValueError('stock 后端要求 pams==0.3.0')

    def __init__(self, stocks, prices, seed):
        self.require()
        from pams import Market, Simulator, LIMIT_ORDER, Order, Cancel
        self.Order, self.Cancel, self.limit_kind = Order, Cancel, LIMIT_ORDER
        self.simulator = Simulator(prng=random.Random(seed))
        self.books = {}
        self.native = {}
        self.mapping = {}
        for index, symbol in enumerate(stocks):
            book = Market(index, random.Random(f'{seed}|{symbol}'), self.simulator, symbol)
            book.setup(dict(tickSize=.01, marketPrice=prices[symbol]))
            book._is_running = True
            book._update_time(prices[symbol])
            self.books[symbol] = book

    def advance(self, fundamentals):
        for symbol, book in self.books.items():
            book._update_time(fundamentals[symbol])

    def submit(self, order):
        book = self.books[order['symbol']]
        native = self.Order(agent_id=order['agent_id'], market_id=book.market_id,
                            is_buy=order['side'] in ('buy', 'cover'), kind=self.limit_kind,
                            volume=order['quantity'], price=order['limit_price'])
        book._add_order(native)
        self.native[order['id']] = native
        self.mapping[(order['symbol'], native.order_id)] = order['id']
        return book._execution()

    def cancel(self, order):
        native = self.native.get(order['id'])
        if native is not None and native.volume > 0 and not native.is_canceled:
            self.books[order['symbol']]._cancel_order(self.Cancel(native))

    def order_id(self, symbol, native_id):
        return self.mapping[(symbol, native_id)]

    def depth(self, symbol):
        book = self.books[symbol]
        buys = book.get_buy_order_book()
        sells = book.get_sell_order_book()
        return dict(bids=[[p, q] for p, q in sorted(buys.items(), reverse=True)[:10] if p is not None],
                    asks=[[p, q] for p, q in sorted(sells.items())[:10] if p is not None])

    def mark(self, symbol, fallback):
        book = self.books[symbol]
        # Unfilled quotes must not manufacture portfolio wealth. With no trades
        # today, the last close remains the mark; depth is separately public.
        last = book.get_last_executed_price()
        return round(last if last is not None else fallback, 2)
