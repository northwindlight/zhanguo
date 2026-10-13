"""Offline configuration contracts, independent games and safe CLI startup."""
from copy import deepcopy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

from engine.config import ConfigError,Field,Schema,configure_components,read_config
from engine.runtime import Runtime
from host import GameHost,ROOT
from tests import test_session_api as counter
import mp_run


class ConsoleCounterSession(counter.CounterSession):
    def records(self):return {'events':[],'messages':[]}
    def announce(self,text):pass
    def send(self,actor,text):pass
    def add(self,name):raise ValueError('Counter has no arrivals')
    def board(self):return str(self.world.counter.rows)


def counter_config(cfg,*,runtime,overrides):
    cfg=Schema({'seats':Field((list,))}).normalize(cfg)
    return configure_components(cfg,cfg['seats'],runtime=runtime,overrides=overrides,path='config.seats')


def counter_client_config(cfg,*,defaults=None,overrides=None,path='config',runtime=None):
    return Schema({'counter_option':Field((int,),minimum=1)}).normalize(
        cfg,defaults=defaults,overrides=overrides,path=path)


counter_client_config.fields=('counter_option',)


class TestConfiguration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native=Runtime.load(ROOT/'mods.toml')
        cls.experimental=Runtime.load(ROOT/'mods.experimental.toml')
        cls.mini=Runtime.load(ROOT/'mods/examples/minigame')

    def cfg(self):
        return {'map_size':12,'seed':43,'provider':'anthropic','model':'fake',
                'api_key':'synthetic-secret','ctx_window':8000,'language':'en-US',
                'nations':[{'name':'甲','client':'llm','ctx_window':4000,'language':'zh-CN'},
                           {'name':'乙','client':'llm'}]}

    def test_defaults_local_values_and_explicit_overrides_without_input_mutation(self):
        cfg=self.cfg();original=deepcopy(cfg)
        prepared=GameHost.prepare(cfg,runtime=self.native)
        left,right=prepared.data['nations']
        self.assertEqual((left['ctx_window'],right['ctx_window']),(4000,8000))
        self.assertEqual((left['language'],right['language']),('zh-CN','en-US'))
        self.assertEqual(right['api_key'],'synthetic-secret')
        forced=GameHost.prepare(cfg,runtime=self.native,overrides={'ctx_window':12000,'language':'en-US'})
        self.assertEqual([n['ctx_window'] for n in forced.data['nations']],[12000,12000])
        self.assertEqual([n['language'] for n in forced.data['nations']],['en-US','en-US'])
        self.assertEqual(cfg,original)
        host=GameHost.new(forced)
        self.assertEqual(host.cfg,forced.data)
        host.cfg['nations'][0]['ctx_window']=999
        self.assertEqual(forced.data['nations'][0]['ctx_window'],12000)

    def test_distribution_and_replaced_ui_own_override_fields(self):
        cfg=self.cfg()
        prepared=GameHost.prepare(cfg,runtime=self.native,overrides={'map_size':20})
        self.assertEqual(prepared.data['map_size'],20)
        runtime=Runtime.load(ROOT/'mods.toml')
        def palette(cfg,*,defaults=None,overrides=None,path='config'):
            return Schema({'palette':Field((str,),choices=('dusk','dawn'))}).normalize(
                cfg,defaults=defaults,overrides=overrides,path=path)
        palette.fields=('palette',)
        runtime.presentations['ui:configure']=palette
        cfg['language']='custom-language';cfg['palette']='dusk'
        result=GameHost.prepare(cfg,runtime=runtime,overrides={'palette':'dawn'})
        self.assertEqual([item['palette'] for item in result.data['nations']],['dawn','dawn'])
        self.assertEqual(result.data['language'],'custom-language')

    def test_ui_module_override_selects_provider_before_validation(self):
        with TemporaryDirectory() as directory:
            entry=Path(directory)/'mod.toml'
            entry.write_text('[Mod]\nId="ui:alternate"\nVersion="1.0.0"\nEntry="tests.test_ui_mod:AlternateUIMod"\n[Capabilities]\nProvides=["ui:presentation"]\n')
            cfg=self.cfg();cfg['language']='language-owned-by-alternate'
            prepared=GameHost.prepare(cfg,runtime=self.native,overrides={'ui_mods':directory})
            self.assertEqual(prepared.data['ui_mods'],directory)
            self.assertEqual([n['ui_mods'] for n in prepared.data['nations']],[directory,directory])

    def test_context_and_reviewer_own_transport_defaults(self):
        cfg=self.cfg();cfg.update(api_timeout=9,api_chunk_timeout=3,
                                 parliament={'reviewer':{'model':'council','ctx_window':6000}})
        prepared=GameHost.prepare(cfg,runtime=self.experimental)
        actor=prepared.data['nations'][0];reviewer=actor['parliament']['reviewer']
        self.assertEqual((reviewer['model'],reviewer['ctx_window']),('council',6000))
        self.assertEqual((reviewer['api_timeout'],reviewer['api_chunk_timeout']),(9,3))
        self.assertEqual(reviewer['provider'],'anthropic')
        rules=deepcopy(cfg);rules['nations'][0]['client']='rule'
        rule_review=GameHost.prepare(rules,runtime=self.experimental).data['nations'][0]['parliament']['reviewer']
        self.assertEqual(rule_review['api_timeout'],9)
        self.assertEqual(rule_review['api_key'],'synthetic-secret')
        forced=GameHost.prepare(cfg,runtime=self.experimental,overrides={'ctx_window':16000})
        self.assertEqual(forced.data['nations'][0]['parliament']['reviewer']['ctx_window'],16000)

    def test_errors_have_paths_and_never_echo_supplied_values(self):
        for key,value in [('provider','synthetic-secret'),('ctx_window',True),('ctx_fill',float('nan')),
                          ('api_timeout',-1),('ctx_compact','invalid')]:
            with self.subTest(key=key):
                cfg=self.cfg();cfg['nations'][0][key]=value
                with self.assertRaises(ConfigError) as caught:GameHost.prepare(cfg,runtime=self.native)
                self.assertIn('config.nations[0].'+key,str(caught.exception))
                self.assertNotIn('synthetic-secret',str(caught.exception))
        cfg=self.cfg();cfg['parliament']={'reviewer':{'max_steps':0}}
        with self.assertRaisesRegex(ConfigError,r'parliament.reviewer.max_steps'):
            GameHost.prepare(cfg,runtime=self.experimental)

    def test_mini_runs_and_resumes_without_native_config_semantics(self):
        cfg={'map_size':12,'seed':7,'language':'en-US','nations':[{'name':'甲'}]}
        prepared=GameHost.prepare(cfg,runtime=self.mini)
        self.assertEqual(prepared.data['nations'][0]['client'],'mini')
        host=GameHost.new(prepared);host.cycle()
        with TemporaryDirectory() as folder:
            path=Path(folder)/'mini.json';host.save(path);loaded=GameHost.load(path,prepared)
            host.cycle();loaded.cycle();self.assertEqual(host.to_save(),loaded.to_save())
        with self.assertRaisesRegex(ConfigError,'ctx_window'):
            GameHost.prepare(cfg,runtime=self.mini,overrides={'ctx_window':4000})

    def test_custom_client_configuration_and_no_nations_cli_new_resume(self):
        host,cfg,_=counter.TestDistributionAPI().host();runtime=host.runtime
        runtime.distribution['Session']['Entry']='tests.test_configuration:ConsoleCounterSession'
        runtime.distribution['Configuration']={'Entry':'tests.test_configuration:counter_config'}
        runtime.distribution['ClientFactories']['counter']['Configuration']='tests.test_configuration:counter_client_config'
        cfg['counter_option']=3
        normalized=GameHost.prepare(cfg,runtime=runtime,overrides={'counter_option':5})
        self.assertEqual([item['counter_option'] for item in normalized.data['seats']],[5,5])
        self.assertNotIn('nations',normalized.data)
        with TemporaryDirectory() as folder:
            root=Path(folder);cfg.update(save=str(root/'session.json'),journal=str(root/'journal.md'))
            source=root/'cfg.json';source.write_text(json.dumps(cfg))
            # No terminal stdin reads, no network, no real session files.
            stdin=Mock(closed=True);stdin.isatty.return_value=False
            with patch('host.Runtime.load',return_value=runtime),patch('sys.stdin',stdin),patch('sys.stdout',io.StringIO()):
                self.assertEqual(mp_run.run(['--config',str(source),'--new','--turns','1']),0)
                self.assertEqual(mp_run.run(['--config',str(source),'--turns','2']),0)
            restored=GameHost.load(root/'session.json',cfg,runtime=runtime)
            self.assertEqual(restored.clock(),2)
            self.assertEqual(restored.world.counter.rows,{'counter:left':2,'counter:right':2})

    def test_cli_invalid_overrides_fail_before_writing_save_or_journal(self):
        with TemporaryDirectory() as folder:
            root=Path(folder);source=root/'cfg.json';save=root/'session.json';journal=root/'journal.md'
            cfg=self.cfg();cfg.update(save=str(save),journal=str(journal));source.write_text(json.dumps(cfg))
            save.write_text('synthetic existing save');original=save.read_bytes()
            for flag in (['--turns','-1'],['--ctx-window','0']):
                with self.subTest(flag=flag),patch('sys.stderr',io.StringIO()),patch('host.Runtime.load',return_value=self.native):
                    self.assertEqual(mp_run.run(['--config',str(source),'--new',*flag]),2)
                    self.assertEqual(save.read_bytes(),original)
                    self.assertFalse(journal.exists())
            source.write_text('{"api_key": "synthetic-secret", BROKEN}')
            with self.assertRaises(ConfigError) as caught:read_config(source)
            self.assertNotIn('synthetic-secret',str(caught.exception))


if __name__=='__main__':unittest.main()
