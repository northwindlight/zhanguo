"""提供一个客户端。

`host.normalize_config` 只接受 `client = "llm"` 与 `client = "rule"` 两种取值。
换实现的途径是在发行版里声明客户端工厂，覆盖其中一种：

    [Distribution.ClientFactories.llm]
    Entry = "包.模块:类名"

配置里仍写 `client = "llm"`，由工厂决定实际用哪个类。

发行版还可以给工厂绑定服务：

    [Distribution.ClientFactories.llm.Services.<别名>]
    Capability = "native:government"
    Methods = ["enroll", "decide"]

宿主据此生成 `services[别名](方法名, *参数)`；未列在 Methods 里的方法不存在，
客户端拿不到，模型也看不到。

客户端需要实现的接口：
    kind                          字符串
    __init__(cfg, *, record=None, services=None)
    play(player, *, emit, head, on_call, on_result) -> 回执列表
    to_save()                     {'kind', 'version', 'state'}
`on_settlement(player)` 可选。
返回值经 `wire()` 处理，允许的类型见 engine/player.py。
"""
from engine.player import wire  # noqa: F401  （说明 wire 的位置）


class IdleClient:
    kind = 'llm'
    version = 'idle/1'

    def __init__(self, cfg, *, record=None, services=None):
        # cfg 是宿主为该国家准备的配置副本，含 joined_at 与剧本常驻文案。
        self.cfg = dict(cfg)
        self.services = dict(services or {})
        self.periods = 0
        if record is not None:
            if record.get('kind') != self.kind or record.get('version') != self.version:
                raise ValueError('Unsupported idle client state')
            self.periods = int(record['state']['periods'])

    def play(self, player, *, emit=None, head=None, on_call=None, on_result=None):
        receipts = []

        def submit(action, arguments):
            receipt = player.submit(action, arguments)
            receipts.append(receipt)
            if on_result:
                on_result(player.actor, action, arguments, receipt)
            return receipt

        observation = player.observe()
        if not observation['can_act']:
            return receipts
        period = observation['period']
        # end_turn 要求先有本期的国策。
        submit('plan', {'text': f'第 {period} 期：按兵不动，只看不动。'})
        submit('end_turn', {'summary': '本回合只观察，不做任何行动。'})
        self.periods += 1
        if emit:
            emit(f'🛋 {player.actor} 按兵不动（第 {period} 期）')
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': {'periods': self.periods}}
