"""最小可玩游戏的客户端：一个不依赖模型、每次都能做出动作的简易策略。

它做三件事：在自己的一块地上补建筑，有兵营就征兵，然后结束回合。
写它是因为 `client` 只接受 `llm` 与 `rule`，覆盖 `llm` 工厂即可让整局
在没有模型、没有 API key 的情况下跑起来。
"""

WANTED = ('native:barracks', 'native:farm', 'native:lumber-camp', 'native:gold-mine')


class MinigameClient:
    kind = 'llm'
    version = 'minigame/1'

    def __init__(self, cfg, *, record=None, services=None):
        self.cfg = dict(cfg)
        self.services = dict(services or {})
        self.periods = 0
        if record is not None:
            if record.get('kind') != self.kind or record.get('version') != self.version:
                raise ValueError('Unsupported minigame client state')
            self.periods = int(record['state']['periods'])

    def play(self, player, *, emit=None, head=None, on_call=None, on_result=None):
        receipts = []

        def submit(action, **arguments):
            receipt = player.submit(action, arguments)
            receipts.append(receipt)
            if on_result:
                on_result(player.actor, action, arguments, receipt)
            return receipt

        observation = player.observe()
        if not observation['can_act']:
            return receipts
        mine = [cell for cell in observer_cells(player) if cell['owner'] == player.actor]
        if mine:
            home = mine[0]
            x, y = home['position']
            built = {kind for cell in mine for kind in cell['buildings']}
            target = next((kind for kind in WANTED if kind not in built), None)
            if target:
                submit('build', x=x, y=y, kind=target)
            elif home['buildings'].get('native:barracks'):
                submit('recruit', x=x, y=y)
        submit('end_turn')
        self.periods += 1
        if emit:
            emit(f'⚙ {player.actor} 第 {observation["period"]} 期行动完毕')
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': {'periods': self.periods}}


def observer_cells(player):
    """land 工具返回可见地块列表，每项含 position / owner / buildings。"""
    return player.query('land')
