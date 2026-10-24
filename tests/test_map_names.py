"""地名表：地图的属性，全图唯一且只由 (seed, size) 决定。"""
import json
import unittest
from unittest.mock import patch

from host import GameHost
from mods.native.map_generation.algorithm import _hash_int
from mods.native.map_generation.names import NAME_PREFIX, NAME_SUFFIX, name_grid


class TestMapNames(unittest.TestCase):
    def test_entire_name_pool_is_used_before_numbering(self):
        pool = {prefix + suffix for prefix in NAME_PREFIX for suffix in NAME_SUFFIX}
        size = 90  # 8100格，跨过8036名字的完整一轮，验证真正耗尽才启用编号。
        grid = name_grid(20261016, size, _hash_int)
        names = [name for row in grid for name in row]
        self.assertEqual(set(names[:len(pool)]), pool)
        self.assertEqual(len(set(names)), size * size)
        self.assertEqual(names[len(pool):], [name + '2' for name in names[:size * size-len(pool)]])
        self.assertEqual(grid, name_grid(20261016, size, _hash_int))
        self.assertNotEqual(grid, name_grid(20261017, size, _hash_int))

    def test_normal_map_has_no_numbered_names(self):
        for seed in range(20261000, 20261040):
            with self.subTest(seed=seed):
                names = [name for row in name_grid(seed, 24, _hash_int) for name in row]
                self.assertEqual(len(set(names)), 576)
                self.assertTrue(all(not name[-1].isdigit() for name in names))

    def test_hash_ties_have_a_stable_order(self):
        # 刻意令哈希全部相同，仍须唯一、稳定；不依赖集合的迭代顺序。
        with patch('mods.native.map_generation.names.NAME_PREFIX', ['南', '北']), \
             patch('mods.native.map_generation.names.NAME_SUFFIX', ['川', '山']):
            self.assertEqual(name_grid(73, 2, lambda *args: 0),
                             [['北山', '北川'], ['南山', '南川']])

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
        self.assertEqual(len(set(n for n in names.values() if n)), sum(bool(n) for n in names.values()))
        self.assertTrue(all(isinstance(name,str) for name in names.values()))
        self.assertIn('',names.values())
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

    def test_every_coordinate_tool_takes_a_name_or_two_integers(self):
        host = self.host()
        player = host.player('scenario:qin')
        land = player.query('land')
        tile = land[0]['tile'];x,y = tile['position']
        # 查询：三种写法指同一格。
        for args in ({'place': tile['name']}, {'place': f'{x} {y}'}, {'x': x, 'y': y}):
            row = player.query('tile', args)
            self.assertEqual((row['position'], row['name']), ([x, y], tile['name']))
        both = player.submit('tile', {'place': tile['name'], 'x': x, 'y': y})
        self.assertFalse(both['ok']);self.assertIn('只能选一种', both['message'])
        unknown = player.submit('tile', {'place': '不存在的地名'})
        self.assertFalse(unknown['ok']);self.assertIn('没有这个地名', unknown['message'])
        # 动工：三条路等价（每格每回合一次，所以各用一块地）。
        refs = [{'site': land[0]['tile']['name']},
                {'site': f"{land[1]['tile']['position'][0]} {land[1]['tile']['position'][1]}"},
                {'x': land[2]['tile']['position'][0], 'y': land[2]['tile']['position'][1]}]
        for ref in refs:
            receipt = player.submit('build', {'kind': 'native:castle', **ref})
            self.assertTrue(receipt['ok'], receipt['message'])
        # 移动：地名与两个整数给出同一份回执。
        target = land[1]['tile']
        by_name, by_ints = self.host(), self.host()
        for world, args in ((by_name, {'place': target['name']}),
                            (by_ints, {'x': target['position'][0], 'y': target['position'][1]})):
            world.world.military.seed('scenario:qin', 'native:infantry', x, y)
            army = [u for u in world.player('scenario:qin').query('army') if u['owner'] == 'scenario:qin']
            receipt = world.player('scenario:qin').submit('military', {'action':'move','id': army[0]['id'], **args})
            self.assertTrue(receipt['ok'], receipt['message'])
            self.assertEqual(receipt['data']['units'], [army[0]['id']])
            moved = [u for u in world.player('scenario:qin').query('army') if u['owner'] == 'scenario:qin'][0]
            self.assertEqual((moved['x'], moved['y']), tuple(target['position']))

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
