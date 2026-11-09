"""Optional update-time penalties; stored environment rewards remain raw delta S."""
import math


def objective(coefficient=0.):
    coefficient=float(coefficient)
    if not math.isfinite(coefficient) or coefficient<0:
        raise ValueError('Invalid-action penalty must be finite and nonnegative')
    return dict(version=1,base='paper_S_consumption_increment',scale=1000.,
                invalid_action_penalty=coefficient,enabled=coefficient>0,
                applies_to='each observed failed build/recruit attempt')


def shape_trajectories(trajectories,coefficient=0.):
    description=objective(coefficient);coefficient=description['invalid_action_penalty']
    shaped=[];count=invalid=0;raw_total=0.
    for stream in trajectories:
        target=[]
        for step in stream:
            kind=step.get('action_kind');executed=step.get('executed')
            if coefficient:
                if kind not in ('finish','build','recruit'):
                    raise ValueError('Penalty requires project action labels')
                if kind!='finish' and type(executed) is not bool:
                    raise ValueError('Penalty requires observed execution outcomes')
            failed=kind in ('build','recruit') and executed is False
            invalid+=int(failed);count+=1;raw=float(step['reward']);raw_total+=raw
            if coefficient:
                item=step.copy()
                item['base_reward']=raw
                item['invalid_action_penalty']=coefficient if failed else 0.
                item['reward']=raw-item['invalid_action_penalty']
                target.append(item)
        if coefficient:shaped.append(target)
    cost=invalid*coefficient
    return (shaped if coefficient else trajectories),dict(objective=description,steps=count,
        invalid_actions=invalid,penalty_total=cost,raw_reward_sum=raw_total,
        training_reward_sum=raw_total-cost)
