"""Independent council conversation and full evidence archive, via player API."""
from copy import deepcopy
import json
from pathlib import Path

from clients import context as ctx
from clients.llm import assert_paired
from clients.provider import make_backend,REPLAY_KEY


DEFAULT_REQUIREMENTS=Path(__file__).with_name('requirements.md').read_text(encoding='utf-8')


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


class CouncilService:
    version='council/1'

    def __init__(self,cfg,*,record=None,services,review_backend=None):
        self.cfg=deepcopy(cfg)
        self.presentation=services['ui'];self.ui=self.presentation.llm(cfg)
        self._invalidate=services['invalidate'];self._period=None
        self._government=services['government'];self._review_backend=review_backend
        settings=cfg.get('parliament',{})
        if not isinstance(settings,dict):raise ValueError('parliament settings must be an object')
        reviewer=settings.get('reviewer',{})
        if not isinstance(reviewer,dict):raise ValueError('parliament.reviewer must be an object')
        from clients.config import normalize
        self.review_cfg=normalize(reviewer,defaults=cfg)
        configure=getattr(self.presentation,'configure',None)
        if configure:self.review_cfg=configure(self.review_cfg,defaults=cfg)
        self.requirements=settings.get('requirements',DEFAULT_REQUIREMENTS)
        if settings.get('requirements_file'):
            self.requirements=Path(settings['requirements_file']).read_text(encoding='utf-8')
        if not isinstance(self.requirements,str) or not self.requirements.strip():raise ValueError('Council requirements cannot be empty')
        self.council={'archive':[],'transcript':[],'dialogue':[],'last_plan':None,
                      'trace_start':0,'succession':None,'usage':{},'event_cursor':0,'last_snapshot':None}
        self._takeover=False
        if record is not None:
            if set(record)!={'version','state'} or record['version']!=self.version:raise ValueError('Unsupported council state')
            data=record.get('state')
            council=data
            if not isinstance(council,dict) or set(council)!=set(self.council):raise ValueError('Invalid council state')
            if any(not isinstance(council[key],list) for key in ('archive','transcript','dialogue')) or not isinstance(council['usage'],dict):raise ValueError('Invalid council history')
            if type(council['trace_start']) is not int or not 0<=council['trace_start']<=len(council['archive']):raise ValueError('Invalid review trace cursor')
            if council['last_plan'] is not None and not isinstance(council['last_plan'],dict):raise ValueError('Invalid previous plan')
            if type(council['event_cursor']) is not int or council['event_cursor']<0 or (council['last_snapshot'] is not None and not isinstance(council['last_snapshot'],dict)):raise ValueError('Invalid evidence snapshot')
            assert_paired(council['dialogue'])
            if council['succession'] is not None and (not isinstance(council['succession'],dict) or set(council['succession'])!={'prompt','summary'}):raise ValueError('Invalid succession briefing')
            self.council=deepcopy(council)
        else:self._government('enroll')

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
        try:state=player.observe()
        except ValueError:return # The actor may have left the active collection.
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
        period=player.observe()['period']
        if self._period!=period:
            self._snapshot(player,'turn.start');self._period=period

    def on_settlement(self,player):self._snapshot(player,'settlement')

    def record_dialogue(self,player,messages):
        self.council['transcript'].append({'period':player.observe()['period'],'messages':deepcopy(messages)})

    def record(self,player,row):
        self.council['archive'].append({'kind':'action',**deepcopy(row)})
        self._snapshot(player,'after.action')
        if self._takeover:
            summary=self._briefing(player,[])
            replacement=self._government('replace')
            prompt=replacement['message']+self.ui.text('experimental.replacement')
            self.council['succession']={'prompt':prompt,'summary':summary}
            self.council['archive'].append({'kind':'succession','period':player.observe()['period'],'summary':summary,'prompt':prompt})
            self._takeover=False
            self._invalidate({'prompt':prompt,'summary':summary,'reset_memory':True})

    def submit(self,player,action,arguments,next_submit):
        self._prepare(player)
        receipt=next_submit()
        if action!='plan' or not receipt['ok']:return receipt
        obs_period=receipt['period']
        candidate=receipt['data']['text']
        # The first national plan establishes the baseline, without a model review.
        # last_plan persists across saves and monarch replacements.
        if self.council['last_plan'] is None:
            decision=self._government('decide',True,'第一版国策直接生效；从第一次修订起接受议会审核')
            decision['message']=self.ui.text('experimental.first_plan')
            self.council['archive'].append({'kind':'plan.initial','period':obs_period,
                                           'text':candidate,'decision':deepcopy(decision)})
            self.council['last_plan']={'text':candidate,'period':obs_period,'approved':True,
                                       'state_at_submission':deepcopy(self.council['last_snapshot'])}
            self.council['trace_start']=len(self.council['archive'])
            return {**receipt,'ok':True,'status':'effective','code':None,
                    'message':decision['message'],'data':decision}
        dialogue=self.council['dialogue']
        if not dialogue:
            dialogue.append({'role':'system','content':self.requirements+self.ui.text('experimental.council',name=self.cfg.get('name',player.actor))})
        # No candidate, monarch reasoning, or post-draft snapshot in step one.
        evidence={'previous_plan':self.council['last_plan'],
                  'trajectory':self.council['archive'][self.council['trace_start']:],
                  'stage':1,'period':obs_period}
        dialogue.append({'role':'user','content':self.ui.text('experimental.review_first')+encode(evidence)})
        # Reviewer sees the same actor-filtered map through the selected UI mod.
        from clients.context import MAP_KEY
        view=self.presentation.llm({**self.cfg,**self.review_cfg})
        current,image=view.map_context(self.council['last_snapshot'])
        dialogue[-1]['content']+='\n'+current
        if image:dialogue[-1][MAP_KEY]=image
        assessment=self._council_turn(player,dialogue)
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

    def to_save(self):
        return {'version':self.version,'state':deepcopy(self.council)}
