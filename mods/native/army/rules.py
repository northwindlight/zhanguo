"""Army-owned type identity and queries; no translated identity fallbacks."""
from .content import native_unit_definitions
from ..localization import text


def unit_kind(unit):
    return unit['type']


def _stats(unit):
    return getattr(unit, 'definition', None) or native_unit_definitions()[unit_kind(unit)]


def unit_max_hp(unit):
    return _stats(unit)['hp']


def unit_speed(unit):
    return _stats(unit)['speed']


def unit_atk(unit):
    return _stats(unit)['atk']


def unit_supply(unit):
    return _stats(unit)['supply']


def unit_move_cost(unit, terrain):
    return _stats(unit)['move_cost'].get(terrain, 1)


def unit_label(kind, locale='zh-CN', definition=None):
    definition = definition if definition is not None else native_unit_definitions()[kind]
    return text(definition['name_key'], locale)


def army_name(owner, seq, kind, locale='zh-CN'):
    # Numbers remain data; only the surrounding name template is translated.
    return text('unit.name', locale, owner=owner, sequence=seq,
                kind=text(f'unit.{kind}.short', locale))
