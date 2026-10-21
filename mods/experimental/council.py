"""Independent council conversation and full evidence archive, via player API."""
from copy import deepcopy
import json
from pathlib import Path

from clients import context as ctx
from engine.deltas import difference
from clients.llm import assert_paired
from clients.model_view import ModelView
from clients.provider import make_backend,REPLAY_KEY
from presentation.facts import map_presentation


DEFAULT_REQUIREMENTS=Path(__file__).with_name('requirements.md').read_text(encoding='utf-8')
VERDICT_REPAIR_ATTEMPTS=1
COUNCIL_WARNING_ROUND=5
COUNCIL_MAX_ROUNDS=10


def encode(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)


def review_government(government):
    # 国策由 previous_plan 指定；否决及解释由保留的对话/区间行动提供。
    # 提交前快照中残留的更早正文不能成为另一份审核基线。
    for key in ('plan','summaries'):government.pop(key,None)
    parliament=government.get('parliament')
    if isinstance(parliament,dict):
        for key in ('draft','reason','explanation'):parliament.pop(key,None)


TRANSIENT_PATHS = (('facts', 'frontier'), ('facts', 'econ', 'preview'))
"""议会证据里不算「事实」的派生/瞬时路径。

  · ``facts.econ.preview`` 是**预估**（补给、回血、折算），每次资源变化就重算；
  · ``facts.frontier`` 是玩家视口的可达/边境格，纯派生。
两者在轨迹里占七成以上体积（2026-10-21 实测：魏一次审核 `preview` 单项 11.9 万
token），却不构成可核对的既有事实。``facts.view`` 是**地图**，保留。
"""

PRUNED_FACTS = tuple(path[1] for path in TRANSIENT_PATHS if len(path) == 2)
PRUNED_ECON = tuple(path[2] for path in TRANSIENT_PATHS if len(path) == 3 and path[1] == 'econ')


def prune_changes(changes):
    """按 TRANSIENT_PATHS 过滤一条 changes 列表（旧档里的行也走这里）。"""
    if not isinstance(changes, list):
        return changes
    return [change for change in changes
            if not any((change.get('path') or [])[:len(prefix)] == list(prefix)
                       for prefix in TRANSIENT_PATHS)]


def prune_row(row):
    """议会证据里的单行轨迹：去掉瞬时/派生路径，动作后的差分只留事件。

    动作后的 `changes` 只是同一批事实的增量重复：动作本身有回执与事件，回合边界
    （settlement/turn.start）的差分已给出净变化与地图。
    """
    item=dict(row)
    if 'changes' in item:
        item['changes']=prune_changes(item['changes'])
    if item.get('kind')=='after.action':
        item.pop('changes',None)
    return item


def evidence_state(state):
    if state is None:return None
    result=deepcopy(state)
    facts=result['facts']
    facts['army']=[u for u in facts.get('army',[]) if u['owner']!='野人']
    for key in PRUNED_FACTS:
        facts.pop(key,None)
    econ=facts.get('econ')
    if isinstance(econ,dict):
        for key in PRUNED_ECON:
            econ.pop(key,None)
    review_government(facts.get('government',{}))
    return result


def review_evidence(value):
    """快照提供当时事实，待核对的国策只由外层 previous_plan.text 指定。"""
    result=deepcopy(value)
    def snapshot(state):
        if not isinstance(state,dict):return
        facts=state.get('facts') if isinstance(state.get('facts'),dict) else {}
        for key in PRUNED_FACTS:facts.pop(key,None)
        econ=facts.get('econ')
        if isinstance(econ,dict):
            for key in PRUNED_ECON:econ.pop(key,None)
        review_government(facts.get('government',{}))
    hidden=[['facts','government',key] for key in ('plan','summaries')]
    hidden.extend(['facts','government','parliament',key] for key in ('draft','reason','explanation'))
    snapshot(result.get('previous_plan',{}).get('state_at_submission'))
    for item in result.get('trajectory',[]):
        snapshot(item.get('state'))
        if isinstance(item.get('changes'),list):
            kept=[]
            for change in item['changes']:
                path=change.get('path',[])
                if any(path[:len(prefix)]==prefix for prefix in hidden):continue
                # 少见的整体替换也只清除快照国策；行动参数/脚本输入保持原文。
                if path==[]:snapshot(change.get('set'))
                elif path==['facts'] and isinstance(change.get('set'),dict):review_government(change['set'].get('government',{}))
                elif path==['facts','government'] and isinstance(change.get('set'),dict):review_government(change['set'])
                elif path==['facts','government','parliament'] and isinstance(change.get('set'),dict):
                    for key in ('draft','reason','explanation'):change['set'].pop(key,None)
                kept.append(change)
            item['changes']=kept
    return result


class CouncilService:
    version='council/1'

    def __init__(self,cfg,*,record=None,services,review_backend=None):
        self.cfg=deepcopy(cfg)
        self.presentation=services['ui'];self.ui=ModelView(cfg,presentation=self.presentation)
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
        # 失职检查的边界写在 requirements.md：只对本期可改变、君主可控的失误追责；
        # 小国被大国吞并、被迫防守战争、实力不对称且不可预见的失败不得作为打回理由。
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

    # ------------------------------------------------------------------
    # 议长请求预算
    # ------------------------------------------------------------------
    # ★ 议会这条路径原先对请求体积**完全没有闸**：证据 = 上次批准以来的整段轨迹，
    #   随回合线性增长。2026-10-21 第 95 回魏的审核首请求证据单条 1,046,455 字符
    #   （≈50 万 token），reviewer 真的吐 400 context_length_exceeded，整局终止。
    #   玩家侧有 ctx.build 管预算，议长侧没有——这里补同一套口径：每次调用前按
    #   reviewer 的 ctx_window 算输入预算，超了就把**轨迹证据按档降级**（只动本次
    #   请求里的副本，议会档案原文一行不删），降到底还超就明确报错而不是让提供方打回。
    EVIDENCE_KEEP_FULL_ROWS = 12   # 降档时仍保留完整事件/回执的最近行数
    EVIDENCE_MIN_ROWS = 4          # 降到底保留的最近行数，更早的记为 omitted

    def _review_input_budget(self):
        """议长一次请求的输入预算（token）；未配 ctx_window 时返回 0＝不设闸。"""
        plan = ctx.make_plan(self.review_cfg)
        return plan.budget if plan.mode == 'budget' else 0

    @staticmethod
    def _request_tokens(dialogue, tools):
        return (ctx.messages_tokens(dialogue)
                + ctx.est_tokens(json.dumps(tools, ensure_ascii=False))
                + ctx.SAFETY_TOKENS)

    def _evidence_fit(self, dialogue, index):
        """把对话里既有的证据消息还原成可降级的 fit（被打回后重提时它仍留在上下文里）。"""
        message = dialogue[index]
        if not isinstance(message, dict) or message.get('role') != 'user':
            return None
        content = message.get('content')
        if not isinstance(content, str):
            return None
        prefix = self.ui.text('experimental.review_first')
        if not content.startswith(prefix):
            return None
        try:
            evidence, end = json.JSONDecoder().raw_decode(content[len(prefix):])
        except ValueError:
            return None
        if not isinstance(evidence, dict) or 'previous_plan' not in evidence:
            return None
        return {'index': index, 'prefix': prefix, 'suffix': content[len(prefix) + end:],
                'evidence': evidence, 'step': 0, 'omitted': []}

    def _fit_request(self, dialogue, tools, budget):
        """每次议长调用前核对输入预算；超了从**最旧的证据**开始逐档降级，直到装得下。

        被打回的审核对话会连同上一轮的证据一起留下来（设计如此）。那时只压新证据
        不够——旧证据也要按同样的档位继续降，否则重提一次就又超窗。
        """
        limit = self._review_input_budget()
        if not limit:
            return
        current = budget.get('_evidence')
        current_index = current.get('index') if current else None
        fits = [self._evidence_fit(dialogue, index) for index in range(len(dialogue))
                if index != current_index]
        fits = [fit for fit in fits if fit]
        if current:
            fits.append(current)
        if not fits:
            return
        for _ in range(64):
            if self._request_tokens(dialogue, tools) <= limit:
                return
            if not any(self._degrade_evidence(fit, dialogue, budget, tools) for fit in fits):
                break
        tokens = self._request_tokens(dialogue, tools)
        if tokens > limit:
            raise ValueError(self.ui.text('experimental.review_budget_overflow',
                                          tokens=tokens, limit=limit))

    def _degrade_evidence(self, fit, dialogue, budget, tools):
        """把证据降一档；返回是否真的变了。

        降级顺序（越早的轨迹越先失去细节，最近行永远保留最全）：
          1. 去掉快照行的 `changes`（状态差分，占证据 6~8 成，事件与回执仍在）；
          2. 旧行再去掉 `events`；
          3. 旧行动回执只留 ok/status/code/message，议会查询只留 ok/message；
          4. 反复砍掉最旧的一半行，被砍范围写进 `omitted`（原文仍在档案里）；
          5. 最后才放弃 `previous_plan.state_at_submission`。
        """
        evidence = fit['evidence']
        rows = evidence.get('trajectory') or []
        step = fit['step']
        fit['step'] = step + 1
        changed = False
        if step == 0:
            for row in rows:
                if isinstance(row, dict) and row.pop('changes', None) is not None:
                    changed = True
        elif step == 1:
            for row in rows[:-self.EVIDENCE_KEEP_FULL_ROWS]:
                if isinstance(row, dict) and row.pop('events', None) is not None:
                    changed = True
        elif step == 2:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                for key, keep in (('receipt', ('ok', 'status', 'code', 'message')),
                                  ('result', ('ok', 'message'))):
                    value = row.get(key)
                    if isinstance(value, dict) and set(value) - set(keep):
                        row[key] = {k: value.get(k) for k in keep}
                        changed = True
        elif step >= 3:
            if len(rows) > self.EVIDENCE_MIN_ROWS:
                keep = max(self.EVIDENCE_MIN_ROWS, len(rows) // 2)
                removed = rows[:len(rows) - keep]
                evidence['trajectory'] = rows[len(rows) - keep:]
                fit.setdefault('omitted', []).extend(removed)
                periods = [int(r.get('period') or 0) for r in fit['omitted'] if isinstance(r, dict)]
                evidence['omitted'] = {
                    'rows': len(fit['omitted']),
                    'periods': [min(periods), max(periods)] if periods else [],
                    'note': '更早的轨迹超出议长输入预算，本次请求未附原文（仍完整保存在议会档案中）；只能就其余证据核对。',
                }
                changed = True
            elif 'state_at_submission' in (evidence.get('previous_plan') or {}):
                evidence['previous_plan'].pop('state_at_submission', None)
                changed = True
        if changed:
            dialogue[fit['index']]['content'] = fit['prefix'] + encode(evidence) + fit['suffix']
            self._review_log('budget', {'step': fit['step'], 'rows': len(evidence.get('trajectory') or []),
                                        'omitted': (evidence.get('omitted') or {}).get('rows', 0),
                                        'tokens': self._request_tokens(dialogue, tools),
                                        'limit': self._review_input_budget()}, budget)
        return changed

    def _complete(self,messages):
        budget={'period':self._period,'_log_round':'—','_log_phase':'交接'}
        self._review_log('request',{'messages':messages},budget)
        text,stats=self._reviewer().complete_text(deepcopy(messages),self._review_config())
        self._review_log('response',{'content':text,'usage':stats},budget)
        if not isinstance(text,str) or not text.strip():raise ValueError('议会返回空结果；停止本局')
        for key,value in stats.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):self.council['usage'][key]=self.council['usage'].get(key,0)+value
        return text

    def _manual(self,player,topic):
        if topic=='说明书':
            return {'source':'当前发行版公共规则和公开目录','rules':player.rules('all'),
                    'actions':player.actions(),
                    'buildings':player.call_api('native:buildings','catalog'),
                    'units':player.call_api('native:army','unit_catalog')}
        sections=player.rules(topic)
        if not sections or sections[0]['title']=='规则目录':raise ValueError('未知指南主题')
        return {'source':'当前发行版公开规则；具体数值以目录和实际回执为准','title':topic,'body':'\n\n'.join(r['body'] for r in sections)}

    def _review_log(self,kind,payload,budget):
        # 议会完整输入/思考/工具往返只写观察者日志，不进入玩家近讯或滚屏。
        body=encode(payload)
        if kind=='response' and isinstance(payload.get('message'),dict):
            message=payload['message'];parts=[]
            for key in ('reasoning_content','content'):
                if message.get(key):parts.append('【'+key+'】\n'+message[key])
            parts.append(encode({**payload,'message':{k:v for k,v in message.items() if k not in ('reasoning_content','content')}}))
            body='\n\n'.join(parts)
        elif kind in ('system','user','tool') and isinstance(payload.get('content'),str):
            body=payload['content']+'\n'+encode({k:v for k,v in payload.items() if k!='content'})
        self._notify(self.ui.text('experimental.review_log',actor=self.cfg.get('name','议长'),
            period=budget.get('period',self._period),count=budget.get('_log_round','—'),
            phase=budget.get('_log_phase','—'),kind=kind,payload=body))

    def _log_dialogue(self,dialogue,budget):
        cursor=budget.get('_logged',0)
        for message in dialogue[cursor:]:self._review_log(message['role'],message,budget)
        budget['_logged']=len(dialogue)

    def _council_turn(self,player,dialogue,*,budget,phase_end=False,finalize=False):
        budget['_log_phase']=1 if phase_end else 2
        budget['_log_round']=budget['rounds']+1
        try:return self._council_exchange(player,dialogue,budget=budget,phase_end=phase_end,finalize=finalize)
        except Exception as exc:
            self._review_log('error',{'type':type(exc).__name__},budget)
            raise
        finally:self._log_dialogue(dialogue,budget)

    def _council_exchange(self,player,dialogue,*,budget,phase_end=False,finalize=False):
        tools=[schema for schema in player.actions() if schema['function']['name']=='player_rules']
        topics=['说明书']+[section['title'] for section in player.rules('all')]
        tools.append({'type':'function','function':{'name':'read_manual','description':'只读查询当前发行版说明书或任一规则章节；数值以说明书、实际回执为准。',
                      'parameters':{'type':'object','properties':{'topic':{'type':'string','enum':topics}},'required':['topic'],'additionalProperties':False}}})
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
        self._review_log('tools',self.ui.actions(tools),budget)
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
            number=budget['rounds']+1
            budget['_log_round']=number
            notice=self.ui.text('experimental.review_round',count=number,total=COUNCIL_MAX_ROUNDS,phase=budget['_log_phase'])
            if number>COUNCIL_WARNING_ROUND:
                notice+='\n'+self.ui.text('experimental.review_urge',remaining=COUNCIL_MAX_ROUNDS-number+1)
            # 保留新证据/反馈的段首与地图块；每次请求都告诉模型真实轮次。
            if dialogue and dialogue[-1]['role']=='user' and budget.get('_logged',0)<len(dialogue):
                dialogue[-1]['content']+='\n'+notice
            else:dialogue.append({'role':'user','content':notice})
            self._log_dialogue(dialogue,budget)
            status='experimental.review_history_status' if phase_end else 'experimental.review_candidate_status'
            self._notify(self.ui.text(status,actor=self.cfg.get('name',player.actor),
                                      count=budget['rounds']+1,total=COUNCIL_MAX_ROUNDS))
            payload_tools=self.ui.actions(tools)
            self._fit_request(dialogue,payload_tools,budget)
            msg,stats=self._reviewer().chat_turn(deepcopy(dialogue),payload_tools,self._review_config(),on_retry=lambda attempt,error,wait,total:self._notify(self.ui.text('llm.retry',actor=self.cfg.get('name',player.actor)+'议长',attempt=attempt,error=error,wait=wait,total=total)))
            budget['rounds']+=1
            self._review_log('response',{'message':msg,'usage':stats},budget)
            for key,value in stats.items():
                if isinstance(value,(int,float)) and not isinstance(value,bool):self.council['usage'][key]=self.council['usage'].get(key,0)+value
            calls=msg.get('tool_calls') or []
            if phase_end and isinstance(msg.get('content'),str) and msg['content'].strip():
                phase_speech.append(msg['content'].strip())
            ids=[call.get('id') for call in calls]
            if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid council tool IDs')
            assistant={key:deepcopy(msg[key]) for key in ('content','reasoning_content','tool_calls',REPLAY_KEY) if key in msg}
            assistant['role']='assistant';dialogue.append(assistant)
            budget['_logged']=len(dialogue) # 此响应已按原始 message/usage 记录，不再重复。
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
        # 地图只需要字形与角色；先把它们压成小字典，再丢掉大目录。
        glyphs,role_kinds=map_presentation(state['facts'])
        state['facts']['glyphs']=glyphs;state['facts']['role_kinds']=role_kinds
        # These are already captured verbatim as event/command records. Avoid
        # copying all old events and monarch summaries after every read query.
        for key in ('news','building_catalog','unit_catalog','terrain_catalog','land','threats','battle','nations','countries'):
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
        return self._plan_review(player,receipt)

    def api(self,player,capability,method,arguments,next_call):
        """Trusted API callers (scripts, rule AI) reach plan as government.plan."""
        self._prepare(player)
        receipt=next_call()
        if (capability,method)!=('native:government','plan') or not receipt['ok']:return receipt
        return self._plan_review(player,receipt)

    def _plan_review(self,player,receipt):
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
        # 同一份国策重复提交：不重开议会（每轮审核要花十次模型调用），
        # 也不把「制定于」和到期日往后推。
        # 只有领域层确认「有效期内未变化」才可跳过。到期原文重交已是新草稿，
        # 必须走完审核，否则会留下 approved=None、永远不能结束回合。
        if receipt['data'].get('status') == 'approved':
            return {**receipt,'ok':True,'status':'effective','code':None,
                    'message':self.ui.text('experimental.plan_unchanged')}
        self._notify(self.ui.text('experimental.review_preparing_status',actor=self.cfg.get('name',player.actor)))
        # 只延续尚未通过的审核。通过即清空；档案存在不代表向议长开放全历史。
        dialogue=self.council['dialogue']
        if not dialogue:
            dialogue.append({'role':'system','content':self.requirements+self.ui.text('experimental.council',name=self.cfg.get('name',player.actor))+
                             ('\n当前剧本：\n'+self.cfg['extra_summary'] if self.cfg.get('extra_summary') else '')})
        # No candidate, monarch reasoning, or post-draft snapshot in step one.
        # 模型可见的写工具就是命令伞加上几个单动作工具；其余只读。
        read_only={s['function']['name'] for s in player.actions()}-{'build','economy','military','relations','intelligence','plan','end_turn','explain_plan','script'}
        trajectory=[]
        for row in self.council['archive'][self.council['trace_start']:]:
            item=prune_row(deepcopy(row))
            if item.get('kind')=='action' and item.get('action') in read_only:
                # Historical queries must not replace the current plan receipt.
                query_receipt=item.get('receipt') or {}
                item['receipt']={k:query_receipt.get(k) for k in ('ok','status','code','message')}
            trajectory.append(item)
        evidence=review_evidence({'previous_plan':self.council['last_plan'],
                  'trajectory':trajectory,
                  'stage':1,'period':obs_period})
        prefix=self.ui.text('experimental.review_first')
        dialogue.append({'role':'user','content':prefix+encode(evidence)})
        # Reviewer sees the same actor-filtered map through the selected UI mod.
        from clients.context import MAP_KEY
        view=ModelView({**self.cfg,**self.review_cfg},presentation=self.presentation)
        current,image=view.map_context(self.council['last_snapshot'])
        suffix='\n'+current
        dialogue[-1]['content']+=suffix
        if image:dialogue[-1][MAP_KEY]=image
        # 证据随回合增长：每次调用前按议长预算降级（见 _fit_request）。
        evidence_fit={'index':len(dialogue)-1,'prefix':prefix,'suffix':suffix,
                      'evidence':evidence,'step':0,'omitted':[]}
        def paralysis_receipt(assessment):
            reason=self.ui.text('experimental.parliament_paralyzed_reason')
            decision=self._government('decide',True,reason)
            decision['message']=self.ui.text('experimental.parliament_paralyzed')
            self._review_log('decision',decision,budget)
            self.council['archive'].append({'kind':'review','period':obs_period,'model':self.review_cfg.get('model'),
                                           'assessment':assessment,'decision':deepcopy(decision),
                                           'dialogue':deepcopy(dialogue),'auto_approved_due_to_paralysis':True})
            self.council['last_plan']={'text':candidate,'period':obs_period,'approved':True,
                                       'state_at_submission':evidence_state(self.council['last_snapshot'])}
            self.council['trace_start']=len(self.council['archive'])
            dialogue.clear()
            self._notify(self.ui.text('experimental.review_autoapproved_status',actor=self.cfg.get('name',player.actor)))
            return {**receipt,'ok':True,'status':'effective','code':None,
                    'message':decision['message'],'data':decision}
        budget={'rounds':0,'warned':False,'period':obs_period,'_evidence':evidence_fit}
        assessment,paralyzed=self._council_turn(player,dialogue,budget=budget,phase_end=True)
        if paralyzed:return paralysis_receipt(assessment)
        dialogue.append({'role':'user','content':self.ui.text('experimental.review_second')+encode({'new_plan':candidate,'period':obs_period})})
        verdict=self._council_turn(player,dialogue,budget=budget,finalize=True)
        if verdict is None:return paralysis_receipt(assessment)
        decision=self._government('decide',verdict['approved'],verdict['reason'])
        self._review_log('decision',decision,budget)
        self.council['archive'].append({'kind':'review','period':obs_period,'model':self.review_cfg.get('model'),
                                           'assessment':assessment,'decision':deepcopy(decision),'dialogue':deepcopy(dialogue)})
        self.council['last_plan']={'text':candidate,'period':obs_period,'approved':verdict['approved'],
                                   'state_at_submission':evidence_state(self.council['last_snapshot'])}
        self.council['trace_start']=len(self.council['archive'])
        if verdict['approved']:dialogue.clear()
        elif decision['streak']==3:self._takeover=True
        status='experimental.review_approved_status' if verdict['approved'] else 'experimental.review_rejected_status'
        self._notify(self.ui.text(status,actor=self.cfg.get('name',player.actor)))
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
