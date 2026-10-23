"""A bounded market scenario model over public snapshots, never hidden clients.

Inventory deltas minus our own last trades estimate other participants' net
orders. A second scenario lets competitors copy a proposed investment. These
are stress scenarios, not assertions about another player's policy.
"""
import math


def finite_map(value):
    return isinstance(value, dict) and all(isinstance(k, str) and type(v) in (int, float)
        and math.isfinite(v) for k, v in value.items())


def slope(market, key):
    """Infer order curvature from the public sell trial, also handles fixed prices."""
    unit = dict(market['sell_prices'])[key]
    trial = next((s for r, _, s in market.get('trials', []) if r == key), None)
    count = market.get('trial_units', 50)
    if not trial or unit <= 0 or count <= 1:
        return 0.0
    ratio = trial / (unit * count)
    if ratio >= .9999:
        return 0.0
    lo, hi = 0.0, 10.0
    for _ in range(35):
        step = (lo + hi) / 2
        average = -math.expm1(-step * count) / (-math.expm1(-step) * count)
        if average > ratio:
            lo = step
        else:
            hi = step
    return (lo + hi) / 2


class MarketModel:
    def __init__(self, market, memory, participants=1):
        self.market = market
        self.inventory = dict(market['inventory'])
        self.reference = dict(market['reference'])
        self.buy = dict(market['buy_prices'])
        self.sell = dict(market['sell_prices'])
        self.base = dict(market['equilibrium'])
        self.elasticity = {k:slope(market, k)*self.reference[k] for k in self.inventory}
        self.external = dict(memory.get('external', {}))
        self.flow = memory.get('flow', 0.0)
        self.participants = max(1, participants)
        self.order_offset = {}
        previous = memory.get('inventory', {})
        elapsed = market['period'] - memory.get('period', market['period'])
        if elapsed > 0:
            ratios = []
            for key, value in self.inventory.items():
                if key not in previous:
                    continue
                net = value - previous[key]
                other = (net - memory.get('own_net', {}).get(key, 0)) / elapsed
                self.external[key] = .5 * self.external.get(key, other) + .5 * other
                drift = self.reference[key] - memory.get('reference', {}).get(key, self.reference[key])
                if abs(net) > 1 and drift * net <= 0:
                    ratios.append(max(0.0, min(1.0, -drift/net)))
            if ratios:
                self.flow = .5*self.flow + .5*sorted(ratios)[len(ratios)//2]

    def remember(self, own_net):
        return {'period': self.market['period'], 'inventory':self.inventory,
                'reference':self.reference, 'external':self.external,
                'flow':self.flow, 'own_net':dict(own_net)}

    def path(self, own_net, extra, horizon, crowd=1):
        """Yield future buy/sell prices, including stock depletion and oversupply."""
        inventory = {k:max(0,v+self.order_offset.get(k,0)) for k,v in self.inventory.items()}
        reference = dict(self.reference)
        for turn in range(horizon):
            buy, sell = {}, {}
            for key in inventory:
                # Observed orders contain temporary construction and liquidation.
                net = self.external.get(key, 0) * .96**turn + own_net.get(key, 0)
                net += crowd * extra.get(key, 0)
                inventory[key] = max(0, inventory[key] + net)
                reference[key] = max(1, reference[key] - self.flow * net)
                elasticity = self.elasticity[key]
                if elasticity > 0:
                    log_price = math.log(self.base[key]) + elasticity * (1-inventory[key]/reference[key])
                    price = math.exp(max(-14, min(21, log_price)))
                    current = dict(self.market['prices'])[key]
                    factor = price / current
                else:
                    factor = 1
                # A finite scarcity shadow cost lets self-supply investments
                # be compared when the baseline imports would be rationed.
                buy[key] = self.buy[key]*factor*(1 if inventory[key]>=1 else 10)
                sell[key] = self.sell[key]*factor
            yield buy, sell

    def value(self, net, own_net, extra, horizon, crowd=1):
        total = weight = 0.0
        for turn, (buy, sell) in enumerate(self.path(own_net, extra, horizon, crowd)):
            discount = .97**turn
            value = sum(amount*(1 if key=='native:gold' else
                                (sell if amount>0 else buy).get(key, math.inf))
                        for key, amount in net.items() if amount)
            if not math.isfinite(value):
                return -math.inf
            total += value*discount
            weight += discount
        return total / weight
