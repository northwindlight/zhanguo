"""回合时钟：回合号、交易日历、行动门，以及两个窗口的动作额度。

一个回合的形状：

* **开盘窗口**（`MarketWindow` 分钟）：玩家打磨脚本，然后跑一次脚本下单。
* **回合末统一工作窗口**（`WorkWindow` 分钟）：脚本跑完以后继续改脚本，
  改动留给下一回合——所有玩家同时改，谁也看不见谁的脚本。

额度按模型侧工具调用计数，是墙钟之外的确定性口径；墙钟耗时也会报给模型，
两者一起写进回合开局简报。

交易日历是纯算术：每 `TradingDays` 个交易日休市 `RestDays` 天，
所以第 1-5 回合交易、第 6-7 回合休市、第 8-12 回合交易……
"""
from __future__ import annotations

import time

from engine.runtime import GameMod, ModError, RuleSection, Stage

MARKET_WINDOW = 'market'
WORK_WINDOW = 'work'


class Clock:
    """回合时钟的领域对象：世界的 `world.clock`。"""

    def __init__(self, trading_days=5, rest_days=2, market_window=20, work_window=20):
        self.trading_days = trading_days
        self.rest_days = rest_days
        self.market_window = market_window
        self.work_window = work_window
        self.round = 1
        self.ended = set()
        self.opened = set()
        self.used = {}
        self.summaries = {}
        self.started_at = time.monotonic()
        self.tick = 0
        self.acted = set()
        self.slice_total = 0
        self.work_started_at = 0.0
        self._world = None
        self._key = None

    # ── 状态绑定 ────────────────────────────────────────────────
    def bind_state(self, world, key):
        if self._world is not None and self._world is not world:
            raise ModError('clock 已绑定到别的世界')
        self._world = world
        self._key = key
        return self

    @property
    def cycle(self):
        """日历循环长度：交易日 + 休市日。"""
        return self.trading_days + self.rest_days

    # ── 对象接口（ActionGate / PlayerClock / 跨模块调用）────────
    def period(self):
        return self.round

    def trading_day(self):
        """本回合是不是交易日。"""
        return (self.round - 1) % self.cycle < self.trading_days

    def days_to_next_open(self):
        """距离下一次开盘还有几个回合（休市日为 0）。"""
        position = (self.round - 1) % self.cycle
        return 0 if position < self.trading_days else self.cycle - position

    def can_act(self, actor):
        return actor not in self.ended

    def slices(self):
        """本回合切成几个时间片。

        行动者一人一片，最后再留一片给收盘：收盘价落在最后一片上，
        谁都看不到它，所以没有人能提前知道本回合的收盘价。
        """
        if self.slice_total:
            return self.slice_total
        world = self._world
        actors = tuple(getattr(world, 'nation_actors', ()) or ()) if world is not None else ()
        return max(1, len(actors)) + 1

    def tick_index(self):
        """已经走过的时间片数。"""
        return self.tick

    def begin_turn(self, actor):
        """新行动者进场：市场往前走一个时间片，股价因此在盘中连续跳动。

        一个行动者一回合只推进一次：回合循环里 player() 会被反复调用，
        结算后的回调也会再调一遍，都不再推进。
        """
        world = self._world
        if world is None or getattr(world, 'phase', '行动') != '行动':
            return self.tick
        if actor in self.acted:
            return self.tick
        if actor not in tuple(getattr(world, 'nation_actors', ()) or ()):
            return self.tick
        self.acted.add(actor)
        self.tick = min(self.tick + 1, self.slices() - 1)
        return self.tick

    def window_of(self, actor):
        """玩家当前所在的窗口：跑过脚本就转入回合末工作窗口。"""
        return WORK_WINDOW if actor in self.opened else MARKET_WINDOW

    def limit_of(self, window):
        return self.work_window if window == WORK_WINDOW else self.market_window

    def used_of(self, actor):
        return self.used.get(actor, {MARKET_WINDOW: 0, WORK_WINDOW: 0})

    def charge(self, actor):
        """记一次动作。额度用满就抛错，模型会看到「窗口已用满」。"""
        window = self.window_of(actor)
        limit = self.limit_of(window)
        counts = self.used.setdefault(actor, {MARKET_WINDOW: 0, WORK_WINDOW: 0})
        if counts[window] >= limit:
            name = '回合末统一工作' if window == WORK_WINDOW else '开盘'
            raise ValueError(f'{name}窗口已用满 {limit} 分钟，本回合不能再调用动作。')
        counts[window] += 1
        return counts[window]

    def close_session(self):
        """收盘：走到最后一片，所有脚本在这里一起执行、看到同一份快照。"""
        self.tick = self.slices()
        self.work_started_at = time.monotonic()
        return self.tick

    def work_expired(self):
        """回合末统一工作是否用满了时限（墙钟）。"""
        if not self.work_started_at:
            return False
        return (time.monotonic() - self.work_started_at) > self.work_window * 60.0

    def work_elapsed(self):
        if not self.work_started_at:
            return 0.0
        return round((time.monotonic() - self.work_started_at) / 60.0, 2)

    def mark_open(self, actor):
        """脚本已执行：开盘窗口用掉，转入回合末统一工作窗口。"""
        self.opened.add(actor)

    def end_turn(self, actor, summary=''):
        if actor in self.ended:
            raise ValueError('本回合已经结束过行动。')
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError('回合小结不能为空：写一句这回合做了什么、打算做什么。')
        self.ended.add(actor)
        self.summaries[actor] = summary.strip()
        return {'actor': actor, 'summary': self.summaries[actor],
                'text': self.summaries[actor], 'round': self.round}

    def view(self, actor):
        """回合开局给模型看的时钟视图。"""
        window = self.window_of(actor)
        counts = self.used_of(actor)
        elapsed = max(0.0, time.monotonic() - self.started_at)
        return {
            'round': self.round,
            'trading_day': self.trading_day(),
            'days_to_next_open': self.days_to_next_open(),
            'cycle': self.cycle,
            'trading_days': self.trading_days,
            'rest_days': self.rest_days,
            'window': window,
            'window_name': '回合末统一工作' if window == WORK_WINDOW else '开盘',
            'work_window_minutes': self.work_window,
            'work_elapsed_minutes': self.work_elapsed(),
            'tick': self.tick,
            'slices': self.slices(),
            'window_limit': self.limit_of(window),
            'window_used': counts[window],
            'market_used': counts[MARKET_WINDOW],
            'work_used': counts[WORK_WINDOW],
            'market_window_minutes': self.market_window,
            'opened': actor in self.opened,
            'elapsed_minutes': round(elapsed / 60.0, 2),
        }

    def advance(self):
        """回合结束：回合号 +1，清空本回合的窗口额度、结束标记与时间片。"""
        self.round += 1
        self.ended = set()
        self.opened = set()
        self.used = {}
        self.summaries = {}
        self.tick = 0
        self.acted = set()
        self.started_at = time.monotonic()
        return self.round

    # ── 存档 ────────────────────────────────────────────────────
    def to_save(self):
        return {
            'round': self.round,
            'ended': sorted(self.ended),
            'opened': sorted(self.opened),
            'used': {actor: dict(counts) for actor, counts in self.used.items()},
            'summaries': dict(self.summaries),
            'tick': self.tick,
            'acted': sorted(self.acted),
        }

    def restore(self, state):
        self.round = state['round']
        self.tick = state.get('tick', 0)
        self.acted = set(state.get('acted', ()))
        self.ended = set(state.get('ended', ()))
        self.opened = set(state.get('opened', ()))
        self.used = {actor: dict(counts) for actor, counts in state.get('used', {}).items()}
        self.summaries = dict(state.get('summaries', {}))


class ClockAPI:
    """玩家侧 API：结束回合、读时钟。"""

    def __init__(self, world):
        self.world = world

    def end_turn(self, actor, summary=''):
        return self.world.clock.end_turn(actor, summary)

    def snapshot(self, actor):
        return self.world.clock.view(actor)


class ClockMod(GameMod):
    def mixins(self):
        return ()

    def numbers(self):
        cfg = self.declaration['Parameters']
        return {f'clock.{key}': value for key, value in cfg.items()}

    def initialize_world(self, world):
        cfg = self.declaration['Parameters']
        world.clock = Clock(cfg['TradingDays'], cfg['RestDays'],
                            cfg['MarketWindow'], cfg['WorkWindow'])

    def objects(self, world):
        return {'stock:clock': world.clock}

    def save_state(self, world):
        return world.clock.to_save()

    def load_state(self, world, state):
        world.clock.restore(state)

    def player_api(self, world):
        return ClockAPI(world)

    def stages(self):
        return (Stage('stock.clock.advance',
                       lambda w, c: c.update(round=w.clock.advance()),
                       hook='cycle.end'),)

    def rule_sections(self, world=None):
        cfg = self.declaration['Parameters']
        return (RuleSection(
            '回合与交易日历',
            f'一个回合分两段：开盘窗口 {cfg["MarketWindow"]} 分钟，用来打磨脚本并跑一次脚本下单；'
            f'回合末统一工作 {cfg["WorkWindow"]} 分钟，跑完脚本以后接着改脚本，改动留给下一回合。'
            f'所有玩家同时改脚本，谁也看不见谁的脚本。'
            f'每 {cfg["TradingDays"]} 个交易日休市 {cfg["RestDays"]} 天：休市日不能买卖，'
            f'但可以继续改脚本、规划、借贷。脚本每回合只能跑一次，失败也算用过。'
            f'end_turn 结束本回合行动，提交一句小结。',
            ('回合', '窗口', '开盘', '休市', '交易日', 'end_turn', '结算'),
            '回合节奏、交易日历与两个窗口的额度',
            system=True),)
