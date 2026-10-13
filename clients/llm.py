"""LLM player: provider tools, replay, compression and memory belong here."""
from copy import deepcopy
from dataclasses import dataclass,field
import json
from types import SimpleNamespace
import ctx
from llm_provider import make_backend,REPLAY_KEY


@dataclass
class LLMState:
    replay:list=field(default_factory=list)
    summaries:list=field(default_factory=list)
    blocks:list=field(default_factory=list)
    long_memory:str=''
    usage:dict=field(default_factory=dict)
    event_cursor:int=0

    def to_save(self):
        return deepcopy({'replay':self.replay,'summaries':self.summaries,'blocks':self.blocks,
                         'long_memory':self.long_memory,'usage':self.usage,'event_cursor':self.event_cursor})

    @classmethod
    def from_save(cls,data):
        if not isinstance(data,dict) or set(data)!={'replay','summaries','blocks','long_memory','usage','event_cursor'}:raise ValueError('Invalid LLM state')
        if any(not isinstance(data[key],list) for key in ('replay','summaries','blocks')) or not isinstance(data['long_memory'],str) or not isinstance(data['usage'],dict) or type(data['event_cursor']) is not int or data['event_cursor']<0:raise ValueError('Invalid LLM state fields')
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
    kind='llm';version='1'
    def __init__(self,cfg,state=None,backend=None):
        self.cfg=deepcopy(cfg);self.state=state or LLMState();self._backend=backend

    def _provider(self):
        if self._backend is None:self._backend=make_backend(self.cfg)
        return self._backend

    def _system(self,player):
        # Stable across turns; rules and action schemas belong to the service.
        return ('你是战国的一国玩家。只用提供的玩家工具行动；内容使用命名空间 ID，坐标从 0 开始。'
                '玩家状态已按你的权限过滤。player_rules 查询规则，player_state 查询最新事实。'
                '外交 submitted 仅表示提交，等待后续结算；不要把提交当作条约已生效。'
                '结束前发布有效国策，再调用 end_turn 并给出行动小结。'
                +'\n'+str(self.cfg.get('system_prompt') or '')+'\n'+str(self.cfg.get('extra_summary') or ''))

    def _usage(self,stats):
        for key,value in stats.items():
            if isinstance(value,(int,float)) and not isinstance(value,bool):self.state.usage[key]=self.state.usage.get(key,0)+value

    def _compact(self,player,messages,plan,tools,emit):
        # ctx.slide operates on a client-owned memory store, never the game world.
        memory=SimpleNamespace(turn_memory={player.actor:self.state.replay},summary_blocks={player.actor:self.state.blocks},turn=player.observe()['period'])
        dropped=ctx.slide(memory,player.actor,plan)
        if not dropped:return
        text='\n'.join(json.dumps(row,ensure_ascii=False) for row in dropped)
        if plan.compact:
            prompt=deepcopy(messages)+[{'role':'user','content':'不要调用工具。以旧长期记忆为基础合并下面的历史，保留国策、盟约、待办和教训，返回新的长期记忆。\n旧记忆：'+self.state.long_memory+'\n历史：'+text}]
            try:
                summary,stats=self._provider().complete_text(prompt,self.cfg,tools=tools)
                if not isinstance(summary,str) or not summary.strip():raise ValueError('Empty memory compression')
            except Exception:
                # Restore records on provider failure; the host will stop the game.
                self.state.replay[:0]=dropped
                raise
            self._usage(stats);self.state.long_memory=summary
        else:
            summary='\n'.join(row['text'] for row in self.state.summaries if dropped[0]['turn']<=row['turn']<=dropped[-1]['turn'])
        self.state.blocks.append({'from':dropped[0]['turn'],'to':dropped[-1]['turn'],'at':memory.turn,'text':summary})
        if emit:emit(f'🧠 {player.actor} 归档 {len(dropped)} 回合')

    def play(self,player,*,max_steps=None,emit=None,head=None,on_call=None,on_result=None):
        obs=player.observe()
        if not obs['can_act']:return []
        tools=player.actions();batch=player.events(self.state.event_cursor)
        tail=json.dumps({'state':obs,'events':batch['items']},ensure_ascii=False,sort_keys=True)
        if obs['period']<self.cfg.get('joined_at',0)+20 and self.cfg.get('extra_prompt'):tail+='\n【临时背景】'+self.cfg['extra_prompt']
        messages,plan=ctx.build(cfg=self.cfg,mem=self.state.replay,sums=self.state.summaries,blocks=self.state.blocks,
                                system_text=self._system(player),tail_text=tail,
                                tool_tokens=ctx.est_tokens(json.dumps(tools,ensure_ascii=False)),long_memory=self.state.long_memory)
        if head:head(f'🧠 {player.actor} 上下文 {plan.describe()}')
        elif emit:emit(f'🧠 {player.actor} 上下文 {plan.describe()}')
        start=len(messages)-1;receipts=[];ended=False
        limit=int(max_steps if max_steps is not None else self.cfg.get('max_steps',28))
        if limit<1:raise ValueError('max_steps must be positive')
        for step in range(limit):
            msg,stats=self._provider().chat_turn(messages,tools,self.cfg,on_retry=(lambda attempt,error,wait,total:emit(f'⚠ {player.actor} 重试 {attempt}/{total} {error}，等待 {wait}s')) if emit else None)
            self._usage(stats)
            calls=msg.get('tool_calls') or []
            assistant={key:deepcopy(msg[key]) for key in ('content','reasoning_content','tool_calls',REPLAY_KEY) if key in msg}
            assistant['role']='assistant';messages.append(assistant)
            if emit and msg.get('content'):emit(f'🗣 {player.actor} {msg["content"]}')
            if emit and msg.get('reasoning_content'):emit(f'💭 {player.actor} {msg["reasoning_content"]}')
            # Validate the batch before any action, to avoid malformed provider
            # responses leaving executed commands without replay counterparts.
            ids=[call.get('id') for call in calls]
            if any(not isinstance(ident,str) or not ident for ident in ids) or len(set(ids))!=len(ids):raise ValueError('Invalid provider tool IDs')
            for call in calls:
                fn=call.get('function') or {};action=fn.get('name')
                try:arguments=json.loads(fn.get('arguments') or '{}')
                except (ValueError,TypeError):
                    receipt=player.submit(action,[])
                else:receipt=player.submit(action,arguments)
                receipts.append(receipt)
                messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(receipt,ensure_ascii=False)})
                if on_result:on_result(action,fn.get('arguments'),receipt)
                if action=='end_turn' and receipt['ok']:
                    ended=True
                    self.state.summaries.append({'turn':obs['period'],'text':receipt['data']['text']})
            if on_call:on_call(step,receipts)
            assert_paired(messages[start:])
            if ended:break
            if not calls:messages.append({'role':'user','content':'请继续使用工具；本轮需要有效国策和 end_turn 才能结束。'})
        # No synthetic end_turn: a provider/step failure must not commit a turn.
        if not ended:raise RuntimeError('LLM 未在步数上限内完成 end_turn；停止本局，保留最近结算边界存档')
        self.state.replay.append({'turn':obs['period'],'messages':deepcopy(messages[start:])})
        self.state.event_cursor=batch['cursor']
        self._compact(player,messages,plan,tools,emit)
        return receipts

    def to_save(self):return {'kind':self.kind,'version':self.version,'state':self.state.to_save()}
