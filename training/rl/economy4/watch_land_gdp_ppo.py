"""Run the configured finite PPO campaign in resumable Kaggle segments."""
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from zipfile import ZipFile,ZIP_DEFLATED

ROOT=Path(__file__).resolve().parent
STAGE=ROOT/'land-gdp-ppo';OUT=ROOT/'land-gdp-ppo-results'
KERNEL='northwindlight/zhanguo-land-gdp-ppo'
os.environ['PATH']=str(Path.home()/'.local/bin')+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')


def sha(path):
    with path.open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def publish(path,row):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(row,indent=2));temp.replace(path)


def command(args,log,timeout=180):
    with log.open('w') as f:
        r=subprocess.run([str(x) for x in args],stdout=f,stderr=subprocess.STDOUT,timeout=timeout)
    return r.returncode


def dataset_status(ref):
    r=subprocess.run(['kaggle','datasets','status',ref,'--format','json'],capture_output=True,text=True,timeout=60)
    if r.returncode:return None
    try:return json.loads(r.stdout)
    except ValueError:return None


def ready(ref,version=1):
    s=dataset_status(ref)
    return bool(s and s.get('status')=='ready' and s.get('current_version_number',0)>=version)


def wait_dataset(ref,version=1):
    while True:
        try:
            if ready(ref,version):return
        except (OSError,subprocess.TimeoutExpired):pass
        time.sleep(30)


def upload_data(folder,marker,version=False):
    ref=json.loads((folder/'dataset-metadata.json').read_text())['id']
    if marker.exists():
        wait_dataset(ref,json.loads(marker.read_text()).get('expected_version',1));return
    if not version and ready(ref):
        publish(marker,dict(ready=True,dataset=ref,time=time.time()));return
    args=['kaggle','datasets','version' if version else 'create','-p',folder]
    expected_version=1
    if version:
        before=dataset_status(ref)
        if not before:raise RuntimeError('Cannot inspect checkpoint dataset version before upload')
        expected_version=before['current_version_number']+1
        resume=folder/'resume-state.json'
        message='Continue finite PPO after '+str(json.loads(resume.read_text())['games'])+' games' if resume.exists() else 'Update finite PPO controller'
        args.extend(['-m',message])
    if command(args,OUT/(marker.stem+'-upload.log'),timeout=1200):raise RuntimeError('Private dataset upload failed: '+ref)
    publish(marker,dict(uploaded=True,dataset=ref,expected_version=expected_version,time=time.time()));wait_dataset(ref,expected_version)


def pull(stage):
    dest=OUT/'segments'/f'{stage:03}';dest.mkdir(parents=True,exist_ok=True)
    args=['kaggle','kernels','output',KERNEL,'-p',dest,'-q',
          '--file-pattern',r'^work/[^/]+\.(json|log|pt)$']
    if command(args,OUT/f'download-{stage:03}.log',timeout=1200):raise RuntimeError('Cannot download completed PPO segment')
    work=dest/'work';state=json.loads((work/'state.json').read_text())
    if sha(work/state['latest'])!=state['sha256']:raise ValueError('PPO checkpoint checksum mismatch')
    segment=json.loads((work/'segment.json').read_text())
    if segment['sha256']!=state['sha256'] or segment['games']!=state['games']:
        raise ValueError('Incomplete segment commit')
    if sha(work/segment['native'])!=segment['native_sha256']:raise ValueError('Native policy checksum mismatch')
    publish(dest/'verified.json',dict(games=state['games'],checkpoint=state['latest'],sha256=state['sha256'],time=time.time()))
    return work,state,segment


def military_health(records):
    # Deduplicate replayed streaming logs by committed batch, not by message count.
    records={r['batch']:r for r in records}
    ordered=[records[k] for k in sorted(records)]
    def window(rows):
        seats=sum(r['seats'] for r in rows)
        if not seats:return None
        return dict(games=sum(r.get('batch_games',r['games']) for r in rows),seats=seats,
            recruited_seat_rate=sum(r['recruited_seats'] for r in rows)/seats,
            attacked_seat_rate=sum(r['attacked_seats'] for r in rows)/seats,
            expanded_seat_rate=sum(r['expanded_seats'] for r in rows)/seats,
            recruits=sum(r['recruits'] for r in rows),attacks=sum(r['attacks'] for r in rows),
            disbands=sum(r['disbands'] for r in rows),starved_units=sum(r['starved_units'] for r in rows),
            mean_land=sum(r['mean_land']*r['seats'] for r in rows)/seats,
            mean_nominal_gdp=sum(r['mean_nominal_gdp']*r['seats'] for r in rows)/seats,
            mean_territory_gdp=sum(r['mean_territory_gdp']*r['seats'] for r in rows)/seats)
    recent=window(ordered[-8:]);previous=window(ordered[-16:-8]);flags=[]
    if recent and recent['games']>=64:
        if recent['expanded_seat_rate']==0:flags.append('no_capture_in_last_64_games')
        if previous and previous['games']>=64 and previous['recruited_seat_rate']>0 and recent['recruited_seat_rate']<previous['recruited_seat_rate']*.5:
            flags.append('recruited_seat_rate_below_half_of_previous_window')
    return dict(batches=len(ordered),recent=recent,previous=previous,flags=flags,
                purpose='Report military regression; never changes reward or stops training automatically')


def record_health():
    records=[]
    for path in OUT.rglob('batch-*.json'):
        try:records.append(json.loads(path.read_text()))
        except (OSError,ValueError):continue
    for path in OUT.glob('run-*-live.log'):
        for line in path.read_text(errors='replace').splitlines():
            if '"stage": "batch_complete"' not in line:continue
            try:records.append(json.loads(line))
            except ValueError:continue
    result=military_health(records)
    publish(OUT/'military-health.json',dict(result,time=time.time()))
    return result


def watch_run(stage):
    log=OUT/f'run-{stage:03}-live.log';f=log.open('ab',buffering=0);follower=None
    try:
        while True:
            if follower is None or follower.poll() is not None:
                follower=subprocess.Popen(['kaggle','kernels','logs',KERNEL,'-f'],stdout=f,stderr=subprocess.STDOUT)
            try:
                r=subprocess.run(['kaggle','kernels','status',KERNEL],capture_output=True,text=True,timeout=60)
            except (OSError,subprocess.TimeoutExpired):
                time.sleep(30);continue
            if r.returncode:
                time.sleep(30);continue
            state=r.stdout.strip();live=dict(stage=stage,status=state,time=time.time())
            resume=STAGE/'state-data/resume-state.json'
            if resume.exists():
                committed=json.loads(resume.read_text());live.update(verified_games=committed['games'],goal=committed['contract']['games'])
            for line in reversed(log.read_text(errors='replace').splitlines()):
                if '"stage": "batch_complete"' not in line:continue
                try:live['last_completed_batch']=json.loads(line);break
                except ValueError:pass
            publish(OUT/'live.json',live)
            record_health()
            if 'COMPLETE' in state.upper():return
            if any(x in state.upper() for x in ['ERROR','CANCELLED','CANCELED']):
                raise RuntimeError('Kaggle PPO failed; no automatic speculative retraining: '+state)
            time.sleep(45)
    finally:
        if follower is not None and follower.poll() is None:follower.terminate()
        f.close()


def finish(work,state):
    done=json.loads((work/'done.json').read_text())
    goal=state['contract']['games']
    if done['games']!=goal or done['sha256']!=state['sha256']:raise ValueError('Finite PPO did not finish the requested game count')
    publish(OUT/'done.json',done)
    target=OUT/f'zhanguo-land-gdp-ppo-{goal}-delivery.zip'
    with ZipFile(target.with_suffix('.partial'),'w',ZIP_DEFLATED,compresslevel=2) as z:
        for p in work.iterdir():
            if p.is_file():z.write(p,'results/'+p.name)
        for name in ['kernel.py','run-config.json','README.md']:
            if (STAGE/name).exists():z.write(STAGE/name,'continuation/'+name)
        z.write(ROOT/'land_gdp_ppo_run.py','continuation/land_gdp_ppo_run.py')
    target.with_suffix('.partial').replace(target)
    publish(OUT/'verified.json',dict(verified=True,games=goal,sha256=state['sha256'],package=target.name,time=time.time()))
    publish(OUT/'live.json',dict(state='completed',games=goal,time=time.time()))
    print('All',goal,'PPO games, final evaluation and local verification complete',flush=True)


def main():
    OUT.mkdir(exist_ok=True)
    if (ROOT/'training-user-stop.json').exists():
        print('Training remains stopped at the user request',flush=True);return
    if (OUT/'verified.json').exists():return
    upload_data(STAGE/'code-data',OUT/'code-uploaded.json')
    while True:
        state=json.loads((STAGE/'state-data/resume-state.json').read_text())
        marker_name='state-initial-uploaded.json' if state['games']==0 else f'state-after-{state["games"]:04}.json'
        upload_data(STAGE/'state-data',OUT/marker_name,version=state['games']>0)
        stage=state['games']//state['contract']['segment_games']+1
        marker=OUT/f'submitted-{stage:03}.json'
        if not marker.exists():
            intent=OUT/f'submit-intent-{stage:03}.json'
            active=subprocess.run(['kaggle','kernels','status',KERNEL],capture_output=True,text=True,timeout=60)
            already=intent.exists() and active.returncode==0 and any(s in active.stdout.upper() for s in ['RUNNING','QUEUED'])
            if not already:
                script=STAGE/'kernel.py';text=script.read_text()
                text,n=re.subn(r'^EXPECTED_START_GAMES = .* # SCHEDULER_EXPECTED_START$',
                    'EXPECTED_START_GAMES = '+str(state['games'])+' # SCHEDULER_EXPECTED_START',text,flags=re.M)
                text,m=re.subn(r"^EXPECTED_INPUT_SHA = .* # SCHEDULER_EXPECTED_SHA$",
                    'EXPECTED_INPUT_SHA = '+repr(state['sha256'])+' # SCHEDULER_EXPECTED_SHA',text,flags=re.M)
                if n!=1 or m!=1:raise ValueError('Missing checkpoint identity guard in kernel source')
                script.write_text(text)
                publish(intent,dict(games=state['games'],sha256=state['sha256'],time=time.time()))
                if command(['kaggle','kernels','push','-p',STAGE,'--accelerator','NvidiaTeslaT4','-t','39600'],
                           OUT/f'submit-{stage:03}.log',timeout=180):raise RuntimeError('Cannot queue finite PPO segment')
            publish(marker,dict(games_before=state['games'],sha256=state['sha256'],time=time.time()))
            print('Queued PPO segment',stage,'after',state['games'],'verified games',flush=True)
        watch_run(stage)
        work,new,segment=pull(stage)
        expected=min(state['contract']['games'],stage*state['contract']['segment_games'])
        if new['games']!=expected or new['contract']!=state['contract']:raise ValueError('Unexpected PPO segment game count or contract')
        if segment['finished']:
            finish(work,new);return
        resume=dict(new,latest='resume.pt')
        source=STAGE/'state-data'
        shutil.copyfile(work/new['latest'],source/'resume.pt.partial');(source/'resume.pt.partial').replace(source/'resume.pt')
        publish(source/'resume-state.json',resume)
        if (OUT/'switch-to-ab.json').exists():
            publish(OUT/'switch-ready.json',dict(games=new['games'],sha256=new['sha256'],work=str(work),time=time.time()))
            print('Verified segment ready for authorized A/B migration at',new['games'],'games',flush=True)
            return
        upload_data(source,OUT/f'state-after-{new["games"]:04}.json',version=True)


if __name__=='__main__':
    try:main()
    except Exception as e:
        OUT.mkdir(exist_ok=True)
        publish(OUT/'failure.json',dict(error=type(e).__name__,message=str(e),time=time.time()))
        print('Finite PPO scheduler stopped:',type(e).__name__,str(e),flush=True)
