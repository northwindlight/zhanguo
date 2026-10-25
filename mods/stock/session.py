"""股市发行版的会话：开局、账户与回合记录。"""
from concurrent.futures import ThreadPoolExecutor

from engine.session import SaveFormatError, Session


class StockSession(Session):
    """没有地图的回合制会话：名单固定，先手每回合轮换一格。

    规划阶段各量化员互相独立（各自的账户、脚本、谍报），模型等待不需要
    互相排队：cycle 默认用线程池并发跑各玩家的 client.play，世界变更仍
    经 BoundPlayer 的会话锁串行入账，观战回调经同一把锁串行输出。
    配置 planning_workers=1 可退回严格串行。
    """

    # 脚本执行期的逐笔回执（下单受理、成交、清算、脚本内借贷）只进审计，
    # 不再逐条刷给看海人；run_all 结束时每个玩家发一条 settlement 摘要代替。
    SCRIPT_QUIET = frozenset(('buy', 'sell', 'short', 'cover', 'cancel',
                              'stock_fill', 'stock_liquidation', 'borrow', 'repay'))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from .backend import PamsBook
        PamsBook.require()  # Before model construction or paid requests, also on load.
        self.after_spawn()  # Also bind audit hooks on the restore path.
        self._settled = False
        self.final_ranking = []
        self._settlement_events = []
        self._bind_final_round()

    def _bind_final_round(self):
        """把配置里的总回合数告诉时钟：第 N 回合只做清算，玩家不再行动。"""
        total = self.cfg.get('max_turns')
        if isinstance(total, int) and total > 0:
            self.world.clock.final_round = total

    def restore(self, data):
        super().restore(data)
        self._bind_final_round()
        self._settled = any(row.get('final_cash') is not None
                            for row in self.world.portfolio.accounts.values())
        self._settlement_events = self._settlement_report() if self._settled else []
        self.final_ranking = []

    def entries(self):
        return self.cfg['nations']

    def identities(self):
        return tuple(item['id'] for item in self.entries() if item.get('enabled', True))

    def finished(self):
        return self._settled or not self.actors()

    def _guard(self, callback):
        """观战回调经会话锁串行：不同量化员的输出行不交织，也不会与入账互锁。"""
        if callback is None:
            return None
        def invoke(*args, **kwargs):
            with self.lock:
                return callback(*args, **kwargs)
        return invoke

    def _plan_one(self, actor, emit, head, on_call, on_stream):
        """跑完一个量化员的整个规划循环（含控制转移），返回其回执。"""
        client = self.clients[actor]
        receipts = []
        transitions = 0
        while True:
            player = self.player(actor)
            generation = player.generation
            if not player.action_state()['can_act']:
                break
            if client.kind == 'rule':
                cfg = next(n for n in self.entries() if n['id'] == actor)
                receipts.extend(client.play(player, max_actions=cfg.get('max_actions')))
            else:
                callback_call = (lambda step, result, a=actor: self._guard(on_call)(a, step, result)) if on_call else None
                receipts.extend(client.play(player, emit=self._guard(emit), head=self._guard(head),
                                            on_call=callback_call,
                                            **({'on_stream': self._guard(on_stream)} if on_stream and client.kind == 'llm' else {})))
            if self.generations[actor] == generation:
                break
            transitions += 1
            if transitions > 8:
                raise RuntimeError('Too many actor control transitions in one cycle')
        return receipts

    def _cycle_parallel(self, *, on_result=None, emit=None, head=None, on_call=None, on_stream=None, workers=2):
        """并发规划：与引擎串行 cycle 同一套语义，只是各玩家的等待不再排队。

        世界变更全部经 BoundPlayer 的会话锁串行入账（submit/observe/events），
        因此并发只重叠模型的网络等待，不重叠任何世界变更；回执按先手轮换
        顺序归并，last_results 与串行一致。
        """
        if self.victory_event is not None:
            raise ValueError('Game has ended with a victory event')
        self.scheduled()
        receipts = []
        self._boundary = False
        self._callbacks = on_result
        self._emit = emit
        try:
            actors = [actor for actor in self.turn_order() if self.world.clock.can_act(actor)]
            with ThreadPoolExecutor(max_workers=min(len(actors), workers)) as pool:
                futures = {actor: pool.submit(self._plan_one, actor, emit, head, on_call, on_stream)
                           for actor in actors}
                for actor in actors:  # deterministic merge order
                    receipts.extend(futures[actor].result())
            self.world.resolve_turn()
            self.world.phase = '行动'
            for actor in self.actors():
                for service in self.services[actor].values():
                    settled = getattr(service, 'on_settlement', None)
                    if settled:
                        settled(self.player(actor))
                settled = getattr(self.clients[actor], 'on_settlement', None)
                if settled:
                    settled(self.player(actor))
            self.last_results = receipts
            self._boundary = True
            return receipts
        finally:
            self._callbacks = None
            self._emit = None

    def cycle(self, **kwargs):
        workers = self.cfg.get('planning_workers')
        actors = [actor for actor in self.actors() if self.world.clock.can_act(actor)]
        parallel = (not isinstance(workers, int) or workers >= 2) and len(actors) > 1
        if parallel:
            receipts = self._cycle_parallel(workers=workers if isinstance(workers, int) else len(actors),
                                            **kwargs)
        else:
            receipts = super().cycle(**kwargs)
        final = self.world.clock.final_round
        if not self._settled and isinstance(final, int) and self.clock() >= final:
            self.final_settlement()
        return receipts

    def final_settlement(self):
        """最终回合：撤单、按最后价平仓、用现金还贷，按结算现金排名。"""
        round_number = self.clock()
        rows = []
        for actor in self.actors():
            result = self.world.portfolio.finalize(actor, round_number)
            rows.append(dict(actor=actor, name=self.roster[actor]['name'],
                             round=round_number, **result))
        ranking = sorted(rows, key=lambda row: row['final_cash'], reverse=True)
        for place, row in enumerate(ranking, 1):
            self.world.portfolio.accounts[row['actor']]['final_rank'] = place
        self.final_ranking = ranking
        self._settled = True
        self._settlement_events = self._settlement_report()
        return ranking

    def _settlement_report(self):
        """把清算结果写成事件，看海控制台和日志都能看到名次（新局与读档共用）。"""
        rows = [(account.get('final_rank') or 999, account) for account in
                self.world.portfolio.accounts.values() if account.get('final_cash') is not None]
        rows.sort(key=lambda item: item[0])
        events = []
        for place, account in enumerate([row for _, row in rows], 1):
            text = (f'【最终清算】第 {place} 名 {account["name"]}：结算现金 {account["final_cash"]:.2f}'
                    f'（现金 {account["cash"]:.2f}，未还贷款 {account["debt"]:.2f}，'
                    f'已还 {account.get("final_repaid", 0.0):.2f}，'
                    f'平仓 {len(account.get("final_liquidations") or [])} 笔）')
            events.append(dict(id=place, period=account.get('final_round') or 0,
                               kind='settlement', text=text, details=None))
        return events

    def after_spawn(self):
        self.world.scripts.recorder = self.record_script
        self.world.market.recorder = self.record_script

    def record_script(self, actor, action, arguments, result):
        from types import SimpleNamespace
        from engine.player import API_VERSION
        player = SimpleNamespace(actor=actor, generation=self.generations[actor])
        receipt = result if 'ok' in result else dict(api=API_VERSION, actor=actor,
            period=self.clock(), action=action, ok=True, status='effective',
            code=None, message='', data=result)
        if action in self.SCRIPT_QUIET:
            # 审计照记（完整可追溯），观战回调静默一整个交易日。
            callbacks, self._callbacks = self._callbacks, None
            try:
                self.record(player, action, arguments, receipt)
            finally:
                self._callbacks = callbacks
            return
        self.record(player, action, arguments, receipt)

    def configure(self):
        """按配置播种：同一 seed 的行情路径完全可复现。"""
        self.world.market.reseed(self.cfg.get('seed', 0))

    def add(self, name):
        """手动登场：把配置里已声明、还没上场的身份拉进这一局。"""
        item = next((entry for entry in self.entries() if name in (entry['id'], entry['name'])), None)
        if item is None:
            raise ValueError('该身份未在配置中声明')
        if item['id'] in self.roster:
            raise ValueError('身份已登场，历史身份不能复用')
        return self.join(item, None)

    def _positions(self, count):
        """没有地图，位置只是占位。"""
        return [(0, 0)] * count

    def spawn(self, item, position):
        actor = item['id']
        self.world.portfolio.open_account(actor, item['name'])
        self.world.compute.open_account(actor)
        return actor

    def validate_boundary(self):
        """有人已经结束行动但回合还没结算时，存档会丢掉未提交的挂单。"""
        if any(not self.world.clock.can_act(actor) for actor in self.actors()):
            raise SaveFormatError('Cannot save a stock cycle that is still in progress')

    def observer_snapshot(self):
        """终端管理接口要的观察快照：回合、行情、面板行。"""
        names = {actor: row['name'] for actor, row in self.roster.items()}
        nations = [self.player(actor).observe() for actor in self.actors()]
        prices = {row['symbol']: row['price'] for row in self.world.market.quotes()}
        return {
            'period': self.clock(),
            'names': names,
            'nations': nations,
            'market': {'prices': prices},
            'bank': {'rate': 0.0, 'loan_rate': self.world.portfolio.interest_rate,
                     'minimum': 0.0, 'maximum': 0.0},
            'board': self.board_rows(),
        }

    def records(self):
        return {'events': list(self._settlement_events), 'messages': []}

    def announce(self, text):
        return None

    def send(self, actor, text):
        return None

    def board_rows(self):
        """控制台面板：每个玩家一行。"""
        rows = []
        for actor in self.roster:
            account = self.world.portfolio.holdings(actor)
            row = {'name': account['name'], 'wealth': account['wealth'],
                         'cash': account['cash'], 'debt': account['debt'],
                         'positions': len(account['positions']),
                         'return_pct': account['return_pct'], 'alive': account['alive'],
                         '算力池': self.world.compute.status(actor)['pool'],
                         '交易槽': self.world.compute.status(actor)['slots'],
                         '研究次数': self.world.compute.status(actor)['research_runs_left']}
            if account.get('final_cash') is not None:
                row.update({'最终现金': account['final_cash'], '名次': account.get('final_rank')})
            rows.append(row)
        return rows
