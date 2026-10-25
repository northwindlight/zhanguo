"""Deterministic factor dynamics behind fundamentals, news and the calendar.

Everything here is a pure function of the game seed and the round: the same
seed always replays the identical world, and factor levels, the volatility
regime and unpublished events stay private to the engine. Players only see
prices, order books and already-published news.

Structure added on top of the persistent per-stock fundamental level:
- a market factor with calm/storm volatility regimes (persistent via a
  deterministic replay chain, so storms span several trading days);
- mean-reverting sector factors (co-movement that decays, i.e. pairs material);
- per-stock transitory noise (OU) plus a tiny permanent walk and drift;
- a public event calendar: one market-wide wire, one staggered stock wire per
  day, and scheduled earnings every REPORT_CYCLE trading days per stock.
"""
import math
import random

STEP_TICKS = 8                 # fundamental refresh granularity in ticks
MARKET_VOL = .012              # daily market-factor volatility, calm-normalised
SECTOR_VOL = .006              # sector-factor stationary daily volatility
SECTOR_HALF_LIFE = 2200        # ticks
TRANSIENT_SHARE = .35          # share of idio vol allocated to OU transients
WALK_SHARE = .55               # share of idio vol allocated to the permanent walk
EVENT_OVERSHOOT = .20          # fraction of an event jump added to the transient
REGIME_TRANSITIONS = {'calm': ('storm', .30), 'storm': ('calm', .55)}  # daily switch probabilities
REGIMES = {'calm': dict(vol=.65, spread=.9), 'storm': dict(vol=2.1, spread=1.6)}
MARKET_WIRE_FRACTION = 1 / 3   # market-wide wire tick position within the day
EARNINGS_FRACTION = .2         # earnings tick position within the day
WIRE_MIN_FRACTION, WIRE_SPAN_FRACTION = .45, .22  # staggered stock-wire window
REPORT_CYCLE = 5               # trading days between two earnings of one stock


def _phi(half_life):
    """Per-step AR(1) coefficient for an OU process with the given half-life."""
    return math.exp(-STEP_TICKS / max(1.0, float(half_life)))


class Dynamics:
    def __init__(self, market):
        self.market = market
        self.sectors = sorted({r['sector'] for r in market.stocks.values()})
        self.sector_phi = _phi(SECTOR_HALF_LIFE)
        self.idio_phi = {r['symbol']: _phi(600 + 1800 * r['vol'] / .042)
                         for r in market.stocks.values()}
        self.calendar = dict(trading_days=5, rest_days=2, cycle=7)
        self.regime = 'calm'
        self.sector_level = {g: 0.0 for g in self.sectors}
        self.events = {}
        self.report_tick = None

    # -- calendar ---------------------------------------------------------
    def calendar_setup(self):
        info = self.market._clock('cycle_info')
        self.calendar = dict(trading_days=info['trading_days'],
                             rest_days=info['rest_days'],
                             cycle=info['trading_days'] + info['rest_days'])

    def trading_seq(self, round_number):
        """1-based index among trading days, None on rest days."""
        week, day = divmod(round_number - 1, self.calendar['cycle'])
        if day >= self.calendar['trading_days']:
            return None
        return week * self.calendar['trading_days'] + day + 1

    def reports_on(self, symbol, round_number):
        seq = self.trading_seq(round_number)
        offset = self.market.stocks[symbol]['report_offset']
        return seq is not None and seq % REPORT_CYCLE == offset % REPORT_CYCLE

    def next_report(self, symbol):
        for candidate in range(self.market.round, self.market.round + 3 * self.calendar['cycle'] + 1):
            if self.reports_on(symbol, candidate):
                return candidate
        return None

    # -- session lifecycle --------------------------------------------------
    def open_session(self):
        self.calendar_setup()
        self.regime = self._regime_for(self.market.round)
        self.sector_level = {g: 0.0 for g in self.sectors}
        for symbol in self.market.stocks:
            self.market.transient[symbol] = 0.0
        total = self.market._clock('slices')
        self.report_tick = round(total * EARNINGS_FRACTION)
        self.events = {}
        for symbol, meta in self.market.stocks.items():
            if self.reports_on(symbol, self.market.round):
                self.events.setdefault(self.report_tick, []).append(('earnings', symbol))
            rng = random.Random(f'{self.market.seed}|wire|{self.market.round}|{symbol}')
            tick = round(total * (WIRE_MIN_FRACTION + WIRE_SPAN_FRACTION * rng.random()))
            self.events.setdefault(tick, []).append(('wire', symbol))
        self.events.setdefault(max(1, round(total * MARKET_WIRE_FRACTION)), []).append(('market', None))

    def _regime_for(self, round_number):
        state = 'calm'
        for step in range(1, round_number + 1):
            roll = random.Random(f'{self.market.seed}|regime|{step}').random()
            target, probability = REGIME_TRANSITIONS[state]
            if roll < probability:
                state = target
        return state

    # -- per-tick evolution -------------------------------------------------
    def step(self, tick, total):
        """Fire scheduled events and diffuse factors; True when news landed."""
        changed = False
        for kind, symbol in self.events.pop(tick, ()):
            self._fire(kind, symbol, tick)
            changed = True
        if tick % STEP_TICKS == 0:
            self._diffuse(tick, total)
        return changed

    def _diffuse(self, tick, total):
        market = self.market
        scale = (STEP_TICKS / max(1, total)) ** .5
        step_index = tick // STEP_TICKS
        vol_mult = REGIMES[self.regime]['vol']
        dm = random.Random(f'{market.seed}|mkt|{market.round}|{step_index}').gauss(
            0, MARKET_VOL * vol_mult * scale)
        for group in self.sectors:
            z = random.Random(f'{market.seed}|sector|{market.round}|{group}|{step_index}').gauss(0, 1)
            self.sector_level[group] = (self.sector_level[group] * self.sector_phi
                                        + SECTOR_VOL * (1 - self.sector_phi ** 2) ** .5 * z)
        for symbol, meta in market.stocks.items():
            rng = random.Random(f'{market.seed}|idio|{market.round}|{symbol}|{step_index}')
            z, walk_z = rng.gauss(0, 1), rng.gauss(0, 1)
            phi = self.idio_phi[symbol]
            sigma = meta['vol'] * TRANSIENT_SHARE
            market.transient[symbol] = (market.transient[symbol] * phi
                                        + sigma * (1 - phi * phi) ** .5 * z)
            walk = meta['vol'] * WALK_SHARE * scale * walk_z
            drift = meta['drift'] * STEP_TICKS / max(1, total)
            change = meta['beta'] * dm + walk + drift
            if abs(change) > .05:  # keep one step from tearing the price grid apart
                change = math.copysign(.05, change)
            market.fundamental[symbol] = round(max(.5, market.fundamental[symbol] * (1 + change)), 2)

    def _fire(self, kind, symbol, tick):
        market = self.market
        if kind == 'market':
            shock = random.Random(f'{market.seed}|market-wire|{market.round}').choice((-.03, -.015, .015, .03))
            for sym, meta in market.stocks.items():
                self._apply(sym, shock * meta['beta'] * meta['sens'], tick, kind)
        elif kind == 'wire':
            meta = market.stocks[symbol]
            rng = random.Random(f'{market.seed}|stock-wire|{market.round}|{symbol}')
            jump = rng.choice((-1, 1)) * rng.uniform(.015, .045) * meta['sens']
            self._apply(symbol, jump, tick, kind)
        else:
            meta = market.stocks[symbol]
            rng = random.Random(f'{market.seed}|earnings|{market.round}|{symbol}')
            jump = rng.choice((-1, 1)) * rng.uniform(.02, .055) * meta['sens']
            self._apply(symbol, jump, tick, kind)

    def _apply(self, symbol, jump, tick, kind):
        market = self.market
        headline = {'market': '市场快讯：宏观现金流指数公布，行业整体调整',
                    'wire': '公司更新公开现金流展望',
                    'earnings': '定期财报：披露经营现金流变化'}[kind]
        market.fundamental[symbol] = round(max(.5, market.fundamental[symbol] * (1 + jump)), 2)
        market.transient[symbol] = market.transient.get(symbol, 0.) + jump * EVENT_OVERSHOOT
        market.news.append(dict(round=market.round, tick=tick, symbol=symbol, kind=kind,
                                headline=headline, cashflow_change_pct=round(jump * 100, 2)))

    # -- reads for the market ----------------------------------------------
    def effective(self, symbol):
        """Fundamental level including transitory sector/idio noise."""
        meta = self.market.stocks[symbol]
        noise = self.market.transient.get(symbol, 0.) + self.sector_level.get(meta['sector'], 0.)
        return max(.05, self.market.fundamental[symbol] * (1 + noise))

    def spread_mult(self):
        return REGIMES[self.regime]['spread']
