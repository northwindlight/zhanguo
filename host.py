"""Load a distribution session. Game-specific APIs belong to the distribution."""
from importlib import import_module
import json
from pathlib import Path
from engine.runtime import Runtime,ModError
from engine.config import PreparedConfig,prepare as prepare_config,RUN_SCHEMA
from engine.session import SaveFormatError,SAVE_FORMAT,write_json

ROOT=Path(__file__).resolve().parent


def create_client(cfg,record,entry,services):
    if entry:
        module,name=entry.split(':')
        return getattr(import_module(module),name)(cfg,record=record,services=services)
    if cfg['client']=='rule':
        import ruleai
        if record and record['version']!='v11plus':raise SaveFormatError('Retired rule client state')
        return ruleai.create(cfg.get('rule_ai'),record['state'] if record else None)
    if cfg['client']=='llm':
        from clients.llm import LLMClient,LLMState
        if record and record['version']!='1':raise SaveFormatError('Unsupported LLM client state')
        return LLMClient(cfg,LLMState.from_save(record['state']) if record else None,ui=services['ui'])
    raise ValueError('Client factory not declared')


class GameHost:
    def __init__(self,prepared):
        runtime,cfg=prepared.runtime,prepared.data
        spec=runtime.distribution.get('Session')
        if not isinstance(spec,dict) or not isinstance(spec.get('Entry'),str):raise ModError('Distribution must declare Session.Entry')
        entry=spec['Entry']
        if entry.count(':')!=1:raise ModError('Invalid Distribution.Session.Entry')
        module,name=entry.split(':')
        factory=getattr(import_module(module),name)
        self.session=factory(runtime,cfg,create_client)
        # 运行接口为必需契约；终端管理是可选能力，由入口按需检查。
        for method in ('start','restore','to_save','save','player','cycle',
                       'actors','clock'):
            if not callable(getattr(self.session,method,None)):
                raise ModError(f'Distribution session is missing {method}()')

    def __getattr__(self,name):return getattr(self.session,name)
    @property
    def world(self):return self.session.world
    @world.setter
    def world(self,value):self.session.world=value
    @property
    def scenario_text(self):return self.session.scenario_text
    @scenario_text.setter
    def scenario_text(self,value):self.session.scenario_text=value

    @classmethod
    def prepare(cls,cfg,*,runtime=None,overrides=None):
        if isinstance(cfg,PreparedConfig):
            if overrides or runtime is not None and runtime is not cfg.runtime:
                raise ValueError('Prepared configuration cannot change runtime or overrides')
            return cfg
        cfg=RUN_SCHEMA.normalize(cfg,overrides=overrides)
        runtime=runtime or Runtime.load(ROOT/cfg.get('mods','mods.toml'))
        return prepare_config(cfg,runtime=runtime,overrides=overrides)

    @classmethod
    def new(cls,cfg,*,runtime=None):
        prepared=cls.prepare(cfg,runtime=runtime)
        host=cls(prepared);host.session.start();return host

    @classmethod
    def load(cls,path,cfg,*,runtime=None):
        prepared=cls.prepare(cfg,runtime=runtime)
        data=json.loads(Path(path).read_text(encoding='utf-8'))
        host=cls(prepared)
        try:host.session.restore(data)
        except ValueError as exc:raise SaveFormatError(str(exc)) from exc
        return host
