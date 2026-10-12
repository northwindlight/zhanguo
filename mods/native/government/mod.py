from ..base import DomainMod

class GovernmentMod(DomainMod):
    package = "mods.native.government"
    logic_class = "GovernmentLogic"

    def finish_world(self, world):
        from ..core import USAGE_FIELDS
        world.token_usage = {n: {k:0 for k in USAGE_FIELDS} for n in world.nations}
