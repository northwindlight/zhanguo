"""One LLM seat, with consultation and consent-based temporary rule."""
from copy import deepcopy
from dataclasses import dataclass
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
CONTROL_TURNS = 5
LOG_PAGE = 40
POLICY_SECTIONS = ('direction','economy','diplomacy','military','intelligence')


@dataclass(frozen=True)
class ControlTerm:
    start: int
    reason: str

    @property
    def end(self):return self.start+CONTROL_TURNS-1

    def covers(self,period):return self.start<=period<=self.end

    def describe(self):return {'start':self.start,'end':self.end,'reason':self.reason}

    @classmethod
    def from_save(cls, data):
        if data is None:return None
        if (not isinstance(data,dict) or data.keys()!={'start','reason'}
                or type(data['start']) is not int or data['start']<1 or not valid_reason(data['reason'])):
            raise SaveFormatError('Invalid monarch control term')
        return cls(**data)


def valid_reason(reason):
    return isinstance(reason,str) and bool(reason.strip()) and len(reason)<=1200


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


def log_schema(name, description):
    return schema(name,description,{'after':{'type':'integer','minimum':0},
                                    'limit':{'type':'integer','minimum':1,'maximum':LOG_PAGE}})


def log_page(ui, records, after=0, limit=LOG_PAGE):
    if type(after) is not int or after<0 or type(limit) is not int or not 1<=limit<=LOG_PAGE:
        raise ValueError(ui.text('orchestration.invalid_log'))
    items = records[after:after+limit]
    cursor = after+len(items)
    return {'records':items,'next_after':cursor,'has_more':cursor<len(records)}


def log_brief(client, tail, image, tool_tokens, records, heading):
    window = ctx.make_plan(client.cfg).budget or 96000
    used = ctx.est_tokens(tail)+tool_tokens+ctx.SAFETY_TOKENS
    if image:used += image.get('estimated_tokens', 2048)
    budget = max(0, min(window//2, window-used))
    lines = []
    for row in reversed(records):
        line = json.dumps(row, ensure_ascii=False, separators=(',', ':'))
        budget -= ctx.est_tokens(line)+1
        if budget < 0:break
        lines.append(line)
    tail += '\n'+client.ui.text(heading,shown=len(lines),total=len(records))
    if lines:tail += '\n'+'\n'.join(reversed(lines))
    return tail, image


class MonarchClient(LLMClient):
    def __init__(self, cfg, state, ui, actions, publish, backend):
        super().__init__(cfg, state=state, ui=ui, backend=backend, role='monarch')
        self.action_log = actions
        self.publish = publish
        self.term = None

    def decide(self, player, reason, *, emit, allow_control_request):
        self.reason = reason
        self.allow_control_request = allow_control_request
        observation = player.observe()
        self.period = observation['period']
        policy = observation['facts']['government'].get('plan')
        self.had_valid_plan = bool(policy and policy['valid_until']>=self.period)
        self.published = None
        self.proposal = None
        def progress(step, _receipts):
            emit(self.ui.text('orchestration.progress',actor=player.actor,step=step+1,limit=MONARCH_STEPS))
        self.play(player, max_steps=MONARCH_STEPS, on_call=progress if emit else None)
        return self.published, self.proposal if self.published or self.had_valid_plan else None

    def govern(self, player, term, *, max_steps=None, **callbacks):
        self.period = player.query('government')['period']
        self.published = None
        self.term = term
        self.ui.role = 'governing_monarch'
        try:
            return self.play(player,max_steps=MONARCH_STEPS if max_steps is None else max_steps,**callbacks)
        finally:
            self.term = None
            self.ui.role = 'monarch'

    def _completion_action(self):return 'end_turn' if self.term else 'plan'

    def _completion_summary(self, action, receipt):
        if not self.term and self.had_valid_plan and action=='request_control' and receipt['ok']:
            return receipt['data']['reason']
        return super()._completion_summary(action,receipt)

    def _continue_prompt(self):
        return self.ui.text('orchestration.governing_continue' if self.term else 'orchestration.continue')

    def _cache_key(self,player):return player.actor+'/monarch'

    def _schemas(self, player):
        schemas = [tool for tool in player.actions(read_only=self.term is None)
                   if tool['function']['name'] not in ('plan','explain_plan','memory_search')]
        sections = {key:{'type':'string','minLength':1,'description':self.ui.text('orchestration.policy.'+key)}
                    for key in (*POLICY_SECTIONS,'details')}
        sections['gdp_growth_pct'] = {'type':'number','description':self.ui.text('orchestration.policy.gdp_growth_pct')}
        plan_description = 'orchestration.governing_plan' if self.term else 'orchestration.plan'
        schemas += [schema('plan',self.ui.text(plan_description),sections,(*POLICY_SECTIONS,'gdp_growth_pct')),
                    log_schema('action_log',self.ui.text('orchestration.log_tool'))]
        if self.term is None and self.allow_control_request:
            schemas.append(schema('request_control',self.ui.text('orchestration.request_tool'),
                                  {'reason':{'type':'string','minLength':1,'maxLength':1200}},('reason',)))
        return schemas

    def _tail(self, obs, batch, tool_tokens):
        tail, image = super()._tail(obs, batch, tool_tokens)
        plan = obs['facts'].get('government', {}).get('plan')
        policy = json.dumps(plan, ensure_ascii=False)
        if self.term:
            tail += '\n\n'+self.ui.text('orchestration.governing_brief',plan=policy,**self.term.describe())
        else:
            tail += '\n\n'+self.ui.text('orchestration.brief',plan=policy,reason=self.reason)
        return log_brief(self,tail,image,tool_tokens,self.action_log,'orchestration.log_head')

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
        if self.term and action not in ('plan','action_log'):
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
                data = log_page(self.ui,self.action_log,**arguments)
            elif action=='request_control':
                if self.proposal is not None:raise ValueError(self.ui.text('orchestration.request_exists'))
                if arguments.keys()!={'reason'} or not valid_reason(arguments['reason']):
                    raise ValueError(self.ui.text('orchestration.invalid_reason'))
                self.proposal = ControlTerm(self.period+1,arguments['reason'].strip())
                data = self.proposal.describe()
            else:
                data = player.query(action,self._arguments(arguments))
        except (ValueError,TypeError,OverflowError) as exc:
            return local_receipt(player,self.period,action,str(exc),code='invalid_arguments')
        return local_receipt(player,self.period,action,data=data)


class OrchestratedClient(LLMClient):
    version = 'orchestration/2'

    def __init__(self, cfg, *, record=None, ui, publish_plan, record_coordination, persist_requests,
                 backend=None, monarch_backend=None):
        restored = record['state'] if record else None
        if record:
            fields = {'minister','monarch','requests','action_log'}
            if record['version']==self.version:fields.add('control_term')
            if record['version'] not in ('orchestration/1',self.version) or not isinstance(restored,dict) or restored.keys()!=fields:
                raise SaveFormatError('Invalid orchestration client state')
        state = LLMState.from_save(restored['minister']) if restored is not None else None
        super().__init__(cfg, state=state, ui=ui, backend=backend, role='minister')
        self.record_coordination = record_coordination
        self.persist_requests = persist_requests
        self.requests = deepcopy(restored['requests']) if restored is not None else []
        self.action_log = deepcopy(restored['action_log']) if restored is not None else []
        self.control_term = ControlTerm.from_save(restored.get('control_term')) if restored is not None else None
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
        self._pending_control = None
        self._control_offer_seen = False
        self._handover = None

    def monarch_in_control(self, period):
        return self.control_term is not None and self.control_term.covers(period)

    def play(self, player, *, emit=None, **kwargs):
        period = player.query('government')['period']
        if self.monarch_in_control(period):
            if emit:emit(self.ui.text('orchestration.governing',model=self.monarch.cfg['model'],**self.control_term.describe()))
            return self.monarch.govern(player,self.control_term,emit=emit,**kwargs)
        term = self.control_term
        self._handover = term if term and period>term.end else None
        if emit and self._handover and period==self._handover.end+1:
            emit(self.ui.text('orchestration.control_returned',**self._handover.describe()))
        self._pending_control = None
        self._emit = emit
        try:return super().play(player, emit=emit, **kwargs)
        finally:
            self._emit = None
            self._pending_control = None
            self._handover = None

    def _schemas(self, player):
        schemas = [tool for tool in super()._schemas(player) if tool['function']['name'] not in ('plan','explain_plan')]
        schemas += [schema('ask_monarch',self.ui.text('orchestration.help_tool'),
                           {'reason':{'type':'string','minLength':1,'maxLength':1200}},('reason',)),
                    schema('decide_control',self.ui.text('orchestration.decide_tool'),
                           {'accept':{'type':'boolean'}},('accept',))]
        if self._handover:
            schemas.append(log_schema('control_history',self.ui.text('orchestration.handover_tool')))
        return schemas

    def _continue_prompt(self):return self.ui.text('orchestration.minister_continue')

    def _exchange(self, player, assistant, replies):
        # Consent requires a subsequent model response after the offer receipt.
        if self._pending_control:self._control_offer_seen = True

    def _system(self, player):
        return super()._system(player)+'\n'+self.ui.text('orchestration.minister_control_guidance')

    def _budget(self, period):
        recent = [used for used in self.requests if period-used<HELP_WINDOW]
        return {'remaining':HELP_LIMIT-len(recent),'next_restore_period':recent[0]+HELP_WINDOW if recent else None}

    def _budget_text(self, budget):
        return self.ui.text('orchestration.budget',**{**budget,'next_restore_period':budget['next_restore_period'] if budget['next_restore_period'] is not None else '—'})

    def _tail(self, obs, batch, tool_tokens):
        tail,image = super()._tail(obs,batch,tool_tokens)
        tail += '\n\n'+self._budget_text(self._budget(obs['period']))
        if self._handover and obs['period']==self._handover.end+1:
            tail += '\n\n'+self.ui.text('orchestration.handover_brief',**self._handover.describe())
            return log_brief(self,tail,image,tool_tokens,self._handover_actions(),'orchestration.handover_log')
        return tail,image

    def _handover_actions(self):return [row for row in self.action_log if self._handover.covers(row['period'])]

    def _decide_control(self, player, period, arguments):
        if not isinstance(arguments,dict) or arguments.keys()!={'accept'} or type(arguments['accept']) is not bool:
            return local_receipt(player,period,'decide_control',self.ui.text('orchestration.invalid_consent'),code='invalid_arguments')
        if not player.action_state()['can_act']:
            return local_receipt(player,period,'decide_control',self.ui.text('orchestration.inactive'),code='unavailable_action')
        proposal = self._pending_control
        if proposal is None or proposal.start!=period+1 or not self._control_offer_seen:
            return local_receipt(player,period,'decide_control',self.ui.text('orchestration.no_request'),code='no_control_request')
        self._pending_control = None
        if arguments['accept']:self.control_term = proposal
        result = 'orchestration.control_accepted' if arguments['accept'] else 'orchestration.control_declined'
        return local_receipt(player,period,'decide_control',self.ui.text(result,**proposal.describe()),
                             data={'accepted':arguments['accept'],**proposal.describe()})

    def _ask(self, player, period, arguments):
        if not isinstance(arguments,dict) or set(arguments)!={'reason'}:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.invalid_help'),code='invalid_arguments')
        reason = arguments['reason']
        if not valid_reason(reason):
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
            allow_request = self._pending_control is None and not self.monarch_in_control(period+1)
            receipt, proposal = self.monarch.decide(player,reason,emit=self._emit,allow_control_request=allow_request)
        except (EndpointError,TimeoutError) as exc:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.endpoint',error=getattr(exc,'status_code',None) or type(exc).__name__,**budget),
                                 code='monarch_endpoint',data=budget)
        if receipt is None and proposal is None:
            return local_receipt(player,period,'ask_monarch',self.ui.text('orchestration.no_plan',**budget),code='monarch_no_plan',data=budget)
        message = self.ui.text('orchestration.published') if receipt else ''
        data = {**budget,'plan':receipt['data'] if receipt else player.query('government')['plan']}
        if proposal:
            self._pending_control = proposal
            self._control_offer_seen = False
            data['control_request'] = proposal.describe()
            if message:message += '\n'
            message += self.ui.text('orchestration.control_offered',**proposal.describe())
        return local_receipt(player,period,'ask_monarch',message,data=data)

    def _submit(self, player, action, arguments):
        period = self._observed_period
        if action=='control_history' and self._handover:
            if not player.action_state()['active']:
                return local_receipt(player,period,action,self.ui.text('orchestration.inactive'),code='unavailable_action')
            try:
                if not isinstance(arguments,dict):raise ValueError(self.ui.text('orchestration.invalid_arguments'))
                data = log_page(self.ui,self._handover_actions(),**arguments)
            except (ValueError,TypeError) as exc:
                return local_receipt(player,period,action,str(exc),code='invalid_arguments')
            return local_receipt(player,period,action,data=data)
        if action in ('ask_monarch','decide_control'):
            handler = self._ask if action=='ask_monarch' else self._decide_control
            receipt = handler(player,period,arguments)
            self.record_coordination(player,arguments,receipt)
            return receipt
        receipt = super()._submit(player,action,arguments)
        if action=='end_turn' and not receipt['ok'] and receipt['message']=='Update the national plan first':
            return {**receipt,'message':self.ui.text('orchestration.expired',**self._budget(period))}
        return receipt

    def record_action(self, action, arguments, receipt, error):
        row = {'id':len(self.action_log)+1,'period':receipt['period'],'action':action,
               'arguments':arguments,'receipt':receipt}
        if error:row['error']=type(error).__name__
        self.action_log.append(deepcopy(row))

    def to_save(self):
        term = self.control_term
        return {'kind':self.kind,'version':self.version,'state':{
            'minister':self.state.to_save(),'monarch':self.monarch.state.to_save(),
            'requests':self.requests[:],'action_log':deepcopy(self.action_log),
            'control_term':{'start':term.start,'reason':term.reason} if term else None}}
