"""Presentation-only locale lookup; content IDs never pass through translation."""
from functools import lru_cache
import json
from pathlib import Path


@lru_cache(maxsize=None)
def _messages(locale):
    if locale not in ('zh-CN', 'en-US'):
        raise ValueError(f'Unsupported locale: {locale}')
    path = Path(__file__).with_name('locales') / f'{locale}.json'
    return json.loads(path.read_text(encoding='utf-8'))


def text(key, locale='zh-CN', **values):
    return _messages(locale).get(key, key).format(**values)


def resource_name(resource_id, locale='zh-CN'):
    return text(f'resource.{resource_id}.name', locale)


def resource_cost(cost, locale='zh-CN'):
    return '+'.join(f'{amount}{text(f"resource.{key}.short", locale)}'
                    for key, amount in cost.items())


def building_name(building_id, locale='zh-CN'):
    return text(f'building.{building_id}.name', locale)
