"""Experimental owns optional module/service/client assembly, before startup."""
from engine.config import ConfigError

QUEUE_FEATURES={
    'construction_queue':{'name':'建筑队列','short_name':'建筑队列','description':'预付建筑订单，同一地块的后续条目按回合逐项开工。','default':False,'clients':['human','llm','rule']},
    'march_orders':{'name':'移动队列','short_name':'移动队列','description':'添加跨回合的移动或攻击节点，由军队在本国行动期继续执行。','default':False,'clients':['human','llm','rule']},
}

FEATURES={
    'council':{'name':'议会审核','short_name':'议会','description':'模型与规则AI提交国策由议会审核；真人免审。需要配置审查模型。','default':True,'clients':['llm','rule']},
    'scripts':{'name':'Python脚本沙盒','short_name':'脚本','description':'模型与规则AI可保存、检查并手动执行受限脚本。','default':True,'clients':['llm','rule']},
    'combat_research':{'name':'规则AI战斗推演','short_name':'战斗推演','description':'规则AI通过本国情报推演战斗概率，不调用模型。','default':True,'clients':['rule']},
    **QUEUE_FEATURES,
}


def selection(cfg, features=FEATURES):
    raw=cfg.get('experimental_features',{})
    if not isinstance(raw,dict) or set(raw)-features.keys():raise ConfigError('config.experimental_features: 未知实验功能')
    if any(type(value) is not bool for value in raw.values()):raise ConfigError('config.experimental_features: 开关须为布尔值')
    return {key:raw.get(key,spec['default']) for key,spec in features.items()}


def assemble_native(distribution,cfg):
    distribution['Features']=selection(cfg,QUEUE_FEATURES)
    return distribution


def assemble(distribution,cfg):
    enabled=selection(cfg);members=distribution['Members'];session=distribution['Session'];factories=distribution['ClientFactories']
    if not enabled['council']:
        members[members.index('government.toml')]='../native/government/mod.toml'
        session['Services'].pop('council');factories['llm'].pop('Entry')
    if not enabled['scripts']:
        members.remove('scripting.toml');distribution['RequiredState'].remove('scripts');session['Services'].pop('scripts')
    if not enabled['combat_research']:
        members.remove('combat_research/mod.toml');factories['rule'].pop('Entry');factories['rule'].pop('Services')
    distribution['Features']=enabled
    return distribution


def normalize(cfg,*,runtime,overrides):
    from mods.native.config import normalize as native
    enabled=selection({**cfg,**overrides})
    if runtime.distribution.get('Features')!=enabled:raise ConfigError('config.experimental_features: 与已装配的发行版不一致')
    result=native({key:value for key,value in cfg.items() if key!='experimental_features'},runtime=runtime,
                  overrides={key:value for key,value in overrides.items() if key!='experimental_features'})
    result['experimental_features']=enabled
    return result


def require_ready(cfg,*,runtime):
    from mods.native.config import require_ready as native
    from clients.config import require_model_fields
    native(cfg,runtime=runtime)
    if not runtime.distribution['Features']['council']:return
    clients=runtime.distribution['Session']['Services']['council']['Clients']
    for index,item in enumerate(cfg['nations']):
        if item.get('enabled',True) and item['client'] in clients:
            reviewer=item['parliament']['reviewer']
            require_model_fields({'nations':[{**reviewer,'client':'llm','name':item['name']+'的议会审查模型'}]},
                                 path=f'config.nations[{index}].parliament.reviewer')
