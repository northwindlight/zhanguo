"""Launch the unchanged pure-script curriculum on a single Colab GPU."""
from pathlib import Path
import subprocess
import sys

root = Path('/content/zhanguo-pure-script-bc')
root.mkdir(exist_ok=True)
worker = r'''
from pathlib import Path
import fcntl, hashlib, json, os, subprocess, sys, traceback
from zipfile import ZipFile
root=Path('/content/zhanguo-pure-script-bc');work=root/'work';work.mkdir(exist_ok=True)
os.environ.update(OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',NVIDIA_TF32_OVERRIDE='0',MALLOC_ARENA_MAX='2')
def publish(path,row):
    temp=path.with_suffix('.partial');temp.write_text(json.dumps(row));temp.replace(path)
def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def run(args,log):
    with log.open('w') as stream:
        p=subprocess.Popen([str(x) for x in args],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
        for line in p.stdout:print(line,end='',flush=True);stream.write(line);stream.flush()
        if p.wait():raise RuntimeError('Subprocess failed: '+log.name)
with (root/'.lock').open('a') as lock:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Already running')
    if (work/'segment.json').exists():raise SystemExit('Already completed')
    try:
        deck=Path('/content');manifest=json.loads((deck/'script-transfer.json').read_text())
        bundle=deck/'script-data.bundle'
        with bundle.open('wb') as output:
            for item in manifest['parts']:
                part=deck/item['name']
                if sha(part)!=item['sha256']:raise ValueError('Transfer checksum failed')
                with part.open('rb') as source:
                    while data:=source.read(1024*1024):output.write(data)
        if sha(bundle)!=manifest['data_sha256'] or sha(deck/'script-code.bundle')!=manifest['code_sha256']:
            raise ValueError('Bundle checksum mismatch')
        for name,directory in (('script-code.bundle','code'),('script-data.bundle','corpus')):
            with ZipFile(deck/name) as z:z.extractall(root/directory)
        hardware=json.loads(subprocess.check_output([sys.executable,'-c',
            "import torch,os,json;assert torch.cuda.is_available();print(json.dumps(dict(gpu=torch.cuda.get_device_name(0),torch=str(torch.__version__),cpu=os.cpu_count())))"],text=True))
        publish(work/'launch.json',dict(initialization='random',goal=10,backend='colab',hardware=hardware))
        print(json.dumps(dict(stage='script_bc_start',hardware=hardware)),flush=True)
        common=[sys.executable,'-u',Path('/content/script_bc_colab_lowmem.py'),'--data',root/'corpus',
                '--python-root',root/'code/python','--device','cuda:0']
        if not (work/'preflight.json').exists():
            run([*common,'--output',root/'preflight','--epochs',1,'--limit-steps',2,'--validation-limit',2,'--seed',20261125],work/'preflight.log')
            publish(work/'preflight.json',dict(passed=True,full_architecture=True,test_weights_discarded=True,single_gpu=True))
        print(json.dumps(dict(stage='script_bc_production_begin')),flush=True)
        run([*common,'--output',work,'--epochs',10],work/'driver.log')
        state=json.loads((work/'state.json').read_text())
        if state['epoch']!=10 or sha(work/state['latest'])!=state['sha256']:raise ValueError('Incomplete BC')
        run([sys.executable,root/'code/cpp/export_policy.py',work/state['latest'],work/'epoch-10-native.pt',
             '--python-root',root/'code/python'],work/'export.log')
        run([sys.executable,root/'code/cpp/build_colab.py','--build',work/'build','--jobs',2,'--target','zhanguo_dual'],work/'build.log')
        sys.path.insert(0,str(root/'code'));os.environ['ZHANGUO_PYTHON_ROOT']=str(root/'code/python')
        from ab_ppo_run import sample
        native=work/'epoch-10-native.pt';results=[]
        for batch in range(6):
            results.append(sample(work/'build/zhanguo_dual',dict(A=native,B=native),('cuda:0','cuda:0'),
                                  work,None,4,560000+4*batch,dict(turns=100,map_size=24),dict(os.environ),'evaluation-'+str(batch)))
        publish(work/'evaluation.json',dict(maps=24,seats=96,parts=results,model='epoch-10',unaided=True))
        publish(work/'segment.json',dict(epoch=10,sha256=state['sha256'],latest=state['latest'],finished=True,
                                        initialization='random',native_sha256=sha(native)))
    except Exception as error:
        publish(work/'failure.json',dict(type=type(error).__name__,message=str(error)))
        traceback.print_exc();raise
'''
script=root/'worker.py'
script.write_text(worker)
with (root/'driver.log').open('ab') as log:
    process=subprocess.Popen([sys.executable,'-u',str(script)],stdin=subprocess.DEVNULL,
                             stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print('Pure-script BC supervisor PID',process.pid,flush=True)
