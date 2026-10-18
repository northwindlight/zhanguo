"""Formal distribution acceptance via mods.toml and real declared members."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import tomllib
import unittest

from engine.runtime import Runtime, ModError

ROOT = Path(__file__).resolve().parents[1]


class TestNativeDistribution(unittest.TestCase):
    def world(self, runtime=None):
        runtime = runtime or Runtime.load(ROOT/'mods.toml')
        world = runtime.world_class()()
        world.nations.create('native:qin','秦',{'native:gold':1000,'native:wood':100})
        world.nations.create('native:chu','楚',{'native:gold':100})
        world.buildings.register_site('native:qin-capital','native:qin',(0,0),resources={'native:gold':10,'native:farmland':10})
        world.buildings.register_site('native:chu-capital','native:chu',(1,0),resources={'native:farmland':10})
        world.buildings.seed('native:qin-capital','native:gold-mine')
        return world

    def call(self, world, name, **args):return world.call_tool('native:qin',name,args)
    def save(self, world):
        return json.loads(json.dumps({'dependencies':world.runtime.dependency_manifest(),
                                      'state':world.runtime.save_mod_state(world)},allow_nan=False))

    def test_official_loader_assembles_only_converted_domains(self):
        runtime=Runtime.load(ROOT/'mods.toml');direct=Runtime.load(ROOT/'mods/native')
        self.assertEqual(runtime.dependency_manifest(),direct.dependency_manifest())
        self.assertEqual(runtime.distribution['Id'],'native')
        self.assertEqual(runtime.distribution['Version'],'4.12.0')
        self.assertEqual(set(runtime.units),{'ui:standard','native:resources','native:buildings','native:market',
                         'native:market-pricing','native:economy','native:economy-valuation',
                         'native:bank','native:economy-queries','native:nations','native:government','native:map-generation',
                         'native:territory','native:diplomacy','native:vision','native:army','native:succession','native:intelligence','native:query','native:manuals','native:huns','scenario:eight-nations'})
        self.assertEqual(set(runtime.state_owners),{'resources','buildings','market','economy','bank','phase','polity','nations','government','territory','diplomacy','units','military','succession','intelligence'})
        self.assertEqual(runtime.hooks,('cycle.start','buildings.production','relations','military.supply',
            'military.battle','military.withdraw','military.claims','succession','military.repatriate',
            'market.settlement','buildings.completion','bank.settlement','economy.reports','cycle.end'))
        self.assertEqual(len(runtime.stage_order),18)
        self.assertTrue({'build','market','econ','bank','foreign','plan','end_turn',
            'land','view','army','news','intel','query','military','economy','relations',
            'intelligence'} <= set(runtime.tools))
        self.assertTrue({'buy','sell','borrow','recruit','attack','retreat','declare_war',
            'offer_peace','vote','send_letter','gift','spy'} <= set(runtime.actions))
        self.assertEqual(len(runtime.schemas()),8)
        self.assertEqual({s['function']['name'] for s in runtime.schemas()},
                         {'build','economy','end_turn','intelligence','military','plan','query','relations'})
        world=runtime.world_class()()
        self.assertEqual(world.economy_actors,())
        self.assertEqual(world.nation_actors,())

    def test_tool_build_trade_produce_complete_and_report_share_one_inventory(self):
        world=self.world()
        panel=self.call(world,'market')
        trade=self.call(world,'buy',resource='native:wood',quantity=3)
        # 期内报价对任何国家一样；只有这一单自己的深度带来溢价。
        self.assertEqual(trade.price_before,dict(panel.prices)['native:wood'])
        self.assertGreater(trade.exact_total,dict(panel.prices)['native:wood']*3)
        receipt=self.call(world,'build',site='native:qin-capital',kind='native:farm')
        self.assertEqual(dict(receipt.costs),{'native:gold':20,'native:wood':15})
        self.assertEqual(world.resources.balance('native:qin','native:wood'),88)
        result=world.resolve_turn();turn=world.economy.turns('native:qin')[-1]
        self.assertEqual(world.resources.balance('native:qin','native:gold'),1000-trade.gold-20+10)
        self.assertEqual(world.resources.balance('native:qin','native:food'),0)
        self.assertEqual(turn.gdp.total,10)
        self.assertEqual(turn.imports,trade.gold)
        self.assertEqual(world.buildings.count('native:qin-capital','native:farm'),1)
        self.assertEqual(tuple(row.id for row in result['building_completions']),(receipt.id,))
        world.resolve_turn();turn=world.economy.turns('native:qin')[-1]
        self.assertEqual(world.resources.balance('native:qin','native:food'),1)
        self.assertAlmostEqual(turn.gdp.total,10+dict(world.market.snapshot().prices)['native:food'])
        self.assertEqual((world.buildings.period,world.market.period,world.bank.next_period,world.economy.next_period),(2,2,2,2))
        self.assertEqual(self.call(world,'econ').latest_turn,turn)

    def test_construction_owner_ids_and_phase_are_enforced_without_partial_changes(self):
        world=self.world();before=self.save(world)
        for args in ({'site':'native:chu-capital','kind':'native:farm'},
                     {'site':'native:qin-capital','kind':'农场'},
                     {'site':'native:missing','kind':'native:farm'}):
            with self.assertRaises((ValueError,KeyError)):self.call(world,'build',**args)
            self.assertEqual(self.save(world),before)
        world.resolve_turn();before=self.save(world)
        with self.assertRaises(ValueError):self.call(world,'build',site='native:qin-capital',kind='native:farm')
        self.assertEqual(self.save(world),before)
        self.assertIsNotNone(self.call(world,'building_economics',kind='native:farm'))

    def test_default_publication_and_bank_tools_work_through_official_entry(self):
        world=self.world()
        for _ in range(9):world.resolve_turn()
        self.assertEqual(self.call(world,'report'),())
        world.resolve_turn();reports=self.call(world,'report')
        self.assertEqual((reports[0].start,reports[0].end,reports[0].span),(0,9,10))
        world.phase='行动';cash=world.resources.balance('native:qin','native:gold')
        loan=self.call(world,'borrow');purchase=self.call(world,'buy_report',target='native:chu')
        self.assertEqual(loan.principal,50)
        self.assertEqual(world.resources.balance('native:qin','native:gold'),cash+50-20-10)
        self.assertEqual(purchase.report.owner,'native:chu')
        self.assertEqual(self.call(world,'bank').purchases,(purchase,))
        world.resolve_turn();row=world.economy.turns('native:qin')[-1]
        self.assertEqual(row.gdp.total,10)
        self.assertEqual(row.liabilities,51)
        self.assertEqual(row.net_assets.total,row.total_assets.total-51)

    def test_dependency_checked_json_roundtrip_resumes_all_domains(self):
        world=self.world();world.resolve_turn();world.phase='行动'
        self.call(world,'borrow');self.call(world,'build',site='native:qin-capital',kind='native:farm')
        bundle=self.save(world)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic-native.json';path.write_text(json.dumps(bundle), encoding='utf-8')
            loaded=json.loads(path.read_text(encoding='utf-8'))
            runtime=Runtime.load(ROOT/'mods.toml')
            runtime.check_save_dependencies(loaded['dependencies'])
            restored=runtime.world_class()();runtime.load_mod_state(restored,loaded['state'])
        self.assertEqual(self.save(restored),bundle)
        for _ in range(10):world.resolve_turn();restored.resolve_turn()
        self.assertEqual(self.save(restored),self.save(world))
        self.assertIsNone(self.call(restored,'bank').loan)
        self.assertEqual(len(self.call(restored,'report')),1)
        bad=deepcopy(bundle['dependencies']);bad['distribution']['Version']='changed'
        with self.assertRaises(ModError):runtime.check_save_dependencies(bad)

    def manifest(self, *, pricing=None, bank=None, tea=False):
        source=(ROOT/'mods/native/mod.toml').read_text(encoding='utf-8')
        for member in tomllib.loads(source)['Distribution']['Members']:
            resolved=ROOT/'mods/native'/member
            if pricing and member=='market_pricing/mod.toml':resolved=ROOT/pricing
            if bank and member=='bank/mod.toml':resolved=ROOT/bank
            source=source.replace('"'+member+'"','"'+resolved.as_posix()+'"')
        if tea:
            source=source.replace('Members = [','Members = ["tea/mod.toml", ',1)
        return source

    def test_formal_distribution_supports_replacement_module_members(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'mod.toml').write_text(self.manifest(pricing='mods/examples/fixed_market_pricing/mod.toml',
                                                     bank='mods/examples/fixed_credit_bank/mod.toml'), encoding='utf-8')
            runtime=Runtime.load(path);world=self.world(runtime)
            self.assertEqual(self.call(world,'borrow').principal,7)
            trade=self.call(world,'buy',resource='native:wood',quantity=3)
            self.assertEqual(trade.gold,6)
            world.resolve_turn()
            self.assertEqual(world.economy.turns('native:qin')[-1].liabilities,7)
            self.assertEqual(world.economy.turns('native:qin')[-1].imports,6)

    def test_formal_distribution_registers_tea_and_build_tool_without_listing_it(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)
            (path/'mod.toml').write_text(self.manifest(tea=True), encoding='utf-8')
            (path/'tea').mkdir()
            (path/'tea/mod.toml').write_text('[Mod]\nId="example:tea-content"\nVersion="1"\n'
                                       'Entry="tests.test_building_objects:TeaContent"\n', encoding='utf-8')
            runtime=Runtime.load(path);world=self.world(runtime)
            self.call(world,'build',site='native:qin-capital',kind='example:tea-garden')
            world.resolve_turn();world.resolve_turn()
            self.assertEqual(world.resources.balance('native:qin','example:tea'),2)
            self.assertNotIn('example:tea',dict(self.call(world,'market').prices))
            self.assertEqual(world.economy.turns('native:qin')[-1].gdp.unpriced,('example:tea',))
            self.assertIsNotNone(self.call(world,'bank').credit_limit)
            schema=runtime.tools['build'].schema()['function']['parameters']
            self.assertIn('example:tea-garden',schema['properties']['kind']['enum'])
            world.phase='行动';before=self.save(world)
            with self.assertRaises(ValueError):self.call(world,'buy',resource='example:tea',quantity=1)
            self.assertEqual(self.save(world),before)


if __name__=='__main__':unittest.main()
