"""Active rule client; receives a PlayerSession, never a World."""
from dataclasses import dataclass,field
from . import economy,military
from .ledger import Ledger

@dataclass
class RuleState:
    targets: dict = field(default_factory=dict)
    exact: bool = True
    generation: int = 0
    def to_save(self):return {'targets':{str(k):list(v) for k,v in sorted(self.targets.items())},'exact':self.exact,'generation':self.generation}
    @classmethod
    def from_save(cls,data):
        if not isinstance(data,dict) or set(data)!={'targets','exact','generation'} or type(data['exact']) is not bool or not isinstance(data['targets'],dict):raise ValueError('Invalid rule client state')
        if type(data['generation']) is not int or data['generation']<0:raise ValueError('Invalid rule control generation')
        result={}
        for ident,point in data['targets'].items():
            if not isinstance(ident,str) or not ident.isdigit() or int(ident)<1 or str(int(ident))!=ident or not isinstance(point,list) or len(point)!=2 or any(type(v) is not int or v<0 for v in point):raise ValueError('Invalid rule target')
            result[int(ident)]=tuple(point)
        return cls(result,data['exact'],data['generation'])

class RuleClient:
    kind='rule';version='v11plus'
    def __init__(self,state=None):self.state=state or RuleState()
    def play(self,player,*,max_actions=None,on_action=None,on_result=None):
        if hasattr(player,'action_state'):
            control=player.action_state()
            if control['generation']!=self.state.generation:
                if control['context'].get('reset_memory'):self.state=RuleState(generation=control['generation'])
                else:self.state.generation=control['generation']
        ledger=Ledger(player,max_actions,on_action,on_result)
        obs=player.observe()
        if not obs['can_act']:return []
        approved=ledger.do('plan',text='按当前经济收益建设，维持补给并集中编组扩张')
        if hasattr(player,'action_state') and not player.action_state()['active']:return ledger.acts
        if not approved and ledger.acts and ledger.acts[-1]['code']=='plan_not_approved':
            receipt=player.submit('explain_plan',{'text':'已收到议会的事实校核意见，下一回合将按实际回执与库存修订国策'})
            ledger.acts.append(receipt)
            if on_result:on_result('explain_plan',{},receipt)
        economy.run(ledger);military.run(ledger,self.state)
        # Ending is a protocol step and is never lost to a strategy action budget.
        receipt=player.submit('end_turn',{'summary':f'本期完成{len(ledger.acts)}项行动，按公共情报继续经营与扩张'})
        if not receipt['ok']:raise RuntimeError(receipt['message'])
        ledger.acts.append(receipt)
        if on_action:on_action('end_turn',{'summary':receipt['data']['text']})
        if on_result:on_result('end_turn',{},receipt)
        return ledger.acts
    def to_save(self):return {'kind':self.kind,'version':self.version,'state':self.state.to_save()}


def expand_rule_turn_v11plus(player,*,state=None,max_actions=None,on_action=None,on_result=None):
    return RuleClient(state).play(player,max_actions=max_actions,on_action=on_action,on_result=on_result)
