from engine.runtime import GameMod
from .pricing import LinearPricing


class LinearPricingMod(GameMod):
    def mixins(self):return ()
    def provides(self):return ('market:pricing',)
    def objects(self, world):return {'market:pricing': LinearPricing()}
