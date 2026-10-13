"""Default price curve: a finite buffer that is priced by how full it is."""
import math

from mods.native.market.contracts import PricingEngine, PricePath, PriceSettlement, integer, number


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
            units = math.exp(elasticity * volume / scale) * math.expm1(elasticity * quantity / scale) / up
            return PricePath(price, price * units * (1 + policy.spread / 2))
        # Selling the s-th unit earns exp(-elasticity * s / scale): a deep lot
        # is what depresses the price, so later units earn less.
        units = math.exp(-elasticity * volume / scale) * -math.expm1(-elasticity * quantity / scale) / down
        return PricePath(price, price * units * (1 - policy.spread / 2))

    def level(self, listing, inventory, reference, policy):
        scale = number(reference, positive=True)
        gap = (scale - number(inventory)) / scale
        # Absolute numerical guardrails, unrelated to the opening base price.
        log_price = math.log(listing.base_price) + number(policy.elasticity) * gap
        price = (policy.low if log_price <= math.log(policy.low) else
                 policy.high if log_price >= math.log(policy.high) else math.exp(log_price))
        return PriceSettlement(price)
