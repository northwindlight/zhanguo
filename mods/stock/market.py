"""Interactive PAMS exchange, causal public news and next-tick orders."""
from copy import deepcopy
import math
import random

from engine.runtime import GameMod, ModError, RuleSection, Stage
from mods.stock.calls import CALLS
from .backend import PamsBook
from .stocks import STOCKS

OPEN = ('accepted', 'partially_filled')
MAKER = 'stock:market-maker'
CLEARING = 'stock:clearing-bank'
FLOW = 'stock:value-trader'


class Market:
    PRE, SESSION, CLOSED = 'pre', 'session', 'closed'

    def __init__(self, parameters):
        self.parameters = dict(parameters)
        self.commission = parameters['Commission']
        self.float_shares = parameters['FloatShares']
        self.seed = 0
        self.stocks = {r[0]: dict(symbol=r[0], name=r[1], sector=r[2], anchor=r[4]) for r in STOCKS}
        self.close = {s: r['anchor'] for s, r in self.stocks.items()}
        self.opening = dict(self.close)
        self.fundamental = dict(self.close)
        self.history = {s: [p] * 20 for s, p in self.close.items()}
        self.last_path = {s: [] for s in self.stocks}
        self.path = {s: [] for s in self.stocks}
        self.last_open = dict(self.close)
        self.last_depth = {s: dict(bids=[], asks=[]) for s in self.stocks}
        self.news = []
        self.orders = {}
        self.live = {}
        self.pending = {}
        self.last_fills = []
        self.seq = 1
        self.trade_seq = 1
        self.state = self.PRE
        self.backend = None
        self.tick = 0
        self.round = 1
        self.recorder = None
        self._public_cache = None
        self._world = None

    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('market 已绑定到别的世界')
        self._world = world
        return self

    def _port(self, method, *args):
        return self._world.runtime.object_call(self._world, 'stock:portfolio', method, *args)

    def _clock(self, method, *args):
        return self._world.runtime.object_call(self._world, 'stock:clock', method, *args)

    def reseed(self, seed):
        self.seed = seed
        for index, actor in enumerate((MAKER, FLOW, CLEARING)):
            quantity = self.float_shares//3 + (self.float_shares % 3 if index == 2 else 0)
            shares = {s: dict(quantity=quantity, cost=p * quantity) for s, p in self.close.items()}
            self._port('open_account', actor, actor, True, 100000000.0, shares)

    def name_of(self, symbol): return self.stocks.get(symbol, {}).get('name', symbol)
    def borrow_limit(self, symbol): return self.parameters['BorrowShares']
    def price_of(self, symbol, actor=None, tick=None):
        if symbol not in self.stocks:
            raise ValueError('没有这只股票')
        if self.state == self.SESSION:
            return self.backend.mark(symbol, self.close[symbol])
        return self.close[symbol]

    def catalog(self):
        return [dict(symbol=s, name=r['name'], sector=r['sector']) for s, r in self.stocks.items()]

    def history_of(self, symbol, limit=30):
        if symbol not in self.history or type(limit) is not int or not 1 <= limit <= 256:
            raise ValueError('股票不存在，或历史条数不是 1～256 的整数')
        return list(self.history[symbol][-limit:])

    def quotes(self, actor=None):
        if self._public_cache is not None:
            return deepcopy(self._public_cache)
        return self._quotes()

    def freeze_public(self):
        self._public_cache = self._quotes()

    def _quotes(self):
        result = []
        for symbol, stock in self.stocks.items():
            price = self.price_of(symbol)
            history = self.history[symbol]
            depth = self.backend.depth(symbol) if self.state == self.SESSION else self.last_depth[symbol]
            ma = {f'ma{n}': round(sum(history[-n:]) / n, 4) if len(history) >= n else None
                  for n in (5, 10, 20)}
            returns = [history[i]/history[i-1]-1 for i in range(max(1, len(history)-10), len(history))]
            mean = sum(returns)/len(returns) if returns else 0
            volatility = (sum((r-mean)**2 for r in returns)/len(returns))**.5 if returns else 0
            result.append(dict(symbol=symbol, name=stock['name'], sector=stock['sector'],
                price=price, prev_close=self.close[symbol], change_pct=price/self.close[symbol]-1,
                open=self.opening[symbol] if self.state == self.SESSION else self.last_open[symbol],
                path=list(self.path[symbol] if self.state == self.SESSION else self.last_path[symbol]),
                tick=self.tick if self.state == self.SESSION else 0, **ma,
                momentum_5=history[-1]/history[-5]-1, volatility=round(volatility, 6),
                commission_rate=self.commission, float_shares=self.float_shares,
                depth=depth, borrow_available=max(0, self.borrow_limit(symbol)-self._port('short_used', symbol)),
                news=deepcopy([n for n in self.news[-20:] if n['symbol'] == symbol])))
        return result

    def fee_of(self, amount): return round(amount * self.commission, 2)
    def execution_price(self, symbol, side, quantity, actor=None):
        return round(self.price_of(symbol) * (1-.001 if side in ('sell', 'short') else 1.001), 4)
    def sell_quote(self, symbol, quantity, actor=None):
        price = self.execution_price(symbol, 'sell', quantity)
        amount = round(price*quantity, 2)
        return dict(symbol=symbol, price=price, amount=amount, fee=self.fee_of(amount))

    def open_session(self):
        self.round = self._clock('period')
        self._port('begin_day', self.round)
        self.tick = 0
        self.orders, self.pending, self.last_fills = {}, {}, []
        self.live = {}
        self._public_cache = None
        self.backend = PamsBook(self.stocks, self.close, f'{self.seed}|{self.round}')
        self.opening = dict(self.close)
        self.path = {s: [p] for s, p in self.close.items()}
        self.state = self.SESSION
        self._quote_maker()

    def _quote_maker(self):
        self.cancel_actor(MAKER)
        positions = self._port('position_of', MAKER)
        depth = self.parameters['DepthShares']
        spread = self.parameters['Spread']
        for symbol in self.stocks:
            current = self.price_of(symbol)
            inventory = positions['shares'].get(symbol, 0)
            baseline = self.float_shares//3
            bias = max(-.015, min(.015, (baseline-inventory)/max(1, baseline) * .04))
            centre = .7 * current + .3 * self.fundamental[symbol]
            centre *= 1+bias
            for level in range(1, 4):
                for side, sign in (('buy', -1), ('sell', 1)):
                    price = round(max(.05, centre * (1 + sign * spread * level)), 2)
                    try:
                        order = self._place(MAKER, side, symbol, depth, price, background=True)
                        self._deliver(order)
                    except ValueError:
                        continue  # Finite maker cash/inventory; no fabricated liquidity.

    def _value_flow(self):
        # This agent reacts only to the same already-public news. Its trades
        # turn gradual quote discovery into real, finite-cash executions.
        for symbol in self.stocks:
            price = self.price_of(symbol)
            gap = self.fundamental[symbol]/price-1
            if abs(gap) < .004: continue
            side = 'buy' if gap > 0 else 'sell'
            try:
                order = self._place(FLOW, side, symbol, self.parameters['FlowShares'], background=True)
                self._deliver(order)
            except ValueError:
                continue

    def advance(self, tick):
        if tick != self.tick + 1:
            raise ModError('市场必须在一条共同时间线上逐 tick 推进')
        self.tick = tick
        self._public_cache = None
        self._clock('set_tick', tick)
        total = self._clock('slices')
        changed = False
        if self.parameters['NewsEnabled'] and tick in (max(1, total//3), max(2, total*2//3)):
            index = 0 if tick == max(1, total//3) else 1
            rng = random.Random(f'{self.seed}|news|{self.round}|{index}')
            for symbol in self.stocks:
                change = rng.choice((-.04, -.02, .02, .04))
                self.fundamental[symbol] = round(max(.5, self.fundamental[symbol]*(1+change)), 2)
                self.news.append(dict(round=self.round, tick=tick, symbol=symbol,
                                      headline='公司更新公开现金流展望', cashflow_change_pct=change*100))
            self.news = self.news[-40:]
            changed = True
        self.backend.advance(self.fundamental)
        if tick % self.parameters['QuoteInterval'] == 0 or changed:
            self._quote_maker()
            changed = True
        if (tick % self.parameters['FlowInterval'] == 0
                or changed and self.news and self.news[-1]['tick'] == tick):
            self._value_flow()
            changed = True
        batch = self.pending.pop(tick, [])
        for kind, item in batch:
            if kind == 'cancel': self._cancel_now(item)
        actors = sorted({o['actor'] for kind, o in batch if kind == 'order'})
        if actors:
            shift = (self.round + tick) % len(actors)
            actors = actors[shift:] + actors[:shift]
        for actor in actors:
            for kind, item in batch:
                if kind == 'order' and item['actor'] == actor and item['status'] in OPEN:
                    self._deliver(item)
                    changed = True
        if tick % max(1, total//20) == 0:
            for symbol in self.stocks:
                self.path[symbol].append(self.price_of(symbol))
        if changed:
            self._port('risk_check', self.round)

    def _place(self, actor, side, symbol, quantity, price=None, background=False):
        if self.state != self.SESSION or not self._clock('trading_day'):
            raise ValueError('当前未开盘或今天休市，不能买卖')
        if side not in ('buy', 'sell', 'short', 'cover') or symbol not in self.stocks:
            raise ValueError('股票或买卖方向无效')
        if type(quantity) is not int or not 1 <= quantity <= 1000000:
            raise ValueError('数量必须是1～1000000的整数')
        if not background and len(self.pending_orders(actor)) >= 64:
            raise ValueError('本账户未完成委托上限为64笔')
        is_market = price is None
        if price is not None and (type(price) not in (int, float) or not math.isfinite(price)
                                  or not .01 <= price <= 1000000000):
            raise ValueError('限价必须在0.01～1000000000之间')
        if price is None:
            depth = self.backend.depth(symbol)
            ladder = depth['asks'] if side in ('buy', 'cover') else depth['bids']
            if not ladder:
                raise ValueError('对手方没有报价')
            price = ladder[0][0] * (1.02 if side in ('buy', 'cover') else .98)
        price = max(.01, round(price, 2))
        order = dict(id=self.seq, actor=actor, agent_id=0 if background else 1,
                     symbol=symbol, name=self.name_of(symbol), side=side, quantity=quantity,
                     remaining=quantity, filled_quantity=0, fill_price=None, amount=0.0, fee=0.0,
                     price=price, limit_price=price, kind='market' if is_market else 'limit',
                     status='accepted', reason='', placed_round=self.round,
                     placed_tick=self.tick, effective_tick=self.tick+1,
                     background=background)
        self._port('reserve', actor, order, self.commission)
        self.seq += 1
        self.orders[order['id']] = order
        self.live.setdefault(actor, set()).add(order['id'])
        return order

    def place(self, world, actor, side, symbol, quantity, price=None):
        order = self._place(actor, side, symbol, quantity, price)
        self.pending.setdefault(self.tick+1, []).append(('order', order))
        return deepcopy(order)

    def _deliver(self, order):
        buying = order['side'] in ('buy', 'cover')
        for ident in tuple(self.live.get(order['actor'], ())):
            resting = self.orders[ident]
            if (resting['id'] != order['id'] and resting['actor'] == order['actor']
                    and resting['symbol'] == order['symbol'] and resting['status'] in OPEN
                    and resting['id'] in self.backend.native
                    and (resting['side'] in ('buy', 'cover')) != buying
                    and (order['limit_price'] >= resting['limit_price'] if buying
                         else order['limit_price'] <= resting['limit_price'])):
                self._cancel_now(order, 'rejected', '禁止本账户跨槽自成交')
                return
        logs = self.backend.submit(order)
        symbol = order['symbol']
        for log in logs:
            trade_id = f'{self.round}:{self.trade_seq}'
            self.trade_seq += 1
            for native_id, leg in ((log.buy_order_id, 'b'), (log.sell_order_id, 's')):
                target = self.orders[self.backend.order_id(symbol, native_id)]
                target['filled_quantity'] += log.volume
                target['remaining'] -= log.volume
                amount = round(log.price*log.volume, 2)
                fee = 0 if target['background'] else self.fee_of(amount)
                target['amount'] = round(target['amount'] + amount, 2)
                target['fee'] = round(target['fee'] + fee, 2)
                target['fill_price'] = round(target['amount']/target['filled_quantity'], 4)
                target['status'] = 'filled' if target['remaining'] == 0 else 'partially_filled'
                if target['remaining'] == 0:
                    self.live[target['actor']].discard(target['id'])
                fill = dict(execution_id=trade_id+':'+leg, order_id=target['id'],
                            actor=target['actor'], side=target['side'], symbol=symbol,
                            name=self.name_of(symbol), quantity=log.volume, price=log.price,
                            amount=amount, fee=fee, tick=self.tick,
                            remaining=target['remaining'])
                self._port('execute', fill, target['remaining'])
                self.last_fills.append(fill)
                if len(self.last_fills) > 4096: del self.last_fills[:1024]
                if self.recorder and not target['background']:
                    self.recorder(target['actor'], 'stock_fill', {}, fill)
        if order['kind'] == 'market' and order['status'] in OPEN:
            self._cancel_now(order, 'expired', '保护限价内剩余数量无法成交，市价单剩余部分到期')

    def _cancel_now(self, order, status='cancelled', reason='玩家撤单'):
        if order['status'] not in OPEN:
            return
        if order['id'] in self.backend.native:
            self.backend.cancel(order)
        self._port('release', order['actor'], order['id'])
        self.live[order['actor']].discard(order['id'])
        order.update(status=status, reason=reason)

    def cancel(self, world, actor, order_id):
        order = self.orders.get(order_id)
        if order is None or order['actor'] != actor:
            raise ValueError('没有本账户的该笔委托')
        if order['status'] not in OPEN:
            raise ValueError('委托已完成，不能撤单')
        # A cancellation queued before order delivery cancels the submission too.
        self.pending.setdefault(self.tick+1, []).append(('cancel', order))
        return dict(order_id=order_id, status='cancel_requested', effective_tick=self.tick+1)

    def cancel_actor(self, actor):
        for ident in sorted(self.live.get(actor, ())):
            self._cancel_now(self.orders[ident])

    def pending_orders(self, actor=None):
        ids = self.live.get(actor, ()) if actor is not None else set().union(*self.live.values())
        return [deepcopy(self.orders[i]) for i in sorted(ids)]

    def order_history(self, actor):
        return [deepcopy(o) for o in self.orders.values() if o['actor'] == actor][-100:]

    def fills(self, actor=None):
        return [deepcopy(f) for f in self.last_fills if actor is None or f['actor'] == actor]

    def liquidate(self, actor, side, symbol, quantity):
        price = self.execution_price(symbol, side, quantity)
        amount = round(price*quantity, 2)
        fee = self.fee_of(amount)
        bank = self._port('position_of', CLEARING)
        if side == 'sell' and bank['cash'] < amount:
            raise ValueError('清算银行现金不足')
        if side == 'cover' and bank['shares'].get(symbol, 0) < quantity:
            raise ValueError('清算银行股票不足')
        ident = f'{self.round}:liquidation:{self.trade_seq}'
        self.trade_seq += 1
        fill = dict(execution_id=ident, actor=actor, side=side, symbol=symbol,
                    quantity=quantity, price=price, amount=amount, fee=fee)
        self._port('execute', fill, 0)
        opposite = 'buy' if side == 'sell' else 'sell'
        self._port('execute', dict(fill, execution_id=ident+':bank', actor=CLEARING,
                                   side=opposite, fee=0), 0)
        if self.recorder: self.recorder(actor, 'stock_liquidation', {}, fill)
        return fill

    def settle(self, world, round_number, trading_day=True):
        self._public_cache = None
        if self.state == self.SESSION:
            for symbol in self.stocks:
                self.close[symbol] = self.price_of(symbol)
                self.last_depth[symbol] = self.backend.depth(symbol)
            for order in list(self.orders.values()):
                self._cancel_now(order, 'expired', '收盘，Day 委托到期')
        for symbol in self.stocks:
            self.history[symbol].append(self.close[symbol])
            self.last_path[symbol] = list(self.path[symbol])
            self.last_open[symbol] = self.opening[symbol]
        self.pending = {}
        self.state = self.CLOSED
        self._clock('close_session')
        return self.fills()

    def prepare_next_day(self, world, round_number, flow=None, trading_day=True):
        self.state = self.PRE
        self.backend = None
        self.orders = {i: o for i, o in self.orders.items() if not o['background']}

    def to_save(self):
        if self.state == self.SESSION or self.pending:
            raise ValueError('市场只允许在完整交易日边界保存')
        return dict(engine=PamsBook.version, seed=self.seed, close=dict(self.close),
                    opening=dict(self.opening), fundamental=dict(self.fundamental),
                    history=deepcopy(self.history), last_path=deepcopy(self.last_path),
                    last_open=dict(self.last_open), last_depth=deepcopy(self.last_depth),
                    news=deepcopy(self.news), orders=deepcopy(list(self.orders.values())),
                    fills=deepcopy(self.last_fills[-2000:]), seq=self.seq, trade_seq=self.trade_seq,
                    state=self.state, round=self.round)

    def restore(self, state):
        if state.get('engine') != PamsBook.version or state.get('state') == self.SESSION:
            raise ValueError('不支持的市场后端或非边界存档')
        self.seed = state['seed']
        for key in ('close', 'opening', 'fundamental', 'history', 'last_path', 'last_open', 'last_depth', 'news'):
            setattr(self, key, deepcopy(state[key]))
        self.orders = {o['id']: deepcopy(o) for o in state['orders']}
        self.live = {}
        self.last_fills = deepcopy(state['fills'])
        self.seq, self.trade_seq, self.state, self.round = state['seq'], state['trade_seq'], state['state'], state['round']
        self.path = {s: [] for s in self.stocks}
        self.pending, self.backend = {}, None
        self._public_cache = None


class MarketAPI:
    def __init__(self, world): self.world = world
    def quotes(self, actor): return self.world.market.quotes(actor)
    def history(self, actor, symbol, limit=30): return self.world.market.history_of(symbol, limit)
    def catalog(self, actor): return self.world.market.catalog()
    def orders(self, actor): return self.world.market.order_history(actor)
    def fills(self, actor): return self.world.market.fills(actor)
    def buy(self, actor, symbol, quantity, price=None): return self.world.market.place(self.world, actor, 'buy', symbol, quantity, price)
    def sell(self, actor, symbol, quantity, price=None): return self.world.market.place(self.world, actor, 'sell', symbol, quantity, price)
    def short(self, actor, symbol, quantity, price=None): return self.world.market.place(self.world, actor, 'short', symbol, quantity, price)
    def cover(self, actor, symbol, quantity, price=None): return self.world.market.place(self.world, actor, 'cover', symbol, quantity, price)
    def cancel(self, actor, order_id): return self.world.market.cancel(self.world, actor, order_id)


class MarketMod(GameMod):
    def mixins(self): return ()
    def initialize_world(self, world): world.market = Market(self.declaration['Parameters'])
    def objects(self, world): return {'stock:market': world.market}
    def save_state(self, world): return world.market.to_save()
    def load_state(self, world, state): world.market.restore(state)
    def player_api(self, world): return MarketAPI(world)
    def before_tool(self, world, actor, name, arguments):
        CALLS.note(actor, world.clock.period())
    def stages(self):
        return (Stage('stock.market.settle', self._settle, hook='market.settlement', after=('stock.python.run',)),
                Stage('stock.market.prepare', self._prepare, hook='market.settlement', after=('stock.portfolio.settle',)))
    def _settle(self, world, changes): changes['fills'] = world.market.settle(world, world.clock.period())
    def _prepare(self, world, changes): world.market.prepare_next_day(world, world.clock.period())
    def rule_sections(self, world=None):
        return (RuleSection('PAMS 交互市场',
            '市场用 PAMS 0.3.0 订单簿撮合，买卖改变盘口与后续成交；同批匹配按统一清算价成交。'
            '每个交易日20000逻辑tick，计算机尽快运行，不等待现实20分钟。'
            '同tick公共快照一致，委托和撤单最早下一tick生效；市价单有2%价格保护，未成交量到期。'
            'price参数可指定限价，Day限价单可部分成交，收盘撤销；orders/fills只返回自己的委托和成交。'
            '价格由有限资金与库存的背景做市者、玩家委托及公开现金流消息共同形成，默认无随机价格扰动。'
            'quotes含盘口、已发生路径和已公布消息，不含种子、未来消息或未来价格。'
            '可用buy/sell开平多头，short/cover开平空头；做空受借券、保证金和借券费约束。',
            ('行情', 'PAMS', '交易', '做空', 'tick', '订单'), '交互撮合、消息与可见性', system=True),)
