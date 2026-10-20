"""A genuinely different, independently selectable pricing provider."""
from engine.runtime import GameMod
from mods.native.market.contracts import PricingEngine, PricePath, PriceSettlement


class FixedPricing(PricingEngine):
    # A flat reference price: no spread, no premium for size, no scarcity.
    def walk(self, listing, price, quantity, side, volume, reference, policy):
        return PricePath(listing.base_price, listing.base_price * quantity)

    def level(self, listing, inventory, reference, policy):
        return PriceSettlement(listing.base_price)


class FixedPricingMod(GameMod):
    def mixins(self):return ()
    def provides(self):return ('market:pricing',)
    def objects(self, world):return {'market:pricing': FixedPricing()}
