"""股市发行版的配置契约：种子与玩家名单。"""
import hashlib

from engine.config import ConfigError, Field, Schema
from engine.identifiers import content_id


GAME = Schema({
    'seed': Field((int,)),
})

PLAYER = Schema({
    'name': Field((str,)),
    'client': Field((str,)),
    'enabled': Field((bool,)),
    'appearance': Field((str,), choices=('initial', 'scheduled', 'manual')),
    'extra_prompt': Field((str,)),
    'extra_summary': Field((str,)),
})


def normalize(cfg, *, runtime, overrides):
    cfg = GAME.normalize(cfg, overrides=overrides)
    cfg.setdefault('seed', 0)
    entries = cfg.get('nations')
    if not isinstance(entries, list) or not entries:
        raise ConfigError('config.nations: expected nonempty list')
    ids, names = set(), set()
    for index, item in enumerate(entries):
        path = f'config.nations[{index}]'
        if not isinstance(item, dict):
            raise ConfigError(f'{path}: expected object')
        item = PLAYER.normalize(item, path=path)
        if not item.get('name', '').strip():
            raise ConfigError(f'{path}.name: expected display name')
        try:
            item['id'] = content_id(item.get('id') or
                                   'stock:actor-' + hashlib.sha256(item['name'].encode()).hexdigest()[:12])
        except (TypeError, ValueError):
            raise ConfigError(f'{path}.id: invalid content id') from None
        if item['id'] in ids or item['name'] in names:
            raise ConfigError(f'{path}: duplicate stock player')
        ids.add(item['id'])
        names.add(item['name'])
        item.setdefault('client', 'llm')
        item.setdefault('enabled', True)
        item.setdefault('appearance', 'initial')
        if item['client'] not in runtime.distribution.get('ClientFactories', {}):
            raise ConfigError(f'{path}.client: factory not declared')
        entries[index] = item
    return cfg
