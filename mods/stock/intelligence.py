"""Paid snapshots of competitors, separate from ordinary account permissions."""
from copy import deepcopy

from engine.runtime import GameMod, RuleSection
from mods.stock.calls import CALLS


class Intelligence:
    def __init__(self, price):
        self.price, self.seq, self.reports = price, 1, {}
    def bind_state(self, world, key): self.world = world; return self
    def _port(self, method, *args):
        return self.world.runtime.object_call(self.world, 'stock:portfolio', method, *args)
    def status(self, actor):
        return dict(price=self.price, targets=self._port('participants'),
                    reports=deepcopy(self.reports.get(actor, [])[-5:]))
    def steal(self, actor, target):
        if not isinstance(target, str) or target == actor:
            raise ValueError('请选择其他玩家；自己的账户可以免费查询')
        snapshot = self._port('intelligence_snapshot', target)
        self._port('spend', actor, self.price, 'intelligence')
        report = dict(id=self.seq, target=target, price=self.price,
                      round=self.world.clock.period(), tick=self.world.clock.tick,
                      phase=self.world.market.state, snapshot=snapshot)
        self.seq += 1
        self.reports.setdefault(actor, []).append(report)
        self.reports[actor] = self.reports[actor][-20:]
        return deepcopy(report)
    def to_save(self): return dict(seq=self.seq, reports=deepcopy(self.reports))
    def restore(self, data):
        if (not isinstance(data, dict) or set(data) != {'seq', 'reports'}
                or type(data['seq']) is not int or data['seq'] < 1
                or type(data['reports']) is not dict
                or any(type(v) is not list or len(v) > 20 for v in data['reports'].values())):
            raise ValueError('无效的商业谍报存档')
        self.seq, self.reports = data['seq'], deepcopy(data['reports'])


class IntelligenceAPI:
    def __init__(self, world): self.world = world
    def status(self, actor): return self.world.intelligence.status(actor)
    def steal(self, actor, target): return self.world.intelligence.steal(actor, target)


class IntelligenceMod(GameMod):
    def mixins(self): return ()
    def initialize_world(self, world):
        world.intelligence = Intelligence(self.declaration['Parameters']['ReportPrice'])
    def objects(self, world): return {'stock:intelligence': world.intelligence}
    def player_api(self, world): return IntelligenceAPI(world)
    def before_tool(self, world, actor, name, arguments):
        CALLS.note(actor, world.clock.period())
        if name == 'intel':
            self.runtime.object_call(world, 'stock:clock', 'charge', actor)
    def save_state(self, world): return world.intelligence.to_save()
    def load_state(self, world, state): world.intelligence.restore(state)
    def rule_sections(self, world=None):
        price = self.declaration['Parameters']['ReportPrice']
        return (RuleSection('商业谍报',
            f'intel(action=steal,target=玩家id)支付{price}游戏现金，确定获得对方调用时的现金、'
            '可用现金、银行贷款、多空市值及各股票持仓数量快照；报告注明回合、tick和阶段。'
            'intel_status列出公开玩家名单及自己最近取得的报告。报告不会随对手后续操作自动更新。'
            '不能偷自己的账户或背景做市商；普通API仍只看自己。研究和交易脚本只能读取已购买报告，'
            '不能在脚本里发动谍报。费用计入净值及商业谍报费用。',
            ('商业谍报', '情报', '现金', '贷款', '持仓规模'), '付费竞争者快照与权限', system=True),)
