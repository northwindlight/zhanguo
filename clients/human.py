"""A human seat delegates input to its host; it never chooses game actions."""
from engine.config import Field, Schema

SCHEMA = Schema({'nickname': Field((str,))})


def normalize(cfg, *, defaults=None, overrides=None, path='config', runtime=None):
    result = SCHEMA.normalize(cfg, path=path)
    if not result.get('nickname', '').strip():
        result['nickname'] = '玩家'
    return result


normalize.fields = SCHEMA.fields


class HumanClient:
    kind = 'human'
    version = 'human/1'

    def __init__(self, cfg, *, record=None, services=None):
        self.cfg = normalize(cfg)
        if record is not None and record != self.to_save():
            raise ValueError('Unsupported human client state')

    def play(self, player, *, request=None):
        if not callable(request):
            raise ValueError('真人席位需要交互宿主，不会自动结束或代打')
        receipts = request(player)
        if player.action_state()['can_act']:
            raise ValueError('真人尚未明确结束本国行动，不能推进结算')
        return receipts

    def to_save(self):
        return {'kind': self.kind, 'version': self.version, 'state': {}}
