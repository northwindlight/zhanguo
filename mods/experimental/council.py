"""Independent council conversation and full evidence archive, via player API."""
from copy import deepcopy
import json
from pathlib import Path

from clients import context as ctx
from engine.deltas import difference
from clients.llm import assert_paired
from clients.provider import make_backend,REPLAY_KEY


DEFAULT_REQUIREMENTS=Path(__file__).with_name('requirements.md').read_text(encoding='utf-8')
VERDICT_REPAIR_ATTEMPTS=1
COUNCIL_WARNING_ROUND=5
COUNCIL_MAX_ROUNDS=10


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def evidence_state(state):
    if state is None:return None
    result=deepcopy(state)
    result['facts']['army']=[u for u in result['facts'].get('army',[]) if u['owner']!='野人']
    return result


class CouncilService:
    version='council/1'

    def __init__(self,cfg,*,record=None,services,review_backend=None):
        self.cfg=deepcopy(cfg)
        self.presentation=services['ui'];self.ui=self.presentation.llm(cfg)
        self._invalidate=services['invalidate'];self._period=None
        self._notify=services.get('notify',lambda text:None)
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
            return {'source':'当前发行版公共规则和公开目录','rules':player.rules('all'),
                    'actions':player.actions(),'buildings':player.query('building_catalog'),
                    'units':player.query('unit_catalog')}
        sections=player.rules(topic)
        if not sections or sections[0]['title']=='规则目录':raise ValueError('未知指南主题')
        return {'source':'当前发行版公开策略参考；具体规则以目录和实际回执为准','title':topic,'body':'\n\n'.join(r['body'] for r in sections)}

    def _council_turn(self,player,dialogue,*,budget,phase_end=False,finalize=False):
        tools=[schema for schema in player.actions() if schema['function']['name']=='player_rules']
        tools.append({'type':'function','function':{'name':'read_manual','description':'只读查询当前发行版说明书或策略手册；数值以说明书、实际回执为准。',
                      'parameters':{'type':'object','properties':{'topic':{'type':'string','enum':['说明书','经济学手册','基础指南','战争手册','战略手册']}},'required':['topic'],'additionalProperties':False}}})
        if finalize:
            tools.extend([
                {'type':'function','function':{'name':'council_approve','description':'结束审核并通过国策。通过时不要附理由、建议或额外上下文；只在确认可以通过时调用。',
                              'parameters':{'type':'object','properties':{},'additionalProperties':False}}},
                {'type':'function','function':{'name':'council_reject','description':'结束审核并拒绝国策。只在核实严重反事实或无解释的反承诺后调用；reason 必须简洁、具体并给出核对依据。',
                              'parameters':{'type':'object','properties':{'reason':{'type':'string','minLength':1}},'required':['reason'],'additionalProperties':False}}},
            ])
        if phase_end:
            tools.append({'type':'function','function':{'name':'council_next_stage','description':'结束第一步的历史与旧国策审核，进入第二步读取新版国策。',
                          'parameters':{'type':'object','properties':{},'additionalProperties':False}}})
        limit=COUNCIL_MAX_ROUNDS-budget['rounds']
        if limit<=0:return ('',True) if phase_end else None
        format_retries=0
        phase_speech=[]
        def round_tick():
            if budget['rounds']==COUNCIL_WARNING_ROUND and not budget['warned']:
                dialogue.append({'role':'user','content':self.ui.text('experimental.phase_warning')})
                budget['warned']=True
            return budget['rounds']>=COUNCIL_MAX_ROUNDS
        def verdict_feedback(error):
            nonlocal format_retries
            if format_retries<VERDICT_REPAIR_ATTEMPTS:
                prompt=self.ui.text('experimental.review_retry',error=error)
                format_retries+=1
            else:prompt=self.ui.text('experimental.verdict_continue')
            dialogue.append({'role':'user','content':prompt})
        for _ in range(limit):
            msg,stats=self._reviewer().chat_turn(deepcopy(dialogue),self.ui.actions(tools),self._review_config(),on_retry=lambda attempt,error,wait,total:self._notify(self.ui.text('llm.retry',actor=self.cfg.get('name',player.actor)+'议长',attempt=attempt,error=error,wait=wait,total=total)))
            budget['rounds']+=1
            for key,value in stats.items():
                if isinstance(value,(int,float)) and not isinstance(value,bool):self.council['usage'][key]=self.council['usage'].get(key,0)+value
            calls=msg.get('tool_calls') or []
            if phase_end and isinstance(msg.get('content'),str) and msg['content'].strip():
                phase_speech.append(msg['content'].strip())
            ids=[call.get('id') for call in calls]
            if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid council tool IDs')
            assistant={key:deepcopy(msg[key]) for key in ('content','reasoning_content','tool_calls',REPLAY_KEY) if key in msg}
            assistant['role']='assistant';dialogue.append(assistant)
            ending_tools=('council_approve','council_reject') if finalize else ('council_next_stage',) if phase_end else ()
            ending_positions=[i for i,call in enumerate(calls) if call.get('function',{}).get('name') in ending_tools]
            if ending_positions and (len(ending_positions)>1 or ending_positions[0]!=len(calls)-1):
                for call in calls:
                    dialogue.append({'role':'tool','tool_call_id':call['id'],
                                     'content':encode({'ok':False,'message':'最终按钮必须是本批次最后一个工具调用'})})
                assert_paired(dialogue)
                if phase_end:
                    dialogue.append({'role':'user','content':self.ui.text('experimental.phase_continue')})
                    if round_tick():return '\n'.join(phase_speech),True
                    continue
                if round_tick():return None
                verdict_feedback('最终按钮必须是本批次最后一个工具调用')
                continue
            retry_reason=None
            submitted_verdict=None
            stage_ended=False
            for call in calls:
                fn=call.get('function',{})
                try:
                    args=json.loads(fn.get('arguments') or '{}')
                    if not isinstance(args,dict):raise ValueError('Arguments must be an object')
                    if fn.get('name')=='player_rules':data=player.query('player_rules',args)
                    elif fn.get('name')=='read_manual':data=self._manual(player,**args)
                    elif fn.get('name')=='council_approve' and finalize:
                        if args:raise ValueError('council_approve 不接受参数')
                        dialogue.append({'role':'tool','tool_call_id':call['id'],'content':encode({'ok':True})})
                        submitted_verdict={'approved':True,'reason':''}
                        continue
                    elif fn.get('name')=='council_reject' and finalize:
                        if set(args)!={'reason'} or not isinstance(args['reason'],str) or not args['reason'].strip():
                            raise ValueError('council_reject 必须提供非空 reason')
                        dialogue.append({'role':'tool','tool_call_id':call['id'],'content':encode({'ok':True})})
                        submitted_verdict={'approved':False,'reason':args['reason'].strip()}
                        continue
                    elif fn.get('name')=='council_next_stage' and phase_end:
                        if args:raise ValueError('council_next_stage 不接受参数')
                        dialogue.append({'role':'tool','tool_call_id':call['id'],'content':encode({'ok':True,'message':'第一步结束，进入第二步审核'})})
                        stage_ended=True
                        continue
                    else:raise ValueError('议长只能查说明书和手册，不能提交国家行动')
                    result={'ok':True,'data':data}
                except (ValueError,TypeError,KeyError) as exc:result={'ok':False,'message':str(exc)}
                dialogue.append({'role':'tool','tool_call_id':call['id'],'content':encode(result)})
                self.council['archive'].append({'kind':'council.query','period':player.observe()['period'],
                                               'tool':deepcopy(fn),'result':deepcopy(result)})
                if finalize and fn.get('name') in ('council_approve','council_reject') and not result['ok']:
                    retry_reason=result['message']
            assert_paired(dialogue)
            if stage_ended:
                return '\n'.join(phase_speech),round_tick()
            if submitted_verdict is not None:return submitted_verdict
            if retry_reason is not None:
                if round_tick():return None
                verdict_feedback(retry_reason)
                continue
            if not calls and isinstance(msg.get('content'),str) and msg['content'].strip():
                if phase_end:
                    dialogue.append({'role':'user','content':self.ui.text('experimental.phase_continue')})
                    if round_tick():return '\n'.join(phase_speech),True
                elif not finalize:return msg['content']
                else:
                    if round_tick():return None
                    verdict_feedback('尚未通过 council_approve 或 council_reject 工具提交裁决')
                    continue
                continue
            if not calls:
                prompt='experimental.phase_continue' if phase_end else 'experimental.continue'
                dialogue.append({'role':'user','content':self.ui.text(prompt)})
            if round_tick():return ('\n'.join(phase_speech),True) if phase_end else None
        return ('\n'.join(phase_speech),True) if phase_end else None

    def _snapshot(self,player,kind):
        try:state=player.observe()
        except ValueError:return # The actor may have left the active collection.
        # These are already captured verbatim as event/command records. Avoid
        # copying all old events and monarch summaries after every read query.
        for key in ('news','handbooks','building_catalog','unit_catalog','terrain_catalog','land','threats','battle','nations','countries'):
            state['facts'].pop(key,None)
        if 'building_sites' in state['facts']:state['facts']['building_sites'].pop('sites',None)
        state['facts']['government'].pop('summaries',None)
        # Intel payloads are authoritative samples and are added only once.
        events=player.events(self.council['event_cursor'])
        previous=self.council['last_snapshot']
        if previous is None:
            self.council['archive'].append({'kind':kind,'state':evidence_state(state),'events':events})
        else:
            changes=difference(evidence_state(previous),evidence_state(state))
            if changes or events['items'] or kind in ('turn.start','settlement'):
                self.council['archive'].append({'kind':kind,'period':state['period'],'changes':changes,'events':events})
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
            try:summary=self._briefing(player,[])
            except Exception as exc:
                summary='交接摘要生成失败，以下为可核验的最近国策与行动索引：'+encode(self.council['last_plan'])[:4000]
                self.council['archive'].append({'kind':'briefing.fallback','period':player.observe()['period'],'error':type(exc).__name__})
                self._notify('议会交接摘要失败，已保留原始证据并使用国策简报继续。')
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
                                       'state_at_submission':evidence_state(self.council['last_snapshot'])}
            self.council['trace_start']=len(self.council['archive'])
            return {**receipt,'ok':True,'status':'effective','code':None,
                    'message':decision['message'],'data':decision}
        dialogue=self.council['dialogue']
        if not dialogue:
            dialogue.append({'role':'system','content':self.requirements+self.ui.text('experimental.council',name=self.cfg.get('name',player.actor))})
        # No candidate, monarch reasoning, or post-draft snapshot in step one.
        read_only={s['function']['name'] for s in player.actions()}-{'build','buy','sell','recruit','move','attack','retreat','disband','plan','end_turn','explain_plan','script_write','script_patch','script_run','send_letter','gift','share_map','spy','borrow','buy_report','vote','declare_war','offer_peace','accept_peace','reject_peace','guarantee','cancel_guarantee','propose_defense','break_defense','accept_proposal','reject_proposal','bloc_found','bloc_join','bloc_leave','bloc_dissolve','bloc_rename','bloc_transfer'}
        trajectory=[]
        for row in self.council['archive'][self.council['trace_start']:]:
            item=deepcopy(row)
            if item.get('kind')=='action':
                action=item.get('action');tool=action in read_only
                if tool:
                    receipt=item.get('receipt') or {}
                    item['receipt']={k:receipt.get(k) for k in ('ok','status','code','message')}
            trajectory.append(item)
        evidence={'previous_plan':self.council['last_plan'],
                  'trajectory':trajectory,
                  'stage':1,'period':obs_period}
        dialogue.append({'role':'user','content':self.ui.text('experimental.review_first')+encode(evidence)})
        # Reviewer sees the same actor-filtered map through the selected UI mod.
        from clients.context import MAP_KEY
        view=self.presentation.llm({**self.cfg,**self.review_cfg})
        current,image=view.map_context(self.council['last_snapshot'])
        dialogue[-1]['content']+='\n'+current
        if image:dialogue[-1][MAP_KEY]=image
        def paralysis_receipt(assessment):
            reason=self.ui.text('experimental.parliament_paralyzed_reason')
            decision=self._government('decide',True,reason)
            decision['message']=self.ui.text('experimental.parliament_paralyzed')
            self.council['archive'].append({'kind':'review','period':obs_period,'assessment':assessment,
                                           'decision':deepcopy(decision),'dialogue':deepcopy(dialogue),
                                           'auto_approved_due_to_paralysis':True})
            self.council['last_plan']={'text':candidate,'period':obs_period,'approved':True,
                                       'state_at_submission':evidence_state(self.council['last_snapshot'])}
            self.council['trace_start']=len(self.council['archive'])
            dialogue.clear()
            return {**receipt,'ok':True,'status':'effective','code':None,
                    'message':decision['message'],'data':decision}
        budget={'rounds':0,'warned':False}
        assessment,paralyzed=self._council_turn(player,dialogue,budget=budget,phase_end=True)
        if paralyzed:return paralysis_receipt(assessment)
        dialogue.append({'role':'user','content':self.ui.text('experimental.review_second')+encode({'new_plan':candidate,'period':obs_period})})
        verdict=self._council_turn(player,dialogue,budget=budget,finalize=True)
        if verdict is None:return paralysis_receipt(assessment)
        decision=self._government('decide',verdict['approved'],verdict['reason'])
        self.council['archive'].append({'kind':'review','period':obs_period,'assessment':assessment,
                                       'decision':deepcopy(decision),'dialogue':deepcopy(dialogue)})
        self.council['last_plan']={'text':candidate,'period':obs_period,'approved':verdict['approved'],
                                   'state_at_submission':evidence_state(self.council['last_snapshot'])}
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
