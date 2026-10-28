"""Read-only startup checks and explicit save-copy upgrade for the desktop."""
import json
from pathlib import Path
import shutil
import sys

from engine.config import require_ready
from engine.runtime import ModError,Runtime
from host import GameHost,ROOT
from mods.native.save_upgrade import prepare_upgrade,upgrade_copy

EDITIONS={'native':('原生版','mods.toml'),'experimental':('实验版','mods.experimental.toml')}


def describe(runtime):
    declaration=runtime.distribution;identity=declaration['Id']
    from mods.experimental.assembly import FEATURES
    selected=declaration.get('Features',{})
    import ruleai
    labels={'random':'随机经营偏好','balanced':'均衡经营','integrated':'自产配套','merchant':'市场贸易','buffer':'资源储备','liquid':'现金储备'}
    return {'id':identity,'name':EDITIONS.get(identity,(identity,''))[0],'version':declaration['Version'],
            'features':{key:{**FEATURES[key],'enabled':value} for key,value in selected.items()},
            'rule_versions':list(ruleai.versions()),'rule_strategies':[{'id':key,'name':labels.get(key,key)} for key in ('random',*ruleai.PROFILES)],
            'clients':list(declaration.get('ClientFactories',{})),'scenarios':list(declaration.get('Scenarios',{}))}


def catalog():
    result=[]
    for identity,(name,entry) in EDITIONS.items():
        runtime=Runtime.load(ROOT/entry)
        result.append({**describe(runtime),'entry':entry})
    return {'format':'zhanguo.gui-distributions/1','editions':result}


def validate(cfg,session=None):
    prepared=GameHost.prepare(cfg)
    if session:
        host=GameHost(prepared);host.session.restore(json.loads(Path(session).read_text(encoding='utf-8')))
        if not host.finished() and host.clock()<prepared.data.get('max_turns',300):require_ready(prepared)
    else:require_ready(prepared)
    return describe(prepared.runtime)


def inspect_save(source,config):
    data=json.loads(Path(source).read_text(encoding='utf-8'));cfg=json.loads(Path(config).read_text(encoding='utf-8'))
    distribution=data.get('dependencies',{}).get('distribution',{})
    result={'id':distribution.get('Id','unknown'),'version':distribution.get('Version','未知'),
            'features':distribution.get('Features',{}),'can_resume':False,'can_upgrade':False,'message':''}
    if result['id']=='experimental' and not result['features']:
        services=distribution.get('Session',{}).get('Services',{})
        research=distribution.get('ClientFactories',{}).get('rule',{}).get('Services',{})
        result['features']={'council':'council' in services,'scripts':'scripts' in services,'combat_research':'combat' in research}
    prepared=GameHost.prepare(cfg);target=describe(prepared.runtime);result['target']=target
    if data.get('dependencies')==prepared.runtime.dependency_manifest():
        GameHost(prepared).session.restore(data)
        result['can_resume']=True;return result
    try:prepare_upgrade(data,cfg)
    except (ValueError,ModError) as exc:result['message']=str(exc)
    else:
        result['can_upgrade']=True;result['message']='已知旧版，可校验并升级为新副本；原文件与历史保留。'
    return result


def upgrade_bundle(source,config,destination):
    source,config,destination=map(Path,(source,config,destination))
    cfg=json.loads(config.read_text(encoding='utf-8'));copied=False;written=False
    destination.mkdir(parents=True,exist_ok=False)
    session=destination/'session.json';target_config=destination/'config.json'
    try:
        upgrade_copy(source,session,cfg);copied=True
        with target_config.open('x',encoding='utf-8') as stream:
            written=True;json.dump(cfg,stream,ensure_ascii=False,indent=2)
    except Exception:
        if written:target_config.unlink(missing_ok=True)
        if copied:
            session.unlink(missing_ok=True)
            history=session.with_suffix('.json.audit')
            if history.exists():shutil.rmtree(history)
        try:destination.rmdir()
        except OSError:pass
        raise
    return {'session':str(session),'config':str(target_config),'distribution':describe(GameHost.prepare(cfg).runtime)}


def dispatch(request):
    action=request.get('action')
    if action=='catalog':return catalog()
    if action=='validate':return validate(json.loads(Path(request['config']).read_text(encoding='utf-8')),request.get('session'))
    if action=='inspect_save':return inspect_save(request['session'],request['config'])
    if action=='upgrade_copy':return upgrade_bundle(request['session'],request['config'],request['destination'])
    raise ValueError('未知的启动检查操作')


def main():
    try:
        request=json.load(sys.stdin)
        if not isinstance(request,dict):raise ValueError('请求须为JSON对象')
        result={'ok':True,'data':dispatch(request)}
    except (ValueError,KeyError,OSError,ModError) as exc:result={'ok':False,'message':str(exc)}
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
