"""Example replacement bank: fixed credit rather than GDP-based credit."""
from mods.native.bank.mod import BankMod
from mods.native.bank.objects import Bank


class FixedCreditBank(Bank):
    def credit_limit(self, owner):
        self._stocks(owner)
        return 7


class FixedCreditBankMod(BankMod):
    def initialize_world(self, world):
        self.validate_currency(world)
        world.bank = FixedCreditBank(self.policy,lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))
