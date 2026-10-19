"""Shared model-facing argument normalizers for every tool/action."""
from engine.coordinates import coordinates

from mods.native.targets import nation_list, nation_reference


def place_ref(world, actor, value):
    """Canonicalize any reference that carries coordinates.

    ``临渡(21, 8)`` -> ``21 8``; bare names and pure IDs are left untouched for
    the domain resolvers. Coordinates stay 0-based.
    """
    if not isinstance(value, str):
        return value
    point = coordinates(value)
    if point is None:
        return value
    return f'{point[0]} {point[1]}'


DEFAULT_NORMALIZERS = {
    'site': place_ref,
    'place': place_ref,
    'target': nation_reference,
    'to': nation_reference,
    'tos': nation_list,
}
