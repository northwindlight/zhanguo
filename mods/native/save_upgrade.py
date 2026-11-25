"""Explicit copy upgrade for known native/experimental saves; loading stays strict."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

from engine.session import SaveFormatError
from engine.runtime import ModError


LEGACY_MANIFESTS={
    '9c67856fdf79f15382d80802ba1f116196105a7eaec384b71ffef6296cf57f07':'native',
    '6ee31c429fcea96b5da5cb0976da96993c8c0deb3ee383e37ef14e87eb5bb7a1':'experimental',
}
PRODUCTION_MANIFESTS={
    '120bd26f256c0fe2be1f62796d306858ee614f85ad309d20a92b162837bf2161':'native',
    '9bd568a9de45c017fe557b1653a1c439f8e75a89e595f5df882521b88e878519':'experimental',
}
CURRENT_MANIFESTS={
    '2a92069a3fdfe521a50c471c74836e8cc20b8d30523a7899daf0e649b3818313':'native',
    '77c0981ebafa5c4c169726e1e27a9b3c6d5a38046e3cd4aa6c6dc469f18d8f82':'experimental',
    '1c2b3fb38cd154d28034bc1558019043501901f23bc882aad39d32e938c1e22a':'native',
    **dict.fromkeys((
        'fe88211c056f508d494db9f027c14193cd909633a726db7242317eb4811dd92c',
        '6e5d6783d06d44bc781c4fc2bdafec2ed23f69e2ffeec85311e93e6f0071761e',
        'c23a584096d868ea1eee81ced0525324a8992d42d1bfabec132056a6d90ecf88',
        '0ffae716ce3bf9747e24b213dd675ef0d1c2329a580eb14338be8bd8d91ed736',
        '0b1939360a303772fe91e382ebb1186fe9328e6148bc1610faa1b34703d51c08',
        '2b278063ab5a667808436092286f4692d467727929b89a849db5ece7c9cb160e',
        'a28ec69c14699ad205b5c0648ba761da2d7bca8220ec0708fc70a6d7fe1b4b6f',
        '557f214bc06d6dbfa45c599fa6772501fc00e5e725b33b75ce4f472844e25495',
    ),'experimental'),
}


def with_pending_queue_features(data,cfg):
    try:
        modules=data.get('modules',{})
        required={
            'construction_queue':bool(modules.get('native:buildings',{}).get('queued')),
            'march_orders':bool(modules.get('native:army',{}).get('military',{}).get('march_orders')),
        }
    except AttributeError:return cfg  # The save validator reports malformed module data.
    flags=cfg.get('experimental_features',{})
    if not isinstance(flags,dict):return cfg
    additions={key:True for key,pending in required.items() if pending and key not in flags}
    return {**cfg,'experimental_features':{**flags,**additions}} if additions else cfg


def prepare_upgrade(data, cfg):
    """Validate the entire copied session without running players or settlement."""
    from host import GameHost, ROOT
    from engine.runtime import Runtime
    from mods.experimental.assembly import FEATURES, QUEUE_FEATURES
    if not isinstance(data,dict):raise SaveFormatError('存档须为完整Session对象')
    digest=hashlib.sha256(json.dumps(data.get('dependencies'),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    edition=LEGACY_MANIFESTS.get(digest) or PRODUCTION_MANIFESTS.get(digest) or CURRENT_MANIFESTS.get(digest)
    if edition is None:raise SaveFormatError('不是已知存档清单，拒绝猜测升级；请保留对应旧发行包')
    configured=GameHost.prepare(cfg)
    runtime=configured.runtime
    filename='mods.toml' if edition=='native' else 'mods.experimental.toml'
    canonical=Runtime.load(ROOT/filename,configuration=cfg) if runtime.distribution['Id']==edition else None
    if canonical is None or runtime.dependency_manifest()!=canonical.dependency_manifest():
        raise SaveFormatError('配置选择的发行版与旧存档不同，不能在升级时混用规则')
    if edition=='experimental':
        old=data['dependencies']['distribution'].get('Features',{})
        for key in FEATURES.keys()-QUEUE_FEATURES.keys():
            if runtime.feature_enabled(key)!=old.get(key,FEATURES[key]['default']):
                raise SaveFormatError('升级时须保留旧存档的已有实验服务')
    candidate=deepcopy(data);candidate['dependencies']=runtime.dependency_manifest()
    try:
        military=candidate['modules']['native:army']['military']
        if digest not in CURRENT_MANIFESTS and 'march_orders' in military:raise SaveFormatError('旧依赖下已有新版军令字段，拒绝覆盖')
        if digest in LEGACY_MANIFESTS:
            if 'queued' in candidate['modules']['native:buildings'] or {'recruit_queue','next_recruit_order'} & military.keys():
                raise SaveFormatError('旧依赖下已有新版队列字段，拒绝覆盖或猜测状态')
            candidate['modules']['native:buildings']['queued']=[]
            military.update(recruit_queue=[],next_recruit_order=1)
        if digest not in CURRENT_MANIFESTS:military['march_orders']=[]
    except (KeyError,TypeError,AttributeError) as exc:raise SaveFormatError('旧存档模块结构不完整') from exc
    host=GameHost(configured)
    try:host.session.restore(candidate)
    except (ValueError,TypeError,KeyError,ModError) as exc:raise SaveFormatError('升级副本校验失败：'+str(exc)) from exc
    restored=host.to_save()
    if restored!=candidate:raise SaveFormatError('升级意外改变既有游戏状态，拒绝输出')
    return restored


def upgrade_copy(source, destination, cfg):
    source=Path(source);destination=Path(destination)
    source_archive=source.with_suffix(source.suffix+'.audit')
    target_archive=destination.with_suffix(destination.suffix+'.audit')
    if destination.exists() or target_archive.exists():raise FileExistsError('升级只写新文件，请选择未使用的副本路径')
    data=prepare_upgrade(json.loads(source.read_text(encoding='utf-8')),cfg)
    destination.parent.mkdir(parents=True,exist_ok=True)
    copied=False;created=False
    try:
        # Preserve companion history. Exclusive creation never overwrites a
        # concurrent file; clean up only artifacts created by this operation.
        if source_archive.exists():
            target_archive.mkdir();copied=True
            shutil.copytree(source_archive,target_archive,dirs_exist_ok=True)
        with destination.open('x',encoding='utf-8') as stream:
            created=True;json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n')
    except Exception:
        if created:destination.unlink(missing_ok=True)
        if copied:shutil.rmtree(target_archive)
        raise
    return destination
