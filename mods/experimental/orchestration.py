"""One LLM seat, with a monarch called on demand by its first minister."""
from copy import deepcopy
import json
import math

from clients import context as ctx
from clients.config import CONTEXT_FIELDS
from clients.llm import LLMClient, LLMState
from clients.provider import EndpointError
from engine.session import SaveFormatError


HELP_WINDOW = 10
HELP_LIMIT = 3
MONARCH_STEPS = 20
LOG_PAGE = 40
POLICY_SECTIONS = ('direction','economy','diplomacy','military','intelligence')


def valid_requests(requests, *, through=None):
    return (isinstance(requests,list) and len(requests)<=HELP_LIMIT
            and all(type(period) is int and period>=0 and (through is None or period<=through) for period in requests)
            and requests==sorted(requests))


def schema(name, description, properties, required=()):
    return {'type':'function','function':{'name':name,'description':description,
        'parameters':{'type':'object','properties':properties,'required':list(required),'additionalProperties':False}}}


def local_receipt(player, period, action, message='', *, code=None, data=None):
    return {'api':player.api_version,'actor':player.actor,'period':period,'action':action,
            'ok':code is None,'status':'rejected' if code else 'effective',
            'code':code,'message':message,'data':data}


class MonarchClient(LLMClient):
    def __init__(self, cfg, state, ui, actions, publish, backend):
        super().__init__(cfg, state=state, ui=ui, backend=backend, role='monarch')
        self.action_log = actions
        self.publish = publish

    def decide(self, player, reason, *, emit):
        self.reason = reason
        self.period = player.observe()['period']
        self.published = None
        def progress(step, _receipts):
            emit(self.ui.text('orchestration.progress',actor=player.actor,step=step+1,limit=MONARCH_STEPS))
        self.play(player, max_steps=MONARCH_STEPS, on_call=progress if emit else None)
        return self.published

    def _completion_action(self):return 'plan'

    def _continue_prompt(self):return self.ui.text('orchestration.continue')

    def _cache_key(self,player):return player.actor+'/monarch'

    def _schemas(self, player):
        reads = [tool for tool in player.actions(read_only=True) if tool['function']['name']!='memory_search']
        sections = {key:{'type':'string','minLength':1,'description':self.ui.text('orchestration.policy.'+key)}
                    for key in (*POLICY_SECTIONS,'details')}
        sections['gdp_growth_pct'] = {'type':'number','description':self.ui.text('orchestration.policy.gdp_growth_pct')}
        return [*reads,
                schema('plan', self.ui.text('orchestration.plan'), sections, (*POLICY_SECTIONS,'gdp_growth_pct')),
                schema('action_log', self.ui.text('orchestration.log_tool'), {
                    'after':{'type':'integer','minimum':0},
                    'limit':{'type':'integer','minimum':1,'maximum':LOG_PAGE}})]

    def _tail(self, obs, batch, tool_tokens):
        tail, image = super()._tail(obs, batch, tool_tokens)
        plan = obs['facts'].get('government', {}).get('plan')
        policy = json.dumps(plan, ensure_ascii=False)
        tail += '\n\n'+self.ui.text('orchestration.brief', plan=policy, reason=self.reason)
        window = ctx.make_plan(self.cfg).budget or 96000
        used = ctx.est_tokens(tail)+tool_tokens+ctx.SAFETY_TOKENS
        if image:used += image.get('estimated_tokens', 2048)
        budget = max(0, min(window//2, window-used))
        lines = []
        for row in reversed(self.action_log):
            line = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
            budget -= ctx.est_tokens(line)+1
            if budget < 0:break
            lines.append(line)
        tail += '\n'+self.ui.text('orchestration.log_head', shown=len(lines), total=len(self.action_log))
        if lines:tail += '\n'+'\n'.join(reversed(lines))
        return tail, image

    def _log_page(self, after=0, limit=LOG_PAGE):
        if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=LOG_PAGE:
            raise ValueError(self.ui.text('orchestration.invalid_log'))
        records = self.action_log[after:after+limit]
        cursor = after+len(records)
        return {'records':records,'next_after':cursor,'has_more':cursor<len(self.action_log)}

    def _policy_text(self, arguments):
        required = {*POLICY_SECTIONS,'gdp_growth_pct'}
        if required-arguments.keys() or arguments.keys()-required-{'details'}:
            raise ValueError(self.ui.text('orchestration.invalid_policy'))
        sections = (*POLICY_SECTIONS,'details') if 'details' in arguments else POLICY_SECTIONS
        target = arguments['gdp_growth_pct']
        if (any(not isinstance(arguments[key],str) or not arguments[key].strip() for key in sections)
                or type(target) not in (int,float) or not math.isfinite(target)):
            raise ValueError(self.ui.text('orchestration.invalid_policy'))
        lines = []
        for key in sections:
            body = arguments[key].strip()
            if key=='economy':body=self.ui.text('orchestration.gdp_target',target=target)+'\n'+body
            lines.append(self.ui.text('orchestration.policy_head.'+key)+'\n'+body)
        return '\n\n'.join(lines)

    def _submit(self, player, action, arguments):
        if not isinstance(action,str) or action not in self._allowed_actions:
            return super()._submit(player,action,arguments)
        if not player.action_state()['can_act']:
            return local_receipt(player,self.period,action,self.ui.text('orchestration.inactive'),code='unavailable_action')
        try:
            if not isinstance(arguments,dict):raise ValueError(self.ui.text('orchestration.invalid_arguments'))
            if action=='plan':
                if self.published is not None:raise ValueError(self.ui.text('orchestration.already_published'))
                receipt = self.publish(player,self.period,{'text':self._policy_text(arguments)})
                if receipt['ok']:self.published = receipt
                return receipt
            if action=='action_log':
                data = self._log_page(**arguments)
            else:
                data = player.query(action,self._arguments(arguments))
        except (ValueError,TypeError,OverflowError) as exc:
            return local_receipt(player,self.period,action,str(exc),code='invalid_arguments')
        return local_receipt(player,self.period,action,data=data)


class OrchestratedClient(LLMClient):
    version = 'orchestration/1'

    def __init__(self, cfg, *, record=None, ui, publish_plan, record_help, persist_requests,
                 backend=None, monarch_backend=None):
        restored = record['state'] if record else None
        if record and (record['version']!=self.version or not isinstance(restored,dict)
                       or set(restored)!={'minister','monarch','requests','action_log'}):
            raise SaveFormatError('Invalid orchestration client state')
        state = LLMState.from_save(restored['minister']) if restored is not None else None
        super().__init__(cfg, state=state, ui=ui, backend=backend, role='minister')
        self.record_help = record_help
        self.persist_requests = persist_requests
        self.requests = deepcopy(restored['requests']) if restored is not None else []
        self.action_log = deepcopy(restored['action_log']) if restored is not None else []
        if not valid_requests(self.requests):
            raise SaveFormatError('Invalid monarch assistance budget')
        if not isinstance(self.action_log,list) or any(
                not isinstance(row,dict) or row.get('id')!=index or type(row.get('period')) is not int
                or not isinstance(row.get('action'),str) or not isinstance(row.get('receipt'),dict)
                or row['receipt'].get('actor')!=cfg['id']
                for index,row in enumerate(self.action_log,1)):
            raise SaveFormatError('Invalid national action log')
        shared = (*CONTEXT_FIELDS,'language','vision','map_max_px','map_estimated_tokens',
                  'model_state_tokens','name','extra_prompt','extra_summary')
        monarch_cfg = {**{key:cfg[key] for key in shared if key in cfg}, **cfg['monarch']}
        monarch_state = LLMState.from_save(restored['monarch']) if restored is not None else None
        self.monarch = MonarchClient(monarch_cfg, monarch_state, ui, self.action_log, publish_plan, monarch_backend)
        self._emit = None

    def play(self, player, *, emit=None, **kwargs):
        self._emit = emit
        try:return super().play(player, emit=emit, **kwargs)
        finally:self._emit = None

    def _schemas(self, player):
        schemas = [tool for tool in super()._schemas(player) if tool['function']['name'] not in ('plan','explain_plan')]
        return [*schemas, schema('ask_monarch', self.ui.text('orchestration.help_tool'),
                                 {'reason':{'type':'string','minLength':1,'maxLength':1200}}, ('reason',))]

    def _continue_prompt(self):return self.ui.text('orchestration.minister_continue')

    def _budget(self, period):
        recent = [used for used in self.requests if period-used<HELP_WINDOW]
        return {'remaining':HELP_LIMIT-len(recent),'next_restore_period':recent[0]+HELP_WINDOW if recent else None}

    def _budget_text(self, budget):
        return self.ui.text('orchestration.budget',**{**budget,'next_restore_period':budget['next_restore_period'] if budget['next_restore_period'] is not None else '—'})

    def _tail(self, obs, batch, tool_tokens):
        tail,image = super()._tail(obs,batch,tool_tokens)
        return tail+'\n\n'+self._budget_text(self._budget(obs['period'])),image

    def _ask(self, player, period, arguments):
        if not isinstance(arguments,dict) or set(arguments)!={'reason'}:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.invalid_help'),code='invalid_arguments')
        reason = arguments['reason']
        if not isinstance(reason,str) or not reason.strip() or len(reason)>1200:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.invalid_help'),code='invalid_arguments')
        state = player.action_state()
        if not state['can_act']:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.inactive'),code='unavailable_action')
        self.requests = [used for used in self.requests if period-used<HELP_WINDOW]
        if len(self.requests)>=HELP_LIMIT:
            budget = self._budget(period)
            return local_receipt(player,period,'ask_monarch',self._budget_text(budget),code='help_limit',data=budget)
        requests = [*self.requests,period]
        if not self.persist_requests(player,period,requests):
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.inactive'),code='unavailable_action')
        self.requests = requests
        if self._emit:self._emit(self.ui.text('orchestration.called',model=self.monarch.cfg['model']))
        budget = self._budget(period)
        try:
            # Planner chat is independent. The seat emits the policy via the
            # regular action receipt and keeps each role's replay private.
            receipt = self.monarch.decide(player, reason, emit=self._emit)
        except (EndpointError,TimeoutError) as exc:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.endpoint',error=getattr(exc,'status_code',None) or type(exc).__name__,**budget),
                                 code='monarch_endpoint',data=budget)
        if receipt is None:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.no_plan',**budget),code='monarch_no_plan',data=budget)
        return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.published'),data={**budget,'plan':receipt['data']})

    def _submit(self, player, action, arguments):
        if action!='ask_monarch':
            receipt = super()._submit(player,action,arguments)
            if action=='end_turn' and not receipt['ok'] and receipt['message']=='Update the national plan first':
                receipt = {**receipt,'message':self.ui.text('orchestration.expired',**self._budget(self._observed_period))}
            return receipt
        receipt = self._ask(player,self._observed_period,arguments)
        self.record_help(player,arguments,receipt)
        return receipt

    def record_action(self, action, arguments, receipt, error):
        row = {'id':len(self.action_log)+1,'period':receipt['period'],'action':action,
               'arguments':arguments,'receipt':receipt}
        if error:row['error']=type(error).__name__
        self.action_log.append(deepcopy(row))

    def to_save(self):
        return {'kind':self.kind,'version':self.version,'state':{
            'minister':self.state.to_save(),'monarch':self.monarch.state.to_save(),
            'requests':self.requests[:],'action_log':deepcopy(self.action_log)}}
