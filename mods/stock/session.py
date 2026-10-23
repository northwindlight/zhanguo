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

    def _positions(self, count):
        """没有地图，位置只是占位。"""
        return [(0, 0)] * count

    def spawn(self, item, position):
        actor = item['id']
        self.world.portfolio.open_account(actor, item['name'])
        return actor

    def player(self, actor):
        """新行动者进场：市场往前走一个时间片，股价因此在盘中连续跳动。"""
        player = super().player(actor)
        self.world.clock.begin_turn(actor)
        return player

    def validate_boundary(self):
        """有人已经结束行动但回合还没结算时，存档会丢掉未提交的挂单。"""
        if any(not self.world.clock.can_act(actor) for actor in self.actors()):
            raise SaveFormatError('Cannot save a stock cycle that is still in progress')

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
