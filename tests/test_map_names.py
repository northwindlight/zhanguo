"""地名表：地图的属性，全图唯一且只由 (seed, size) 决定。"""
import json
import unittest

from host import GameHost


class TestMapNames(unittest.TestCase):
    def host(self, seed=73, size=12):
        return GameHost.new({'map_size': size, 'seed': seed,
                             'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}]})

    def grid(self, seed=73, size=12):
        host = self.host(seed, size)
        view = host.player('scenario:qin').query('view')
        return {(tuple(row['position'])): row['name'] for row in view}

    def test_names_are_unique_complete_and_derived_from_the_map(self):
        names = self.grid()
        self.assertTrue(names)
        self.assertEqual(len(set(names.values())), len(names))
        self.assertTrue(all(isinstance(name, str) and name for name in names.values()))
        # Same map, same names; another seed is another map.
        self.assertEqual(self.grid(), names)
        self.assertNotEqual(self.grid(seed=74), names)

    def test_every_visible_surface_carries_the_name(self):
        host = self.host()
        player = host.player('scenario:qin')
        view = {tuple(row['position']): row['name'] for row in player.query('view')}
        land = {tuple(row['tile']['position']): row['tile']['name'] for row in player.query('land')}
        for position, name in land.items():
            self.assertEqual(view[position], name)
        x, y = next(iter(land))
        tile = player.query('tile', {'x': x, 'y': y})
        self.assertEqual(tile['name'], land[(x, y)])
        # 观察者看全图：扫描序首格是 (0,0)，全图名字也不重复。
        rows = host.session.observer_snapshot()['map']['facts']['view']
        self.assertEqual(rows[0]['position'], [0, 0])
        self.assertTrue(rows[0]['name'])
        self.assertEqual(len({row['name'] for row in rows}), len(rows))

    def test_names_survive_save_and_reload(self):
        host = self.host()
        before = {tuple(row['tile']['position']): row['tile']['name'] for row in host.player('scenario:qin').query('land')}
        state = host.to_save()
        # 地名不进存档：它是地图的属性，读档时从地图取回同一个名字。
        self.assertNotIn('name', json.dumps(state['modules']['native:territory'], ensure_ascii=False))
        reloaded = GameHost.new({'map_size': 12, 'seed': 73, 'nations': [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}]})
        reloaded.session.restore(state)
        after = {tuple(row['tile']['position']): row['tile']['name']
                 for row in reloaded.player('scenario:qin').query('land')}
        self.assertEqual(after, before)


if __name__ == '__main__':unittest.main()
