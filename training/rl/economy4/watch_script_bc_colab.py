"""Transfer, start, and continuously back up the Colab pure-script BC run."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib,json,subprocess,time
from mixed_bc_run import publish,digest

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'script-bc-colab-results'
REMOTE='/content/zhanguo-pure-script-bc/work'
SESSION='zg-pure-bc-t4'
COLAB='/home/northwind/.local/bin/colab'

def command(args,timeout=300,input=None):
    # CLI transport errors can contain proxy credentials: never relay raw output.
    result=subprocess.run([COLAB,*map(str,args)],input=input,text=True,capture_output=True,timeout=timeout)
    if result.returncode:raise RuntimeError('Colab command failed: '+str(args[0]))
    return result.stdout

def upload(path,name):
    for attempt in range(4):
        try:
            command(['upload','-s',SESSION,path,'/content/'+name],timeout=300)
            publish(OUT/'transfer'/('uploaded-'+name+'.json'),dict(sha256=digest(path)))
            return
        except (RuntimeError,subprocess.TimeoutExpired):
            if attempt==3:raise
            time.sleep(5)

def download(name,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix(target.suffix+'.download')
    command(['download','-s',SESSION,REMOTE+'/'+name,temp],timeout=300)
    temp.replace(target)

def main():
    OUT.mkdir(exist_ok=True);transfer=OUT/'transfer';transfer.mkdir(exist_ok=True)
    prepared=json.loads((ROOT/'pure-script-bc/prepared.json').read_text())
    parts=[]
    with (ROOT/'pure-script-bc/corpus-data/script-data.bundle').open('rb') as source:
        index=0
        while data:=source.read(8*1024*1024):
            name=f'script-data.part-{index:03}';path=transfer/name
            if not path.exists():path.write_bytes(data)
            if hashlib.sha256(data).hexdigest()!=digest(path):raise ValueError('Bad local transfer part')
            parts.append(dict(name=name,sha256=digest(path)));index+=1
    manifest=transfer/'script-transfer.json';publish(manifest,dict(parts=parts,**prepared))
    pending=[item for item in parts if not (transfer/('uploaded-'+item['name']+'.json')).exists()]
    publish(OUT/'live.json',dict(state='uploading',parts=len(parts),remaining=len(pending),time=time.time()))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(upload,transfer/item['name'],item['name']) for item in pending]
        for i,future in enumerate(jobs):
            future.result();publish(OUT/'live.json',dict(state='uploading',parts=len(parts),remaining=len(jobs)-i-1,time=time.time()))
    upload(ROOT/'pure-script-bc/code-data/script-code.bundle','script-code.bundle')
    upload(manifest,manifest.name)
    upload(ROOT/'script_bc_colab_lowmem.py','script_bc_colab_lowmem.py')
    command(['exec','-s',SESSION,'--file',ROOT/'launch_script_bc_colab.py','--timeout',60],timeout=90)
    probe="""from pathlib import Path
import json
p=Path('/content/zhanguo-pure-script-bc/work');r={}
for name in ('state.json','preflight.json','failure.json','segment.json'):
 f=p/name
 if f.exists():r[name]=json.loads(f.read_text())
f=p/'driver.log'
if f.exists():
 for line in f.read_text(errors='replace').splitlines():
  try:v=json.loads(line)
  except ValueError:continue
  if v.get('stage') in ('bc_training','bc_epoch_complete'):r['progress']=v
print(json.dumps(r))
"""
    backed=0
    while True:
        try:
            output=command(['exec','-s',SESSION,'--timeout',45],timeout=75,input=probe)
            rows=[json.loads(line) for line in output.splitlines() if line.startswith('{')]
            if not rows:raise RuntimeError('No Colab progress reply')
            remote=rows[-1]
            if 'failure.json' in remote:raise ValueError('Remote failure: '+json.dumps(remote['failure.json']))
            state=remote.get('state.json',{});epoch=state.get('epoch',0)
            publish(OUT/'live.json',dict(state='bc' if 'preflight.json' in remote else 'gpu_preflight',
                remote_epoch=epoch,verified_epoch=backed,goal=10,progress=remote.get('progress'),time=time.time()))
            if epoch>backed:
                work=OUT/'work';download(state['latest'],work/state['latest'])
                if digest(work/state['latest'])!=state['sha256']:raise ValueError('BC checkpoint checksum mismatch')
                for n in range(backed+1,epoch+1):download(f'epoch-{n:02}.json',work/f'epoch-{n:02}.json')
                publish(work/'state.json',state);backed=epoch
                publish(OUT/'backup.json',dict(epoch=epoch,sha256=state['sha256'],time=time.time()))
            if 'segment.json' in remote:
                work=OUT/'work';segment=remote['segment.json']
                for name in ('epoch-10-native.pt','evaluation.json','training-contract.json','launch.json','preflight.json','driver.log','segment.json','done.json'):
                    download(name,work/name)
                if epoch!=10 or digest(work/'epoch-10-native.pt')!=segment['native_sha256']:raise ValueError('Final model identity mismatch')
                ready=dict(work=str(work),checkpoint=state['latest'],sha256=state['sha256'],
                           reward='nominal_gdp+territory_holding',actor_observation_gdp='nominal_only',backend='colab',time=time.time())
                publish(OUT/'verified.json',dict(epoch=10,initialization='random',targets='pure_script_only',**ready))
                # The established PPO scheduler consumes this provider-neutral handoff path.
                target=ROOT/'script-bc-kaggle-results'
                publish(target/'verified.json',dict(epoch=10,initialization='random',targets='pure_script_only',**ready))
                publish(target/'next-stage-ready.json',ready)
                publish(OUT/'live.json',dict(state='bc_completed',verified_epoch=10,next_stage='global_ppo',time=time.time()))
                command(['stop','-s',SESSION],timeout=90)
                return
        except (RuntimeError,subprocess.TimeoutExpired):
            publish(OUT/'connection-retry.json',dict(time=time.time(),verified_epoch=backed))
        time.sleep(45)

if __name__=='__main__':
    try:main()
    except Exception as error:
        OUT.mkdir(exist_ok=True);publish(OUT/'failure.json',dict(type=type(error).__name__,message=str(error),time=time.time()))
        raise
