from engine.runtime import GameMod
from .pricing import InventoryPricing


class InventoryPricingMod(GameMod):
    def mixins(self):return ()
    def objects(self, world):return {'market:pricing': InventoryPricing()}
