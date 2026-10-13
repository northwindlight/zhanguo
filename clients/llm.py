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
        if any(not isinstance(data[key],list) for key in ('replay','summaries','blocks','history')) or not isinstance(data['long_memory'],str) or not isinstance(data['usage'],dict) or type(data['event_cursor']) is not int or data['event_cursor']<0:raise ValueError('Invalid LLM state fields')
        if type(data['generation']) is not int or data['generation']<0:raise ValueError('Invalid control generation')
        if any(not isinstance(row,dict) or set(row)!={'period','text'} or type(row['period']) is not int or row['period']<0 or not isinstance(row['text'],str) for row in data['history']):raise ValueError('Invalid searchable history')
        for record in data['replay']:
            if not isinstance(record,dict) or type(record.get('turn')) is not int or record['turn']<0 or not isinstance(record.get('messages'),list):raise ValueError('Invalid replay')
            assert_paired(record['messages'])
        return cls(**deepcopy(data))


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
    def _submit(self,player,action,arguments):
        if action!='memory_search':return player.submit(action,arguments)
        base={'api':player.api_version,'actor':player.actor,'action':action,'period':player.observe()['period']}
        try:
            if not isinstance(arguments,dict) or set(arguments)-{'query','limit'}:raise ValueError('memory_search 参数为 query 与可选 limit')
            data=memory.search(self.state.history,**arguments)
        except (ValueError,TypeError) as exc:return {**base,'ok':False,'status':'rejected','code':'invalid_arguments','message':str(exc),'data':None}
        return {**base,'ok':True,'status':'effective','code':None,'message':'','data':data}

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
        text='\n'.join(json.dumps(row,ensure_ascii=False) for row in dropped)
        if plan.compact:
            prompt=deepcopy(messages)+[{'role':'user','content':self.ui.text('llm.compact',memory=self.state.long_memory,history=text)}]
            try:
                summary,stats=self._provider().complete_text(prompt,self._request_config(),tools=tools)
                if not isinstance(summary,str) or not summary.strip():raise ValueError('Empty memory compression')
            except Exception:
                # Restore records on provider failure; the host will stop the game.
                self.state.replay[:0]=dropped
                raise
            self._usage(stats);self.state.long_memory=summary
        else:
            summary='\n'.join(row['text'] for row in self.state.summaries if dropped[0]['turn']<=row['turn']<=dropped[-1]['turn'])
        self.state.blocks.append({'from':dropped[0]['turn'],'to':dropped[-1]['turn'],'at':memory.turn,'text':summary})
        if emit:emit(self.ui.text('llm.archived',actor=player.actor,count=len(dropped)))

    def play(self,player,*,max_steps=None,emit=None,head=None,on_call=None,on_result=None):
        self._prepare(player)
        rules=getattr(self.ui,'rules',None)
        if callable(rules):rules(player.rules('all'))
        obs=player.observe()
        if not obs['can_act']:return []
        schemas=player.actions()
        if any(s['function']['name']=='memory_search' for s in schemas):raise ValueError('memory_search 与客户端记忆工具重名')
        tools=self.ui.actions([*schemas,deepcopy(memory.SCHEMA)]);batch=player.events(self.state.event_cursor)
        messages,plan=self._context(player,obs,batch,tools)
        self._dialogue(player,[messages[0],messages[-1]])
        if head:head(self.ui.text('llm.context',actor=player.actor,plan=self.ui.context_plan(plan)))
        elif emit:emit(self.ui.text('llm.context',actor=player.actor,plan=self.ui.context_plan(plan)))
        start=len(messages)-1;receipts=[];ended=False
        limit=int(max_steps if max_steps is not None else self.cfg.get('max_steps',28))
        if limit<1:raise ValueError('max_steps must be positive')
        for step in range(limit):
            if emit:emit(self.ui.text('llm.request',actor=player.actor,count=step+1))
            msg,stats=self._provider().chat_turn(messages,tools,self._request_config(),on_retry=(lambda attempt,error,wait,total:emit(self.ui.text('llm.retry',actor=player.actor,attempt=attempt,total=total,error=error,wait=wait))) if emit else None)
            self._usage(stats)
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
            batch_receipts=[]
            for call in calls:
                fn=call.get('function') or {};action=fn.get('name')
                try:arguments=json.loads(fn.get('arguments') or '{}')
                except (ValueError,TypeError):
                    receipt=self._submit(player,action,[])
                else:receipt=self._submit(player,action,arguments)
                receipts.append(receipt);batch_receipts.append(receipt)
                render=getattr(self.ui,'receipt',None)
                messages.append({'role':'tool','tool_call_id':call['id'],'content':render(receipt) if callable(render) else json.dumps(receipt,ensure_ascii=False)})
                self._receipt(player,action,fn.get('arguments'),receipt)
                if on_result:on_result(action,fn.get('arguments'),receipt)
                if action=='end_turn' and receipt['ok']:
                    ended=True
                    self.state.summaries.append({'turn':obs['period'],'text':receipt['data']['text']})
            memory.record(self.state.history,obs['period'],memory.action_text(assistant,calls,batch_receipts))
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
            refresh=getattr(self.ui,'refresh',None)
            if calls and callable(refresh):
                update=refresh(current_observation)
                if update:
                    current={'role':'user','content':update}
                    messages.append(current);self._dialogue(player,[current])
            if not calls:messages.append({'role':'user','content':self.ui.text('llm.continue')})
        # No synthetic end_turn: a provider/step failure must not commit a turn.
        if not ended:raise RuntimeError(self.ui.text('llm.limit'))
        self.state.replay.append({'turn':obs['period'],'messages':deepcopy(messages[start:])})
        self.state.event_cursor=batch['cursor']
        self._compact(player,messages,plan,tools,emit)
        return receipts

    def to_save(self):return {'kind':self.kind,'version':self.version,'state':self.state.to_save()}
