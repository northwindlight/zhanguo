"""Paid snapshots of competitors, separate from ordinary account permissions."""
from copy import deepcopy
import random

from engine.runtime import GameMod, RuleSection
from .market import CLEARING, FLOW, MAKER, NOISE

FRAGMENT_LINES = 6


class Intelligence:
    def __init__(self, price, code_price):
        self.price, self.code_price, self.seq, self.reports = price, code_price, 1, {}
    def bind_state(self, world, key): self.world = world; return self
    def _port(self, method, *args):
        return self.world.runtime.object_call(self.world, 'stock:portfolio', method, *args)
    def status(self, actor):
        return dict(price=self.price, code_price=self.code_price,
                    targets=self._port('participants'),
                    reports=deepcopy(self.reports.get(actor, [])[-5:]))
    def steal(self, actor, target):
        if not isinstance(target, str) or target == actor:
            raise ValueError('请选择其他玩家；自己的账户可以免费查询')
        snapshot = self._port('intelligence_snapshot', target)
        self._port('spend', actor, self.price, 'intelligence')
        report = dict(id=self.seq, target=target, price=self.price,
                      round=self.world.clock.period(), tick=self.world.clock.tick,
                      phase=self.world.market.state, kind='snapshot', snapshot=snapshot)
        self._record(actor, report)
        return deepcopy(report)

    def source(self, actor, target):
        """Steal one random contiguous fragment of the target's strategy source."""
        if not isinstance(target, str) or target == actor:
            raise ValueError('请选择其他玩家；自己的源码用 script(action=status) 免费查看')
        if target in (MAKER, FLOW, NOISE, CLEARING):
            raise ValueError('背景做市账户没有策略源码')
        rows = self.world.scripts.rows.get(target, {})
        sources = [(slot, row) for slot, row in sorted(rows.items()) if row['revision']]
        if not sources:
            raise ValueError('对方还没有保存任何策略源码，无码可偷')
        # 片段由(种子,回合,偷取者,目标,序号)决定：同一种子与行动顺序重放结果一致。
        rng = random.Random('|'.join((
            str(self.world.market.seed), 'intel-source', str(self.world.clock.period()),
            actor, target, str(self.seq))))
        slot, row = sources[int(rng.random() * len(sources))]
        lines = row['source'].splitlines()
        window = min(FRAGMENT_LINES, len(lines))
        start = int(rng.random() * max(1, len(lines) - window + 1))
        self._port('spend', actor, self.code_price, 'intelligence')
        report = dict(id=self.seq, target=target, price=self.code_price,
                      round=self.world.clock.period(), tick=self.world.clock.tick,
                      phase=self.world.market.state, kind='source',
                      fragment=dict(slot=slot, revision=row['revision'], start_line=start + 1,
                                    total_lines=len(lines),
                                    code='\n'.join(lines[start:start + window])))
        self._record(actor, report)
        return deepcopy(report)

    def _record(self, actor, report):
        self.seq += 1
        self.reports.setdefault(actor, []).append(report)
        self.reports[actor] = self.reports[actor][-20:]
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
    def source(self, actor, target): return self.world.intelligence.source(actor, target)


class IntelligenceMod(GameMod):
    def mixins(self): return ()
    def initialize_world(self, world):
        world.intelligence = Intelligence(self.declaration['Parameters']['ReportPrice'],
                                          self.declaration['Parameters']['CodePrice'])
    def objects(self, world): return {'stock:intelligence': world.intelligence}
    def player_api(self, world): return IntelligenceAPI(world)
    def before_tool(self, world, actor, name, arguments):
        if name == 'intel':
            self.runtime.object_call(world, 'stock:clock', 'charge', actor)
    def save_state(self, world): return world.intelligence.to_save()
    def load_state(self, world, state): world.intelligence.restore(state)
    def rule_sections(self, world=None):
        price = self.declaration['Parameters']['ReportPrice']
        code_price = self.declaration['Parameters']['CodePrice']
        return (RuleSection('商业谍报',
            f'intel(action=steal,target=玩家id)支付{price}游戏现金，确定获得对方调用时的现金、'
            '可用现金、银行贷款、多空市值及各股票持仓数量快照；报告注明回合、tick和阶段。'
            f'intel(action=source,target=玩家id)支付{code_price}游戏现金，从对方已保存源码的全部'
            f'交易槽/研究槽中随机挑一个，再随机截取连续至多{FRAGMENT_LINES}行源码；报告注明槽位、'
            '源码版本与行号范围（第X～Y行/共N行）。同目标重复偷取会得到不同片段，对方修订后版本号'
            '变化；源码很短时可能整段被偷走。'
            'intel_status列出公开玩家名单及自己最近取得的报告。报告不会随对手后续操作自动更新。'
            '不能偷自己、背景做市商或还没保存过源码的目标；普通API仍只看自己。研究和交易脚本'
            '只能读取已购买报告，不能在脚本里发动谍报。费用计入净值及商业谍报费用。',
            ('商业谍报', '情报', '源码', '现金', '持仓规模'), '付费竞争者快照与源码片段', system=True),)
