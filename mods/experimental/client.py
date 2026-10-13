"""Independent council conversation and full evidence archive, via player API."""
from copy import deepcopy
import json
from pathlib import Path

import ctx
from clients.llm import LLMClient,LLMState,assert_paired
from llm_provider import make_backend,REPLAY_KEY


DEFAULT_REQUIREMENTS=Path(__file__).with_name('requirements.md').read_text(encoding='utf-8')
TRANSPORT_KEYS=('provider','base_url','api_key','model','thinking','reasoning_effort',
                'temperature','max_tokens','api_retries','api_ttft_timeout',
                'vision','map_max_px','map_estimated_tokens','language','ui_catalog','anthropic_auth','anthropic_beta','anthropic_cache_control','anthropic_thinking_budget')


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


class ParliamentClient(LLMClient):
    version='parliament/1'

    def __init__(self,cfg,*,record=None,services,backend=None,review_backend=None):
        super().__init__(cfg,backend=backend,ui=services.get('ui'))
        self._government=services['government'];self._review_backend=review_backend
        settings=cfg.get('parliament',{})
        if not isinstance(settings,dict):raise ValueError('parliament settings must be an object')
        self.review_cfg={key:deepcopy(cfg[key]) for key in TRANSPORT_KEYS if key in cfg}
        reviewer=settings.get('reviewer',{})
        if not isinstance(reviewer,dict):raise ValueError('parliament.reviewer must be an object')
        self.review_cfg.update(deepcopy(reviewer))
        self.requirements=settings.get('requirements',DEFAULT_REQUIREMENTS)
        if settings.get('requirements_file'):
            self.requirements=Path(settings['requirements_file']).read_text(encoding='utf-8')
        if not isinstance(self.requirements,str) or not self.requirements.strip():raise ValueError('Council requirements cannot be empty')
        self.council={'archive':[],'transcript':[],'dialogue':[],'last_plan':None,
                      'trace_start':0,'succession':None,'usage':{},'event_cursor':0,'last_snapshot':None}
        self._takeover=False
        if record is not None:
            if record.get('kind')!='llm' or record.get('version')!=self.version:raise ValueError('Unsupported parliament client state')
            data=record.get('state')
            if not isinstance(data,dict) or set(data)!={'monarch','council'}:raise ValueError('Invalid parliament client state')
            self.state=LLMState.from_save(data['monarch'])
            council=data['council']
            if not isinstance(council,dict) or set(council)!=set(self.council):raise ValueError('Invalid council state')
            if any(not isinstance(council[key],list) for key in ('archive','transcript','dialogue')) or not isinstance(council['usage'],dict):raise ValueError('Invalid council history')
            if type(council['trace_start']) is not int or not 0<=council['trace_start']<=len(council['archive']):raise ValueError('Invalid review trace cursor')
            if council['last_plan'] is not None and not isinstance(council['last_plan'],dict):raise ValueError('Invalid previous plan')
            if type(council['event_cursor']) is not int or council['event_cursor']<0 or (council['last_snapshot'] is not None and not isinstance(council['last_snapshot'],dict)):raise ValueError('Invalid evidence snapshot')
            assert_paired(council['dialogue'])
            if council['succession'] is not None and (not isinstance(council['succession'],dict) or set(council['succession'])!={'prompt','summary'}):raise ValueError('Invalid succession briefing')
            self.council=deepcopy(council)

    def _reviewer(self):
        if self._review_backend is None:self._review_backend=make_backend(self.review_cfg)
        return self._review_backend

    def _review_config(self):
        return {**self.review_cfg,'_ui_expand_message':self.presentation.expand_message}

    def _complete(self,messages):
        text,stats=self._reviewer().complete_text(deepcopy(messages),self._review_config())
        if not isinstance(text,str) or not text.strip():raise ValueError('议会返回空结果；停止本局')
        for key,value in stats.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):self.council['usage'][key]=self.council['usage'].get(key,0)+value
        return text

    def _manual(self,player,topic):
        if topic=='说明书':
            return {'source':'当前发行版公共规则和公开目录','rules':player.rules(),
                    'actions':player.actions(),'buildings':player.query('building_catalog'),
                    'units':player.query('unit_catalog')}
        titles=('经济学手册','基础指南','战争手册','战略手册')
        if topic not in titles:raise ValueError('可查：说明书、经济学手册、基础指南、战争手册、战略手册')
        return {'source':'experimental 发行版策略参考；具体规则以说明书和引擎回执为准',
                'title':topic,'body':Path(__file__).with_name('handbooks').joinpath(topic+'.md').read_text(encoding='utf-8')}

    def _council_turn(self,player,dialogue):
        tools=[schema for schema in player.actions() if schema['function']['name']=='player_rules']
        tools.append({'type':'function','function':{'name':'read_manual','description':'只读查询当前发行版说明书或策略手册；数值以说明书、实际回执为准。',
                      'parameters':{'type':'object','properties':{'topic':{'type':'string','enum':['说明书','经济学手册','基础指南','战争手册','战略手册']}},'required':['topic'],'additionalProperties':False}}})
        limit=int(self.review_cfg.get('max_steps',16))
        if limit<1:raise ValueError('Council max_steps must be positive')
        for _ in range(limit):
            msg,stats=self._reviewer().chat_turn(deepcopy(dialogue),self.ui.actions(tools),self._review_config())
            for key,value in stats.items():
                if isinstance(value,(int,float)) and not isinstance(value,bool):self.council['usage'][key]=self.council['usage'].get(key,0)+value
            calls=msg.get('tool_calls') or []
            ids=[call.get('id') for call in calls]
            if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid council tool IDs')
            assistant={key:deepcopy(msg[key]) for key in ('content','reasoning_content','tool_calls',REPLAY_KEY) if key in msg}
            assistant['role']='assistant';dialogue.append(assistant)
            for call in calls:
                fn=call.get('function',{})
                try:
                    args=json.loads(fn.get('arguments') or '{}')
                    if not isinstance(args,dict):raise ValueError('Arguments must be an object')
                    if fn.get('name')=='player_rules':data=player.query('player_rules',args)
                    elif fn.get('name')=='read_manual':data=self._manual(player,**args)
                    else:raise ValueError('议长只能查说明书和手册，不能提交国家行动')
                    result={'ok':True,'data':data}
                except (ValueError,TypeError,KeyError) as exc:result={'ok':False,'message':str(exc)}
                dialogue.append({'role':'tool','tool_call_id':call['id'],'content':encode(result)})
                self.council['archive'].append({'kind':'council.query','period':player.observe()['period'],
                                               'tool':deepcopy(fn),'result':deepcopy(result)})
            assert_paired(dialogue)
            if not calls and isinstance(msg.get('content'),str) and msg['content'].strip():return msg['content']
            if not calls:dialogue.append({'role':'user','content':self.ui.text('experimental.continue')})
        raise RuntimeError('议长未在步数上限内完成审核；停止本局')

    def _snapshot(self,player,kind):
        state=player.observe()
        # These are already captured verbatim as event/command records. Avoid
        # copying all old events and monarch summaries after every read query.
        state['facts'].pop('news',None)
        state['facts']['government'].pop('summaries',None)
        events=player.events(self.council['event_cursor'])
        previous=self.council['last_snapshot']
        if previous is None:
            self.council['archive'].append({'kind':kind,'state':deepcopy(state),'events':events})
        else:
            changes={key:value for key,value in state['facts'].items() if value!=previous['facts'].get(key)}
            if changes or events['items'] or kind in ('turn.start','settlement'):
                self.council['archive'].append({'kind':kind,'period':state['period'],'can_act':state['can_act'],
                                               'fact_changes':deepcopy(changes),'events':events})
        self.council['event_cursor']=events['cursor'];self.council['last_snapshot']=state

    def _prepare(self,player):
        self._government('enroll')
        self._snapshot(player,'turn.start')

    def on_settlement(self,player):self._snapshot(player,'settlement')

    def _system(self,player):
        text=super()._system(player)
        text+=self.ui.text('experimental.monarch')
        if self.council['succession']:
            text+='\n'+self.council['succession']['prompt']+self.ui.text('experimental.handover')+self.council['succession']['summary']
        return text

    def _context(self,player,obs,batch,tools):
        messages,plan=super()._context(player,obs,batch,tools)
        self.council['transcript'].append({'period':obs['period'],'messages':deepcopy([messages[0],messages[-1]])})
        return messages,plan

    def _receipt(self,player,action,arguments,receipt):
        self.council['archive'].append({'kind':'action','action':action,'arguments':arguments,'receipt':deepcopy(receipt)})
        self._snapshot(player,'after.action')

    def _exchange(self,player,assistant,replies):
        self.council['transcript'].append({'period':player.observe()['period'],'messages':deepcopy([assistant,*replies])})

    def _submit(self,player,action,arguments):
        if self._takeover:
            return {'api':1,'actor':player.actor,'action':action,'period':player.observe()['period'],
                    'ok':False,'status':'rejected','code':'unavailable_action',
                    'message':'上一任君主已被罢免，此批剩余命令未执行；由新君主决定。','data':None}
        # Public plan records a draft, never an approved policy. The authority
        # to decide is a host-bound service, absent from the model's tool list.
        receipt=player.submit(action,arguments)
        if action!='plan' or not receipt['ok']:return receipt
        obs_period=receipt['period']
        dialogue=self.council['dialogue']
        if not dialogue:
            dialogue.append({'role':'system','content':self.requirements+self.ui.text('experimental.council',name=self.cfg.get('name',player.actor))})
        # No candidate, monarch reasoning, or post-draft snapshot in step one.
        evidence={'previous_plan':self.council['last_plan'],
                  'trajectory':self.council['archive'][self.council['trace_start']:],
                  'stage':1,'period':obs_period}
        dialogue.append({'role':'user','content':self.ui.text('experimental.review_first')+encode(evidence)})
        # Reviewer sees the same actor-filtered map through the selected UI mod.
        from ctx import MAP_KEY
        view=self.presentation.llm({**self.cfg,**self.review_cfg})
        current,image=view.map_context(self.council['last_snapshot'])
        dialogue[-1]['content']+='\n'+current
        if image:dialogue[-1][MAP_KEY]=image
        assessment=self._council_turn(player,dialogue)
        candidate=receipt['data']['text']
        dialogue.append({'role':'user','content':self.ui.text('experimental.review_second')+encode({'new_plan':candidate,'period':obs_period})})
        verdict_text=self._council_turn(player,dialogue)
        try:verdict=json.loads(verdict_text)
        except ValueError as exc:raise ValueError('议会未返回有效 JSON；停止本局') from exc
        if not isinstance(verdict,dict) or set(verdict)!={'approved','reason'} or type(verdict['approved']) is not bool or not isinstance(verdict['reason'],str) or not verdict['reason'].strip():raise ValueError('议会裁决字段无效；停止本局')
        decision=self._government('decide',verdict['approved'],verdict['reason'])
        self.council['archive'].append({'kind':'review','period':obs_period,'assessment':assessment,
                                       'decision':deepcopy(decision),'dialogue':deepcopy(dialogue)})
        self.council['last_plan']={'text':candidate,'period':obs_period,'approved':verdict['approved'],
                                   'state_at_submission':deepcopy(self.council['last_snapshot'])}
        self.council['trace_start']=len(self.council['archive'])
        if verdict['approved']:dialogue.clear()
        elif decision['streak']==3:self._takeover=True
        return {**receipt,'ok':verdict['approved'],'status':'effective' if verdict['approved'] else 'rejected',
                'code':None if verdict['approved'] else 'plan_not_approved','message':decision['message'],'data':decision}

    def _briefing(self,player,messages):
        # Each input character is consumed. Oversized history is summarized in
        # consecutive chunks, then merged; no old evidence is silently dropped.
        rows=[encode({'actor':player.actor,'context':messages}),
              *[encode(row) for row in self.council['archive']],
              *[encode(row) for row in self.council['transcript']],
              encode({'council_dialogue':self.council['dialogue']})]
        window=self.review_cfg.get('ctx_window',1000000)
        if type(window) is not int or window<4096:raise ValueError('Reviewer ctx_window must be at least 4096')
        budget=max(512,int(window*.65)-int(self.review_cfg.get('max_tokens') or 4096))
        chunks=[];current=[];size=0
        for row in rows:
            # Split an individual large record without throwing away its tail.
            parts=[row]
            while parts:
                part=parts.pop(0)
                tokens=ctx.est_tokens(part)
                if tokens>budget:
                    middle=len(part)//2
                    parts[:0]=[part[:middle],part[middle:]];continue
                if current and size+tokens>budget:
                    chunks.append('\n'.join(current));current=[];size=0
                current.append(part);size+=tokens
        if current:chunks.append('\n'.join(current))
        system={'role':'system','content':self.ui.text('experimental.historian')}
        pieces=[self._complete([system,{'role':'user','content':self.ui.text('experimental.chunk',index=i+1,count=len(chunks))+chunk}]) for i,chunk in enumerate(chunks)]
        while len(pieces)>1:
            groups=[];group=[];size=0
            for piece in pieces:
                tokens=ctx.est_tokens(piece)
                if tokens>budget:raise ValueError('交接摘要超出合并预算；请增大审核模型窗口')
                if group and size+tokens>budget:groups.append(group);group=[];size=0
                group.append(piece);size+=tokens
            if group:groups.append(group)
            if len(groups)==len(pieces):raise ValueError('交接摘要无法在窗口内合并；请增大审核模型窗口')
            pieces=[self._complete([system,{'role':'user','content':self.ui.text('experimental.merge')+'\n'.join(group)}]) for group in groups]
        return pieces[0]

    def _after_batch(self,player,messages,emit):
        if not self._takeover:return False
        summary=self._briefing(player,messages)
        replacement=self._government('replace')
        prompt=replacement['message']+self.ui.text('experimental.replacement')
        # Audit trails and the unapproved council conversation survive. All
        # monarch replay, summaries, blocks and recursive memory are replaced.
        self.state=LLMState(usage=deepcopy(self.state.usage),event_cursor=player.events(0)['cursor'])
        self.council['succession']={'prompt':prompt,'summary':summary}
        self.council['archive'].append({'kind':'succession','period':player.observe()['period'],'summary':summary,'prompt':prompt})
        self._takeover=False
        if emit:emit('🏛 '+prompt)
        return True

    def to_save(self):
        return {'kind':self.kind,'version':self.version,'state':{'monarch':self.state.to_save(),'council':deepcopy(self.council)}}
