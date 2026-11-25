"""Finite two-epoch pure-script BC segment; release GPUs between segments."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import subprocess
import sys
from zipfile import ZipFile

EXPECTED_EPOCH = 0 # SCRIPT_EXPECTED_EPOCH
EXPECTED_SHA = None # SCRIPT_EXPECTED_SHA


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def run(args,log):
    with log.open('w') as stream:
        process=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in process.stdout:print(line,end='',flush=True);stream.write(line);stream.flush()
        if process.wait():raise RuntimeError('Script BC subprocess failed: '+log.name)


def main():
    root=Path('/kaggle/working');work=root/'work';work.mkdir(exist_ok=True);inputs=Path('/kaggle/input')
    os.environ.update(OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',NVIDIA_TF32_OVERRIDE='0')
    code=list(inputs.rglob('script-code.bundle'));data=list(inputs.rglob('script-data.bundle'));states=list(inputs.rglob('resume-state.json'))
    if any(len(v)!=1 for v in (code,data,states)):raise ValueError('Ambiguous script BC inputs')
    state=json.loads(states[0].read_text())
    if state['epoch']!=EXPECTED_EPOCH or state['sha256']!=EXPECTED_SHA or state['initialization']!='random':raise ValueError('Stale or pretrained input')
    with ZipFile(code[0]) as z:z.extractall(root/'code')
    with ZipFile(data[0]) as z:z.extractall(root/'corpus')
    if state['epoch']:
        source=states[0].parent/state['latest']
        if sha(source)!=state['sha256']:raise ValueError('Bad BC input checkpoint')
        shutil.copyfile(source,work/state['latest']);(work/'state.json').write_text(json.dumps(state))
    import torch
    if torch.cuda.device_count()!=2:raise RuntimeError('BC DataParallel requires two GPUs')
    hardware=dict(gpus=[torch.cuda.get_device_name(i) for i in range(2)],torch=str(torch.__version__),cpu=os.cpu_count())
    target=min(10,state['epoch']+2)
    (work/'launch.json').write_text(json.dumps(dict(initialization='random',epoch=state['epoch'],target=target,goal=10,hardware=hardware),indent=2))
    print(json.dumps(dict(stage='script_bc_start',epoch=state['epoch'],target=target,hardware=hardware)),flush=True)
    common=[sys.executable,'-u',root/'code/script_bc_train.py','--data',root/'corpus','--python-root',root/'code/python','--device','cuda:0']
    # Real architecture, both GPUs, fresh disposable weights; never continue from this check.
    run([*common,'--output','/tmp/script-bc-preflight','--epochs',1,'--limit-steps',2,'--validation-limit',2,'--seed',20261125],work/'preflight.log')
    (work/'preflight.json').write_text(json.dumps(dict(passed=True,full_architecture=True,two_gpu_dataparallel=True,test_weights_discarded=True)))
    print(json.dumps(dict(stage='script_bc_production_begin',epoch=state['epoch']+1)),flush=True)
    run([*common,'--output',work,'--epochs',10,'--stop-after-epoch',target],work/'driver.log')
    new=json.loads((work/'state.json').read_text());checkpoint=work/new['latest']
    if new['epoch']!=target or sha(checkpoint)!=new['sha256']:raise ValueError('Incomplete BC segment')
    if target==10:
        run([sys.executable,root/'code/cpp/export_policy.py',checkpoint,work/'epoch-10-native.pt','--python-root',root/'code/python'],work/'export.log')
        run([sys.executable,root/'code/cpp/build_colab.py','--build',work/'build','--jobs',4,'--target','zhanguo_dual'],work/'build.log')
        sys.path.insert(0,str(root/'code'));os.environ['ZHANGUO_PYTHON_ROOT']=str(root/'code/python')
        from ab_ppo_run import sample
        results=[];native=work/'epoch-10-native.pt'
        for batch in range(3):
            results.append(sample(work/'build/zhanguo_dual',dict(A=native,B=native),('cuda:0','cuda:1'),work,None,8,560000+8*batch,
                                  dict(turns=100,map_size=24),dict(os.environ),'evaluation-'+str(batch)))
        (work/'evaluation.json').write_text(json.dumps(dict(maps=24,seats=96,parts=results,model='epoch-10',unaided=True),indent=2))
    (work/'segment.json').write_text(json.dumps(dict(epoch=target,sha256=new['sha256'],latest=new['latest'],finished=target==10,initialization='random',
        native_sha256=sha(work/'epoch-10-native.pt') if target==10 else None),indent=2))
    print(json.dumps(dict(stage='script_bc_segment_complete',epoch=target,sha256=new['sha256'],finished=target==10)),flush=True)


if __name__=='__main__':main()
