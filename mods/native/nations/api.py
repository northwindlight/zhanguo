"""Player-facing nation API; tool schemas live in the distribution tool layer."""


class NationAPI:
    def __init__(self, world):
        self.world = world

    def snapshot(self, actor):
        return self.world.nations.snapshot()

    def overview(self, actor):
        return self.world.nations.overview(actor)
