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
        version=cfg.get('rule_ai') or (record['version'] if record else ruleai.DEFAULT_RULE_AI)
        if record and (record['version'] not in ruleai.versions() or record['version']!=version):
            raise SaveFormatError('Rule client version mismatch')
        return ruleai.create(version,record['state'] if record else None,
                             profile=cfg.get('rule_strategy','random'),seed=cfg.get('rule_seed'))
    if cfg['client']=='llm':
        from clients.llm import LLMClient,LLMState
        if record and record['version']!=LLMClient.version:raise SaveFormatError('Unsupported LLM client state')
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
        from ui import load_ui
        self.presentation=load_ui(cfg,runtime=runtime)
        self.session=factory(runtime,cfg,create_client,presentation=self.presentation)
        # 运行接口为必需契约；终端管理是可选能力，由入口按需检查。
        for method in ('start','restore','to_save','save','player','cycle',
                       'actors','clock'):
            if not callable(getattr(self.session,method,None)):
                raise ModError(f'Distribution session is missing {method}()')

    def __getattr__(self,name):return getattr(self.session,name)
    @property
    def ui(self):return self.presentation
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
        runtime=runtime or Runtime.load(ROOT/cfg.get('mods','mods.toml'),configuration={**cfg,**(overrides or {})})
        prepared=prepare_config(cfg,runtime=runtime,overrides=overrides)
        # 顶层的 ctx_* 是「所有玩家默认值」，但客户端只拿到自己那条国家配置；
        # 不在这里下沉，顶层 ctx_window 等参数会被静默忽略（--ctx-window 同理）。
        from clients.context import apply_defaults
        return PreparedConfig(prepared.runtime,apply_defaults(prepared.data))

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
