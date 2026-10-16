"""Synthetic economic-system acceptance; never opens a real game save."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest
import tomllib
from tempfile import TemporaryDirectory

from engine.runtime import Runtime, Unit, ModError
from engine.state import unit_context
from mods.native.economy_reports.mod import EconomyMod
from mods.native.bank.mod import BankMod
from tests.test_building_objects import TeaContent

ROOT = Path(__file__).resolve().parents[1]


class TestEconomicSystem(unittest.TestCase):
    def runtime(self, *, tea=False, bank=True, fixed_market=False, fixed_bank=False, bank_policy=None):
        loaded = Runtime.load(ROOT/'mods/economic_system')
        units = list(loaded.units.values())
        for unit in units:
            if unit.name=='native:economy':
                declaration = deepcopy(unit.mod.declaration);declaration['ReportEvery']=1
                mod=EconomyMod();mod.configure(declaration,unit.mod.declaration_path);unit.mod=mod
            if unit.name=='native:bank' and bank_policy:
                declaration = deepcopy(unit.mod.declaration);declaration['Bank'].update(bank_policy)
                mod=BankMod();mod.configure(declaration,unit.mod.declaration_path);unit.mod=mod
        if fixed_market:
            replacement = Runtime.load(ROOT/'mods/examples/fixed_market_pricing')
            units = [unit for unit in units if unit.name!='native:market-pricing']+list(replacement.units.values())
        if fixed_bank:
            from mods.examples.fixed_credit_bank.mod import FixedCreditBankMod
            path = ROOT/'mods/examples/fixed_credit_bank/mod.toml'
            declaration = tomllib.loads(path.read_text())
            module = FixedCreditBankMod();module.configure(declaration,path)
            meta=declaration['Mod']
            replacement=Unit(meta['Id'],module,tuple(meta['Requires']),tuple(meta['After']))
            units = [unit for unit in units if unit.name!='native:bank']+[replacement]
        distribution = deepcopy(loaded.distribution)
        if not bank:
            units = [unit for unit in units if unit.name!='native:bank']
            distribution['RequiredState'].remove('bank')
            distribution['Hooks'] = [row for row in distribution['Hooks'] if row['Id']!='bank.settlement']
        if tea:units.append(Unit('example:tea-content',TeaContent()))
        return Runtime(units,distribution)

    def world(self, **kwargs):
        r = self.runtime(**kwargs);w=r.world_class()()
        w.resources.open('Qin',{'native:gold':1000,'native:wood':100,'native:ore':20,'native:food':20,'native:oil':20})
        w.resources.open('Chu',{'native:gold':100})
        w.buildings.register_site('example:qin','Qin',(0,0),resources={'native:gold':10,'native:farmland':10})
        return w

    def call(self,w,name,**args):return w.runtime.call(w,'Qin',name,args)
    def row(self,w):return w.economy.turns('Qin')[-1]
    def save(self,w):return json.loads(json.dumps(w.runtime.save_mod_state(w)))

    def test_loading_module_supplies_bank_without_enable_switch(self):
        w=self.world()
        self.assertNotIn('on',w.bank.to_save());self.assertNotIn('enabled',w.bank.to_save())
        self.assertIn('borrow',w.runtime.tools)
        w=self.world(bank=False)
        self.assertNotIn('economy:bank',w.runtime.capabilities)
        self.assertFalse(hasattr(w,'bank'))
        self.assertNotIn('borrow',w.runtime.tools)
        self.assertIsNotNone(self.call(w,'econ'))
        w.resolve_turn();self.assertEqual(self.row(w).liabilities,0)
        self.assertEqual(self.row(w).net_assets,self.row(w).total_assets)

    def test_no_credit_before_production_then_gdp_times_five(self):
        w=self.world();before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,'borrow')
        self.assertEqual(self.save(w),before)
        w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn()
        w.phase='行动'
        loan=self.call(w,'borrow')
        self.assertEqual((loan.principal,loan.due,loan.remaining,loan.taken),(50,50,5,1))
        self.assertEqual(w.resources.balance('Qin','native:gold'),1060)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,'borrow')
        self.assertEqual(self.save(w),before)

    def test_compounding_dynamic_rate_and_forced_negative_cash_repayment(self):
        w=self.world();w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn();loan=w.bank.borrow('Qin')
        w.bank.set_rate(.10)
        due=loan.due
        for period in range(1,6):
            # No income remains in the synthetic bank-only periods.
            w.resources._stock('Qin','native:gold').set_amount(0)
            due=round(due*1.12)
            entries=w.bank.settle(period)
            if period<5:self.assertEqual(w.bank.position('Qin').loan.due,due)
        self.assertEqual(w.resources.balance('Qin','native:gold'),-due)
        self.assertIsNone(w.bank.position('Qin').loan)
        self.assertEqual(w.bank.liabilities(),{})
        self.assertEqual(next(row for row in entries if row.owner=='Qin' and row.kind=='repayment').cash,-due)
        w.bank.settle(6)
        self.assertEqual(w.resources.balance('Qin','native:gold'),-due)
        self.assertEqual(w.economy.turns('Qin')[-1].gdp.total,10)

    def test_rate_clamp_and_truncation_negative_rates(self):
        w=self.world();self.assertEqual(w.bank.set_rate(9),.5)
        self.assertEqual(w.bank.set_rate(-9),-.5)
        w.resources._stock('Qin','native:gold').set_amount(3)
        w.resources._stock('Chu','native:gold').set_amount(-10)
        rows=w.bank.settle(0)
        self.assertEqual(w.resources.balance('Qin','native:gold'),2)
        self.assertEqual(w.resources.balance('Chu','native:gold'),-10)
        self.assertEqual(rows[0].cash,-1)
        before=self.save(w)
        for rate in (True,float('inf'),float('nan')):
            with self.assertRaises(ValueError):w.bank.set_rate(rate)
            self.assertEqual(self.save(w),before)

    def test_bank_income_and_principal_do_not_inflate_gdp(self):
        w=self.world();w.bank.set_rate(.1);w.buildings.seed('example:qin','native:gold-mine')
        first=w.resolve_turn();self.assertEqual(self.row(w).gdp.total,10)
        w.bank.borrow('Qin');w.resolve_turn();row=self.row(w)
        self.assertEqual(row.gdp.total,10);self.assertEqual(row.spending.total,0)
        self.assertEqual(row.liabilities,56)
        self.assertEqual(row.net_assets.total,row.total_assets.total-56)
        self.assertEqual(w.economy.reports('Qin')[-1].net_assets,row.net_assets)
        self.assertTrue(first['bank_entries'])

    def test_fee_is_prechecked_before_loan_disbursement(self):
        w=self.world();w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn()
        w.resources._stock('Qin','native:gold').set_amount(0);before=self.save(w)
        with self.assertRaises(ValueError):w.bank.borrow('Qin',fee=5)
        self.assertEqual(self.save(w),before)
        w.resources.credit('Qin','native:gold',5);w.bank.borrow('Qin',fee=5)
        self.assertEqual(w.resources.balance('Qin','native:gold'),50)

    def test_tea_unlisted_is_ignored_by_market_but_not_hidden_in_credit(self):
        w=self.world(tea=True);w.buildings.seed('example:qin','example:tea-garden')
        w.resolve_turn();w.phase='行动'
        self.assertEqual(w.resources.balance('Qin','example:tea'),2)
        self.assertNotIn('example:tea',dict(self.call(w,'market').prices))
        self.assertIsNotNone(self.call(w,'bank').credit_limit)
        self.assertIsNone(self.call(w,'econ').gross_assets.total)
        before=self.save(w)
        with self.assertRaises(ValueError):self.call(w,'borrow')
        self.assertEqual(self.save(w),before)
        with self.assertRaises(ValueError):self.call(w,'buy',resource='example:tea',quantity=1)
        self.assertEqual(self.save(w),before)

    def test_report_purchase_checks_visibility_payment_and_retains_snapshot(self):
        w=self.world();before=self.save(w)
        with self.assertRaises(ValueError):w.bank.buy_report('Qin','Chu')
        self.assertEqual(self.save(w),before)
        w.resolve_turn();w.phase='行动';purchase=self.call(w,'buy_report',target='Chu')
        self.assertEqual(purchase.report,w.economy.reports('Chu')[-1])
        self.assertEqual(w.resources.balance('Qin','native:gold'),980)
        with self.assertRaises(FrozenInstanceError):purchase.fee=0
        w.resolve_turn();self.assertEqual(purchase.report.end,0)
        self.assertEqual(w.bank.position('Qin').purchases,(purchase,))
        self.assertEqual(w.bank.position('Chu').purchases,())
        with self.assertRaises(ValueError):w.bank.buy_report('Qin','Qin')
        with self.assertRaises(KeyError):w.bank.buy_report('Qin','missing')
        w.resources._stock('Qin','native:gold').set_amount(19);before=self.save(w)
        with self.assertRaises(ValueError):w.bank.buy_report('Qin','Chu')
        self.assertEqual(self.save(w),before)

    def test_repeat_purchases_charge_and_bound_each_owners_history(self):
        w=self.world(bank_policy={'purchase_limit':2});w.resolve_turn()
        for _ in range(3):w.bank.buy_report('Qin','Chu')
        self.assertEqual(w.resources.balance('Qin','native:gold'),940)
        self.assertEqual(len(w.bank.position('Qin').purchases),2)
        self.assertEqual(len([e for e in w.bank.snapshot().entries if e.kind=='report-fee']),3)

    def test_signed_cash_is_narrow_and_atomic(self):
        w=self.world();before=self.save(w)
        for currency,changes in [('native:food',{'Qin':-1}),('native:gold',{'Qin':1,'missing':1}),('native:gold',{'Qin':1,'Chu':float('nan')})]:
            with self.assertRaises((ValueError,KeyError)):w.runtime.object_call(w,'native:bank-cash','adjust',currency,changes)
            self.assertEqual(self.save(w),before)
        with self.assertRaises(ValueError):w.resources.exchange('Qin',{'native:gold':1001},{})
        w.runtime.object_call(w,'native:bank-cash','adjust','native:gold',{'Qin':-1001})
        self.assertEqual(w.resources.balance('Qin','native:gold'),-1)
        with unit_context('native:market'):
            with self.assertRaises(ModError):w.bank.set_rate(.2)
            with self.assertRaises(ModError):w.runtime.object_call(w,'native:bank-cash','adjust','native:gold',{'Qin':100})

    def test_failed_multi_account_settlement_keeps_bank_and_resources(self):
        w=self.world();w.bank.set_rate(.1);before=self.save(w)
        call=w.bank._call
        def fail(cap,method,*args):
            if cap=='native:bank-cash':raise ValueError('synthetic rejection')
            return call(cap,method,*args)
        w.bank._call=fail
        with self.assertRaises(ValueError):w.bank.settle(0)
        self.assertEqual(self.save(w),before)
        w.bank._call=call
        w.bank.settle(0)
        self.assertEqual(w.resources.balance('Qin','native:gold'),1100)
        self.assertEqual(w.resources.balance('Chu','native:gold'),110)

    def test_fresh_world_restore_midloan_and_purchase_then_same_future(self):
        w=self.world();w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn()
        w.bank.borrow('Qin');w.bank.buy_report('Qin','Chu');w.bank.set_rate(.07);w.resolve_turn()
        saved=self.save(w)
        restored=w.runtime.world_class()()
        w.runtime.load_mod_state(restored,saved)
        self.assertEqual(self.save(restored),saved)
        for _ in range(5):w.resolve_turn();restored.resolve_turn()
        self.assertEqual(self.save(restored),self.save(w))
        self.assertIsNone(restored.bank.position('Qin').loan)

    def test_corrupt_bank_records_reject_without_mutating_bank(self):
        w=self.world();w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn();w.bank.borrow('Qin');w.bank.buy_report('Qin','Chu')
        valid=w.bank.to_save()
        candidates=[]
        for field,value in [('remaining',0),('due',-1),('due',51),('remaining',3),('principal',True),('owner','missing')]:
            data=deepcopy(valid);data['loans'][0][field]=value;candidates.append(data)
        data=deepcopy(valid);data['purchases'][0]['report']['gdp']['known']=999;candidates.append(data)
        data=deepcopy(valid);data['rate']=float('nan');candidates.append(data)
        data=deepcopy(valid);data['policy']['loan_term']=10;candidates.append(data)
        for data in candidates:
            with self.subTest(data=data):
                with self.assertRaises(ValueError):w.bank.restore(data)
                self.assertEqual(w.bank.to_save(),valid)

    def test_player_tools_actor_phase_and_private_queries(self):
        w=self.world();self.assertFalse(hasattr(w,'nations'))
        self.assertEqual(w.economy_actors,('Qin','Chu'))
        before=self.save(w);panel=self.call(w,'market')
        self.assertEqual(self.save(w),before)
        trade=self.call(w,'buy',resource='native:wood',quantity=2)
        self.assertEqual(trade.price_before,dict(panel.prices)['native:wood'])
        public=self.call(w,'market');self.assertFalse(hasattr(public,'trades'));self.assertFalse(hasattr(public,'carry'))
        w.resolve_turn()
        for tool,args in [('buy',{'resource':'native:wood','quantity':1}),('borrow',{}),('buy_report',{'target':'Chu'})]:
            with self.assertRaises(ValueError):self.call(w,tool,**args)
        self.assertEqual(len(self.call(w,'report')),1)
        with self.assertRaises(ValueError):w.runtime.call(w,'missing','econ',{})
        self.assertTrue(all(row.owner=='Qin' for row in self.call(w,'report')))

    def test_estimates_follow_registered_recipes_and_explicit_energy_assumption(self):
        w=self.world(tea=True)
        farm=self.call(w,'building_economics',kind='native:farm')
        food_sell=w.market.quote('native:food',1,'sell').exact_total
        buy=lambda r:w.market.quote('native:'+r,1,'buy').exact_total
        self.assertEqual(farm.construction.total,50);self.assertAlmostEqual(farm.net_per_period.total,food_sell)
        self.assertAlmostEqual(farm.payback_periods,50/food_sell)
        factory=self.call(w,'building_economics',kind='native:supply-factory')
        self.assertAlmostEqual(factory.energy_unit_cost,buy('wood')/2)
        self.assertAlmostEqual(factory.net_per_period.total,2*buy('supply')-2*buy('food')-buy('ore')-buy('wood')/2)
        castle=self.call(w,'building_economics',kind='native:castle',level=2)
        self.assertEqual(castle.construction.total,140);self.assertIsNone(castle.payback_periods)
        # 市政厅按调用方给的实际本格座数算，不再凭空"乐观再增"几座。
        hall=self.call(w,'building_economics',kind='native:town-hall',other_slots=6)
        self.assertAlmostEqual(hall.net_per_period.total,14-buy('wood')/2)
        self.assertEqual(hall.projected_slots,9)
        tea=self.call(w,'building_economics',kind='example:tea-garden')
        self.assertEqual(tea.net_per_period.unpriced,('example:tea',));self.assertIsNone(tea.payback_periods)

    def test_academy_public_estimate_does_not_predict_local_savings(self):
        w=self.world()
        for _ in range(6):w.buildings.seed('example:qin','native:farm')
        before=self.save(w)
        e=self.call(w,'building_economics',kind='native:academy')
        # 工程院不产出，也不预测"以后建造能省多少"（那要靠假设未来的建造计划）。
        self.assertFalse(hasattr(e,'academy_saved_per_period'))
        self.assertFalse(hasattr(e,'academy_density_per_period'))
        self.assertGreater(e.net_per_period.total,0)
        self.assertGreater(e.payback_periods,0)
        self.assertIn(('build_discount',25),e.effects)
        self.assertEqual(self.save(w),before)

    def test_reversed_bank_and_reports_is_rejected_by_declared_dependency(self):
        loaded=Runtime.load(ROOT/'mods/economic_system');distribution=deepcopy(loaded.distribution)
        rows=distribution['Hooks'];rows[-1],rows[-2]=rows[-2],rows[-1]
        with self.assertRaises(ModError):Runtime(list(loaded.units.values()),distribution)

    def test_bank_replacement_is_a_module_change(self):
        w=self.world(fixed_bank=True)
        self.assertEqual(w.runtime.capabilities['economy:bank'],'example:fixed-credit-bank')
        self.assertEqual(self.call(w,'borrow').principal,7)
        w.resolve_turn()
        self.assertEqual(self.row(w).gdp.total,0)
        self.assertEqual(self.row(w).liabilities,7)
        saved=self.save(w);restored=w.runtime.world_class()()
        w.runtime.load_mod_state(restored,saved)
        self.assertEqual(self.save(restored),saved)

    def test_credit_policy_validation_and_unrecognized_currency(self):
        for settings in ({'currency':'native:food'},{'currency':'example:missing'},
                         {'loan_term':0},{'initial_rate':True},{'credit_multiple':-1}):
            with self.subTest(settings=settings):
                with self.assertRaises((ValueError,ModError)):self.world(bank_policy=settings)

    def test_negative_loan_rate_reduces_debt_without_cash_income(self):
        w=self.world();w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn();w.bank.borrow('Qin')
        w.bank.set_rate(-.5);w.resources._stock('Qin','native:gold').set_amount(-1)
        w.bank.settle(1)
        self.assertEqual(w.bank.position('Qin').liabilities,26)
        self.assertEqual(w.resources.balance('Qin','native:gold'),-1)
        saved=w.bank.to_save();w.bank.restore(saved);self.assertEqual(w.bank.to_save(),saved)

    def test_integrated_maturity_reports_negative_inventory_assets(self):
        w=self.world();w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn();w.bank.borrow('Qin')
        w.buildings.transfer_site('example:qin','Chu')
        for _ in range(5):
            w.resources._stock('Qin','native:gold').set_amount(0)
            result=w.resolve_turn()
        row=self.row(w)
        self.assertEqual(w.resources.balance('Qin','native:gold'),-55)
        self.assertEqual(row.liabilities,0)
        self.assertEqual(row.gdp.total,0)
        self.assertEqual(row.net_assets,row.total_assets)
        self.assertEqual(next(e.cash for e in result['bank_entries'] if e.kind=='repayment'),-55)

    def test_registered_building_ids_reach_schema_and_unknown_names_reject(self):
        w=self.world(tea=True)
        schema=w.runtime.tools['building_economics'].schema()['function']['parameters']
        self.assertIn('example:tea-garden',schema['properties']['kind']['enum'])
        before=self.save(w)
        with self.assertRaisesRegex(ValueError,'未知内容键值'):self.call(w,'building_economics',kind='农场')
        self.assertEqual(self.save(w),before)
        self.assertEqual(len(w.runtime.schemas()),12)

    def test_replacement_modules_load_from_real_distribution_members(self):
        source=(ROOT/'mods/economic_system/mod.toml').read_text()
        for member in tomllib.loads(source)['Distribution']['Members']:
            resolved=(ROOT/'mods/economic_system'/member).resolve()
            if resolved==ROOT/'mods/native/bank/mod.toml':resolved=ROOT/'mods/examples/fixed_credit_bank/mod.toml'
            if resolved==ROOT/'mods/native/market_pricing/mod.toml':resolved=ROOT/'mods/examples/fixed_market_pricing/mod.toml'
            source=source.replace('"'+member+'"','"'+str(resolved)+'"')
        with TemporaryDirectory() as directory:
            path=Path(directory)/'mod.toml';path.write_text(source)
            runtime=Runtime.load(directory);world=runtime.world_class()()
            world.resources.open('Qin',{'native:gold':100})
            self.assertEqual(runtime.call(world,'Qin','borrow',{}).principal,7)
            trade=runtime.call(world,'Qin','buy',{'resource':'native:wood','quantity':3})
            self.assertEqual(trade.gold,6)
            world.resolve_turn()
            self.assertEqual(world.economy.turns('Qin')[-1].imports,6)
            self.assertEqual(world.economy.turns('Qin')[-1].liabilities,7)

    def test_market_pricing_replacement_needs_no_query_or_bank_changes(self):
        w=self.world(fixed_market=True)
        trade=self.call(w,'buy',resource='native:wood',quantity=3)
        self.assertEqual(trade.gold,6)
        w.buildings.seed('example:qin','native:gold-mine');w.resolve_turn()
        self.assertEqual(w.bank.borrow('Qin').principal,50)
        self.assertEqual(self.row(w).imports,6)


if __name__=='__main__':unittest.main()
