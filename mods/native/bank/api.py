"""Player-facing bank API; tool schemas live in the distribution tool layer."""


class BankAPI:
    def __init__(self, world):
        self.world = world

    def borrow(self, actor):
        runtime = self.world.runtime
        fee = runtime.object_call(self.world, 'native:diplomacy', 'fee', actor) if 'native:diplomacy' in runtime.capabilities else 0
        return self.world.bank.borrow(actor, fee)

    def position(self, actor):
        return self.world.bank.position(actor)

    def buy_report(self, actor, target, turn=None):
        return self.world.bank.buy_report(actor, target, turn)
