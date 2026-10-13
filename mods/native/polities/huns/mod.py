from engine.runtime import GameMod, Panel, RuleSection
from copy import deepcopy


class HunsMod(GameMod):
    def mixins(self):return ()
    def needs(self):return ('native:resource-inspection','native:resource-exchange','native:territory','native:army')
    def provides(self):return ()
    def parameter_overrides(self):return self.declaration['Policy']['Overrides']
    def numbers(self):return {'huns.start.'+key:value for key,value in self.declaration['Start'].items()}
    def activate_actor(self, world, actor, *, site=None):
        cfg=self.declaration['Start']
        tiles=self.runtime.object_call(world,'native:territory','owned',actor)
        if site is None:
            if not tiles:raise ValueError('Establish a territorial site before activating the policy')
            place=tiles[0]
        else:
            place=next((t for t in tiles if t.tile.id==site),None)
            if place is None:raise ValueError('Policy start site is not owned')
        stocks=self.runtime.object_call(world,'native:resource-inspection','snapshot',actor)
        target={'native:gold':cfg['Gold'],'native:supply':cfg['Supply']}
        debits={s.id:s.amount-target.get(s.id,0) for s in stocks if s.amount>target.get(s.id,0)}
        credits={s.id:target.get(s.id,0)-s.amount for s in stocks if s.amount<target.get(s.id,0)}
        x,y=place.tile.position
        self.runtime.object_call(world,'native:army','seed',actor,'native:cavalry',x,y,cfg['Cavalry'])
        if debits or credits:self.runtime.object_call(world,'native:resource-exchange','exchange',actor,debits,credits)
    def prompt(self, world, actor):return '游牧政体：骑兵征召资源由政体覆盖，建筑成本修正由统一参数入口提供。'
    def panels(self):return (Panel('polity',lambda w,a:{'id':self.declaration['Mod']['Id'],'overrides':deepcopy(self.parameter_overrides())}),)
    def rule_sections(self, world=None):return (RuleSection('匈奴政体','匈奴模块以国家作用域加载。开局资源和骑兵、外交禁用项及建造/征召覆盖都由本模块声明；卸载本模块不影响普通国家。'),)
