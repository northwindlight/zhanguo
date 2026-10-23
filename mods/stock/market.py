"""股市行情与撮合。

价格引擎借鉴开源 **TraderSim**（Sad-Sun678/TraderSim）的分层做法：均值回归、
动量、波动率、订单流冲击、板块情绪与大盘情绪叠加成下一回合的日内涨跌。
滑点用固定比例的冲击成本近似（参考 tliesnham/quant 的 SlippageModel 思路），
手续费按单边比例收取。

**价格不是瞬间跳的**：每个回合的行情是一整条日内路径，本回合切成若干时间片
（一个行动者一片），行动者进场时市场往前挪一片，股价随之变化。
所以同��个回合里，先行动的人看到开盘价附近，后行动的人看到更靠后的价格；
本回合的收盘价就是最后一个时间片的价格，结算后不再有额外跳空，
只有玩家自己的订单流会把下一回合的开盘价推开一点。

撮合口径：

* 下单时按「当前时间片的价格 ± 冲击成本」锁定成交价，脚本拿到的回执就是这个价；
* 回合末所有玩家的挂单一起按各自锁定价撮合，先进先出；
* 现金不够、持仓不够的挂单在撮合时拒单，原因写进回执；
* 本回合的净买卖量作为订单流，推动下一回合的开盘价。
"""
from __future__ import annotations

import math
import random
import threading

from engine.runtime import GameMod, ModError, RuleSection, Stage

# symbol, 名称, 板块, 初始价, 锚定价, 波动率, 大盘 beta, 板块敏感度, 长期漂移
STOCKS = (
    ('shanhai', '山海智造', '科技', 42.0, 42.0, 0.035, 1.25, 1.10, 0.0006),
    ('qingzhou', '青舟生物', '医药', 28.5, 28.5, 0.030, 0.85, 1.05, 0.0009),
    ('yundu', '云度传媒', '传媒', 15.2, 15.2, 0.042, 1.35, 1.15, -0.0005),
    ('changfeng', '长风电能', '能源', 63.4, 63.4, 0.026, 0.80, 0.95, 0.0011),
    ('beiyuan', '北原乳业', '消费', 21.8, 21.8, 0.022, 0.70, 0.90, 0.0005),
    ('hongfan', '宏帆银行', '金融', 9.6, 9.6, 0.018, 0.95, 1.00, 0.0003),
    ('zhongli', '中力机械', '制造', 34.7, 34.7, 0.028, 1.10, 1.00, 0.0002),
    ('lanhai', '蓝海物流', '交运', 18.9, 18.9, 0.026, 1.00, 1.00, 0.0004),
    ('jinhui', '金晖黄金', '资源', 55.0, 55.0, 0.033, 0.55, 0.85, 0.0008),
    ('taiping', '太平保险', '金融', 12.3, 12.3, 0.020, 0.85, 1.00, 0.0002),
)


class Market:
    """行情与撮合的领域对象：世界的 `world.market`。

    一个回合的行情走三段：

    * **盘前**（玩家行动阶段）：今天还没开盘，玩家只看得到昨收与均线指标；
    * **开盘窗口**（20 分钟）：脚本在这里一起执行，行情按时间片连续变动，
      脚本能看到盘口随自己的进度往前挪；
    * **闭市**：窗口走完，收盘价定下来，账户按收盘价估值。
    """

    PRE = 'pre'
    SESSION = 'session'
    CLOSED = 'closed'

    def __init__(self, seed=0, *, float_shares=20000, impact_rate=0.05, impact_cap=0.05,
                 commission=0.001, flow_impact=0.03, momentum=0.35, reversion=0.05):
        self.seed = seed
        self.float_shares = float_shares
        self.impact_rate = impact_rate
        self.impact_cap = impact_cap
        self.commission = commission
        self.flow_impact = flow_impact
        self.momentum_weight = momentum
        self.reversion_weight = reversion
        self.stocks = {}
        self.history = {}
        for symbol, name, sector, price, anchor, volatility, beta, gamma, drift in STOCKS:
            self.stocks[symbol] = {
                'symbol': symbol, 'name': name, 'sector': sector,
                'anchor': anchor, 'volatility': volatility,
                'beta': beta, 'gamma': gamma, 'drift': drift,
            }
            self.history[symbol] = [price]
        self.opening = {symbol: price for symbol, price in
                        ((row['symbol'], row['anchor']) for row in self.stocks.values())}
        self.daily = {symbol: 0.0 for symbol in self.stocks}
        self.close = {symbol: row['anchor'] for symbol, row in self.stocks.items()}
        self.last_path = {symbol: [] for symbol in self.stocks}
        self.last_open = {symbol: row['anchor'] for symbol, row in self.stocks.items()}
        self.state = self.PRE
        self.orders = {}
        self.seq = 0
        self.last_fills = []
        # 线程模式下并发挂单要串行化取号与入簿，其余读取不受影响。
        self._lock = threading.Lock()
        self._world = None
        self._key = None

    # ── 状态绑定 ────────────────────────────────────────────────
    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('market 已绑定到别的世界')
        self._world = world
        self._key = key
        return self

    # ── 时钟接口 ────────────────────────────────────────────────
    def _tick(self, actor=None):
        world = self._world
        if world is None:
            return 0
        if actor is None:
            return world.runtime.object_call(world, 'stock:clock', 'tick_index')
        return world.runtime.object_call(world, 'stock:clock', 'tick_for', actor)

    def _slices(self):
        world = self._world
        if world is None:
            return 1
        return max(1, world.runtime.object_call(world, 'stock:clock', 'slices'))

    def open_session(self):
        """开盘：今天的价格开始连续变动，脚本在窗口里执行。"""
        self.state = self.SESSION
        return self.state

    def reopen(self):
        """下一回合的盘前：回到只看昨收的状态。"""
        self.state = self.PRE
        return self.state

    # ── 价格 ────────────────────────────────────────────────────
    def price_of(self, symbol, actor=None, tick=None):
        """某个时间片上的价格。

        盘前只看得到昨收（今天还没开盘）；开盘后按时间片连续变动。
        """
        stock = self.stocks.get(symbol)
        if stock is None:
            raise ValueError(f'没有这只股票：{symbol}')
        if self.state == self.PRE:
            return self.close[symbol]
        if tick is None:
            tick = self._tick(actor)
        slices = self._slices()
        ratio = self.daily.get(symbol, 0.0) * (tick / slices)
        return round(self.opening.get(symbol, stock['anchor']) * math.exp(ratio), 2)

    def session_path(self, symbol):
        """本次开盘窗口的价格路径（从开盘到收盘）。"""
        stock = self.stocks.get(symbol)
        if stock is None:
            raise ValueError(f'没有这只股票：{symbol}')
        slices = self._slices()
        base = self.opening.get(symbol, stock['anchor'])
        daily = self.daily.get(symbol, 0.0)
        return [round(base * math.exp(daily * index / slices), 2) for index in range(slices + 1)]

    def visible_path(self, symbol):
        """当前看得见的那条路径：盘前是上一次的，开盘后是今天的。"""
        if self.state == self.SESSION:
            return self.session_path(symbol)
        return list(self.last_path.get(symbol, ()))

    def catalog(self):
        return [{'symbol': s['symbol'], 'name': s['name'], 'sector': s['sector']}
                for s in self.stocks.values()]

    def name_of(self, symbol):
        stock = self.stocks.get(symbol)
        return stock['name'] if stock else symbol

    def history_of(self, symbol, limit=30):
        if symbol not in self.stocks:
            raise ValueError(f'没有这只股票：{symbol}')
        series = self.history[symbol]
        limit = max(1, min(int(limit), len(series)))
        return list(series[-limit:])

    def _moving_average(self, series, window):
        if len(series) < window:
            return None
        return round(sum(series[-window:]) / window, 4)

    def _realized_volatility(self, series, window=10):
        if len(series) < 3:
            return None
        sample = series[-(window + 1):]
        returns = [sample[i] / sample[i - 1] - 1 for i in range(1, len(sample))]
        if not returns:
            return None
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return round(variance ** 0.5, 6)

    def quotes(self, actor=None):
        """行情快照：当前价格、相对上一片的涨跌、均线、动量与交易成本。"""
        tick = self._tick(actor)
        result = []
        for symbol, stock in self.stocks.items():
            series = self.history[symbol]
            price = self.price_of(symbol, actor, tick)
            previous = self.price_of(symbol, actor, tick - 1) if tick > 0 else self.close.get(symbol, price)
            momentum = round(series[-1] / series[-2] - 1, 6) if len(series) >= 2 else 0.0
            result.append({
                'symbol': symbol,
                'name': stock['name'],
                'sector': stock['sector'],
                'price': price,
                'prev_close': self.close.get(symbol, price),
                'change_pct': round(price / previous - 1, 6) if previous else 0.0,
                'open': self.opening.get(symbol, price) if self.state == self.SESSION
                        else self.last_open.get(symbol, price),
                'path': self.visible_path(symbol),
                'ma5': self._moving_average(series, 5),
                'ma10': self._moving_average(series, 10),
                'ma20': self._moving_average(series, 20),
                'momentum_5': momentum,
                'volatility': self._realized_volatility(series),
                'float_shares': self.float_shares,
                'impact_rate': self.impact_rate,
                'impact_cap': self.impact_cap,
                'commission_rate': self.commission,
            })
        return result

    def quote_of(self, symbol):
        for row in self.quotes():
            if row['symbol'] == symbol:
                return row
        raise ValueError(f'没有这只股票：{symbol}')

    # ── 交易成本 ────────────────────────────────────────────────
    def impact_of(self, quantity):
        """冲击成本比例：本次下单量占流通盘的比例。"""
        return min(self.impact_cap, self.impact_rate * abs(quantity) / self.float_shares)

    def execution_price(self, symbol, side, quantity, actor=None):
        """按当前时间片的价格与冲击成本算出成交价（买入更贵、卖出更便宜）。"""
        price = self.price_of(symbol, actor)
        impact = self.impact_of(quantity)
        factor = (1 + impact) if side == 'buy' else (1 - impact)
        return round(price * factor, 4)

    def fee_of(self, amount):
        return round(amount * self.commission, 2)

    def sell_quote(self, symbol, quantity, actor=None):
        """卖出报价：给银行强制平仓用，含冲击成本与手续费。"""
        price = self.execution_price(symbol, 'sell', quantity, actor)
        amount = round(price * quantity, 2)
        return {'symbol': symbol, 'name': self.name_of(symbol), 'quantity': quantity,
                'price': price, 'amount': amount, 'fee': self.fee_of(amount)}

    # ── 挂单 ────────────────────────────────────────────────────
    def place(self, world, actor, side, symbol, quantity):
        """挂一笔市价单：锁定成交价，回执立刻返回给脚本。"""
        if side not in ('buy', 'sell'):
            raise ValueError('方向只能是 buy 或 sell')
        if symbol not in self.stocks:
            raise ValueError(f'没有这只股票：{symbol}')
        if type(quantity) is not int or quantity <= 0:
            raise ValueError('数量必须是正整数')
        runtime = self._runtime(world)
        round_number = runtime.object_call(world, 'stock:clock', 'period')
        if not runtime.object_call(world, 'stock:clock', 'trading_day'):
            raise ValueError('今天是休市日，交易所不开盘，不能买卖。')
        position = runtime.object_call(world, 'stock:portfolio', 'position_of', actor)
        price = self.execution_price(symbol, side, quantity, actor)
        amount = round(price * quantity, 2)
        fee = self.fee_of(amount)
        if side == 'buy':
            if amount + fee > position['cash'] + 1e-9:
                raise ValueError(f'现金不足：这笔要 {round(amount + fee, 2)}，账上只有 {round(position["cash"], 2)}。')
        else:
            held = position['shares'].get(symbol, 0)
            if quantity > held:
                raise ValueError(f'持仓不足：{symbol} 只持有 {held} 股，想卖 {quantity} 股。')
        order = {
            'id': 0,
            'actor': actor,
            'side': side,
            'symbol': symbol,
            'name': self.name_of(symbol),
            'quantity': quantity,
            'price': price,
            'amount': amount,
            'fee': fee,
            'impact_pct': round(self.impact_of(quantity) * 100, 4),
            'status': 'pending',
            'reason': '',
            'placed_round': round_number,
            'placed_tick': self._tick(actor),
        }
        with self._lock:
            order['id'] = self.seq
            self.seq += 1
            self.orders.setdefault(actor, []).append(order)
        return dict(order)

    def cancel(self, world, actor, order_id):
        """撤回本玩家还没成交的挂单。"""
        for order in self.orders.get(actor, ()):
            if order['id'] == order_id:
                if order['status'] != 'pending':
                    raise ValueError('这笔挂单已经成交或被拒，撤不了。')
                order['status'] = 'cancelled'
                order['reason'] = '玩家撤单。'
                return dict(order)
        raise ValueError(f'没有这笔挂单：{order_id}')

    def fills(self):
        """上一回合的成交明细。"""
        return [dict(fill) for fill in self.last_fills]

    def pending_orders(self, actor=None):
        if actor is None:
            return [dict(order) for orders in self.orders.values() for order in orders]
        return [dict(order) for order in self.orders.get(actor, ())]

    # ── 撮合与结算 ──────────────────────────────────────────────
    def settle(self, world, round_number, trading_day=True):
        """回合末统一撮合；然后收盘、记账、推出下一回合的日内路径。"""
        runtime = self._runtime(world)
        cash = {}
        shares = {}
        for actor in self.orders:
            position = runtime.object_call(world, 'stock:portfolio', 'position_of', actor)
            cash[actor] = position['cash']
            shares[actor] = dict(position['shares'])
        fills = []
        flow = {}
        for actor in sorted(self.orders):
            for order in sorted(self.orders[actor], key=lambda item: item['id']):
                symbol = order['symbol']
                quantity = order['quantity']
                price = order['price']
                amount = round(price * quantity, 2)
                fee = self.fee_of(amount)
                if order['side'] == 'buy':
                    if cash[actor] + 1e-9 < amount + fee:
                        order['status'] = 'rejected'
                        order['reason'] = '结算时现金不足，这笔买单被拒。'
                        continue
                    cash[actor] = round(cash[actor] - amount - fee, 2)
                    shares[actor][symbol] = shares[actor].get(symbol, 0) + quantity
                else:
                    held = shares[actor].get(symbol, 0)
                    if held < quantity:
                        order['status'] = 'rejected'
                        order['reason'] = '结算时持仓不足，这笔卖单被拒。'
                        continue
                    cash[actor] = round(cash[actor] + amount - fee, 2)
                    shares[actor][symbol] = held - quantity
                order['status'] = 'filled'
                order['reason'] = ''
                fills.append({'actor': actor, 'side': order['side'], 'symbol': symbol,
                              'name': order['name'], 'quantity': quantity, 'price': price,
                              'amount': amount, 'fee': fee})
                flow[symbol] = flow.get(symbol, 0) + (quantity if order['side'] == 'buy' else -quantity)
        self.last_fills = fills
        self.orders = {}
        self._close_round(world, round_number, flow, trading_day)
        return fills

    def _close_round(self, world, round_number, flow, trading_day):
        """闭市：窗口走完，收盘价定下来；再按订单流推出下一回合的开盘与日内路径。"""
        world.runtime.object_call(world, 'stock:clock', 'close_session')
        closing_tick = self._slices()
        for symbol in self.stocks:
            close = self.price_of(symbol, None, closing_tick)
            self.close[symbol] = close
            self.history[symbol].append(close)
            self.last_path[symbol] = self.session_path(symbol)
            self.last_open[symbol] = self.opening.get(symbol, close)
        for symbol in self.stocks:
            flow_ratio = flow.get(symbol, 0) / self.float_shares
            self.opening[symbol] = round(self.close[symbol] * (1 + self.flow_impact * flow_ratio), 2)
        if trading_day:
            self._daily_return(round_number + 1)
        else:
            for symbol in self.stocks:
                self.daily[symbol] = 0.0
        self.state = self.CLOSED

    def _daily_return(self, round_number):
        """算下一回合的日内涨跌：大盘情绪 + 板块情绪 + 个股波动 + 动量 + 均值回归。"""
        seed = self.seed
        market_ret = random.Random(f'{seed}|mkt|{round_number}').gauss(0, 0.012)
        sectors = sorted({stock['sector'] for stock in self.stocks.values()})
        sector_ret = {sector: random.Random(f'{seed}|sec|{sector}|{round_number}').gauss(0, 0.010)
                      for sector in sectors}
        for symbol, stock in self.stocks.items():
            series = self.history[symbol]
            momentum = 0.0
            if len(series) >= 2:
                momentum = series[-1] / series[-2] - 1
                if len(series) >= 4:
                    momentum = 0.6 * momentum + 0.4 * (series[-1] / series[-4] - 1)
            idio = random.Random(f'{seed}|{symbol}|{round_number}').gauss(0, stock['volatility'])
            opening = self.opening[symbol]
            self.daily[symbol] = (stock['beta'] * market_ret
                                   + stock['gamma'] * sector_ret[stock['sector']]
                                   + stock['drift']
                                   + self.momentum_weight * momentum
                                   + self.reversion_weight * (stock['anchor'] / opening - 1))

    # ── 存档 ────────────────────────────────────────────────────
    def reseed(self, seed):
        """按种子重掷开局行情：同一个 seed 完全可复现。

        开局先给一段历史，玩家第一回合就有均线和动量可用；
        历史之后每回合往后接一段。
        """
        self.seed = seed
        for symbol, stock in self.stocks.items():
            rng = random.Random(f'{seed}|init|{symbol}')
            price = stock['anchor']
            series = [price]
            for _ in range(6):
                shock = rng.gauss(0, stock['volatility'] * 0.5)
                price = max(0.5, round(price * (1 + shock), 2))
                series.append(price)
            self.opening[symbol] = price
            self.close[symbol] = series[-2]
            self.history[symbol] = series
        self._daily_return(1)

    def to_save(self):
        return {
            'stocks': {symbol: dict(stock) for symbol, stock in self.stocks.items()},
            'history': {symbol: list(series) for symbol, series in self.history.items()},
            'opening': dict(self.opening),
            'daily': dict(self.daily),
            'close': dict(self.close),
            'last_path': {symbol: list(path) for symbol, path in self.last_path.items()},
            'last_open': dict(self.last_open),
            'state': self.state,
            'orders': {actor: [dict(order) for order in orders] for actor, orders in self.orders.items()},
            'seq': self.seq,
            'fills': [dict(fill) for fill in self.last_fills],
        }

    def restore(self, state):
        self.stocks = {symbol: dict(stock) for symbol, stock in state['stocks'].items()}
        self.history = {symbol: list(series) for symbol, series in state['history'].items()}
        self.opening = dict(state.get('opening', {}))
        self.daily = dict(state.get('daily', {}))
        self.close = dict(state.get('close', {}))
        self.last_path = {symbol: list(path) for symbol, path in state.get('last_path', {}).items()}
        self.last_open = dict(state.get('last_open', {}))
        self.state = state.get('state', self.PRE)
        self.orders = {actor: [dict(order) for order in orders] for actor, orders in state.get('orders', {}).items()}
        self.seq = state.get('seq', 0)
        self.last_fills = [dict(fill) for fill in state.get('fills', ())]

    def _runtime(self, world):
        return world.runtime


class MarketAPI:
    """玩家侧 API：读行情、挂买单、挂卖单、撤单。"""

    def __init__(self, world):
        self.world = world

    def quotes(self, actor):
        return self.world.market.quotes(actor)

    def history(self, actor, symbol, limit=30):
        return self.world.market.history_of(symbol, limit)

    def catalog(self, actor):
        return self.world.market.catalog()

    def buy(self, actor, symbol, quantity):
        return self.world.market.place(self.world, actor, 'buy', symbol, quantity)

    def sell(self, actor, symbol, quantity):
        return self.world.market.place(self.world, actor, 'sell', symbol, quantity)

    def cancel(self, actor, order_id):
        return self.world.market.cancel(self.world, actor, order_id)


class MarketMod(GameMod):
    def mixins(self):
        return ()

    def numbers(self):
        cfg = self.declaration['Parameters']
        return {f'market.{key}': value for key, value in cfg.items()}

    def initialize_world(self, world):
        cfg = self.declaration['Parameters']
        world.market = Market(getattr(world, 'seed', 0),
                              float_shares=cfg['FloatShares'],
                              impact_rate=cfg['ImpactRate'],
                              impact_cap=cfg['ImpactCap'],
                              commission=cfg['Commission'],
                              flow_impact=cfg['FlowImpact'],
                              momentum=cfg['Momentum'],
                              reversion=cfg['Reversion'])

    def objects(self, world):
        return {'stock:market': world.market}

    def save_state(self, world):
        return world.market.to_save()

    def load_state(self, world, state):
        world.market.restore(state)

    def player_api(self, world):
        return MarketAPI(world)

    def stages(self):
        return (Stage('stock.market.settle', self._settle, hook='market.settlement',
                       after=('stock.python.run',)),
                Stage('stock.market.reopen', self._reopen, hook='cycle.end'),)

    def _reopen(self, world, changes):
        """下一回合的盘前：回到只看昨收的状态。"""
        world.market.reopen()

    def _settle(self, world, changes):
        runtime = self.runtime
        round_number = runtime.object_call(world, 'stock:clock', 'period')
        trading = runtime.object_call(world, 'stock:clock', 'trading_day')
        fills = world.market.settle(world, round_number, trading)
        changes.update(fills=fills)

    def rule_sections(self, world=None):
        cfg = self.declaration['Parameters']
        return (RuleSection(
            '行情与交易成本',
            f'每个回合的行情是一条日内路径，回合切成若干时间片（一个行动者一片）：'
            f'行动者进场时市场往前挪一片，股价随之变化——所以同一回合里，'
            f'后行动的人看到的价格已经比先行动的人走了一段。'
            f'每只股票有现价、昨收、均线、动量与已实现波动，都是公开信息。'
            f'下单按现价加冲击成本锁价：单量占流通盘的比例越高，成交价越吃亏，'
            f'冲击成本最高 {cfg["ImpactCap"] * 100:.0f}%；手续费单边 {cfg["Commission"] * 100:.2f}%。'
            f'回合末所有玩家的挂单一起撮合，先进先出；现金或持仓不够就拒单，原因写进回执。'
            f'本回合的净买卖会推动下一回合的开盘价。',
            ('行情', '报价', '下单', '买入', '卖出', '冲击成本', '手续费', '撮合', '时间片'),
            '行情口径、盘中时间片、冲击成本与撮合规则'),)
