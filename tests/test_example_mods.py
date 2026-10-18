"""示例模块与《Mod 开发指南》里各条配方的验收。

全部使用合成配置与临时存档，不读写真实对局存档。
"""
from copy import deepcopy
import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from engine.runtime import ModError, Runtime, Unit
from host import GameHost
from mods.examples.hello.mod import HelloMod

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'mods' / 'examples'


def config(dist, nations):
    return {'mods': str(dist), 'map_size': 12, 'seed': 7, 'nations': nations}


def stocks(player):
    return {row['id']: row['amount'] for row in player.query('econ')['stocks']}


class TestMinimalMod(unittest.TestCase):
    """配方一：最小 Mod。"""

    def test_runs_in_process_and_from_a_module_directory(self):
        runtime = Runtime([Unit('example:hello', HelloMod(), version='1.0.0')])
        world = runtime.world_class()()
        self.assertEqual(runtime.call(world, 'demo', 'hello_add', {'amount': 3}),
                         {'actor': 'demo', 'count': 3})
        # 参数按类型标注转换，别名等价于本名。
        self.assertEqual(runtime.call(world, 'demo', 'hello_add', {'amount': '4'})['count'], 7)
        self.assertEqual(runtime.call(world, 'demo', '打招呼', {'amount': 2})['count'], 9)
        self.assertEqual(runtime.settle(world), {'hello_total': 9})
        self.assertEqual(runtime.save_mod_state(world)['example:hello'], {'demo': 9})

        # 目录里只有模块声明、没有发行版时，也能装载并运行。
        directory = Runtime.load(EXAMPLES / 'hello')
        self.assertEqual(directory.order, ['example:hello'])
        self.assertEqual(directory.hooks, ())
        self.assertEqual(directory.stage_order, ['example:hello.settle'])
        self.assertEqual(directory.world_class()().hello, {})

    def test_rejects_invalid_argument(self):
        runtime = Runtime([Unit('example:hello', HelloMod(), version='1.0.0')])
        world = runtime.world_class()()
        with self.assertRaisesRegex(ValueError, '正整数'):
            runtime.call(world, 'demo', 'hello_add', {'amount': 0})


class TestMinimalPlayableGame(unittest.TestCase):
    """配方二：最小可玩游戏。"""

    def test_fifteen_modules_play_and_round_trip(self):
        entry = ROOT / 'mods.playable.toml'
        runtime = Runtime.load(entry)
        self.assertEqual(len(runtime.order), 15)
        self.assertEqual(runtime.order.count('native:economy-queries'), 0)
        cfg = config(entry, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'},
                             {'id': 'scenario:chu', 'name': '楚', 'client': 'llm'}])
        host = GameHost.new(cfg)
        # PlayerState 里每一项都必须存在且只读；不存在的会在这里就报错。
        facts = host.player('scenario:qin').observe()['facts']
        self.assertEqual(sorted(facts), ['army', 'building_sites', 'dispatches', 'foreign',
                                         'government', 'intel', 'land', 'news', 'view'])
        host.cycle(); host.cycle()
        self.assertEqual(host.world.government.period(), 2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'session.json'
            host.save(path)
            self.assertEqual(GameHost.load(path, cfg).to_save(), host.to_save())

    def test_distribution_directory_loads_the_same_way(self):
        # 发行版清单在目录里，cfg['mods'] 指向目录也能装载。
        self.assertEqual(len(Runtime.load(EXAMPLES / 'playable_min').order), 15)


class TestNewGood(unittest.TestCase):
    """配方三：添加一种资源并上市。"""

    def test_registered_resource_trades_on_the_market(self):
        dist = EXAMPLES / 'tea_dist'
        runtime = Runtime.load(dist)
        self.assertEqual(type(runtime.units['native:market'].mod).__name__, 'TeaMarketMod')
        cfg = config(dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm',
                             'resources': {'example:tea': 20}}])
        host = GameHost.new(cfg)
        player = host.player('scenario:qin')
        before = stocks(player)
        self.assertEqual(before['example:tea'], 20)
        panel = player.query('market')
        receipt = player.submit('economy', {'action': 'sell', 'resource': 'example:tea', 'quantity': 5})
        self.assertEqual(receipt['ok'], True)
        after = stocks(player)
        self.assertEqual(before['example:tea'] - after['example:tea'], 5)
        self.assertEqual(after['native:gold'] - before['native:gold'], receipt['data']['gold'])
        self.assertAlmostEqual(dict(panel['prices'])['example:tea'], 3)
        self.assertEqual(dict(panel['inventory'])['example:tea'], 100)

    def test_registered_but_unlisted_resource_is_rejected_by_the_market(self):
        """注册资源不等于上市：市场只认自己那份声明。"""
        body = (EXAMPLES / 'tea_dist' / 'mod.toml').read_text(encoding='utf-8')
        declaration = tomllib.loads(body)
        members = [(EXAMPLES / 'tea_dist' / row).resolve()
                   for row in declaration['Distribution']['Members']]
        # 去掉多上市了茶的那份市场声明，换回 native 自己的那一份。
        members = [row for row in members if row.parent.name != 'tea' or row.name != 'market.toml']
        members.append((ROOT / 'mods' / 'native' / 'market' / 'mod.toml').resolve())
        span = body[body.index('Members = ['):body.index(']\nRequiredState') + 1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'mod.toml'
            path.write_text(body.replace(span, 'Members = '
                                         + json.dumps([row.as_posix() for row in members])), encoding='utf-8')
            runtime = Runtime.load(Path(tmp))
            cfg = config(Path(tmp), [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm',
                                      'resources': {'example:tea': 20}}])
            host = GameHost.new(cfg, runtime=runtime)
            player = host.player('scenario:qin')
            # 账户里有茶（资源域枚举注册表），但市场不认它。
            self.assertEqual(stocks(player)['example:tea'], 20)
            receipt = player.submit('economy', {'action': 'sell', 'resource': 'example:tea', 'quantity': 1})
            self.assertFalse(receipt['ok']);self.assertIn('not listed', receipt['message'])


class TestStagesAndUnits(unittest.TestCase):
    """配方四、五：插入新阶段；添加兵种；给该兵种添加结算阶段。"""

    def setUp(self):
        self.dist = EXAMPLES / 'recipes_dist'
        self.runtime = Runtime.load(self.dist)

    def test_new_hook_and_stage_land_in_the_declared_position(self):
        self.assertIn('example.harvest', self.runtime.hooks)
        harvest = self.runtime.stage_order.index('example:harvest.reap')
        self.assertEqual(self.runtime.stage_order[harvest - 1], 'native:buildings.produce')
        upkeep = self.runtime.stage_order.index('example:elephants.upkeep')
        self.assertEqual(self.runtime.stage_order[upkeep - 1], 'native:army.supply')
        self.assertEqual(self.runtime.stage_order[upkeep + 1], 'native:army.battle')

    def test_registered_unit_reaches_the_catalog_and_the_recruit_schema(self):
        host = GameHost.new(config(self.dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}]))
        player = host.player('scenario:qin')
        self.assertIn('example:war-elephant', str(player.query('unit_catalog')))
        military = next(t for t in player.actions() if t['function']['name'] == 'military')
        self.assertIn('war-elephant',
                      military['function']['parameters']['properties']['kind']['enum'])

    def test_unit_specific_stage_charges_exactly_its_own_declared_upkeep(self):
        host = GameHost.new(config(self.dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}]))
        world, player = host.world, host.player('scenario:qin')
        site = player.query('building_sites')['sites'][0]['id']
        x, y = (int(v) for v in site.split('land-')[1].split('-'))
        self.runtime.object_call(world, 'native:army', 'seed', 'scenario:qin',
                                 'example:war-elephant', x, y, 2)
        panel = player.query('elephant_heads')
        self.assertEqual(panel['heads'], 2)
        before = stocks(player)
        context = world.resolve_turn()
        upkeep = context['elephant_upkeep']
        self.assertEqual(upkeep['heads'], {'scenario:qin': 2})
        self.assertEqual(upkeep['fed'], {'scenario:qin': panel['total_food']})
        self.assertEqual(upkeep['starved'], {})
        # 秋收发的粮食与草料扣的粮食，正好解释本期粮食的变化。
        self.assertEqual(context['harvest']['yield'], 4)
        after = stocks(player)
        self.assertEqual(after['native:food'] - before['native:food'],
                         context['harvest']['yield'] - panel['total_food'])

    def test_starved_nation_is_recorded_without_touching_its_stock(self):
        host = GameHost.new(config(self.dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}]))
        world, player = host.world, host.player('scenario:qin')
        site = player.query('building_sites')['sites'][0]['id']
        x, y = (int(v) for v in site.split('land-')[1].split('-'))
        self.runtime.object_call(world, 'native:army', 'seed', 'scenario:qin',
                                 'example:war-elephant', x, y, 20)   # 草料远超库存
        before = stocks(player)['native:food']
        context = world.resolve_turn()
        self.assertEqual(context['elephant_upkeep']['starved'], {'scenario:qin': 40})
        self.assertEqual(context['elephant_upkeep']['fed'], {})
        # 付不出就不扣：食物只多了秋收那一笔。
        self.assertEqual(stocks(player)['native:food'],
                         before + context['harvest']['yield'])


class TestReplaceExistingContent(unittest.TestCase):
    """配方六：替换已有内容 —— 去掉电力系统。"""

    def test_power_plants_disappear_and_the_grid_stops_gating_recruitment(self):
        def verdict(dist):
            runtime = Runtime.load(dist)
            cfg = config(dist, [{'id': 'scenario:qin', 'name': '秦', 'client': 'llm'}])
            host = GameHost.new(cfg)
            player = host.player('scenario:qin')
            host.cycle()                       # 电网状态在结算的补给阶段记录
            kinds = [row['id'] for row in player.query('building_catalog')]
            site = player.query('building_sites')['sites'][0]['id']
            # 这座门控测的是电网：先给该格补一座已落成兵营，否则先撞上"该地没有兵营"。
            host.world.buildings.seed(site, 'native:barracks')
            try:
                receipt=player.submit('military', {'action': 'recruit', 'site': site, 'kind': 'native:infantry'})
                if not receipt['ok']:raise ValueError(receipt['message'])
                return kinds, None
            except ValueError as exc:
                return kinds, str(exc)

        native_kinds, native_error = verdict(EXAMPLES / 'recipes_dist')
        plain_kinds, plain_error = verdict(EXAMPLES / 'no_power_dist')
        self.assertIn('native:wood-power-plant', native_kinds)
        self.assertIn('电网不足', native_error)
        self.assertNotIn('native:wood-power-plant', plain_kinds)
        self.assertTrue(plain_error is None or 'energy' not in plain_error)


class TestMinigame(unittest.TestCase):
    """从零写的最小可玩游戏：9 个模块、3 种资源、1 种兵、外交只有宣战与议和。"""

    entry = ROOT / 'mods.minigame.toml'

    def host(self, nations):
        cfg = {'mods': str(self.entry), 'map_size': 12, 'seed': 3, 'nations': nations}
        return GameHost.new(cfg), cfg

    def test_declares_exactly_the_intended_content(self):
        runtime = Runtime.load(self.entry)
        self.assertEqual(len(runtime.order), 9)
        self.assertEqual(set(runtime.hooks), {'cycle.start', 'cycle.end'})
        host, _ = self.host([{'id': 'scenario:qin', 'name': '秦', 'client': 'mini'}])
        world = host.world
        # 三种资源、一种地形、开局只有一座市政厅、没有部队。
        self.assertEqual(set(world.accounts.ids()), {'mini:gold', 'mini:food', 'mini:wood'})
        self.assertEqual({cell.terrain for cell in world.territory.cells()}, {'mini:plain'})
        capital = next(cell for cell in world.territory.cells()
                       if world.buildings.at(*cell.position).get('mini:town-hall'))
        self.assertEqual(world.buildings.at(*capital.position), {'mini:town-hall': 1})
        self.assertEqual(world.territory.owner(*capital.position), 'scenario:qin')
        self.assertEqual(world.military.snapshot(), ())

    def test_plays_several_periods_and_round_trips(self):
        host, cfg = self.host([{'id': 'scenario:qin', 'name': '秦', 'client': 'mini'},
                               {'id': 'scenario:chu', 'name': '楚', 'client': 'mini'}])
        for _ in range(10):
            host.cycle()
        self.assertEqual(host.world.government.period(), 10)
        player = host.player('scenario:qin')
        built = {kind for cell in player.query('land') if cell['owner'] == 'scenario:qin'
                 for kind in cell['buildings']}
        self.assertIn('mini:barracks', built)
        self.assertIn('mini:farm', built)
        self.assertTrue(player.query('army'))                 # 客户端已经征到兵
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'minigame.json'
            host.save(path)
            self.assertEqual(GameHost.load(path, cfg).to_save(), host.to_save())

    def test_war_is_required_before_attacking_and_capture_transfers_land(self):
        host, _ = self.host([{'id': 'scenario:qin', 'name': '秦', 'client': 'mini', 'position': [2, 5]},
                             {'id': 'scenario:chu', 'name': '楚', 'client': 'mini', 'position': [5, 5]}])
        world = host.world
        player = host.player('scenario:qin')
        world.runtime.object_call(world, 'mini:army', 'seed', 'scenario:qin',
                                  'mini:infantry', 3, 5, 1)
        unit = player.query('army')[0]
        before = len([c for c in player.query('land') if c['owner'] == 'scenario:qin'])
        receipt = player.submit('attack', {'unit': unit['id'], 'x': 4, 'y': 5})
        self.assertFalse(receipt['ok'])
        self.assertIn('并未交战', receipt['message'])
        self.assertTrue(player.submit('declare_war', {'target': 'scenario:chu'})['ok'])
        self.assertTrue(player.submit('attack', {'unit': unit['id'], 'x': 4, 'y': 5})['ok'])
        after = len([c for c in player.query('land') if c['owner'] == 'scenario:qin'])
        self.assertEqual(after, before + 1)
        self.assertEqual(player.query('diplomacy')['wars'], [['scenario:chu', 'scenario:qin']])
        self.assertTrue(player.submit('make_peace', {'target': 'scenario:chu'})['ok'])
        self.assertEqual(player.query('diplomacy')['wars'], [])

    def test_own_ids_arrival_and_failed_client_assembly_are_transactional(self):
        host,cfg=self.host([{'id':'mini:alpha','name':'甲','client':'mini'},
                           {'id':'mini:beta','name':'乙','client':'mini','appearance':'manual'},
                           {'id':'mini:broken','name':'丙','client':'mini','appearance':'manual'}])
        invalid=deepcopy(cfg);invalid['nations'][-1]['client']='missing'
        with self.assertRaises(ValueError):GameHost.prepare(invalid)
        host.cfg['nations'][-1]['client']='missing'
        player=host.player('mini:alpha');before=host.to_save()
        with self.assertRaises(ValueError):host.add('丙')
        self.assertEqual(host.to_save(),before)
        self.assertEqual(host.add('乙'),'mini:beta')
        self.assertEqual(player.query('land'),host.player('mini:alpha').query('land'))
        self.assertEqual(set(host.world.accounts.ids()),{'mini:gold','mini:food','mini:wood'})
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'mini-arrival.json';host.save(path)
            loaded=GameHost.load(path,cfg)
            self.assertEqual(loaded.to_save(),host.to_save())
            host.cycle();loaded.cycle()
            self.assertEqual(loaded.to_save(),host.to_save())

    def test_losing_every_town_hall_extinguishes_the_nation(self):
        host, _ = self.host([{'id': 'scenario:qin', 'name': '秦', 'client': 'mini'},
                             {'id': 'scenario:chu', 'name': '楚', 'client': 'mini'}])
        world = host.world
        capital = next(cell for cell in world.territory.cells()
                       if cell.owner == 'scenario:chu'
                       and world.buildings.at(*cell.position).get('mini:town-hall'))
        world.runtime.object_call(world, 'mini:territory', 'transfer',
                                  *capital.position, 'scenario:qin')
        self.assertEqual(world.nations.ids(), ('scenario:qin', 'scenario:chu'))
        context = world.resolve_turn()
        self.assertEqual(context['extinct'], ['scenario:chu'])
        self.assertEqual(world.nations.ids(), ('scenario:qin',))
        self.assertTrue(any('失去' in row['text'] for row in world.intelligence.events()))


class TestAssemblyErrors(unittest.TestCase):
    """配方七：装配期的三类报错。"""

    def _load_without(self, drop):
        body = (EXAMPLES / 'recipes_dist' / 'mod.toml').read_text(encoding='utf-8')
        declaration = tomllib.loads(body)
        members = [(EXAMPLES / 'recipes_dist' / row).resolve()
                   for row in declaration['Distribution']['Members']]
        kept = [row for row in members if row.parent.name != drop]
        span = body[body.index('Members = ['):body.index(']\nRequiredState') + 1]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'mod.toml').write_text(
                body.replace(span, 'Members = ' + json.dumps([row.as_posix() for row in kept])), encoding='utf-8')
            Runtime.load(Path(tmp))

    def test_missing_state_owner(self):
        with self.assertRaisesRegex(ModError, 'state 无主：resources'):
            self._load_without('resources')

    def test_missing_dependency_named_by_another_module(self):
        with self.assertRaisesRegex(ModError, '缺少依赖'):
            self._load_without('map_generation')

    def test_capability_without_provider(self):
        with self.assertRaisesRegex(ModError, '所需能力无提供者：market:pricing'):
            self._load_without('market_pricing')


if __name__ == '__main__':
    unittest.main()
