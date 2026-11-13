"""Explain formal recruitment/funding blockers at an offline boundary."""
import argparse
import json
import math
from pathlib import Path

import torch

from host import GameHost
from rl.economy.model import collate,load_policy,prepare
from rl.economy.projects import InvestmentPlanner
from rl.economy.scenarios import config
from ruleai.v13.entry import RuleClient
from ruleai.v13.economy import stocks,quote,GOLD


class Done(Exception):
    pass


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--load',type=Path,required=True)
    p.add_argument('--checkpoint',type=Path,nargs='+',required=True)
    p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();torch.set_num_threads(1)
    models=[(str(path),load_policy(path)[0]) for path in a.checkpoint]
    host=GameHost.load(a.load,config(8102,size=24,swap=True))
    assert host.turn_order()[0]=='scenario:n0', 'Audit expects n0 to move first'
    result={}
    class Inspect(RuleClient):
        def run_economy(self,ledger):
            ledger.economy_market=ledger.query('market')
            planner=InvestmentPlanner(ledger,self.state)
            planner.operations()
            encoded=planner.encode();wallet=stocks(ledger)
            result.update(period=ledger.player.observe()['period'],wallet=wallet,
                energy=ledger.player.observe()['facts']['econ']['preview']['energy'],
                operations_receipts=ledger.acts[1:],recruits=[],models=[])
            for site in planner.sites['sites']:
                query=dict(zip(('x','y'),site['position']))
                for choice in ledger.player.call_api('native:army','planning',query):
                    if not choice['has_facility']:continue
                    args=dict(site=site['id'],kind=choice['kind'],count=1)
                    index=encoded['actions'].index(('recruit',args))
                    package=encoded['packages'][index]
                    entry=dict(site=site['id'],kind=choice['kind'],formal=choice,
                        project_legal=bool(encoded['legal'][index]),package=package)
                    if package:
                        recipe,reserve=package
                        gaps={r:math.ceil(v-wallet.get(r,0)) for r,v in recipe.items()
                              if r!=GOLD and v>wallet.get(r,0)}
                        entry.update(gaps=gaps,reserve=reserve,
                            sold_conflicts=sorted(planner._traded('sell').intersection(gaps)),
                            buy_quotes={r:quote(ledger,r,q,'buy') for r,q in gaps.items()},
                            funding=planner.funding(*package))
                    result['recruits'].append(entry)
            with torch.inference_mode():
                for name,model in models:
                    logits,value,_=model(prepare(collate([encoded]),'cpu'),return_value=True)
                    probabilities=logits[0].softmax(-1)
                    top=logits[0].topk(min(10,len(encoded['actions']))).indices.tolist()
                    result['models'].append(dict(path=name,value=float(value[0]),
                        family=model.last_family_logits.softmax(-1)[0].tolist(),
                        top=[dict(action=encoded['actions'][i],probability=float(probabilities[i]),
                                  legal=bool(encoded['legal'][i])) for i in top]))
            raise Done()
    host.clients['scenario:n0']=Inspect(state=host.clients['scenario:n0'].state)
    try:host.cycle()
    except Done:pass
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
