"""Package only pure-script demonstrations and a fresh-init BC trainer."""
import json
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED,ZIP_STORED

from mixed_bc_run import digest,publish

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'pure-script-bc';DATA=ROOT/'script-bc-results'


def prepare():
    done=json.loads((DATA/'done.json').read_text())
    if done['contract']['source']!='pure_script' or done['games']!=1000:raise ValueError('Wrong script corpus')
    stage=STAGE;code=stage/'code-data';data=stage/'corpus-data';state=stage/'state-data'
    for directory in (code,data,state):directory.mkdir(parents=True,exist_ok=True)
    with ZipFile(data/'script-data.bundle','w',ZIP_STORED) as z:
        for name,row in sorted(done['archives'].items()):
            path=DATA/'chunks'/name
            if digest(path)!=row['sha256']:raise ValueError('Script corpus checksum mismatch')
            z.write(path,'chunks/'+name)
        for name in ('done.json','inventory.json','contract.json'):z.write(DATA/name,name)
    with ZipFile(code/'script-code.bundle','w',ZIP_DEFLATED,compresslevel=6) as z:
        with ZipFile(ROOT/'industry-bc-results/code.zip') as source:
            for name in source.namelist():
                if name.startswith('python/') and not name.endswith('/'):z.writestr(name,source.read(name))
        for path in (ROOT/'cpp').rglob('*'):
            if path.is_file() and (path.suffix in ('.cpp','.hpp','.py') or path.name=='CMakeLists.txt'):z.write(path,path.relative_to(ROOT))
        for name in ('script_bc_train.py','mixed_bc_run.py','ab_ppo_run.py','land_gdp_ppo_run.py'):z.write(ROOT/name,name)
    for directory,slug,title in ((code,'zhanguo-pure-script-code-20261124','Zhanguo Pure Script Code 20261124'),
            (data,'zhanguo-pure-script-corpus-20261124','Zhanguo Pure Script Corpus 20261124'),
            (state,'zhanguo-pure-script-state-20261124','Zhanguo Pure Script State 20261124')):
        publish(directory/'dataset-metadata.json',dict(id='northwindlight/'+slug,title=title,licenses=[dict(name='other')]))
    if not (state/'resume-state.json').exists():publish(state/'resume-state.json',dict(epoch=0,latest=None,sha256=None,seed=20261124,epochs=10,initialization='random'))
    publish(stage/'kernel-metadata.json',dict(id='northwindlight/zhanguo-pure-script-bc',title='Zhanguo Pure Script BC',code_file='kernel.py',
        language='python',kernel_type='script',is_private=True,enable_gpu=True,enable_internet=False,
        dataset_sources=['northwindlight/zhanguo-pure-script-code-20261124','northwindlight/zhanguo-pure-script-corpus-20261124',
                         'northwindlight/zhanguo-pure-script-state-20261124'],kernel_sources=[],competition_sources=[]))
    publish(stage/'prepared.json',dict(code_sha256=digest(code/'script-code.bundle'),data_sha256=digest(data/'script-data.bundle'),
        initialization='random',bc_epochs=10,script_worlds=done['games'],script_accepted=done['accepted'],old_model_targets=False,
        ppo_reward='nominal_gdp+territory_holding',actor_observation_gdp='nominal_only'))


if __name__=='__main__':prepare()
