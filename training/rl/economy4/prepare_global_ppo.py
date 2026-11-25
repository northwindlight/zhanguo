"""Prepare a fresh PPO campaign from the verified tenth pure-script BC epoch."""
import hashlib
import json
from pathlib import Path
import shutil
import re
from zipfile import ZipFile,ZIP_DEFLATED

from mixed_bc_run import publish,digest

ROOT=Path(__file__).resolve().parent;STAGE=ROOT/'script-global-ppo'


def source_digest():
    paths=[p for p in (ROOT/'cpp').rglob('*') if p.is_file() and (p.suffix in ('.cpp','.hpp','.py') or p.name=='CMakeLists.txt')]
    paths += [ROOT/n for n in ('global_ppo_run.py','ab_ppo_run.py','land_gdp_ppo_run.py','mixed_bc_run.py','prepare_global_ppo.py',
                              'watch_global_ppo.py','watch_ab_ppo.py','watch_land_gdp_ppo.py')]
    paths.append(STAGE/'kernel.py')
    h=hashlib.sha256()
    for path in sorted(paths):
        data=path.read_bytes()
        if path==STAGE/'kernel.py':data=re.sub(rb'^EXPECTED_(GAMES|PAIR) = .* # AB_EXPECTED_(GAMES|PAIR)$',rb'EXPECTED_\1 = DYNAMIC_INPUT',data,flags=re.M)
        h.update(str(path.relative_to(ROOT)).encode()+b'\0'+data)
    return h.hexdigest()


def prepare():
    ready=json.loads((ROOT/'script-bc-kaggle-results/next-stage-ready.json').read_text());work=Path(ready['work']);parent=work/ready['checkpoint']
    state=json.loads((work/'state.json').read_text())
    if state['epoch']!=10 or state['initialization']!='random' or digest(parent)!=ready['sha256']:raise ValueError('Wrong pure-script BC parent')
    segment=json.loads((work/'segment.json').read_text())
    if digest(work/'epoch-10-native.pt')!=segment['native_sha256']:raise ValueError('Corrupt BC evaluation baseline')
    if (STAGE/'migration.json').exists():return
    existing=STAGE/'state-data/resume-state.json'
    if existing.exists() and json.loads(existing.read_text())['games']!=0:raise FileExistsError('Refusing to replace progressed global PPO')
    config=dict(games=2000,turns=100,map_size=24,seats=4,seed=570100,batch_games=8,segment_games=200,
        lr=1e-4,minibatch=16,max_minibatches=128,gamma=1.,gae_lambda=1.,reward_scale=3000,gdp_reward='nominal',
        land_gdp_mode='holding',marginal_land_gdp=[10,5,3,2,1],precision='fp32',target_kl=.02,critic_batches=64,
        eval_games=24,eval_seed=590000,preflight_seed=569000,mode='dynamic_ab_global_sequence',sequence_lengths=[2,4],
        actor_observation_gdp='nominal_only',observation='budget60-power-v1',parent_bc_epoch=10,initial_sha256=ready['sha256'],
        session_timeout_seconds=14400)
    code=STAGE/'code-data';data=STAGE/'state-data'
    for path in (code,data):path.mkdir(parents=True,exist_ok=True)
    models={}
    for identity in ('A','B'):
        target=data/f'{identity}-resume.pt';shutil.copyfile(parent,target);models[identity]=dict(latest=target.name,sha256=digest(target))
    shutil.copyfile(work/'epoch-10-native.pt',data/'baseline-native.pt')
    publish(data/'resume-state.json',dict(games=0,batches=0,models=models,contract=config))
    publish(STAGE/'run-config.json',config);publish(STAGE/'restart-ready.json',dict(kind='pure_script_bc_to_global_ppo',sha256=ready['sha256']))
    with ZipFile(code/'ab-code.bundle','w',ZIP_DEFLATED,compresslevel=6) as z:
        with ZipFile(ROOT/'industry-bc-results/code.zip') as original:
            for name in original.namelist():
                if name.startswith('python/') and not name.endswith('/'):z.writestr(name,original.read(name))
        for path in (ROOT/'cpp').rglob('*'):
            if path.is_file() and (path.suffix in ('.cpp','.hpp','.py') or path.name=='CMakeLists.txt'):z.write(path,path.relative_to(ROOT))
        for name in ('global_ppo_run.py','ab_ppo_run.py','land_gdp_ppo_run.py','mixed_bc_run.py'):z.write(ROOT/name,name)
        z.writestr('run-config.json',json.dumps(config))
    for path,slug,title in ((code,'zhanguo-script-global-code-20261124','Zhanguo Script Global Code 20261124'),
                            (data,'zhanguo-script-global-state-20261124','Zhanguo Script Global State 20261124')):
        publish(path/'dataset-metadata.json',dict(id='northwindlight/'+slug,title=title,licenses=[dict(name='other')]))
    publish(STAGE/'kernel-metadata.json',dict(id='northwindlight/zhanguo-script-global-ppo',title='Zhanguo Script Global PPO',code_file='kernel.py',
        language='python',kernel_type='script',is_private=True,enable_gpu=True,enable_internet=False,
        dataset_sources=['northwindlight/zhanguo-script-global-code-20261124','northwindlight/zhanguo-script-global-state-20261124'],kernel_sources=[],competition_sources=[]))
    publish(STAGE/'migration.json',dict(parent_games=0,parent_sha256=ready['sha256'],code_sha256=digest(code/'ab-code.bundle'),source_sha256=source_digest()))


if __name__=='__main__':prepare()
