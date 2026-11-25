"""Resumable train/evaluate/takeover loop. All acceptance games are unaided 80-turn self-play."""

import argparse
from collections import Counter, defaultdict
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pickle
import random
import re
import subprocess
import sys
import tempfile
import time
from zipfile import ZipFile, ZIP_DEFLATED

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT/'cpp')]
from mixed_bc_run import publish, digest, run, reported_games, BASE_SHA
from read_trajectory import episodes
from expansion_bc_v2 import category


def snapshot(work):
    """Publish checksummed, bounded-size transfer pieces for resumable backup."""
    backup=work/'backup'
    backup.mkdir(exist_ok=True)
    cache=json.loads((work/'inventory.json').read_text()) if (work/'inventory.json').exists() else {}
    for path in sorted(work.glob('epoch-*.pt'))+sorted((work/'chunks').glob('*.zip')):
        relative=path.relative_to(work).as_posix()
        if relative in cache:
            continue
        pieces=[]
        with path.open('rb') as source:
            index=0
            while data:=source.read(7*1024*1024):
                part=backup/f'{path.name}.{index:03}'
                temporary=part.with_suffix(part.suffix+'.partial')
                temporary.write_bytes(data)
                temporary.replace(part)
                pieces.append(dict(name=part.relative_to(work).as_posix(),sha256=digest(part)))
                index+=1
        cache[relative]=dict(sha256=digest(path),parts=pieces)
    publish(work/'inventory.json',cache)


def pack(directory, log, archive, seed, count, production_only=False,industry=False):
    outcomes = {}
    for line in log.read_text().splitlines():
        if line.startswith('expansion_end '):
            row = dict(re.findall(r'(\w+)=(\S+)', line))
            if int(row['seed']) in outcomes:
                raise ValueError('Duplicate takeover outcome')
            outcomes[int(row['seed'])] = row
    expected = set(outcomes) if industry else set(range(seed,seed+count))
    if industry and (len(expected)!=count or min(expected)<seed):
        raise ValueError('Invalid industry map selection')
    if set(outcomes) != expected:
        raise ValueError('Missing takeover outcomes')
    summary = dict(games=count, expanded=0, clean=0, teacher=0, anchor=0, starved_units=0, seed=seed)
    if industry:summary.update(last_seed=max(expected),filtered_maps=sum(line.startswith('curriculum_skip ') for line in log.read_text().splitlines()))
    if production_only:summary.update(teacher_short_turns=0,teacher_deaths=0,supply_purchases=0)
    seen = set()
    caps = dict(setup=20, recruit=24, attack=32, supply=20, materials=16, finance=16, end=12)
    if production_only:
        caps=dict(supply_factory=8,power=8,supply_inputs=16,fuel=12,recruit=16,attack=24,
                  materials=8,finance=8,setup=12,end=12,extraction=16,equipment_factory=8,economy=28,demobilize=8)
    temporary = archive.with_suffix('.partial')
    with ZipFile(temporary, 'w', ZIP_DEFLATED, compresslevel=2) as bundle:
        for decisions, _, meta, features, _ in episodes(directory, mixed_bc=True):
            current = meta['seed']
            if current in seen:
                raise ValueError('Duplicate takeover map')
            seen.add(current)
            row = outcomes[current]
            success = int(row['first_gain']) > 0
            deaths = int(row['starved_units'])
            clean = success and deaths == 0
            if production_only:
                if int(row['sustain_turns'])!=(0 if industry else 10) or int(row['teacher_short_turns'])<0:
                    raise ValueError('Production-only sample lacks sustained teacher validation')
                clean=success and int(row['teacher_short_turns'])==0 and int(row['teacher_deaths'])==0
                if industry and (int(row['industry'])!=1 or (success and int(row['land'])<12)):
                    raise ValueError('Industry curriculum did not use the 12-tile target')
                summary['teacher_short_turns']+=int(row['teacher_short_turns'])
                summary['teacher_deaths']+=int(row['teacher_deaths'])
            grouped = defaultdict(list)
            first_teacher = min((d['ti'] for d in decisions if d['teacher']), default=999)
            for decision in decisions:
                if production_only and decision['teacher']:
                    ob,ac=decision['obs'],decision['a']
                    if int(ob['cand_type'][ac])==4 and ob['cand'][ac,11]>.5:
                        raise ValueError('Forbidden supply purchase in actual tutor trajectory')
                if not clean or not decision['teacher']:
                    continue
                obs, action, turn = (decision[k] for k in ('obs','a','ti'))
                if obs['cand_exec'][action] <= .5:
                    raise ValueError('Illegal tutor action')
                kind = int(obs['cand_type'][action])
                # Retain military infrastructure, inputs, maneuver, finance and turn completion.
                if not industry and kind == 1 and int(obs['cand'][action,6:22].argmax()) not in (6,7,8,9,10,15):
                    continue
                if kind not in ((0,1,2,3,4,5,6,7,8,9,26,27) if industry else (0,1,3,4,5,6,7,8)):
                    continue
                group = 'economy' if industry and decision.get('source')=='economy' else category(obs,action,production_only)
                grouped[group].append((obs,action,turn,group))
            rng = random.Random(current)
            teacher = [label for group, labels in grouped.items()
                       for label in rng.sample(labels, min(len(labels),caps[group]))]
            anchors = [(d['obs'], d['a'], d['ti'], 'anchor') for d in decisions
                       if not d['teacher'] and d['ti'] < first_teacher]
            anchors = rng.sample(anchors,min(16,len(anchors)))
            item = dict(seed=current, features=np.stack(features), teacher=teacher, anchor=anchors,
                        outcome=dict(reached=success,starved=deaths,settled_turns=meta['turns']))
            if production_only:
                item['outcome'].update(production_only=True,teacher_short_turns=int(row['teacher_short_turns']),
                                       teacher_deaths=int(row['teacher_deaths']),sustain_turns=0 if industry else 10)
            if industry:item['outcome'].update(industry=True,land=int(row['land']),setup_turn=int(row['setup_turn']),
                equipment_takeovers=int(row['equipment_takeovers']),heal_takeovers=int(row['heal_takeovers']),
                economic_actions=int(row['economic_actions']),suppressed_actions=int(row['suppressed_actions']),
                allow_ore_import=bool(int(row.get('allow_ore_import',0))))
            bundle.writestr(f'{current}.pkl',pickle.dumps(item,pickle.HIGHEST_PROTOCOL))
            summary['expanded'] += success
            summary['clean'] += clean
            summary['teacher'] += len(teacher)
            summary['anchor'] += len(anchors)
            summary['starved_units'] += deaths
        if seen != expected:
            raise ValueError('Missing takeover maps')
        bundle.writestr('summary.json',json.dumps(summary))
        bundle.write(log,'rollout.log')
    temporary.replace(archive)
    return summary


def summarize(log, maps):
    rows = reported_games(log.read_text().splitlines())
    if len(rows) != maps*4 or len({(r['seed'],r['seat']) for r in rows}) != maps*4:
        raise ValueError('Incomplete or duplicate independent evaluation')
    if any(int(r['turns']) != 80 or int(r['initial_land']) != 5 for r in rows):
        raise ValueError('Evaluation must use full 80-turn games from the standard five-tile opening')
    n = len(rows)
    out = dict(maps=maps,seats=n,turns=80,land=dict(Counter(int(r['land']) for r in rows)),
               gdp=sum(float(r['reward']) for r in rows)/(80*n),
               recruit_rate=sum(int(r['recruits']) > 0 for r in rows)/n,
               attack_rate=sum(int(r['attacks']) > 0 for r in rows)/n,
               captured_rate=sum(int(r['first_capture']) > 0 for r in rows)/n,
               two_capture_rate=sum(int(r['first_two']) > 0 for r in rows)/n,
               retained_two_rate=sum(int(r['land']) >= int(r['initial_land'])+2 for r in rows)/n,
               starved_rate=sum(int(r['starved_units']) > 0 for r in rows)/n,
               starved_units=sum(int(r['starved_units']) for r in rows),
               supply_short_turns=sum(int(r['supply_short_turns']) for r in rows),
               blackout_rate=sum(int(r['blackout']) for r in rows)/(80*n))
    for key in ('first_recruit','first_attack','first_capture','first_two'):
        values = [int(r[key]) for r in rows if int(r[key]) > 0]
        out[key+'_median'] = float(np.median(values)) if values else None
    return out


def evaluate(sampler, weights, prefix, maps, seed, env):
    report = prefix.with_suffix('.json')
    if report.exists():
        return json.loads(report.read_text())
    log = prefix.with_suffix('.log')
    run(prefix.name,[sampler,'sample',weights,'cuda',maps,maps,24,80,seed,8,'fp32',
                     '--market4-selfplay'],log,env)
    result = summarize(log,maps)
    publish(report,result)
    return result


def accepted(result, baseline):
    return (result['captured_rate'] >= .75 and result['retained_two_rate'] >= .5
            and result['starved_rate'] <= .05 and result['gdp'] >= .8*baseline['gdp'])


def export(checkpoint, native, python_root, env):
    if not native.exists():
        run('export '+checkpoint.stem,[sys.executable,ROOT/'cpp/export_policy.py',checkpoint,native,
                                      '--python-root',python_root],native.with_suffix('.log'),env)


def collect(sampler,native,work,seed,first,count,env,label):
    archive=work/'chunks'/f'chunk-{seed}.zip'
    if archive.exists():return
    with tempfile.TemporaryDirectory(dir=work,prefix='recover-') as temp:
        directory=Path(temp)
        log=directory/'sample.log'
        run(label,[sampler,'sample',native,'cuda',count,count,24,80,seed,8,
            'fp32',directory,'--market4-selfplay','--mixed-bc',first,60],log,env)
        result=pack(directory,log,archive,seed,count)
        snapshot(work)
        print(json.dumps(dict(stage='recovery',first=first,**result)),flush=True)


def campaign(args):
    work, initial = args.output.resolve(), args.existing.resolve()
    work.mkdir(parents=True,exist_ok=True)
    (work/'chunks').mkdir(exist_ok=True)
    for source,destination in ((initial/'base.pt',work/'base.pt'),(initial/'base-native.pt',work/'base-native.pt')):
        if not destination.exists():
            destination.symlink_to(source)
    base, base_native = work/'base.pt', work/'base-native.pt'
    if digest(base) != BASE_SHA:
        raise ValueError('Unexpected economic base')
    old_report = json.loads((initial/'done.json').read_text())
    if old_report['games'] != 1000 or len(list((initial/'chunks').glob('chunk-*.zip'))) != 32:
        raise ValueError('Incomplete original 1000-world dataset')
    env = os.environ.copy()
    env['NVIDIA_TF32_OVERRIDE']='0'
    env.pop('TORCH_ALLOW_TF32_CUBLAS_OVERRIDE',None)
    sampler = work/'build/zhanguo_torch'
    if not sampler.exists():
        run('sampler build',[sys.executable,ROOT/'cpp/build_colab.py','--build',work/'build','--jobs',4],
            work/'build.log',env)
    baseline = evaluate(sampler,base_native,work/'baseline',12,270000,env)
    # The old pack discarded turn completion and some prerequisites. Add a small,
    # complete set before the first update, while the original economy is intact.
    publish(work/'progress.json',dict(state='collecting_prerequisites',epoch=0,base_games=1000))
    for index,first in enumerate((12,20,28)):
        collect(sampler,base_native,work,259000+index*100,first,8,env,
                f'prerequisite takeover from turn {first}')
    current = base
    best = json.loads((work/'best.json').read_text()) if (work/'best.json').exists() else None
    if not (work/'budget.json').exists():
        publish(work/'budget.json',dict(deadline=time.time()+args.hours*3600))
    deadline=json.loads((work/'budget.json').read_text())['deadline']
    saved=[int(p.stem.split('-')[1]) for p in work.glob('epoch-[0-9][0-9].pt')]
    first_epoch=max(saved,default=1)
    for epoch in range(first_epoch,args.max_epochs+1):
        # This budget covers training and evaluation on a single attached runtime.
        if time.time() > deadline:
            break
        checkpoint = work/f'epoch-{epoch:02}.pt'
        native = work/f'epoch-{epoch:02}-native.pt'
        status = dict(state='training',epoch=epoch,max_epochs=args.max_epochs,base_games=1000,
                      recovery_chunks=sorted(p.name for p in (work/'chunks').glob('chunk-*.zip')))
        publish(work/'progress.json',status)
        if not checkpoint.exists():
            run(f'balanced epoch {epoch}',[sys.executable,ROOT/'expansion_bc_v2.py',
                '--base',base,'--initial',current,'--output',checkpoint,'--data',initial/'chunks',work/'chunks',
                '--python-root',args.python_root,'--epoch',epoch],work/f'epoch-{epoch:02}-train.log',env)
        current = checkpoint
        snapshot(work)
        if epoch % 2:
            continue
        export(checkpoint,native,args.python_root,env)
        publish(work/'progress.json',dict(status,state='evaluating'))
        result = evaluate(sampler,native,work/f'epoch-{epoch:02}-eval',12,270000,env)
        score = (result['retained_two_rate'] + .5*result['captured_rate'] - result['starved_rate']
                 + .1*result['recruit_rate'] + .2*result['attack_rate']
                 + .05*result['gdp']/max(baseline['gdp'],1e-6))
        economy_ok = result['gdp'] >= .8*baseline['gdp']
        if best is None or (economy_ok and not best['economy_ok']) or (economy_ok == best['economy_ok'] and score > best['score']):
            best = dict(epoch=epoch,checkpoint=checkpoint.name,native=native.name,result=result,
                        score=score,economy_ok=economy_ok)
        publish(work/'best.json',best)
        print(json.dumps(dict(stage='unaided',epoch=epoch,**result)),flush=True)
        if accepted(result,baseline):
            break
        if epoch in (2,4,6):
            # On-policy prefixes followed by actual legal W3 takeover, never synthetic labels.
            publish(work/'progress.json',dict(status,state='collecting_recovery'))
            for index,first in enumerate((12,20,28)):
                seed=260000+epoch*1000+index*100
                collect(sampler,native,work,seed,first,16,env,f'recovery epoch {epoch} from turn {first}')
    if best is None:
        raise RuntimeError('Training budget expired before first independent evaluation')
    publish(work/'progress.json',dict(state='final_evaluation',epoch=best['epoch']))
    # Fresh maps used only for acceptance, never for selecting epochs or demonstrations.
    final_base=evaluate(sampler,base_native,work/'final-base',24,280000,env)
    final=evaluate(sampler,work/best['native'],work/'final-model',24,280000,env)
    checkpoint,native=work/best['checkpoint'],work/best['native']
    snapshot(work)
    done=dict(state='completed',accepted=accepted(final,final_base),selected=best,baseline=final_base,
              evaluation=final,checkpoint=checkpoint.name,sha256=digest(checkpoint),native=native.name,
              native_sha256=digest(native),base_games=1000,recovery_chunks=sorted(p.name for p in (work/'chunks').glob('chunk-*.zip')))
    publish(work/'done.json',done)
    publish(work/'progress.json',dict(state='completed',accepted=done['accepted'],epoch=best['epoch']))


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--existing',type=Path,required=True)
    p.add_argument('--python-root',type=Path,required=True)
    p.add_argument('--max-epochs',type=int,default=10)
    p.add_argument('--hours',type=float,default=3)
    args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with (args.output/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if not (args.output/'done.json').exists():
            campaign(args)


if __name__=='__main__':
    main()
