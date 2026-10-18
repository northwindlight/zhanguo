"""LLM player: provider tools, replay, compression and memory belong here."""
from copy import deepcopy
from dataclasses import dataclass,field
import json
from types import SimpleNamespace
from clients import context as ctx
from clients import memory
from ui import load_ui
from clients.provider import make_backend,REPLAY_KEY


@dataclass
class LLMState:
    history:list=field(default_factory=list)
    replay:list=field(default_factory=list)
    summaries:list=field(default_factory=list)
    blocks:list=field(default_factory=list)
    long_memory:str=''
    usage:dict=field(default_factory=dict)
    event_cursor:int=0
    generation:int=0

    def to_save(self):
        return deepcopy({'replay':self.replay,'summaries':self.summaries,'blocks':self.blocks,
                         'long_memory':self.long_memory,'usage':self.usage,'event_cursor':self.event_cursor,'generation':self.generation,'history':self.history})

    @classmethod
    def from_save(cls,data):
        if not isinstance(data,dict) or set(data)!={'replay','summaries','blocks','long_memory','usage','event_cursor','generation','history'}:raise ValueError('Invalid LLM state')
        data=deepcopy(data)
        if any(not isinstance(data[key],list) for key in ('replay','summaries','blocks','history')) or not isinstance(data['long_memory'],str) or not isinstance(data['usage'],dict) or type(data['event_cursor']) is not int or data['event_cursor']<0:raise ValueError('Invalid LLM state fields')
        if type(data['generation']) is not int or data['generation']<0:raise ValueError('Invalid control generation')
        if any(not isinstance(row,dict) or set(row)!={'period','text'} or type(row['period']) is not int or row['period']<0 or not isinstance(row['text'],str) for row in data['history']):raise ValueError('Invalid searchable history')
        for record in data['replay']:
            if not isinstance(record,dict) or type(record.get('turn')) is not int or record['turn']<0 or not isinstance(record.get('messages'),list):raise ValueError('Invalid replay')
            assert_paired(record['messages'])
            record['_tokens']=ctx.messages_tokens(record['messages'])
        return cls(**data)


def assert_paired(messages):
    pending=set()
    for msg in messages:
        if not isinstance(msg,dict):raise ValueError('Invalid replay message')
        if msg.get('role')=='assistant':
            if pending:raise ValueError('Unanswered tool batch')
            for call in msg.get('tool_calls') or []:
                ident=call.get('id')
                if not isinstance(ident,str) or not ident or ident in pending:raise ValueError('Invalid tool ID')
                pending.add(ident)
        elif msg.get('role')=='tool':
            ident=msg.get('tool_call_id')
            if ident not in pending:raise ValueError('Orphan tool reply')
            pending.remove(ident)
    if pending:raise ValueError('Unanswered tool call')


class LLMClient:
    kind='llm';version='2'
    def __init__(self,cfg,state=None,backend=None,ui=None):
        self.cfg=deepcopy(cfg);self.state=state or LLMState();self._backend=backend
        self.presentation=ui or load_ui(self.cfg)
        self.ui=self.presentation.llm(self.cfg)
        self._allowed_actions=None

    def _provider(self):
        if self._backend is None:self._backend=make_backend(self.cfg)
        return self._backend

    def _request_config(self):
        return {**self.cfg,'_ui_expand_message':self.presentation.expand_message}

    def _system(self,player):
        text=self.ui.system()
        state=player.action_state() if hasattr(player,'action_state') else {}
        context=state.get('context',{})
        if context.get('prompt'):text+='\n'+context['prompt']
        if context.get('summary'):text+='\n'+context['summary']
        return text

    def _usage(self,stats):
        for key,value in stats.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):self.state.usage[key]=self.state.usage.get(key,0)+value

    def _prepare(self,player):
        if hasattr(player,'action_state'):
            state=player.action_state()
            if state['generation']!=self.state.generation:
                if state['context'].get('reset_memory'):
                    self.state=LLMState(usage=deepcopy(self.state.usage),event_cursor=player.events(0)['cursor'],generation=state['generation'])
                else:self.state.generation=state['generation']

    @staticmethod
    def _dialogue(player,messages):
        if hasattr(player,'record_dialogue'):player.record_dialogue(messages)
    PROSE={'query','text','summary','note','old','new','source','content','reason','output','inputs'}

    def _arguments(self,arguments):
        """入口：模型用本地化键值（金/农场/步兵），翻回引擎认的短键。

        正文参数（text/summary/note/…）不碰：里面出现"木"是文章，不是键值。
        """
        values=getattr(self.ui,'localized_values',None)
        if not isinstance(arguments,dict) or values is None:return arguments
        table=values()
        def fix(value):
            if isinstance(value,str):return table.get(value,value)
            if isinstance(value,list):return [fix(item) for item in value]
            if isinstance(value,dict):return {k:fix(v) for k,v in value.items()}
            return value
        return {key:(value if key in self.PROSE else fix(value)) for key,value in arguments.items()}

    def _submit(self,player,action,arguments):
        if self._allowed_actions is not None and (not isinstance(action,str) or action not in self._allowed_actions):
            return {'api':player.api_version,'actor':player.actor,'action':action,'period':self._observed_period,
                    'ok':False,'status':'rejected','code':'tool_not_exposed',
                    'message':self.ui.text('llm.tool_not_exposed'),'data':None}
        if action!='memory_search' or hasattr(player,'action_state'):return player.submit(action,self._arguments(arguments))
        base={'api':player.api_version,'actor':player.actor,'action':action,'period':player.observe()['period']}
        try:
            if not isinstance(arguments,dict) or set(arguments)-{'query','limit'}:raise ValueError('memory_search 参数为 query 与可选 limit')
            data=memory.search(self.state.history,render=self._memory_render,**arguments)
        except (ValueError,TypeError) as exc:return {**base,'ok':False,'status':'rejected','code':'invalid_arguments','message':str(exc),'data':None}
        return {**base,'ok':True,'status':'effective','code':None,'message':'','data':data}

    def _memory_render(self,action,arguments,receipt):
        """Render an old JSON history line through the same UI the model sees."""
        render=getattr(self.ui,'receipt',None)
        if not callable(render):return ''
        try:return render({**receipt,'action':action},arguments)
        except (KeyError,TypeError,ValueError,AttributeError,IndexError):return ''

    def _receipt(self,player,action,arguments,receipt):pass
    def _exchange(self,player,assistant,replies):pass
    def _after_batch(self,player,messages,emit):return False
    def _context(self,player,obs,batch,tools):
        tail,image=self.ui.state(obs,batch['items'])
        return ctx.build(cfg=self.cfg,mem=self.state.replay,sums=self.state.summaries,blocks=self.state.blocks,
                         system_text=self._system(player),tail_text=tail,tail_map=image,archive_messages=self.ui.text,
                         tool_tokens=ctx.est_tokens(json.dumps(tools,ensure_ascii=False)),long_memory=self.state.long_memory)

    def _compact(self,player,messages,plan,tools,emit):
        # ctx.slide operates on a client-owned memory store, never the game world.
        memory=SimpleNamespace(turn_memory={player.actor:self.state.replay},summary_blocks={player.actor:self.state.blocks},turn=player.observe()['period'])
        dropped=ctx.slide(memory,player.actor,plan)
        if not dropped:return
        summary='\n'.join(f'第{row["turn"]}期：{row["text"]}' for row in self.state.summaries if dropped[0]['turn']<=row['turn']<=dropped[-1]['turn'])
        # 压缩成功后块里存的是「当时那份长期记忆」，会被下一次压缩整体取代；压缩失败时
        # 存的是逐期小结，不能被取代——归档渲染据此决定要不要重播这个块。
        snapshot=False
        if plan.compact:
            # Reuse the actual request prefix once, without dumping the same
            # replay as JSON again. Compression has its own input/output budget.
            ceiling=min(120000,int(self.cfg.get('ctx_window') or 128000))
            reserve=int(self.cfg.get('ctx_compact_tokens') or 1500)+ctx.SAFETY_TOKENS
            instruction={'role':'user','content':self.ui.text('llm.compact',memory=self.state.long_memory,history=summary)}
            budget=max(0,ceiling-reserve-ctx.msg_tokens(instruction))
            prefix=[];pending=set();used=0
            for message in messages:
                cost=ctx.msg_tokens(message)
                if used+cost>budget:break
                prefix.append(deepcopy(message));used+=cost
                if message.get('role')=='assistant':pending.update(c['id'] for c in message.get('tool_calls',[]))
                elif message.get('role')=='tool':pending.discard(message.get('tool_call_id'))
            while pending and prefix:
                last=prefix.pop()
                if last.get('role')=='tool':pending.add(last['tool_call_id'])
                if last.get('role')=='assistant':pending.difference_update(c['id'] for c in last.get('tool_calls',[]))
            prompt=prefix+[instruction]
            try:
                if ctx.messages_tokens(prompt)+reserve>ceiling:
                    raise ValueError('Compression instruction and output cannot fit the configured window')
                for attempt in range(1,11):
                    if emit:emit(self.ui.text('llm.compacting',actor=player.actor,attempt=attempt,limit=10))
                    compressed,stats=self._provider().complete_text(prompt,{**self._request_config(),'max_tokens':int(self.cfg.get('ctx_compact_tokens') or 1500)},tools=[])
                    self._usage(stats)
                    if isinstance(compressed,str) and len(compressed.strip())>=20:break
                else:raise ValueError('Empty or too short memory compression after 10 attempts')
            except Exception as exc:
                # Legacy behavior: compression is optional. Keep the previous
                # long-term memory and archive these turns using their summaries.
                if emit:emit(self.ui.text('llm.compact_failed',actor=player.actor,error=type(exc).__name__))
            else:
                summary=compressed.strip();self.state.long_memory=summary;snapshot=True
        if plan.compact and summary:self.state.blocks.append({'from':dropped[0]['turn'],'to':dropped[-1]['turn'],'at':memory.turn,'text':summary,'memory':snapshot})
        # Long memory subsumes older archive blocks; summaries remain available
        # through the bounded searchable history.
        self.state.blocks=self.state.blocks[-20:]
        self.state.summaries=self.state.summaries[-240:]
        if emit:emit(self.ui.text('llm.archived',actor=player.actor,count=len(dropped)))

    def play(self,player,*,max_steps=None,emit=None,head=None,on_call=None,on_result=None):
        self._prepare(player)
        rules=getattr(self.ui,'rules',None)
        if callable(rules):rules(player.rules('all'))
        obs=player.observe()
        if not obs['can_act']:return []
        schemas=player.actions()
        tools=self.ui.actions(schemas if any(s['function']['name']=='memory_search' for s in schemas) else [*schemas,deepcopy(memory.SCHEMA)]);batch=player.events(self.state.event_cursor)
        self._allowed_actions={tool['function']['name'] for tool in tools}
        self._observed_period=obs['period']
        messages,plan=self._context(player,obs,batch,tools)
        self._dialogue(player,[messages[0],messages[-1]])
        description=self.ui.context_plan(plan);hit=ctx.rolling_hit(player.actor)
        if hit is not None:description+=self.ui.text('context.observed',ratio=hit)
        if head:head(self.ui.text('llm.context',actor=player.actor,plan=description))
        elif emit:emit(self.ui.text('llm.context',actor=player.actor,plan=description))
        start=len(messages)-1;receipts=[];ended=False
        limit=int(max_steps if max_steps is not None else self.cfg.get('max_steps',28))
        if limit<1:raise ValueError('max_steps must be positive')
        for step in range(limit):
            if emit:emit(self.ui.text('llm.request',actor=player.actor,count=step+1))
            msg,stats=self._provider().chat_turn(messages,tools,self._request_config(),on_retry=(lambda attempt,error,wait,total:emit(self.ui.text('llm.retry',actor=player.actor,attempt=attempt,total=total,error=error,wait=wait))) if emit else None)
            self._usage(stats)
            ctx.record_hit(player.actor,stats.get('hit',0),stats.get('miss',0))
            calls=msg.get('tool_calls') or []
            assistant={key:deepcopy(msg[key]) for key in ('content','reasoning_content','tool_calls',REPLAY_KEY) if key in msg}
            assistant['role']='assistant';messages.append(assistant)
            if emit and msg.get('content'):emit(self.ui.text('llm.speech',actor=player.actor,text=msg['content']))
            if emit and msg.get('reasoning_content'):emit(self.ui.text('llm.thinking',actor=player.actor,text=msg['reasoning_content']))
            # Validate the batch before any action, to avoid malformed provider
            # responses leaving executed commands without replay counterparts.
            ids=[call.get('id') for call in calls]
            if any(not isinstance(ident,str) or not ident for ident in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid provider tool IDs')
            self._dialogue(player,[assistant])
            batch_receipts=[];batch_contents=[]
            for call in calls:
                fn=call.get('function') or {};action=fn.get('name');arguments={}
                try:arguments=json.loads(fn.get('arguments') or '{}')
                except (ValueError,TypeError):
                    receipt=self._submit(player,action,[])
                else:receipt=self._submit(player,action,arguments)
                receipts.append(receipt);batch_receipts.append(receipt)
                if receipt.get('code')=='tool_not_exposed' and emit:emit('⚠ '+receipt['message'])
                render=getattr(self.ui,'receipt',None)
                content=render(receipt,arguments) if callable(render) else json.dumps(receipt,ensure_ascii=False)
                messages.append({'role':'tool','tool_call_id':call['id'],'content':content})
                batch_contents.append(content if callable(render) and isinstance(content,str) else '')
                self._receipt(player,action,fn.get('arguments'),receipt)
                if on_result:on_result(action,fn.get('arguments'),receipt)
                if action=='end_turn' and receipt['ok']:
                    ended=True
                    self.state.summaries.append({'turn':obs['period'],'text':receipt['data']['text']})
            memory.record(self.state.history,obs['period'],memory.action_text(assistant,calls,batch_receipts,batch_contents))
            if on_call:on_call(step,receipts)
            assert_paired(messages[start:])
            self._dialogue(player,messages[-len(calls):] if calls else [])
            self._exchange(player,assistant,messages[-len(calls):] if calls else [])
            if hasattr(player,'action_state') and not player.action_state()['active']:
                if emit:emit(player.action_state()['context'].get('prompt','Actor control changed'))
                return receipts
            current_observation=player.observe()
            if not current_observation['can_act']:ended=True
            if self._after_batch(player,messages,emit):
                obs=player.observe();batch=player.events(self.state.event_cursor)
                messages,plan=self._context(player,obs,batch,tools)
                start=len(messages)-1;ended=False
                continue
            if ended:break
            # 不再在批次之间追加上下文：模型本来就带着自己的对话历史与全部工具回执，
            # 要看最新状态就再调一次专门工具。回合开局的战情总纲负责给全景。
            if not calls:messages.append({'role':'user','content':self.ui.text('llm.continue')})
        # Only the normal end_turn gate can finish a step-limited turn.
        if not ended:
            fallback='达到本回合调用上限；已执行行动以回执为准，其余计划下回合继续。'
            receipt=self._submit(player,'end_turn',{'summary':fallback})
            receipts.append(receipt)
            if not receipt['ok']:raise RuntimeError(self.ui.text('llm.limit')+' '+receipt['message'])
            self.state.summaries.append({'turn':obs['period'],'text':fallback})
            messages.append({'role':'user','content':fallback})
            if emit:emit(fallback)
            if on_result:on_result('end_turn',{'summary':fallback},receipt)
        record={'turn':obs['period'],'messages':deepcopy(messages[start:])}
        record['_tokens']=ctx.messages_tokens(record['messages']);self.state.replay.append(record)
        self.state.event_cursor=batch['cursor']
        self._compact(player,messages,plan,tools,emit)
        return receipts

    def search_memory(self,query,limit=3):return memory.search(self.state.history,query,limit,render=self._memory_render)

    def to_save(self):return {'kind':self.kind,'version':self.version,'state':self.state.to_save()}
