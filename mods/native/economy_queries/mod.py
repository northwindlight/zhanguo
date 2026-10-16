from engine.runtime import GameMod, Tool
from .objects import EconomicQueries
from mods.native.buildings.types import NativeBuilding


class EconomicActors:
    @property
    def economy_actors(self):
        return self.runtime.object_call(self,'native:resource-inspection','owners')


class QueriesMod(GameMod):
    def mixins(self):return (EconomicActors,)
    def needs(self):return ('native:resource-inspection','native:buildings','native:economy','native:market','economy:valuation')
    def provides(self):return ('economy:queries',)
    def objects(self, world):
        return {'economy:queries':EconomicQueries(self.runtime,lambda cap,method,*args:self.runtime.object_call(world,cap,method,*args))}
    def _queries(self, world):return self.objects(world)['economy:queries']
    def tools(self):
        kinds = sorted(key for key,cls in self.runtime.content.definitions()
                       if isinstance(cls,type) and issubclass(cls,NativeBuilding))
        estimate_parameters = {'type':'object','properties':{
            'kind':{'type':'string','enum':kinds},'level':{'type':'integer','minimum':1},
            'other_slots':{'type':'integer','minimum':0,'description':'该格已落成的其他建筑数（市政厅按每座加成；市政厅另加3座乐观密度假设，非保证收益）'}},'additionalProperties':False}
        return (Tool('econ',lambda world,actor:self._queries(world).balance(actor),read_only=True,
                     description='本国库存、库存折金、建筑重置值和总资产，无法估值的内容明确列出；不是本期现金收入。'),
                Tool('report',self._report,read_only=True,description='本国免费已发布经济报表；缺省仅最新一期，period 查以该周期结束的报表，all=true 返回全部跨期。报表自动结算、有日期，不是当前库存或未完成收益。'),
                Tool('building_economics',self._estimate,read_only=True,parameters=estimate_parameters,content_parameters=('kind',),
                     description='优先不带参数查询：一次返回全部建筑的造价、产销、能源、效果、门槛与按当前市价算的净收益和回本，不必逐个查询。无需坐标，kind 可选筛选。收益口径写在报告首行（采集按卖价、工厂按买价替代、电耗按木电燃料）；满负荷口径，不代表本回合实收，也不判定建造资格。'),
                Tool('market',lambda world,actor:self._queries(world).market(),read_only=True,
                     description='世界市场的现价与买卖单价、周期；无需参数。本期单价对任何数量和任何国家都一样，买入的实际支出与取整以 buy 回执为准。'),
                Tool('buy',self._buy,content_parameters=('resource',),description='从市场买入正整数数量的资源，resource 使用短键；成功返回实际支付、成交数量与价格，不把单价当成交。'),Tool('sell',self._sell,content_parameters=('resource',),description='卖出自己持有的正整数数量资源，返回实际收入与成交价格。不能卖不存在的货；成交以回执为准。'))
    def _report(self,world,actor,period: int | None=None,all: bool=False):
        rows=self._queries(world).report(actor)
        if period is not None and all:raise ValueError('period 与 all 不能同时指定')
        if period is not None:
            if type(period) is not int or period<0:raise ValueError('period 须为非负整数')
            selected=tuple(r for r in rows if r.end==period)
            if not selected:raise ValueError('指定周期没有已发布报表')
            return selected
        return rows if all else rows[-1:]
    def _estimate(self, world, actor, kind: str | None=None, level: int=1, other_slots: int=0):
        queries=self._queries(world)
        if kind is None:
            return tuple(self._estimate(world,actor,key,level,other_slots) for key in sorted(queries.building_types()))
        policy=world.runtime.parameter(world,actor,'build_cost_pct',100)
        return queries.estimate(kind,level,other_slots,policy_pct=policy)
    def _buy(self, world, actor, resource: str, quantity: int):
        return self.runtime.object_call(world,'native:market','buy',actor,resource,quantity)
    def _sell(self, world, actor, resource: str, quantity: int):
        return self.runtime.object_call(world,'native:market','sell',actor,resource,quantity)
