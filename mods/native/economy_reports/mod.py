from engine.runtime import GameMod, ModError, Stage, RuleSection
from .contracts import EconomicFact
from .objects import Economy


class EconomyMod(GameMod):
    def configure(self, declaration, path):
        super().configure(declaration,path)
        if declaration['ValuationAt']!='report-stage':raise ModError('Unsupported accounting valuation timing')

    def mixins(self):return ()

    def initialize_world(self, world):
        def call(capability,method,*args):return self.runtime.object_call(world,capability,method,*args)
        world.economy = Economy(self.declaration['ReportEvery'],self.declaration['Currency'],
            lambda values:call('economy:valuation','quote',values),
            lambda:call('native:resource-inspection','owners'),
            lambda owner:call('native:resource-inspection','snapshot',owner),
            lambda owner:call('native:buildings','replacement_costs',owner),
            (lambda:call('native:nation-inspection','ids')) if 'native:nation-inspection' in self.runtime.capabilities else None)

    def objects(self, world):return {'native:economy':world.economy}
    def save_state(self, world):return world.economy.to_save()
    def load_state(self, world, state):world.economy.restore(state)
    def pending_archive(self,world):return world.economy.pending_archive()
    def archive_committed(self,world):world.economy.archive_committed()
    def restore_archive(self,world,rows):world.economy.restore_archive(rows)

    def rule_sections(self,world=None):
        return (RuleSection('消费指标与终局','消费累计建造、征召、军费（含回血装备）与行政四类，以每期报表阶段市价估值，未定价资源单列。终局按各历史国家已记录总消费降序排名，亡国也保留成绩；GDP、军力、领土、建筑资产仅作参考，不重新按终局价格估值。离线成绩单使用 settlement.py --save 路径 --table；结算厅另需外部模型配置。',keywords=('消费','排名','终局','结算','GDP','报表','scoring')), )

    def stages(self):
        return tuple(Stage(row['Id'],self._reports,hook=row['Hook'],needs=tuple(row.get('Needs',())),
                           provides=tuple(row.get('Provides',())),requires=tuple(row.get('Requires',())),
                           after=tuple(row.get('After',())),before=tuple(row.get('Before',())))
                     for row in self.declaration.get('Stages',()))

    @staticmethod
    def _reports(world, context):
        production = context['building_production']
        facts = []
        for row in production.records:
            if row.inputs or row.outputs:
                facts.append(EconomicFact(f'native:building-production/{production.period}/{row.id}',row.owner,
                                          production.period,'production',row.inputs,row.outputs))
        for row in production.construction:
            facts.append(EconomicFact(f'native:building-construction/{row.id}',row.owner,row.period,'construction',row.costs))
        currency = world.economy.currency
        for index,row in enumerate(context['market_trades']):
            if row.period!=production.period:raise ValueError('Market and building report periods differ')
            inputs = ((currency,row.gold),) if row.side=='buy' else ((row.resource,row.quantity),)
            outputs = ((row.resource,row.quantity),) if row.side=='buy' else ((currency,row.gold),)
            facts.append(EconomicFact(f'native:market-trade/{row.period}/{index}',row.owner,row.period,
                                      'import' if row.side=='buy' else 'export',inputs,outputs,row.gold))
        facts.extend(context.get('economic_facts',()))
        facts.extend(EconomicFact(**row) for row in context.get('economic_records',()))
        context['economy_turns'] = world.economy.settle(production.period,tuple(facts),context.get('bank_liabilities'))
        context['economy_publications'] = tuple(row for row in world.economy.reports() if row.end==production.period)
