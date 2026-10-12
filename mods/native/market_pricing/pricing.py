"""Default native price curve, extracted from the legacy economy domain."""
from mods.native.market.contracts import PricingEngine, PricePath, PriceSettlement


class LinearPricing(PricingEngine):
    IMPACT = 0.02
    DEPTH_DIVISOR = 4
    SPREAD = 0.10
    RETENTION = 0.9
    MIN_RATIO = 0.1
    MIN_ABSOLUTE = 0.5
    MAX_RATIO = 3.0
    SENSITIVITY = 0.5
    EQ_MIN_RATIO = 0.5
    EQ_MAX_RATIO = 1.5
    ONE_SIDE_GAP = 0.4

    def clamp(self, listing, price):
        return min(max(price, max(listing.base_price * self.MIN_RATIO, self.MIN_ABSOLUTE)),
                   listing.base_price * self.MAX_RATIO)

    def walk(self, listing, price, quantity, side, participants):
        p0 = self.clamp(listing, price)
        depth = max(1, round(listing.depth * max(1, participants) / self.DEPTH_DIVISOR))
        tick = listing.base_price * self.IMPACT / depth * (1 if side == 'buy' else -1)
        p1 = self.clamp(listing, p0 + tick * quantity)
        linear = min(float(quantity), abs((p1-p0)/tick)) if tick else 0.0
        integral = linear * (p0+p1)/2 + (quantity-linear) * p1
        total = integral * (1 + self.SPREAD/2 if side == 'buy' else 1 - self.SPREAD/2)
        return PricePath(p1, total)

    def settle(self, listing, price, production, consumption):
        if production and consumption:
            gap = (consumption-production)/(consumption+production)
        elif production or consumption:
            gap = -self.ONE_SIDE_GAP if production else self.ONE_SIDE_GAP
        else:
            gap = 0.0
        base = listing.base_price
        equilibrium = min(max(base*(1+self.SENSITIVITY*gap), base*self.EQ_MIN_RATIO),
                          base*self.EQ_MAX_RATIO)
        return PriceSettlement(round(self.clamp(listing, equilibrium+(price-equilibrium)*self.RETENTION), 3),
                               round(equilibrium, 3))
