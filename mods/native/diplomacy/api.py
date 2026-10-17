"""Player-facing diplomacy API; action schemas live in the distribution tool layer."""


class DiplomacyAPI:
    def __init__(self, world):
        self.world = world

    def submit(self, actor, action, **arguments):
        return self.world.diplomacy.submit(actor, action, **arguments)

    def vote(self, actor, vote, accept):
        return self.world.diplomacy.vote(actor, vote, accept)

    def snapshot(self, actor):
        return self.world.diplomacy.snapshot(actor)
