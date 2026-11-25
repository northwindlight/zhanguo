"""Package the finite PPO job, keeping credentials out of uploaded data."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
HERE=Path(__file__).resolve().parent
CONFIG=dict(games=2000,turns=100,map_size=24,seats=4,seed=430000,batch_games=8,segment_games=200,
            lr=5e-4,minibatch=32,max_minibatches=384,gamma=1.0,gae_lambda=.99,reward_scale=3000,
            gdp_reward='nominal',land_gdp_mode='holding',marginal_land_gdp=[10,5,3,2,1],
            precision='fp32',kl_ref=0,exec_weight=0,aux_weight=0,invalid_penalty=0,
            eval_games=24,eval_seed=450000,initial_sha256='e972957a0a2689b840da0c1f831cc8abb6eee75055544be52047bad3e00842bf')


def sha(p):
    with p.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def main(code_only=False):
    code=HERE/'code-data';state=HERE/'state-data'
    code.mkdir(exist_ok=True);state.mkdir(exist_ok=True)
    (HERE/'run-config.json').write_text(json.dumps(CONFIG,indent=2))
    with ZipFile(code/'land-gdp-code.bundle','w',ZIP_DEFLATED,compresslevel=6) as z:
        with ZipFile(ROOT/'industry-bc-results/code.zip') as old:
            for name in old.namelist():
                if name.startswith(('python/','cpp/')):
                    path=ROOT/name
                    z.writestr(name,path.read_bytes() if name.startswith('cpp/') and path.is_file() else old.read(name))
        z.write(ROOT/'cpp/rewarding.py','cpp/rewarding.py')
        z.write(ROOT/'land_gdp_ppo_run.py','land_gdp_ppo_run.py')
        for name in ['mixed_bc_run.py']:
            z.write(ROOT/name,name)
        z.writestr('run-config.json',json.dumps(CONFIG,indent=2))
    source=ROOT/'industry-bc-results/epoch-04.pt'
    if sha(source)!=CONFIG['initial_sha256']:raise ValueError('Must start from the verified fourth BC epoch')
    if not code_only:
        if (state/'resume-state.json').exists() and json.loads((state/'resume-state.json').read_text())['games']>0:
            raise ValueError('Refusing to replace a trained checkpoint; use --code-only')
        shutil.copyfile(source,state/'resume.pt')
        initial=dict(games=0,batches=0,latest='resume.pt',sha256=sha(source),contract=CONFIG)
        (state/'resume-state.json').write_text(json.dumps(initial,indent=2))
    for directory,slug,title in [(code,'zhanguo-land-gdp-ppo-code-20261123','Zhanguo Land GDP PPO Code 20261123'),
                                  (state,'zhanguo-land-gdp-ppo-state-20261123','Zhanguo Land GDP PPO State 20261123')]:
        (directory/'dataset-metadata.json').write_text(json.dumps(dict(id='northwindlight/'+slug,title=title,
            licenses=[dict(name='other')]),indent=2))
    metadata=dict(id='northwindlight/zhanguo-land-gdp-ppo',title='Zhanguo Land GDP PPO',code_file='kernel.py',
        language='python',kernel_type='script',is_private=True,enable_gpu=True,enable_internet=False,
        dataset_sources=['northwindlight/zhanguo-industry-bc-eval-handoff-20261123',
                         'northwindlight/zhanguo-land-gdp-ppo-code-20261123',
                         'northwindlight/zhanguo-land-gdp-ppo-state-20261123'],competition_sources=[],kernel_sources=[])
    (HERE/'kernel-metadata.json').write_text(json.dumps(metadata,indent=2))
    print(json.dumps(dict(config=CONFIG,code_bytes=(code/'land-gdp-code.bundle').stat().st_size,
                          checkpoint_bytes=(state/'resume.pt').stat().st_size)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--code-only',action='store_true')
    main(parser.parse_args().code_only)
