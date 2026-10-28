"""Feature switches must remove the actual modules, tools and service bindings."""
from copy import deepcopy
from itertools import product
import unittest
from unittest.mock import patch

from engine.config import require_ready
from engine.runtime import ModError
from host import GameHost
from tests.test_human import config,A,B


class TestExperimentalFeatures(unittest.TestCase):
    def test_llm_factory_and_tools_follow_switches_without_model_requests(self):
        from clients.llm import LLMClient
        from mods.experimental.client import ParliamentClient
        for values in product((False,True),repeat=3):
            with self.subTest(values=values):
                cfg=config('mods.experimental.toml');cfg['experimental_features']=dict(zip(('council','scripts','combat_research'),values))
                cfg['nations'][1].update(client='llm',base_url='http://offline.invalid',api_key='unused',model='offline')
                prepared=require_ready(GameHost.prepare(cfg));h=GameHost.new(prepared)
                self.assertIs(type(h.clients[B]),ParliamentClient if values[0] else LLMClient)
                self.assertEqual('council' in h.services[B],values[0])
                self.assertEqual('scripts' in h.services[B],values[1])
                self.assertNotIn('combat',h.services[B])
                saved=h.to_save();loaded=GameHost(GameHost.prepare(cfg));loaded.session.restore(saved)
                self.assertEqual(saved,loaded.to_save())

    def test_all_combinations_are_real_assemblies_and_roundtrip_strictly(self):
        for values in product((False,True),repeat=3):
            with self.subTest(values=values):
                cfg=config('mods.experimental.toml');flags=dict(zip(('council','scripts','combat_research'),values));cfg['experimental_features']=flags
                h=GameHost.new(cfg);r=h.runtime
                self.assertEqual(len(r.stage_order),18)
                self.assertEqual('experimental:python' in r.units,flags['scripts'])
                self.assertEqual('experimental:combat-research' in r.units,flags['combat_research'])
                self.assertEqual('script' in r.tools,flags['scripts'])
                self.assertEqual('explain_plan' in r.tools,flags['council'])
                self.assertEqual('council' in h.session.services[B],flags['council'])
                self.assertEqual('scripts' in h.session.services[B],flags['scripts'])
                self.assertFalse(h.session.services[A])
                self.assertEqual(callable(h.clients[B].forecast),flags['combat_research'])
                saved=deepcopy(h.to_save());loaded=GameHost(GameHost.prepare(cfg));loaded.session.restore(saved)
                self.assertEqual(saved,loaded.to_save())
                changed=deepcopy(cfg);changed['experimental_features']['scripts']=not flags['scripts']
                with self.assertRaises(ModError):GameHost(GameHost.prepare(changed)).session.restore(saved)

    def test_disabled_council_requires_no_model_and_never_constructs_reviewer(self):
        cfg=config('mods.experimental.toml');cfg['experimental_features']={'council':False}
        with patch('mods.experimental.council.CouncilService',side_effect=AssertionError('disabled council constructed')):
            prepared=require_ready(GameHost.prepare(cfg));h=GameHost.new(prepared)
            h.cycle(on_human=lambda p:[p.call_api('native:government','end_turn',summary='完成本国本期操作')])
        self.assertEqual(h.clock(),1)
        cfg['experimental_features']['council']=True
        with self.assertRaisesRegex(ValueError,'parliament.reviewer'):require_ready(GameHost.prepare(cfg))

    def test_invalid_flags_and_explicit_runtime_disagreement_fail_before_start(self):
        for flags in ({'unknown':True},{'council':1},[]):
            cfg=config('mods.experimental.toml');cfg['experimental_features']=flags
            with self.assertRaises(ValueError):GameHost.prepare(cfg)
        cfg=config();cfg['experimental_features']={'council':False}
        with self.assertRaises(ValueError):GameHost.prepare(cfg)
        cfg=config('mods.experimental.toml');runtime=GameHost.prepare(cfg).runtime
        cfg['experimental_features']={'scripts':False}
        with self.assertRaises(ValueError):GameHost.prepare(cfg,runtime=runtime)
