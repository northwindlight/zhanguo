"""Default price curve: a finite buffer that is priced by how full it is."""
import math

from mods.native.market.contracts import PricingEngine, PricePath, PriceSettlement, integer, number

# exp(700) ≈ 1.01e304：离 float 的 1.8e308 还剩一个量级的安全边际。
_MAX_LOG = 700.0
_OUT_OF_RANGE = '单笔委托超出市场可定价范围（成本已超出浮点上限），请拆成多笔'


class InventoryPricing(PricingEngine):
    """Three statements and no calculus.

    Inventory is state: trades and the outside refill move it, nothing else.

    Reference stock is the market's own scale. It starts at the declared depth
    and drifts with the period's net flow (the market keeps a bigger book when
    it is being bought out and a smaller one when it is being dumped), so heavy
    flow moves the scale instead of only squeezing the surviving stock.

    Level: p = base * exp(elasticity * (reference - inventory) / reference). A
    buffer that matches its reference trades at the declared base price.

    Order: every unit carries its own price, exp(elasticity * s / reference)
    times the period's level, where s counts the units this owner has already
    traded that way this period. The order costs the sum of those unit prices,
    so a deep order pays more per unit and a large lot sells for less, while
    nobody's quote depends on what other players did.
    """
    def walk(self, listing, price, quantity, side, volume, reference, policy):
        integer(quantity, 1);integer(volume)
        elasticity, scale = number(policy.elasticity), number(reference, positive=True)
        # Sum over the units of exp(+-elasticity * s / depth), the one geometric
        # series each direction needs, in closed form.
        up, down = math.expm1(elasticity / scale), -math.expm1(-elasticity / scale)
        if side == 'buy':
            # Buying the s-th unit costs exp(+elasticity * s / scale) times the
            # period's level: the buffer thins, so later units cost more.
            self._finite(elasticity, scale, price, volume, quantity)
            units = math.exp(elasticity * volume / scale) * math.expm1(elasticity * quantity / scale) / up
            return PricePath(price, self._total(price, units, 1 + policy.spread / 2))
        # Selling the s-th unit earns exp(-elasticity * s / scale): a deep lot
        # is what depresses the price, so later units earn less.
        units = math.exp(-elasticity * volume / scale) * -math.expm1(-elasticity * quantity / scale) / down
        return PricePath(price, self._total(price, units, 1 - policy.spread / 2))

    @staticmethod
    def _finite(elasticity, scale, price, volume, quantity):
        """指数曲线本身没有上界（这是口径），但 float 有：实参到了这个量级，
        expm1 会直接抛 OverflowError 掀翻整局。单笔成本到这个量级早已超过任何
        国库，按「超出可定价范围」拒绝才是对的——这是业务拒绝，不是崩溃。"""
        if elasticity * max(volume, quantity) / scale > _MAX_LOG:
            raise ValueError(_OUT_OF_RANGE)

    @staticmethod
    def _total(price, units, factor):
        if units > 0 and math.log(price) + math.log(units) + math.log(factor) > _MAX_LOG:
            raise ValueError(_OUT_OF_RANGE)
        return price * units * factor

    def level(self, listing, inventory, reference, policy):
        scale = number(reference, positive=True)
        gap = (scale - number(inventory)) / scale
        # Absolute numerical guardrails, unrelated to the opening base price.
        log_price = math.log(listing.base_price) + number(policy.elasticity) * gap
        price = (policy.low if log_price <= math.log(policy.low) else
                 policy.high if log_price >= math.log(policy.high) else math.exp(log_price))
        return PriceSettlement(price)
