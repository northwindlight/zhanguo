"""Army interprets unit definitions; the engine only registers their IDs."""
from collections.abc import Mapping
from engine.identifiers import content_id
from .parameters import UNIT_TYPES, MOVE_COST


def native_unit_definitions():
    return {key: {'category': 'unit', **values, 'move_cost': MOVE_COST[key]}
            for key, values in UNIT_TYPES.items()}


def unit_definitions(content):
    result = {}
    for key, definition in content.definitions():
        if not isinstance(definition, Mapping) or definition.get('category') != 'unit':
            continue
        for field in ('hp', 'speed', 'supply', 'atk'):
            minimum = 1 if field in ('hp', 'speed') else 0
            if type(definition.get(field)) is not int or definition[field] < minimum:
                raise ValueError(f'Invalid {field} for unit {key}')
        if not isinstance(definition.get('name_key'), str) or not definition['name_key']:
            raise ValueError(f'Unit {key} must declare its presentation key')
        cost = definition.get('recruit')
        if not isinstance(cost, Mapping) or not cost:
            raise ValueError(f'Unit {key} must declare recruitment resources')
        for resource_id, amount in cost.items():
            content_id(resource_id)
            if type(amount) is not int or amount <= 0:
                raise ValueError(f'Invalid recruitment cost for unit {key}')
        movement = definition.get('move_cost')
        if not isinstance(movement, Mapping) or not movement or any(v is not None and (type(v) is not int or v < 1) for v in movement.values()):
            raise ValueError(f'Unit {key} must declare movement costs')
        result[key] = definition
    return result
