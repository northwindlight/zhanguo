from engine.runtime import GameMod, RuleSection


class HunsMod(GameMod):
    def mixins(self):return ()
    def parameter_overrides(self):return self.declaration['Policy']['Overrides']
    def numbers(self):return {'huns.start.'+key:value for key,value in self.declaration['Start'].items()}
    def activate_actor(self, world, actor, *, site=None, start=None):
        cfg={**self.declaration['Start'],**(start or {})}
        if set(cfg)!=set(self.declaration['Start']) or any(type(v) is not int or v<0 for v in cfg.values()) or cfg['Cavalry']<1:raise ValueError('Invalid policy start parameters')
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
    def prompt(self, world, actor):return '金矿优先，以战养战；分兵抢无主地，集中夺市政厅，时间不等人。骑兵征召8粮8装、建造130%，补给耗尽前主动缩军。可宣战求和、写信与间谍，不参加联盟、保障、共同防御、送礼或共享地图；只向允许谈判的对象谈条件。'
    def rule_sections(self, world=None):return (RuleSection('匈奴政体',self.prompt(world,None),keywords=('huns','匈奴','游牧')),)
