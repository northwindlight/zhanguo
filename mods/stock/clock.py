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
        self.final_round = None   # 由会话按 max_turns 注入；最后一回合只做清算
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
        final = self.final_round
        left = max(0, final - self.round) if isinstance(final, int) else None
        return dict(round=self.round, trading_day=self.trading_day(),
                    days_to_next_open=self.days_to_next_open(), cycle=self.cycle,
                    trading_days=self.trading_days, rest_days=self.rest_days,
                    tick=self.tick, slices=self.session_ticks, market_time=self.market_time(),
                    session_start=self.session_start, session_hours=self.session_hours,
                    window='planning', window_name='盘前规划',
                    window_limit=self.planning_actions, window_used=self.used.get(actor, 0),
                    final_round=final, rounds_left=left,
                    is_final_round=isinstance(final, int) and self.round >= final)
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
        final = getattr(getattr(world, 'clock', None), 'final_round', None)
        total = f'本局共 {final} 回合' if isinstance(final, int) else '本局回合数见 round 工具的 final_round'
        return (RuleSection('游戏目标与回合',
            '【怎么赢】最终回合开始时，现金最多的人赢。股票、空头、未还贷款都不直接算分：'
            '最后一回合系统会撤掉所有未成交委托、按最后成交价平掉全部多头和空头（照收佣金）、'
            '再用现金尽量归还贷款；结算现金 = 现金 − 仍未还清的贷款，按结算现金排名。'
            '所以越接近终局，越应该把仓位换成现金，别把风险留到最后一刻。'
            f'【回合】{total}；第 N 回合不行动，只做清算。随时用 round 工具看 round（当前第几回合）、'
            'final_round（最终回合）、rounds_left（还剩几回合）。'
            f'每 {p["TradingDays"]} 个交易日后休市 {p["RestDays"]} 天，休市日不撮合，但仍可规划、研究和租用资源。'
            '【一回合怎么过】① 盘前规划：查行情、写/改策略脚本、买算力或情报、借贷，'
            f'每账户最多 {p["PlanningActions"]} 次规划动作；② end_turn 提交一句小结；'
            f'③ 全体结束后锁定策略，行情沿每天 {p["SessionTicks"]} 个逻辑 tick 推进，脚本自动执行，委托最早下一 tick 生效；'
            '④ 收盘结算费用、利息与风险，进入下一回合，简报会给你脚本输出与成交结果。'
            '【第一回合做什么】先看 round（还剩几回合）、portfolio（现金与持仓）、market（行情与盘口）、'
            'compute_status（算力预算）；写一份保守的小仓位策略到槽 1（先用研究槽验证字段更稳），再 end_turn。'
            '【重要】模型工具里没有买卖按钮，所有交易都必须写成脚本；sleep 只安排下次回调，不推进行情。',
            ('目标', '回合', '怎么玩', '清算', 'end_turn'), '胜负条件、回合流程与最终清算', system=True),)
