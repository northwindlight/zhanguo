"""Explicit copy upgrade for known v0.6.3 saves; normal loading stays strict."""
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


def prepare_upgrade(data, cfg):
    """Validate the entire copied session without running players or settlement."""
    from host import GameHost, ROOT
    from engine.runtime import Runtime
    if not isinstance(data,dict):raise SaveFormatError('存档须为完整Session对象')
    digest=hashlib.sha256(json.dumps(data.get('dependencies'),sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
    edition=LEGACY_MANIFESTS.get(digest)
    if edition is None:raise SaveFormatError('不是已知v0.6.3依赖清单，拒绝猜测升级；请保留对应旧发行包')
    filename='mods.toml' if edition=='native' else 'mods.experimental.toml'
    runtime=Runtime.load(ROOT/filename)
    configured=GameHost.prepare(cfg)
    if configured.runtime.dependency_manifest()!=runtime.dependency_manifest():
        raise SaveFormatError('配置选择的发行版与旧存档不同，不能在升级时混用规则')
    candidate=deepcopy(data);candidate['dependencies']=runtime.dependency_manifest()
    try:
        if 'queued' in candidate['modules']['native:buildings'] or {'recruit_queue','next_recruit_order'} & candidate['modules']['native:army']['military'].keys():
            raise SaveFormatError('旧依赖下已有新版队列字段，拒绝覆盖或猜测状态')
        candidate['modules']['native:buildings']['queued']=[]
        candidate['modules']['native:army']['military'].update(recruit_queue=[],next_recruit_order=1)
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
