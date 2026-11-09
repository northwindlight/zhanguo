"""Review the user's round checkpoint; prepare model trials without stopping PPO."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.econ_ppo_watch import write


def improvement(reference,candidate):
    from rl.economy.acceptance import regresses,summary
    before=reference['results'];after=candidate['results']
    return (min(r['total_ratio'] for r in after)>min(r['total_ratio'] for r in before)+1e-9
            and not regresses(summary(after),summary(before)))


def review(out):
    policy=json.loads((out/'model-review-policy.json').read_text())
    checkpoint=policy['after_round'];reference_round=policy['reference_round']
    result=dict(accepted=False,checkpoint_round=checkpoint,reference_round=reference_round,complete=False)
    records={int(p.parent.name.split('-')[-1]):json.loads(p.read_text())
             for p in (out/'live').glob('round-*/candidate.json')}
    if checkpoint not in records:
        return dict(result,status='waiting_for_round',latest_round=max(records,default=0))
    if records[reference_round]['policy_sha256']!=policy['reference_policy_sha256']:
        raise ValueError('Review reference policy changed')
    measured={}
    for p in (out/'evaluations').glob('*/summary.json'):
        report=json.loads(p.read_text());entries=report.get('screen',{}).get('evaluations',[])
        if report.get('phase')!='complete' or not entries:continue
        entry=entries[-1]
        if entry.get('complete') and len(entry.get('results',[]))==2:
            measured[entry['policy_sha256']]=entry
    required=[reference_round,*range(reference_round+1,checkpoint+1)]
    missing=[n for n in required if n not in records or records[n]['policy_sha256'] not in measured]
    if missing:return dict(result,status='waiting_for_evaluations',missing_rounds=missing)
    reference=measured[policy['reference_policy_sha256']]
    improved=[n for n in range(reference_round+1,checkpoint+1)
              if improvement(reference,measured[records[n]['policy_sha256']])]
    result.update(complete=True,improved_rounds=improved,reference_policy_sha256=policy['reference_policy_sha256'],
                  status='continue_current_model' if improved else 'prepare_model_change')
    if not improved:
        folder=out/'model-change-preparation';folder.mkdir(exist_ok=True)
        write(folder/'comparison-plan.json',dict(status='prepared_not_trained',accepted=False,
            reference_policy_sha256=policy['reference_policy_sha256'],
            baseline_checkpoint=str(out/f'live/round-{reference_round:06d}/candidate.pt'),
            prerequisites=['fix candidate padding consistency before enabling extra candidate blocks'],
            variants=[dict(name='pure_candidate_ffn',width=160,depth=2,heads=4,
                           candidate_block='pre-LN residual FFN 160->320->160',last_projection_init='zero',
                           head_pool='context0'),
                      dict(name='larger_context_encoder',width=256,depth=3,heads=4,
                           candidate_block='none',head_pool='context0')],
            exploration_controls=[
                dict(name='more_entropy',changed_factor='entropy_coef',baseline=.001,trial=.005),
                dict(name='weaker_opening_anchor',changed_factor='anchor_coef',baseline=.05,trial=.01),
                dict(name='stronger_execution_auxiliary',changed_factor='execution_coef',baseline=.1,trial=.3)],
            diagnostic_protocol=dict(change_one_factor_at_a_time=True,architecture='unchanged for exploration controls',
                                     measure=['invalid project fraction','successful build/recruit fraction',
                                              'new economic paths','fixed-environment greedy S and seat/tail protection']),
            reward_shaping_candidate=dict(option='--invalid-action-penalty',default=0.,
                                          candidate_values=[.001,.003,.01],review_after_round=20,
                                          automatic_activation=False,evaluation='raw actual S, no penalties applied'),
            controls=dict(teacher_data='same 14 worlds and split',training_budget='matched per comparison',
                          action_mask='padding only',reward='unchanged delta S',
                          evaluation='same greedy worlds, per-seat and tail protection; fresh confirmation'),
            production_change=False,continue_exploratory_training=True))
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();result=review(args.out)
    write(args.out/'model-review-result.json',result)
    print(json.dumps(result),flush=True)


if __name__=='__main__':main()
