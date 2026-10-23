"""股市发行版的会话：开局、账户与回合记录。"""
from engine.session import SaveFormatError, Session


class StockSession(Session):
    """没有地图的回合制会话：名单固定，先手每回合轮换一格。"""

    def entries(self):
        return self.cfg['nations']

    def identities(self):
        return tuple(item['id'] for item in self.entries() if item.get('enabled', True))

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
        return {'events': [], 'messages': []}

    def announce(self, text):
        return None

    def send(self, actor, text):
        return None

    def board_rows(self):
        """控制台面板：每个玩家一行。"""
        rows = []
        for actor in self.actors():
            account = self.world.portfolio.holdings(actor)
            rows.append({'name': account['name'], 'wealth': account['wealth'],
                         'cash': account['cash'], 'debt': account['debt'],
                         'positions': len(account['positions']),
                         'return_pct': account['return_pct']})
        return rows
