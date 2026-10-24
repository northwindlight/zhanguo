"""One logical exchange clock; planning and calculation quotas are separate."""
from copy import deepcopy

from engine.runtime import GameMod, RuleSection, Stage


class Clock:
    def __init__(self, trading_days=5, rest_days=2, session_ticks=20000,
                 session_start='09:30', session_hours=8, planning_actions=40):
        self.trading_days, self.rest_days = trading_days, rest_days
        self.session_ticks = session_ticks
        self.session_start, self.session_hours = session_start, session_hours
        self.planning_actions = planning_actions
        self.round, self.tick = 1, 0
        self.ended, self.used, self.summaries = set(), {}, {}

    def bind_state(self, world, key): return self
    @property
    def cycle(self): return self.trading_days + self.rest_days
    def period(self): return self.round
    def trading_day(self): return (self.round-1) % self.cycle < self.trading_days
    def days_to_next_open(self):
        i = (self.round-1) % self.cycle
        return 0 if i < self.trading_days else self.cycle-i
    def can_act(self, actor): return actor not in self.ended
    def slices(self): return self.session_ticks
    def tick_index(self): return self.tick
    def tick_for(self, actor): return self.tick
    def set_tick(self, tick):
        if type(tick) is not int or not 0 <= tick <= self.session_ticks:
            raise ValueError('tick 越界')
        self.tick = tick
    def open_session(self): self.tick = 0
    def close_session(self): self.tick = self.session_ticks
    def market_time(self, tick=None):
        hours, minutes = map(int, self.session_start.split(':'))
        total = hours*60+minutes+int(self.session_hours*60*(self.tick if tick is None else tick)/self.session_ticks)
        return f'{total//60:02d}:{total%60:02d}'
    def charge(self, actor):
        if self.used.get(actor, 0) >= self.planning_actions:
            raise ValueError('本回合规划动作额度已用满，仍可查询和 end_turn')
        self.used[actor] = self.used.get(actor, 0)+1
    def sleep(self, actor, ticks=100):
        raise ValueError('sleep 只在 on_tick 回调内安排下次唤醒，不推进行情')
    def end_turn(self, actor, summary=''):
        if actor in self.ended:
            raise ValueError('本回合已经结束过行动')
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError('回合小结不能为空')
        self.ended.add(actor)
        self.summaries[actor] = summary.strip()
        return dict(actor=actor, summary=summary.strip(), text=summary.strip(), round=self.round)
    def view(self, actor):
        return dict(round=self.round, trading_day=self.trading_day(),
                    days_to_next_open=self.days_to_next_open(), cycle=self.cycle,
                    trading_days=self.trading_days, rest_days=self.rest_days,
                    tick=self.tick, slices=self.session_ticks, market_time=self.market_time(),
                    session_start=self.session_start, session_hours=self.session_hours,
                    window='planning', window_name='盘前规划',
                    window_limit=self.planning_actions, window_used=self.used.get(actor, 0))
    def advance(self):
        self.round += 1
        self.tick = 0
        self.ended, self.used, self.summaries = set(), {}, {}
        return self.round
    def to_save(self):
        return dict(round=self.round, tick=self.tick, ended=sorted(self.ended),
                    used=dict(self.used), summaries=dict(self.summaries))
    def restore(self, state):
        if type(state.get('round')) is not int or state['round'] < 1 or state.get('tick') != 0:
            raise ValueError('无效的边界时钟')
        self.round = state['round']
        self.tick = 0
        self.ended = set(state['ended'])
        self.used, self.summaries = deepcopy(state['used']), deepcopy(state['summaries'])


class ClockAPI:
    def __init__(self, world): self.world = world
    def end_turn(self, actor, summary=''): return self.world.clock.end_turn(actor, summary)
    def snapshot(self, actor): return self.world.clock.view(actor)
    def sleep(self, actor, ticks=100): return self.world.clock.sleep(actor, ticks)


class ClockMod(GameMod):
    def mixins(self): return ()
    def initialize_world(self, world):
        p = self.declaration['Parameters']
        world.clock = Clock(p['TradingDays'], p['RestDays'], p['SessionTicks'],
                            p['SessionStart'], p['SessionHours'], p['PlanningActions'])
    def objects(self, world): return {'stock:clock': world.clock}
    def player_api(self, world): return ClockAPI(world)
    def save_state(self, world): return world.clock.to_save()
    def load_state(self, world, state): world.clock.restore(state)
    def stages(self):
        return (Stage('stock.clock.advance', lambda w, c: c.update(round=w.clock.advance()), hook='cycle.end'),)
    def rule_sections(self, world=None):
        p = self.declaration['Parameters']
        return (RuleSection('回合与共同时间线',
            f'每{p["TradingDays"]}个交易日后休市{p["RestDays"]}天。每天固定{p["SessionTicks"]}逻辑tick。'
            f'盘前每账户有{p["PlanningActions"]}次规划动作，可改策略、租算力、使用已解锁研究槽、借贷。'
            'end_turn提交小结。全体结束后锁定策略，沿共同tick运行，成交最早下一tick生效。'
            'sleep只安排下次回调，不等待现实时间、不按等待tick扣计算量。'
            '休市日不撮合，仍可规划、研究和租用资源。下回合收到每槽输出、错误、用量及账户反馈。',
            ('回合', 'tick', '休市', '窗口'), '回合节奏与规划动作额度', system=True),)
