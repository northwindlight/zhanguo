from engine.runtime import GameMod, ModError, Stage, RuleSection
from .api import BankAPI
from .objects import Bank, BankPolicy


class BankMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration,path)
        self.policy = BankPolicy(**declaration['Bank'])
    def mixins(self):return ()
    def state_keys(self):return ('bank',)
    def needs(self):return ('native:resource-inspection','native:bank-cash','native:economy')
    def optional(self):return ('native:nation-inspection','native:diplomacy')
    def provides(self):return ('economy:bank',)
    def initialize_world(self, world):
        self.validate_currency(world)
        world.bank = Bank(self.policy,lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args),
            (lambda:self.runtime.object_call(world,'native:nation-inspection','ids')) if 'native:nation-inspection' in self.runtime.capabilities else None)
    def validate_currency(self, world):
        try:self.runtime.content.definition(self.policy.currency)
        except KeyError as exc:raise ModError('Bank currency is not registered') from exc
        if 'currency' not in self.runtime.content.roles_of(self.policy.currency):
            raise ModError('Bank currency must declare the currency role')
        if self.runtime.object_call(world,'native:economy','currency_id')!=self.policy.currency:
            raise ModError('Bank credit and accounting currencies differ')
    def objects(self, world):return {'economy:bank':world.bank}
    def save_state(self, world):return world.bank.to_save()
    def load_state(self, world, state):world.bank.restore(state)
    def player_api(self, world):return BankAPI(world)
    def stages(self):
        return tuple(Stage(row['Id'],self._settle,hook=row['Hook'],needs=tuple(row.get('Needs',())),
                           provides=tuple(row.get('Provides',())),after=tuple(row.get('After',())),
                           before=tuple(row.get('Before',())),requires=tuple(row.get('Requires',())))
                     for row in self.declaration.get('Stages',()))
    @staticmethod
    def _settle(world, context):
        context['bank_entries'] = world.bank.settle(context['building_production'].period)
        context['bank_liabilities'] = world.bank.liabilities()
        for index,row in enumerate(world.bank.fees(context['building_production'].period)):
            if row.cash:context.setdefault('economic_records',[]).append({'id':f'native:bank-fee/{row.period}/{index}', 'owner':row.owner,'period':row.period,'category':'administration','inputs':(('native:gold',-row.cash),)})
    def numbers(self):return {'bank.'+key:value for key,value in self.declaration['Bank'].items()}
    def rule_sections(self,world=None):
        p=self.policy
        return (RuleSection('央行、贷款与报表',f'储蓄利率范围 {p.minimum_rate:.0%} 至 {p.maximum_rate:.0%}，贷款加息 {p.loan_spread:.0%}。额度为最近已结算 GDP 的已估值部分×{p.credit_multiple:g}，每国最多一笔，期限 {p.loan_term} 周期；到期强制扣款，可使现金为负。借款成功才收外交费。买别国报表每次 {p.report_fee} 金，turn 可指定已公布期，本国 report 免费。亡国账户停止结息与生成新报表。',keywords=('bank','borrow','loan','report','buy_report','银行','借款','利率','贷款','报表')),)
