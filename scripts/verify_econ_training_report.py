"""Independently verify the frozen report without original weights or PyTorch.

Checks copied source hashes, continuous policy ancestry, raw optimizer logs,
world totals and evaluation seat arithmetic. Referenced weights are explicitly
not reverified here: their original cold-load audit is supplied as evidence.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    root=parser.parse_args().root.resolve()
    def read(path):return json.loads(path.read_text())
    def table(name):return list(csv.DictReader((root/'tables'/name).open()))
    def same(a,b):
        if not math.isclose(float(a),float(b),rel_tol=1e-9,abs_tol=1e-9):raise ValueError(f'{a} != {b}')
    manifest=root/'SHA256SUMS'
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            digest,name=line.split('  ',1);path=(root/name).resolve()
            if not path.is_relative_to(root):raise ValueError('Invalid manifest path')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:raise ValueError('Archive checksum mismatch: '+name)
    source_rows=table('sources.csv');copied=0;referenced=0
    for row in source_rows:
        if row.get('weight_not_copied')=='True':referenced+=1;continue
        path=root/'frozen'/row['path']
        if hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Frozen source mismatch')
        same(path.stat().st_size,row['bytes']);copied+=1
    rows=table('rounds.csv');seats=table('evaluation_seats.csv');summary=read(root/'report-data.json')
    base=root/'frozen/archive/previews/ppo-continue-20261107';previous=None
    decisions=updates=worlds=0;actions=dict(failures=0,build=0,recruit=0,finish=0)
    for row in rows:
        n=int(row['round']);folder=base/f'live/round-{n:06d}'
        record=read(folder/'candidate.json');config=read(folder/'manifest.json');metrics=record['metrics']
        if record['policy_sha256']!=row['policy_sha256']:raise ValueError('Wrong selected policy')
        if previous and config['behavior_policy_sha256']!=previous:raise ValueError('Broken policy ancestry')
        previous=record['policy_sha256']
        if not (metrics['coverage']==1 and metrics['epochs_completed']==1 and not metrics['kl_stopped']):
            raise ValueError('Incomplete update')
        logs=[json.loads(s)for s in (folder/'updates.jsonl').read_text().splitlines()]
        same(len(logs),metrics['updates']);same(logs[-1]['examples_used'],metrics['steps'])
        count=0
        for case in config['cases']:
            report=read(folder/f"report-{case['seed']}.json");worlds+=1;count+=report['decisions']
            countries=report['countries'].values();failed=sum(c['project_failures']for c in countries)
            build=sum(c['economic_actions'].get('build',0)for c in countries)
            recruit=sum(c['economic_actions'].get('recruit',0)for c in countries)
            finish=report['decisions']-failed-build-recruit
            if finish<0:raise ValueError('Invalid action partition')
            for key,value in dict(failures=failed,build=build,recruit=recruit,finish=finish).items():actions[key]+=value
        same(count,metrics['steps']);same(count,metrics['examples_used']);decisions+=count;updates+=len(logs)
        for seed in (8101,8102):
            selected=[s for s in seats if int(s['round'])==n and int(s['seed'])==seed]
            if len(selected)!=4:raise ValueError('Missing evaluation seat')
            same(sum(float(s['student_S'])for s in selected)/sum(float(s['teacher_S'])for s in selected),row[f'S_ratio_{seed}'])
            same(min(float(s['S_ratio'])for s in selected),row[f'worst_seat_{seed}'])
            same(min(float(s['tail_ratio'])for s in selected),row[f'worst_tail_{seed}'])
    same(decisions,summary['aggregate_training']['decisions']);same(updates,summary['total_updates'])
    for key,value in actions.items():same(value,summary['aggregate_training'][key])
    same(len(rows),summary['round_count']);same(worlds,summary['world_count'])
    print(json.dumps(dict(passed=True,completed_rounds=len(rows),worlds=worlds,decisions=decisions,updates=updates,
                          copied_sources_verified=copied,weight_references_not_reverified=referenced),indent=2))


if __name__=='__main__':main()
