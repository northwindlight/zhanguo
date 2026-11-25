"""Package A/B code and migrate the last verified shared-policy checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import re
from zipfile import ZipFile,ZIP_DEFLATED

from mixed_bc_run import digest,publish

ROOT=Path(__file__).resolve().parent
HERE=ROOT/'ab-dynamic-ppo'


def source_digest():
    paths=[p for p in (ROOT/'cpp').rglob('*') if p.is_file() and (p.suffix in ('.cpp','.hpp','.py') or p.name=='CMakeLists.txt')]
    paths += [ROOT/name for name in ('ab_ppo_run.py','land_gdp_ppo_run.py','mixed_bc_run.py','watch_ab_ppo.py','watch_land_gdp_ppo.py','prepare_ab.py')]
    paths.append(HERE/'kernel.py');hasher=hashlib.sha256()
    for path in sorted(paths):
        data=path.read_bytes()
        if path==HERE/'kernel.py':
            data=re.sub(rb'^EXPECTED_(GAMES|PAIR) = .* # AB_EXPECTED_(GAMES|PAIR)$',rb'EXPECTED_\1 = DYNAMIC_INPUT',data,flags=re.M)
        hasher.update(str(path.relative_to(ROOT)).encode()+b'\0'+data)
    return hasher.hexdigest()


def prepare(ready):
    verified=json.loads(ready.read_text());work=Path(verified['work'])
    parent=json.loads((work/'state.json').read_text());checkpoint=work/parent['latest']
    if parent['games']!=verified['games'] or digest(checkpoint)!=verified['sha256'] or parent['sha256']!=verified['sha256']:
        raise ValueError('Migration checkpoint is not the verified boundary')
    config=dict(parent['contract'],mode='dynamic_ab',preflight_seed=530000,
                migration_parent_games=parent['games'],migration_parent_sha256=parent['sha256'],
                rules_reference='72724fd7a3b214ab6690b09d5e1141dae5869da1',observation_projection='legacy_pending_v1')
    code=HERE/'code-data';data=HERE/'state-data';code.mkdir(parents=True,exist_ok=True);data.mkdir(exist_ok=True)
    if (data/'resume-state.json').exists():
        previous=json.loads((data/'resume-state.json').read_text())
        if previous['games']!=parent['games'] or previous['contract']!=config or any(digest(data/r['latest'])!=parent['sha256'] for r in previous['models'].values()):
            raise FileExistsError('Refusing to replace a different A/B training state')
    models={}
    for identity in ('A','B'):
        target=data/f'{identity}-resume.pt';shutil.copyfile(checkpoint,target)
        if digest(target)!=parent['sha256']:raise ValueError('Fork copy corrupted')
        models[identity]=dict(latest=target.name,sha256=parent['sha256'])
    publish(data/'resume-state.json',dict(games=parent['games'],batches=parent['batches'],models=models,contract=config))
    publish(HERE/'run-config.json',config)
    with ZipFile(code/'ab-code.bundle','w',ZIP_DEFLATED,compresslevel=6) as z:
        with ZipFile(ROOT/'industry-bc-results/code.zip') as original:
            for name in original.namelist():
                if name.startswith('python/') and not name.endswith('/'):z.writestr(name,original.read(name))
        for path in (ROOT/'cpp').rglob('*'):
            if path.is_file() and (path.suffix in ('.cpp','.hpp','.py') or path.name=='CMakeLists.txt'):
                z.write(path,path.relative_to(ROOT))
        for name in ('ab_ppo_run.py','land_gdp_ppo_run.py','mixed_bc_run.py'):z.write(ROOT/name,name)
        z.writestr('run-config.json',json.dumps(config,indent=2))
    for directory,slug,title in ((code,'zhanguo-ab-dynamic-code-20261124','Zhanguo Dynamic AB Code 20261124'),
                                  (data,'zhanguo-ab-dynamic-state-20261124','Zhanguo Dynamic AB State 20261124')):
        publish(directory/'dataset-metadata.json',dict(id='northwindlight/'+slug,title=title,licenses=[dict(name='other')]))
    publish(HERE/'kernel-metadata.json',dict(id='northwindlight/zhanguo-dynamic-ab-ppo',title='Zhanguo Dynamic AB PPO',code_file='kernel.py',
        language='python',kernel_type='script',is_private=True,enable_gpu=True,enable_internet=False,
        dataset_sources=['northwindlight/zhanguo-ab-dynamic-code-20261124','northwindlight/zhanguo-ab-dynamic-state-20261124',
                         'northwindlight/zhanguo-industry-bc-eval-handoff-20261123'],kernel_sources=[],competition_sources=[]))
    publish(HERE/'migration.json',dict(parent_games=parent['games'],parent_sha256=parent['sha256'],
                                     code_sha256=digest(code/'ab-code.bundle'),source_sha256=source_digest(),
                                     checkpoint_bytes_preserved=True,initialization=verified.get('kind','migration'),
                                     ppo_optimizer_mode=verified.get('ppo_optimizer_mode','preserved')))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--ready',type=Path,default=ROOT/'land-gdp-ppo-results/switch-ready.json')
    prepare(parser.parse_args().ready)
