from engine.runtime import GameMod, ModError, Stage, Tool
from mods.native.resources.types import Gold
from .objects import Bank, BankPolicy


class BankMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration,path)
        self.policy = BankPolicy(**declaration['Bank'])
    def mixins(self):return ()
    def state_keys(self):return ('bank',)
    def needs(self):return ('native:resource-inspection','native:bank-cash','native:economy')
    def provides(self):return ('economy:bank',)
    def initialize_world(self, world):
        self.validate_currency(world)
        world.bank = Bank(self.policy,lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))
    def validate_currency(self, world):
        try:definition = self.runtime.content.definition(self.policy.currency)
        except KeyError as exc:raise ModError('Bank currency is not registered') from exc
        if not isinstance(definition,type) or not issubclass(definition,Gold):
            raise ModError('Bank currency must be a cash resource')
        if self.runtime.object_call(world,'native:economy','currency_id')!=self.policy.currency:
            raise ModError('Bank credit and accounting currencies differ')
    def objects(self, world):return {'economy:bank':world.bank}
    def save_state(self, world):return world.bank.to_save()
    def load_state(self, world, state):world.bank.restore(state)
    def stages(self):
        return tuple(Stage(row['Id'],self._settle,hook=row['Hook'],needs=tuple(row.get('Needs',())),
                           provides=tuple(row.get('Provides',())),after=tuple(row.get('After',())),
                           before=tuple(row.get('Before',())),requires=tuple(row.get('Requires',())))
                     for row in self.declaration.get('Stages',()))
    @staticmethod
    def _settle(world, context):
        context['bank_entries'] = world.bank.settle(context['building_production'].period)
        context['bank_liabilities'] = world.bank.liabilities()
    def tools(self):
        return (Tool('borrow',lambda world,actor:world.bank.borrow(actor),description='按发行版声明的 GDP 信用额度借入；期限与应还金额由银行固定，不能自选。'),
                Tool('bank',lambda world,actor:world.bank.position(actor),read_only=True,description='本国现金、信用额度、负债与贷款剩余；无需参数。'),
                Tool('buy_report',self._buy_report,description='付费购买目标国家最新已发布的经济报表；不包含未完成收益。'))
    @staticmethod
    def _buy_report(world, actor, target: str):return world.bank.buy_report(actor,target)
