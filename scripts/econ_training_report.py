"""Freeze completed PPO evidence and export hash-matched, auditable report tables.

Read-only with respect to training. No model updates, evaluations or allocations.
Only explicit experiment metrics and code are copied; CLI/auth logs are excluded.
"""
import argparse
import csv
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def sha(data):return hashlib.sha256(data).hexdigest()


class Evidence:
    def __init__(self,out):
        self.out=out;self.sources={}

    def read(self,path):
        path=path.resolve();relative=str(path.relative_to(ROOT))
        destination=self.out/'frozen'/relative
        if relative not in self.sources:
            data=path.read_bytes();destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(data)
            self.sources[relative]=dict(path=relative,bytes=len(data),sha256=sha(data))
        return destination.read_bytes()

    def json(self,path):return json.loads(self.read(path))

    def weight(self,path,expected):
        data=path.read_bytes()
        if sha(data)!=expected:raise ValueError('Checkpoint checksum mismatch: '+str(path))
        relative=str(path.relative_to(ROOT))
        self.sources[relative]=dict(path=relative,bytes=len(data),sha256=expected,weight_not_copied=True)


def table(out,name,rows):
    path=out/'tables'/name;path.parent.mkdir(exist_ok=True)
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys,lineterminator='\n');writer.writeheader();writer.writerows(rows)


def same(actual,expected,label):
    if not math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-9):
        raise ValueError(f'{label}: {actual} != {expected}')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--rounds',type=int,default=20)
    p.add_argument('--base',type=Path,default=ROOT/'archive/previews/ppo-continue-20261107')
    args=p.parse_args();args.out=args.out.resolve();args.out.mkdir(parents=True,exist_ok=False)
    evidence=Evidence(args.out);base=args.base.resolve();read=evidence.json
    metadata=dict(frozen_at=datetime.now(timezone.utc).isoformat(),through_round=args.rounds,
                  git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  scope='Completed continuous PPO chain only; historical duplicates separately identified')
    screens={};duplicates=[]
    for path in sorted((base/'evaluations').glob('*/screen.json')):
        report=read(path)
        if report['phase']!='complete':continue
        for entry in report['evaluations']:
            if not entry.get('complete'):continue
            key=entry['policy_sha256']
            if key in screens:
                if screens[key][0]['results']!=entry['results']:
                    raise ValueError('Conflicting evaluations for policy '+key)
            else:screens[key]=(entry,path)
    teacher_root=ROOT/'archive/previews/econ-handoff-20261107/teachers'
    rounds=[];worlds=[];train_seats=[];eval_seats=[];updates=[];windows=[];histories=[];identities=[];gradients=[]
    policies=set();training_seeds=set();contract_pairs=set();budget_hits=[];previous_policy=None
    aggregate_training=dict(decisions=0,failures=0,build=0,recruit=0,finish=0,S=0.)
    for number in range(1,args.rounds+1):
        folder=base/f'live/round-{number:06d}'
        record=read(folder/'candidate.json');metrics=record['metrics'];policy=record['policy_sha256']
        if record['round']!=number:raise ValueError('Round number mismatch')
        evidence.weight(folder/'candidate.pt',record['sha256'])
        if policy not in screens:raise ValueError('Missing matched evaluation: '+policy)
        entry,screen_path=screens[policy]
        if len(entry['results'])!=2:raise ValueError('Incomplete two-world screen')
        policies.add(policy)
        manifest=read(folder/'manifest.json');reports=[];manifest_steps=0
        if previous_policy is not None and manifest['behavior_policy_sha256']!=previous_policy:
            raise ValueError('Round ancestry does not follow the selected preceding checkpoint')
        previous_policy=policy
        for case in manifest['cases']:
            if case['seed'] in training_seeds:raise ValueError('Training seed reused')
            training_seeds.add(case['seed'])
            report=read(folder/f"report-{case['seed']}.json");reports.append(report)
            if any(report[k]!=case[k] for k in ('seed','size','turns','swap','mode')):
                raise ValueError('World configuration mismatch')
            contract_pairs.add((report['feature_contract'],report['teacher_contract']))
            manifest_steps+=report['decisions']
            actions=dict(build=0,recruit=0);failures=0;consumption=sum(report['consumption'].values())
            for actor,country in report['countries'].items():
                failed=country['project_failures'];failures+=failed
                for kind in actions:actions[kind]+=country['economic_actions'].get(kind,0)
                milestone=report['milestones'].get(actor,{})
                success=report['success_metrics'][actor]['successful_projects']
                same(success,sum(country['economic_actions'].values()),'Successful action count')
                train_seats.append(dict(round=number,seed=case['seed'],size=case['size'],swap=case['swap'],actor=actor,
                    profile=country['profile'],S=report['consumption'][actor],tail_S_per_turn=report['tail_consumption'][actor],
                    failed_projects=failed,build=country['economic_actions'].get('build',0),
                    recruit=country['economic_actions'].get('recruit',0),truncated=country['truncated'],
                    receipt_failures=sum(country['failures'].values()),land=country['land'],army=country['army'],
                    buildings=country['buildings'],gold=country['gold'],investment=country['investment'],
                    first_recruit=milestone.get('first_recruit'),first_expansion=milestone.get('first_expansion'),
                    total_S_per_successful_project=report['success_metrics'][actor]['total_S_per_successful_project'],
                    success_period_S_per_project=report['success_metrics'][actor]['success_period_S_per_project'],
                    S_in_periods_without_success=report['success_metrics'][actor]['S_in_periods_without_success']))
            finish=report['decisions']-failures-sum(actions.values())
            if finish<0:raise ValueError('Action partition exceeds decisions')
            world=dict(round=number,**case,decisions=report['decisions'],failed_projects=failures,
                       build=actions['build'],recruit=actions['recruit'],finish=finish,S=consumption,
                       seconds=report['seconds'],family_agreement=report['family_agreement'],
                       receipt_failures=sum(sum(c['failures'].values())for c in report['countries'].values()),
                       truncated=sum(c['truncated']for c in report['countries'].values()),
                       row_budget=report['row_budget'])
            worlds.append(world)
            if report['decisions']>=report['row_budget']:budget_hits.append((number,case['seed']))
        if len(reports)!=8:raise ValueError('Expected eight training worlds')
        same(manifest_steps,metrics['steps'],'Rollout/PPO decision count')
        same(metrics['examples_used'],manifest_steps,'Consumed examples')
        same(metrics['unique_examples_used'],manifest_steps,'Unique consumed examples')
        if metrics['coverage']!=1 or metrics['epochs_completed']!=1 or metrics['kl_stopped']:
            raise ValueError('Incomplete PPO update')
        uw=[json.loads(line) for line in evidence.read(folder/'updates.jsonl').decode().splitlines()]
        same(len(uw),metrics['updates'],'Update log length')
        same(uw[-1]['examples_used'],metrics['examples_used'],'Update log coverage')
        for row in uw:
            updates.append(dict(round=number,**{k:v for k,v in row.items() if isinstance(v,(int,float,bool,str))}))
            for term,detail in row.get('gradients',{}).items():
                gradients.append(dict(round=number,update=row['updates'],term=term,
                                      scope=row['gradient_scope'],**detail))
        for index,row in enumerate(metrics['kl_windows']):windows.append(dict(round=number,window=index,**row))
        totals=dict(decisions=manifest_steps,failures=sum(w['failed_projects']for w in worlds if w['round']==number),
                    build=sum(w['build']for w in worlds if w['round']==number),
                    recruit=sum(w['recruit']for w in worlds if w['round']==number),
                    finish=sum(w['finish']for w in worlds if w['round']==number),
                    S=sum(w['S']for w in worlds if w['round']==number))
        for key,value in totals.items():aggregate_training[key]+=value
        row=dict(round=number,policy_sha256=policy,checkpoint_sha256=record['sha256'],screen=str(screen_path.relative_to(ROOT)),
                 **{k:v for k,v in metrics.items() if isinstance(v,(int,float,bool,str))},
                 failed_projects=totals['failures'],successful_build=totals['build'],successful_recruit=totals['recruit'],
                 finish=totals['finish'],failed_fraction=totals['failures']/manifest_steps,
                 successful_project_fraction=(totals['build']+totals['recruit'])/manifest_steps,
                 sampled_total_S=totals['S'],sampled_S_per_successful_project=totals['S']/(totals['build']+totals['recruit']),
                 logp_guard_max_error=metrics['behavior_logp_max_error'])
        for case in entry['results']:
            key=f"{case['seed']}-{case['size']}-{case['turns']}-{int(case['swap'])}"
            student_path=base/'evaluation-cache'/f'{key}-{policy[:16]}.json'
            cached=read(student_path);identity=cached['identity'];student=cached['student']
            teacher=read(teacher_root/f'{key}.json')
            if identity['policy_sha256']!=policy:raise ValueError('Evaluation cache policy mismatch')
            identities.append(dict(round=number,seed=case['seed'],**identity))
            expected_case={k:case[k] for k in ('seed','size','turns','swap')}
            if identity['case']!=expected_case:raise ValueError('Evaluation cache case mismatch')
            if case['seed'] in training_seeds:raise ValueError('Evaluation seed seen in training')
            same(case['total_ratio'],sum(s['student_S']for s in case['seats'])/sum(s['teacher_S']for s in case['seats']),'Screen total ratio')
            row[f"S_ratio_{case['seed']}"]=case['total_ratio']
            row[f"worst_seat_{case['seed']}"]=case['worst_ratio']
            row[f"worst_tail_{case['seed']}"]=case['worst_tail_ratio']
            row[f"near_{case['seed']}"]=case['near'];row[f"surpass_{case['seed']}"]=case['surpass']
            for seat in case['seats']:
                actor=seat['actor'];sc=student['history'][-1]['countries'][actor]
                tc=teacher['history'][-1]['countries'][actor]
                same(seat['student_S'],student['consumption'][actor],'Student S source')
                same(seat['teacher_S'],teacher['consumption'][actor],'Teacher S source')
                same(seat['tail_ratio'],student['tail_consumption'][actor]/teacher['tail_consumption'][actor],'Tail source')
                extra={}
                for label,game,c in [('student',student,sc),('teacher',teacher,tc)]:
                    at100=next(x['countries'][actor] for x in game['history'] if x['turn']==100)
                    extra.update({label+'_land_100':at100['land'],label+'_land_150':c['land'],
                                  label+'_army_150':c['army'],label+'_buildings_150':c['buildings'],
                                  label+'_gold_150':c['gold'],label+'_investment':c['investment'],
                                  label+'_tail_S_per_turn':game['tail_consumption'][actor]})
                eval_seats.append(dict(round=number,seed=case['seed'],size=case['size'],swap=case['swap'],**seat,**extra))
            for period in student['history']:
                actors=[s['actor'] for s in case['seats']]
                baseline=next(x for x in teacher['history'] if x['turn']==period['turn'])
                histories.append(dict(round=number,seed=case['seed'],turn=period['turn'],
                    student_S=sum(period['countries'][a]['consumption']for a in actors),
                    teacher_S=sum(baseline['countries'][a]['consumption']for a in actors),
                    student_land=sum(period['countries'][a]['land']for a in actors),
                    teacher_land=sum(baseline['countries'][a]['land']for a in actors)))
        row['weighted_value_loss']=.5*metrics['value'];row['weighted_execution_loss']=.1*metrics['execution']
        row['weighted_anchor_loss']=.05*metrics['anchor'];row['weighted_entropy_loss']=-.001*metrics['entropy']
        row['weighted_kl_loss']=metrics['loss']-(metrics['policy']+row['weighted_value_loss']+row['weighted_execution_loss']+
                                                   row['weighted_anchor_loss']+row['weighted_entropy_loss'])
        row['worst_environment_total']=min(r['total_ratio']for r in entry['results'])
        row['worst_seat']=entry['worst_seat'];row['worst_tail']=entry['worst_tail']
        rounds.append(row)
    if len(contract_pairs)!=1:raise ValueError('Mixed feature/teacher contracts')
    for policy,(entry,path) in screens.items():
        if policy not in policies:
            duplicates.append(dict(policy_sha256=policy,checkpoint=entry['checkpoint'],screen=str(path.relative_to(ROOT)),
                S_ratios=[r['total_ratio']for r in entry['results']],note='Historical or BC baseline; excluded from continuous-chain totals'))
    bcroot=ROOT/'archive/previews/econ14-20261107'
    bcrun=read(bcroot/'live/run.json');bcmetrics=[json.loads(s)for s in evidence.read(bcroot/'live/metrics.jsonl').decode().splitlines()]
    bceval=read(bcroot/'evaluation/summary.json');bctable=[]
    for x in bcmetrics:
        bctable.append(dict(epoch=x['epoch'],seconds=x['seconds'],train_loss=x['train']['loss'],valid_loss=x['valid']['loss'],
                            valid_accuracy=x['valid']['accuracy'],valid_build_accuracy=x['valid']['by_kind']['build']['accuracy'],
                            valid_recruit_accuracy=x['valid']['by_kind']['recruit']['accuracy'],valid_finish_accuracy=x['valid']['by_kind']['finish']['accuracy'],
                            valid_family_accuracy=x['valid']['family_accuracy'],valid_exec_loss=x['valid']['exec_loss']))
    for rel in ['live/run.json','model-review-policy.json','model-review-result.json','model-change-preparation/comparison-plan.json',
                'ecs-watch-state.json','watch-state.json','native-tpu-probe-state.json','all-quota-now.json','ecs-recovery.json',
                'colab-watch-20261110.log','ecs-watch.log']:
        if (base/rel).exists():evidence.read(base/rel)
    historical={}
    for rel in ['ppo-parameter-ablation-20261103/results.json','ppo-parameter-ablation-20261103/sample-audit.json',
                'candidate-ffn-audit-20261109/result.json','econ14-20261107/input-audit.json',
                'inference-profile-20261107/pi-engine.json','inference-profile-20261107/ecs-engine.json',
                'inference-profile-20261107/pi-controlled-forward.json','inference-profile-20261107/ecs-forward.json']:
        path=ROOT/'archive/previews'/rel
        if path.exists():historical[rel]=read(path)
    for name in ['acceptance.py','model.py','layers.py','online.py','ppo_cycle.py','scenarios.py','trajectories.py','reward_shaping.py']:
        evidence.read(ROOT/'rl/economy'/name)
    source_rows=list(evidence.sources.values())
    tables=dict(rounds=rounds,training_worlds=worlds,training_seats=train_seats,evaluation_seats=eval_seats,
                updates=updates,kl_windows=windows,gradient_probes=gradients,
                evaluation_history=histories,bc_epochs=bctable,sources=source_rows)
    for name,rows in tables.items():table(args.out,name+'.csv',rows)
    comparison={}
    legacy=historical['ppo-parameter-ablation-20261103/results.json']
    for name,arm in legacy['arms'].items():
        m=arm['metrics'];comparison[name]=dict(updates=m['updates'],examples_used=m['examples_used'],
            coverage=m['examples_used']/m['steps'],kl_stopped=m['kl_stopped'])
    result=dict(metadata=metadata,aggregate_training=aggregate_training,
        round_count=len(rounds),world_count=len(worlds),evaluation_seat_count=len(eval_seats),
        total_updates=sum(r['updates']for r in rounds),contracts=list(contract_pairs),
        training_seed_count=len(training_seeds),budget_hits=budget_hits,
        current_chain_policies=[dict(round=r['round'],policy_sha256=r['policy_sha256'],checkpoint_sha256=r['checkpoint_sha256'])for r in rounds],
        excluded_historical=duplicates,evaluation_identities=identities,bc_run=bcrun,bc_evaluations=bceval,
        legacy_ablation=comparison,historical=historical,
        checks=dict(weight_file_sha=True,matched_policy_evaluations=True,source_S_recalculated=True,continuous_policy_ancestry=True,
                    decision_partition=True,coverage_and_update_logs=True,unique_training_seeds=True),
        caveats=['Raw older trajectories were pruned by retention; complete reports/updates/weights are retained',
                 'Loss and action fractions use different on-policy worlds each round, not a fixed validation distribution',
                 'Two selection worlds are repeatedly consulted; no untouched generalization estimate or confidence interval'])
    (args.out/'report-data.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(out=str(args.out),rounds=len(rounds),worlds=len(worlds),updates=result['total_updates'],
                         decisions=aggregate_training['decisions'],sources=len(source_rows)),ensure_ascii=False))


if __name__=='__main__':main()
