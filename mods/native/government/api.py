"""Player-facing government API; tool schemas live in the distribution tool layer."""


class GovernmentAPI:
    def __init__(self, world):
        self.world = world

    def plan(self, actor, text):
        return self.world.government.plan(actor, text)

    def end_turn(self, actor, summary=''):
        return self.world.government.end_turn(actor, summary)

    def snapshot(self, actor):
        return self.world.government.snapshot(actor)
