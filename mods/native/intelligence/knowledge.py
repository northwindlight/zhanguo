"""Persisted field-wise map memory. Missing observations never mean deletion."""
from copy import deepcopy


class MapKnowledge:
    def __init__(self):
        self.cells = {}
        self.next_order = 1

    def order(self):
        value = self.next_order
        self.next_order += 1
        return value

    def merge(self, owner, rows, *, sampled, order, source, acquired, building_kinds=(), complete=False):
        cells = self.cells.setdefault(owner, {})
        for item in rows:
            point = list(item['position'])
            cell = cells.setdefault(f'{point[0]},{point[1]}', {'position': point, 'fields': {}})
            fields = cell['fields']
            values = {key: deepcopy(item[key]) for key in ('name', 'terrain', 'owner', 'resources') if key in item}
            if 'buildings' in item:
                buildings = item['buildings']
                construction = item.get('construction', {})
                kinds = set(buildings) | set(construction) | set(building_kinds)
                if complete:kinds.update(key[9:] for key in fields if key.startswith('building/'))
                for kind in kinds:
                    values['building/' + kind] = {'count': buildings.get(kind, 0),
                        'construction': construction.get(kind, 0)}
            for key, value in values.items():
                old = fields.get(key)
                if old is None or (sampled, order) > (old['sampled'], old['order']):
                    fields[key] = {'value': value, 'sampled': sampled, 'order': order,
                                   'source': source, 'acquired': acquired}

    def snapshot(self, owner):
        return deepcopy(sorted(self.cells.get(owner, {}).values(), key=lambda row: row['position']))

    def to_save(self):
        return {'cells': deepcopy(self.cells), 'next_order': self.next_order}

    def restore(self, data, nation, size, max_period=None):
        if not isinstance(data, dict) or set(data) != {'cells', 'next_order'} or not isinstance(data['cells'], dict):
            raise ValueError('Invalid map knowledge')
        if type(data['next_order']) is not int or data['next_order'] < 1:
            raise ValueError('Invalid map observation sequence')
        for owner, cells in data['cells'].items():
            nation(owner)
            if not isinstance(cells, dict):raise ValueError('Invalid map cells')
            for key, cell in cells.items():
                if not isinstance(cell, dict) or set(cell) != {'position', 'fields'}:raise ValueError('Invalid map cell')
                point = cell['position']
                if (not isinstance(point, list) or len(point) != 2 or
                    any(type(v) is not int or not 0 <= v < size for v in point) or key != f'{point[0]},{point[1]}'):
                    raise ValueError('Invalid map position')
                if not isinstance(cell['fields'], dict):raise ValueError('Invalid map fields')
                for field, record in cell['fields'].items():
                    if field not in ('name', 'terrain', 'owner', 'resources') and not field.startswith('building/'):
                        raise ValueError('Unknown map field')
                    if not isinstance(record, dict) or set(record) != {'value', 'sampled', 'order', 'source', 'acquired'}:
                        raise ValueError('Invalid map provenance')
                    if any(type(record[k]) is not int or record[k] < 0 for k in ('sampled', 'order', 'acquired')):
                        raise ValueError('Invalid map dates')
                    if not 1 <= record['order'] < data['next_order'] or record['sampled'] > record['acquired'] or not isinstance(record['source'], str):
                        raise ValueError('Invalid map provenance')
                    if max_period is not None and record['acquired'] > max_period:
                        raise ValueError('Future map observation')
                    value = record['value']
                    if field in ('name', 'terrain') and not isinstance(value, str):raise ValueError('Invalid map text')
                    if field == 'owner' and value is not None:nation(value)
                    if field == 'resources' and (not isinstance(value, dict) or any(type(v) is not int or v < 0 for v in value.values())):
                        raise ValueError('Invalid map resources')
                    if field.startswith('building/') and (not isinstance(value, dict) or set(value) != {'count', 'construction'} or any(type(v) is not int or v < 0 for v in value.values())):
                        raise ValueError('Invalid map buildings')
        self.cells = deepcopy(data['cells']);self.next_order = data['next_order']
