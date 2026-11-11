"""Update-time reward shaping; stored environment rewards remain raw delta S.

Two independent switches, both applied only to the training copy:
- ``invalid_action_penalty``: charge each observed failed build/recruit attempt
  (historical, default 0).
- ``credit_mode``: ``turn`` keeps the historical contract, where a period's dS is
  written on its last decision (measured on round 20: 98.8% of the reward landed on
  the trailing finish/other decision, so PPO was being taught that ending the
  economic phase is what pays). ``project`` splits that period's dS equally across
  the successful build/recruit decisions of the same period, so the credit reaches
  the actions that actually did the economy; finishes keep no reward.
"""
import math

CREDIT_MODES=('turn','project')


def objective(coefficient=0.,credit_mode='turn'):
    coefficient=float(coefficient)
    if not math.isfinite(coefficient) or coefficient<0:
        raise ValueError('Invalid-action penalty must be finite and nonnegative')
    if credit_mode not in CREDIT_MODES:raise ValueError('Unknown credit mode')
    return dict(version=2,base='paper_S_consumption_increment',scale=1000.,
                invalid_action_penalty=coefficient,enabled=coefficient>0,credit_mode=credit_mode,
                applies_to='each observed failed build/recruit attempt',
                credit_rule=('period delta-S stays on the trailing decision' if credit_mode=='turn' else
                             'period delta-S split equally across that period\'s successful build/recruit decisions'))


def _periods(stream):
    start=0
    for index in range(1,len(stream)+1):
        if index==len(stream) or stream[index].get('period')!=stream[start].get('period'):
            yield start,index
            start=index


def shape_trajectories(trajectories,coefficient=0.,credit_mode='turn'):
    description=objective(coefficient,credit_mode)
    coefficient=description['invalid_action_penalty']
    rewrite=coefficient>0 or credit_mode!='turn'
    shaped=[];steps=invalid=0
    raw_total=training_total=project_reward=finish_reward=0.
    for stream in trajectories:
        if rewrite:
            target=[dict(step) for step in stream]
            for start,stop in _periods(stream):
                raw_turn=0.
                for index in range(start,stop):
                    step=stream[index];kind=step.get('action_kind');executed=step.get('executed')
                    if coefficient and kind not in ('finish','build','recruit'):
                        raise ValueError('Penalty requires project action labels')
                    if coefficient and kind!='finish' and type(executed) is not bool:
                        raise ValueError('Penalty requires observed execution outcomes')
                    failed=kind in ('build','recruit') and executed is False
                    base=float(step['reward']);raw_turn+=base
                    item=target[index]
                    item['base_reward']=base
                    item['invalid_action_penalty']=coefficient if failed else 0.
                    item['reward']=base-item['invalid_action_penalty']
                if credit_mode=='project':
                    succeeded=[index for index in range(start,stop)
                               if stream[index].get('action_kind') in ('build','recruit')
                               and stream[index].get('executed') is True]
                    if succeeded:
                        share=raw_turn/len(succeeded)
                        for index in range(start,stop):target[index]['reward']=-target[index]['invalid_action_penalty']
                        for index in succeeded:target[index]['reward']+=share
        else:
            target=stream
        for index,step in enumerate(target):
            original=stream[index] if rewrite else step
            steps+=1
            kind=original.get('action_kind');executed=original.get('executed')
            invalid+=int(kind in ('build','recruit') and executed is False)
            raw=float(step['base_reward']) if rewrite else float(step['reward'])
            shaped_reward=float(step['reward'])
            raw_total+=raw;training_total+=shaped_reward
            if kind in ('build','recruit') and executed is True:project_reward+=shaped_reward
            else:finish_reward+=shaped_reward
        if rewrite:shaped.append(target)
    result_streams=shaped if rewrite else trajectories
    return result_streams,dict(objective=description,steps=steps,invalid_actions=invalid,
        penalty_total=invalid*coefficient,raw_reward_sum=raw_total,
        training_reward_sum=training_total,project_reward=project_reward,finish_reward=finish_reward,
        credit_mode=credit_mode)
