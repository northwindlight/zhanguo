"""Resume paired economic evaluations; report every environment without promotion."""
import argparse
import json
from pathlib import Path

import torch

from .acceptance import TARGET_RATIO, cases, compare, regresses, summary
from .evaluate import run
from .model import load_policy
from .overnight import write_json
from .provenance import contracts, feature_equivalences, inference_implementation, require_compatible
from .scenarios import config
from .trajectories import model_digest


SCREEN = [dict(seed=8101, size=20, turns=150, swap=False),
          dict(seed=8102, size=24, turns=150, swap=True)]


def cache_identity_matches(actual, expected):
    if actual == expected:
        return True
    if not isinstance(actual,dict) or actual.keys()!=expected.keys():
        return False
    if any(actual[k]!=v for k,v in expected.items() if k!='runtime'):
        return False
    before,after=actual['runtime'],expected['runtime']
    if not isinstance(before,dict) or before.keys()!=after.keys():
        return False
    if any(before[k]!=v for k,v in after.items() if k!='implementation'):
        return False
    transition=dict(before=before['implementation'],after=after['implementation'])
    return any(record.get('implementation_transition')==transition and
               record.get('feature_contract')==expected['contracts']['feature_contract']
               for record in feature_equivalences().values())


def check_teacher(report, case, expected):
    if require_compatible(report, expected)['teacher_contract'] != expected['teacher_contract']:
        raise ValueError('Teacher policy contract mismatch')
    if any(report.get(k) != case[k] for k in ('seed', 'turns', 'swap')):
        raise ValueError('Teacher environment mismatch')
    if report.get('config') != config(case['seed'], size=case['size'], swap=case['swap']):
        raise ValueError('Teacher configuration mismatch')


def assessment(results, baseline=None, expected_cases=len(SCREEN)):
    """A diagnostic ranking cannot generate an accepted policy."""
    complete = len(results) == expected_cases
    protected = complete and (baseline is None or not regresses(summary(results), summary(baseline)))
    return dict(complete=complete, cases=len(results), expected_cases=expected_cases,
                worst_environment_total=min((r['total_ratio'] for r in results), default=0.),
                worst_seat=min((r['worst_ratio'] for r in results), default=0.),
                worst_tail=min((r['worst_tail_ratio'] for r in results), default=0.),
                baseline_protected=protected,
                target_met_on_screen=complete and all(r['surpass'] for r in results),
                accepted=False, results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', type=Path, nargs='*', default=[])
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--formal', action='store_true', help='Run all six 150/200/250-turn selection environments')
    parser.add_argument('--target-ratio', type=float, default=TARGET_RATIO)
    args = parser.parse_args()
    if args.threads < 1 or args.target_ratio <= 0:
        parser.error('threads and target-ratio must be positive')
    paths = [args.baseline, *args.checkpoint]
    manifest = cases() if args.formal else SCREEN
    # Validate all inputs before spending time on long games.
    expected = contracts()
    for path in paths:
        _, saved = load_policy(path)
        require_compatible(saved, expected)
        seen = set(saved.get('seen_seeds', [])) | set(saved.get('train_seeds', [])) | set(saved.get('valid_seeds', []))
        if seen & {case['seed'] for case in manifest}:
            raise ValueError(f'Evaluation seeds overlap checkpoint ancestry: {path}')
    torch.set_num_threads(args.threads)
    args.out.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    runtime = dict(implementation=inference_implementation(), torch=str(torch.__version__), threads=args.threads)
    evaluations = []
    try:
        for path in paths:
            model, _ = load_policy(path)
            digest = model_digest(model)
            results = []
            entry = dict(checkpoint=str(path.resolve()), policy_sha256=digest, results=results)
            evaluations.append(entry)
            for case in manifest:
                key = f"{case['seed']}-{case['size']}-{case['turns']}-{int(case['swap'])}"
                write_json(args.out/'state.json', dict(phase='evaluating', checkpoint=str(path), case=case))
                teacher_path = args.cache/f'{key}.json'
                if teacher_path.exists():
                    teacher = json.loads(teacher_path.read_text())
                else:
                    teacher = run(**case)
                    write_json(teacher_path, teacher)
                check_teacher(teacher, case, expected)
                student_path = args.out/f'{key}-{digest[:16]}.json'
                cache_identity = dict(runtime=runtime, case=case, policy_sha256=digest, contracts=expected)
                if student_path.exists():
                    cached = json.loads(student_path.read_text())
                    if not cache_identity_matches(cached.get('identity'),cache_identity):
                        raise ValueError(f'Student cache identity mismatch: {student_path}')
                    student = cached['student']
                    require_compatible(student, expected)
                else:
                    student = run(**case, model=model)
                    write_json(student_path, dict(identity=cache_identity, student=student))
                result = dict(**case, **compare(teacher, student, args.target_ratio))
                results.append(result)
                print(json.dumps(dict(checkpoint=str(path), **result), ensure_ascii=False), flush=True)
                entry.update(assessment(results, evaluations[0]['results'] if len(evaluations)>1 else None, len(manifest)))
                write_json(args.out/'summary.json', dict(phase='evaluating', accepted=False, evaluations=evaluations))
        protected = [e for e in evaluations[1:] if e['baseline_protected']]
        selected = max(protected, key=lambda e:e['worst_environment_total'])['checkpoint'] if protected else None
        write_json(args.out/'summary.json', dict(phase='complete', accepted=False,
                   selection='six-environment selection; fresh confirmation still required' if args.formal else
                             'two-environment screen; formal six environments and fresh confirmation still required',
                   target_ratio=args.target_ratio, baseline=str(args.baseline.resolve()),
                   protected_candidate=selected, evaluations=evaluations))
        write_json(args.out/'state.json', dict(phase='complete', accepted=False, protected_candidate=selected))
    except BaseException as error:
        write_json(args.out/'state.json', dict(phase='failed', accepted=False, error=repr(error)))
        raise


if __name__ == '__main__':
    main()
