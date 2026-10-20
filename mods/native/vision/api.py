"""Player-facing vision API; map rendering stays in the UI mod."""


class VisionAPI:
    def __init__(self, world):
        self.world = world

    def tiles(self, actor):
        return self.world.runtime.object_call(self.world, 'native:vision', 'tiles', actor)
