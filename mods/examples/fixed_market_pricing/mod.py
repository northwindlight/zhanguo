"""A genuinely different, independently selectable pricing provider."""
from engine.runtime import GameMod
from mods.native.market.contracts import PricingEngine, PricePath, PriceSettlement


class FixedPricing(PricingEngine):
    def walk(self, listing, price, quantity, side, participants):
        return PricePath(listing.base_price, listing.base_price * quantity)

    def settle(self, listing, price, production, consumption):
        return PriceSettlement(listing.base_price, listing.base_price)


class FixedPricingMod(GameMod):
    def mixins(self):return ()
    def provides(self):return ('market:pricing',)
    def objects(self, world):return {'market:pricing': FixedPricing()}
