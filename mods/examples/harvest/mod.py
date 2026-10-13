"""往回合里插入一个新阶段，并新增一个发行版挂点。

两步：
  ① 发行版顶层加一条 `[[Hooks]] Id = "..."`，位置就是阶段要执行的位置；
  ② 本模块的 `stages()` 里给出 `Stage(..., hook="...")` 指向它。
能挂在已有挂点上就不必新增；只有骨架里没有你需要的位置时才加。

本模块没有状态：`state_keys()` 返回空元组，不进存档的所有权表。
"""
from engine.runtime import GameMod, RuleSection, Stage


class HarvestMod(GameMod):
    def mixins(self):
        return ()

    def state_keys(self):
        return ()

    # 需要别的模块的能力时，在这里声明能力名。
    def needs(self):
        return ('native:nation-inspection', 'native:resource-exchange')

    def provides(self):
        return ()

    def stages(self):
        # provides 的条件名全局唯一，供别的阶段用 needs 消费。
        return (Stage('example:harvest.reap', self._reap,
                      hook='example.harvest', provides=('harvest.reaped',)),)

    def _reap(self, world, context):
        """秋收：给每个存活国家发粮食。

        跨域写入走声明的对象接口：`object_call(世界, 能力, 方法, *参数)`。
        调用的能力必须在 needs() 里声明，方法必须在 [Interfaces] 里列出。
        """
        amount = self.declaration['Harvest']['Yield']
        actors = world.runtime.object_call(world, 'native:nation-inspection', 'ids')
        operations = [{'owner': actor, 'debits': {}, 'credits': {'native:food': amount}}
                      for actor in actors]
        if operations:
            # exchange_batch 把所有账户的变动作为一批原子提交。
            world.runtime.object_call(world, 'native:resource-exchange', 'exchange_batch', operations)
        context['harvest'] = {'yield': amount, 'nations': list(actors)}

    def rule_sections(self, world=None):
        return (RuleSection('秋收',
                            f'每个行动期在「秋收」挂点按国发放 {self.declaration["Harvest"]["Yield"]} 单位粮食。',
                            ('秋收', 'harvest')),)
