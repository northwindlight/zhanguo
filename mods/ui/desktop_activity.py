"""Observer-only, bounded presentation of real action receipts and settlement.

Capture runs on the session thread. Batches are drained after all tools in a
model response have executed; the renderer never drives a rule action.
"""
import time
from copy import deepcopy


def key(collection, row):
    if collection in ('armies','guards'):return f'{row["owner"]}/{row["id"]}'
    if collection=='ownership':return f'{row["x"]},{row["y"]}'
    return str(row['id'])


# Live per-action labels for the map chip; anything unmapped is 处理政务.
ACTION_LABELS={
    'plan':'正在制定国策',
    'send_letter':'正在写信','gift':'正在写信','guarantee':'正在写信',
    'propose_defense':'正在写信','propose_vassalage':'正在写信','submit_vassalage':'正在写信',
    'declare_war':'正在写信','offer_peace':'正在写信','accept_peace':'正在写信',
    'reject_peace':'正在写信','accept_proposal':'正在写信','reject_proposal':'正在写信',
    'break_defense':'正在写信','cancel_guarantee':'正在写信','share_map':'正在写信',
    'release_vassal':'正在写信','vote':'正在写信','independence_war':'正在写信',
    'build':'正在施工','demolish':'正在拆除建筑','recruit':'正在整军','army':'正在整军','disband':'正在整军',
    'move':'正在调兵','attack':'正在调兵','retreat':'正在调兵',
    'buy':'正在交易','sell':'正在交易','bank':'正在交易','borrow':'正在交易',
    'spy':'正在打探情报','intel':'正在打探情报','buy_report':'正在打探情报',
    'end_turn':'本期政务告一段落',
}

def activity_label(action):
    return ACTION_LABELS.get(action,'在处理政务')


def difference(before, after):
    delta={}
    for collection, rows in after.items():
        old={key(collection,r):r for r in before.get(collection,[])}
        new={key(collection,r):r for r in rows}
        upsert=[r for k,r in new.items() if old.get(k)!=r]
        remove=[k for k in old if k not in new]
        if upsert or remove:delta[collection]={'upsert':deepcopy(upsert),'remove':remove}
    return delta


class TimelineCapture:
    LIMIT=2048
    def __init__(self,host,contexts=None,activity=None):
        self.host=host;self.period=host.clock();self.beats=[];self.warning=None;self.actor=None
        self.contexts=contexts if contexts is not None else {}
        self.activity=activity
        self.turn_started=None;self.turn_tokens=None
        self.count=0
        self.previous=self.scene()
        self.actual=deepcopy(self.previous)
        self.event_cursor=max((e['id'] for e in host.records()['events']),default=0)
        self.names={a:host.roster[a]['name'] for a in host.roster}
        self.message_ids={m['id'] for m in host.records()['messages']}
        self.initial_homes={a:self.home(a) for a in host.actors()}
        self.attack_sources={}

    def _token_total(self,actor):
        """LLM 客户端累计 token 用量；规则 AI 无模型调用，计 0。"""
        state=getattr(self.host.clients.get(actor),'state',None)
        usage=getattr(state,'usage',None)
        if not isinstance(usage,dict):return 0
        return sum(value for key,value in usage.items() if 'token' in key and isinstance(value,(int,float)))

    def _turn_meta(self,actor,closing=False):
        """上下文/用时/token 概览：上下文来自宿主缓存的模型 emit，用时为实测。"""
        seconds=None
        if self.turn_started is not None:
            seconds=round(time.monotonic()-self.turn_started,2)
        tokens=None
        if self.turn_tokens is not None:
            tokens=max(0,self._token_total(actor)-self.turn_tokens)
        elif closing:
            tokens=0
        return {'context':self.contexts.get(actor) or None,'turn_seconds':seconds,'tokens':tokens}

    def scene(self):return self.host.ui.desktop_scene(self.host.observer_scene())

    def add(self,phase,title,text,position=None,*,after=None,source='',duration=1.5,**extra):
        if after is not None:
            from .desktop_defense import project
            project(after)
        if self.count>=self.LIMIT:raise ValueError('本期演出超过2048片段，改用完整快照同步')
        self.count+=1
        delta=difference(self.previous,after) if after is not None else {}
        turn_title=f'{self.names.get(self.actor,self.actor)} · 第 {self.period+1} 回合' if self.actor else f'第 {self.period+1} 回合 · 结算'
        self.beats.append({'phase':phase,'title':title,'turn_title':turn_title,'actor':self.actor,'text':text[:180],'position':position,
                           'client_kind':getattr(self.host.clients.get(self.actor),'kind',None) if self.actor else None,
                           'playback_scope':'actor' if self.actor else 'settlement',
                           'duration':duration,'source':source,'delta':delta,**extra})
        if after is not None:self.previous=deepcopy(after)

    def home(self,actor):
        halls=sorted((b for b in self.previous['buildings'] if b['owner']==actor and
                     b['kind']=='native:town-hall' and b['remaining']==0),key=lambda b:b['id'])
        if halls:return [halls[0]['x'],halls[0]['y']]
        land=next((t for t in self.previous['ownership'] if t['owner']==actor),None)
        if land:return [land['x'],land['y']]
        unit=next((u for u in self.previous['armies'] if u['owner']==actor),None)
        return [unit['x'],unit['y']] if unit else None

    def national_settlements(self,final,events,phase):
        """Aggregate within each existing stage, never reorder the stages.

        Apply only the recorded production/military/tribute stock movements;
        banking, deliveries and other later changes still arrive at the final
        boundary. Maintenance uses its real before/after units, before battle.
        """
        flows={r['id']:r for r in final.get('resource_flows',[]) if r['period']==self.period}
        maintenance={}
        for event in events:
            if event['kind']=='maintenance':maintenance.setdefault(event['details']['owner'],[]).append(event)
        for account in list(self.previous['accounts']):
            owner=account['id'];flow=flows.get(owner)
            if flow is None:continue
            self.actor=owner;stage=deepcopy(self.previous)
            records=maintenance.get(owner,[]) if phase=='maintenance' else []
            if phase=='maintenance' and not records:continue
            flow=deepcopy(flow)
            for category in ('production_in','production_out','military_out','tribute_in','tribute_out'):
                if (category=='military_out')!=(phase=='maintenance'):flow[category]=[]
            changes={}
            for category in ('production_in','production_out','military_out','tribute_in','tribute_out'):
                sign=1 if category.endswith('_in') else -1
                for resource,amount in flow[category]:changes[resource]=changes.get(resource,0)+sign*amount
            flow['net']=[[r,a] for r,a in changes.items() if a]
            need=used=healed=starvation=0
            for event in records:
                d=event['details'];ids={key('armies',u) for u in d['before']}
                stage['armies']=[u for u in stage['armies'] if key('armies',u) not in ids]+deepcopy(d['after'])
                need+=d['need'];used+=d['used'];healed+=sum(h['hp'] for h in d['healing'])
                hp_after=sum(u['hp'] for u in d['after'])
                starvation+=max(0,sum(u['hp'] for u in d['before'])+sum(h['hp'] for h in d['healing'])-hp_after)
            updated=next(a for a in stage['accounts'] if a['id']==owner)
            for stock in updated['stocks']:stock['amount']+=changes.get(stock['id'],0)
            # The left panel keeps the last *complete* period's CLI flow until
            # the final boundary. The card shows this stage's actual movements.
            facts=[f for f in final.get('production',[]) if f['period']==self.period and f['owner']==owner] if phase=='production' else []
            ledger={**deepcopy(flow),'facilities':len(facts),'supply_need':need,'supply_used':used,
                    'healed_hp':healed,'starvation_hp':starvation,'units':sum(len(e['details']['before']) for e in records),'stage':phase}
            source=[f['id'] for f in facts]+[f'event/{e["id"]}' for e in records]
            text=f'{len(facts)}处设施的实际投入与产出' if phase=='production' else f'全国军队补给 {used}/{need}'
            if need>used:text+=f' · 缺补给 {need-used}'
            if healed:text+=f' · 恢复 {healed:g}HP'
            if starvation:text+=f' · 断粮损失 {starvation:g}HP'
            title='采集与生产' if phase=='production' else '补给与恢复'
            self.add(phase,f'{self.names.get(owner,owner)} · {title}',text,self.home(owner),
                     after=stage,source=','.join(source) or f'period/{self.period}/{owner}',duration=4 if facts or records else 1.6,
                     anchor_building=next((b['id'] for b in sorted(self.previous['buildings'],key=lambda b:b['id'])
                         if b['owner']==owner and b['kind']=='native:town-hall' and b['remaining']==0),None),
                     ledger=ledger,playback_scope='settlement',
                     turn_title=f'{self.names.get(owner,owner)} · 第 {self.period+1} 回合结算')
        self.actor=None

    def drain(self):
        """Keep the replay cursor; release only this completed call's beats."""
        beats,self.beats=self.beats,[]
        return beats

    def capture(self,actor,action,arguments,receipt):
        # Session.record treats callback exceptions as rule faults. Presentation
        # errors must instead fall back to the already valid cycle boundary.
        resolve=getattr(self.host.runtime.tool_layer,'action_name',None)
        if resolve and isinstance(action,str):action=resolve(action,arguments if isinstance(arguments,dict) else {})
        if self.activity is not None:
            try:self.activity(actor,activity_label(action))
            except Exception:pass
        if self.warning:return
        try:self._capture(actor,action,arguments,receipt)
        except Exception as exc:self.warning=f'演出采集退回完整快照：{exc}';self.beats.clear()

    def _capture(self,actor,action,arguments,receipt):
        spec=self.host.runtime.tools.get(action) or self.host.runtime.actions.get(action)
        if spec and spec.read_only:return
        if action in ('player_state','player_rules','player_events'):return
        args=arguments if isinstance(arguments,dict) else {}
        before=self.actual;after=self.scene();self.actual=deepcopy(after)
        source=f'audit/{self.host.audit[-1]["id"]}'
        text=self.host.ui.console.action(self.period+1,actor,action,args,receipt,self.names)
        text=' '.join(text.splitlines())
        position=[args['x'],args['y']] if type(args.get('x')) is int and type(args.get('y')) is int else self.home(actor)
        name=self.names.get(actor,actor)
        if actor!=self.actor:
            self.actor=actor
            self.turn_started=time.monotonic();self.turn_tokens=self._token_total(actor)
            client=self.host.clients[actor]
            version=getattr(client,'version','rule')
            label='规则 AI '+version if client.kind=='rule' else getattr(client,'cfg',{}).get('model','模型')
            self.add('actor',f'{name}开始行动',f'{label} · 逐项展示本期行动',self.home(actor),source=source,duration=2.2,**self._turn_meta(actor))
        if not receipt.get('ok'):
            self.add('rejected',f'{name} · 行动未执行',text,position,source=source,duration=1.4,
                     market_actor=actor if action in ('buy','sell') else None)
            return
        old_units={key('armies',u):u for u in before['armies']}
        new_units=[u for u in after['armies'] if key('armies',u) not in old_units and u['owner']==actor]
        if new_units:position=[new_units[0]['x'],new_units[0]['y']]
        remaining={key('armies',u) for u in after['armies']}
        removed=[u for k,u in old_units.items() if k not in remaining and u['owner']==actor]
        if removed:position=[removed[0]['x'],removed[0]['y']]
        motions=[];cache={}
        for u in after['armies']:
            old=old_units.get(key('armies',u))
            if old and u['owner']==actor and (old['x'],old['y'])!=(u['x'],u['y']):
                route_key=(u['kind'],old['x'],old['y'],u['x'],u['y'])
                if route_key not in cache:
                    queued_attack=action=='queue_orders' and any(r['aid']==u['id'] and r['mode']=='attack' for r in receipt.get('data',{}).get('started',[]))
                    cache[route_key]=self.host.observer_route(actor,u['id'],[old['x'],old['y']],[u['x'],u['y']],action=='attack' or queued_attack)
                route=cache[route_key]
                if route:motions.append({'unit':key('armies',u),'points':route})
        if action=='queue_orders' and motions:
            first=next(u for u in after['armies'] if key('armies',u)==motions[0]['unit']);position=[first['x'],first['y']]
            stage=deepcopy(after);stage['ownership']=deepcopy(before['ownership'])
            self.add('march',name+' · 路线行军',text,position,after=stage,source=source,duration=2.4,motions=motions)
            self.add('arrival',name+' · 军令执行',text,position,after=after,source=source,duration=1.8)
        elif action in ('move','attack'):
            data=receipt.get('data') or {};ids=set(data.get('units',args.get('ids') or [args.get('aid')]))
            selected=[u for u in after['armies'] if u['owner']==actor and u['id'] in ids]
            if selected:position=[selected[0]['x'],selected[0]['y']]
            approaches=[]
            if action=='attack':
                for u in selected:
                    old=old_units.get(key('armies',u))
                    if old:
                        direction={'actor':actor,'from':[old['x'],old['y']],'to':[u['x'],u['y']]}
                        if direction not in approaches:approaches.append(direction)
                        entries=self.attack_sources.setdefault((u['x'],u['y']),[])
                        if direction not in entries:entries.append(direction)
            self.add('order',name+(' · 进攻军令' if action=='attack' else ' · 调动军令'),
                     f'{len(selected)}支军队指向 ({position[0]},{position[1]})',position,source=source,duration=1,approaches=approaches)
            if motions:
                stage=deepcopy(after)
                stage['ownership']=deepcopy(before['ownership'])
                for u in stage['armies']:
                    if u['owner']==actor and u['id'] in ids:u['engaged']=False
                self.add('march',name+' · 行军',f'{len(motions)}支军队沿合法路线行进',position,
                         after=stage,source=source,duration=2.4,motions=motions)
            self.add('engage' if action=='attack' else 'arrival',name+(' · 接敌' if action=='attack' else ' · 抵达'),
                     text,position,after=after,source=source,duration=1.8)
        else:
            phase={'build':'build','demolish':'demolish','recruit':'recruit','move':'arrival','retreat':'retreat',
                   'buy':'trade','sell':'trade','plan':'plan','end_turn':'turn-end'}.get(action,'action')
            captions={'build':'动工','demolish':'拆除建筑','recruit':'征召','move':'抵达','retreat':'提交撤退','buy':'采购','sell':'出售','plan':'本期国策','end_turn':'行动结束'}
            new_buildings=[b for b in after['buildings'] if b['id'] not in {v['id'] for v in self.previous['buildings']}]
            if new_buildings:position=[new_buildings[0]['x'],new_buildings[0]['y']]
            if action=='demolish':
                removed=next((b for b in before['buildings'] if b['id']==args.get('id') and b['owner']==actor),None)
                if removed:position=[removed['x'],removed['y']]
            trade=None
            automatic=(receipt.get('data') or {}).get('auto_trades',{}) if action=='end_turn' else {}
            auto_fills=[o for o in automatic.get('orders',[]) if o['quantity']>0]
            if action in ('buy','sell') or auto_fills:
                def stocks(scene):return {r['id']:r['amount'] for a in scene['accounts'] if a['id']==actor for r in a['stocks']}
                prior=stocks(before);current=stocks(after)
                goods=[{'resource':r,'amount':amount-prior.get(r,0)} for r,amount in current.items() if r!='native:gold' and amount!=prior.get(r,0)]
                trade={'side':'auto' if auto_fills else action,'goods':goods,'gold':current.get('native:gold',0)-prior.get('native:gold',0)}
            if auto_fills:
                self.add('trade',name+' · 自动交易',f'按库存与价格限制实际成交 {len(auto_fills)} 笔',self.home(actor),after=after,source=source,
                         market_actor=actor,trade=trade)
                trade=None
            self.add(phase,f'{name} · {captions.get(action,"行动")}',text,position,after=after,source=source,
                     duration=2 if phase=='build' else 1.4,
                     market_actor=actor if phase=='trade' else None,trade=trade,
                     **(self._turn_meta(actor,closing=True) if phase=='turn-end' else {}))

    def finish(self):
        if self.warning:return []
        try:
            self.actor=None
            final=self.scene()
            events=[e for e in self.host.records()['events'] if e['id']>self.event_cursor]
            self.national_settlements(final,events,'production')
            self.national_settlements(final,events,'maintenance')
            for event in events:
                if event['id']<=self.event_cursor or event['kind']!='battle':continue
                detail=deepcopy(event.get('details',{}))
                if not {'before','after'}<=detail.keys():continue
                position=event['position'];x,y=position;source=f'event/{event["id"]}'
                detail['approaches']=deepcopy(self.attack_sources.get((x,y),[]))
                stage=deepcopy(self.previous)
                stage['armies']=[u for u in stage['armies'] if (u['x'],u['y'])!=(x,y)]+deepcopy(detail['before'])
                self.add('battle','交锋 · 本期战斗结算','双方接战；伤亡以本次结算为准',position,after=stage,
                         source=source,duration=4.6,combat=detail)
                stage['armies']=[u for u in stage['armies'] if (u['x'],u['y'])!=(x,y)]+deepcopy(detail['after'])
                self.add('casualties','战果 · 伤亡确认',event['text'],position,after=stage,
                         source=source,duration=2,combat=detail)
                if detail['owner']!=detail['previous_owner']:
                    stage['ownership']=[r for r in stage['ownership'] if (r['x'],r['y'])!=(x,y)]
                    stage['ownership'].append({'x':x,'y':y,'owner':detail['owner']})
                    self.add('occupation','易帜 · 归属变更',f'{self.names.get(detail["owner"],detail["owner"])}取得 ({x},{y})',
                             position,after=stage,source=source,duration=1.6,actor=detail['owner'],playback_scope='settlement')
            pending={b['id']:b for b in self.previous['buildings'] if b['remaining']>0}
            for b in final['buildings']:
                if b['id'] not in pending or b['remaining']>0:continue
                stage=deepcopy(self.previous)
                stage['buildings']=[deepcopy(b) if old['id']==b['id'] else old for old in stage['buildings']]
                self.add('complete',f'{self.names.get(b["owner"],b["owner"])} · 落成',f'{b["label"]}竣工，下期起按规则运营',
                         [b['x'],b['y']],after=stage,source=f'building/{b["id"]}',duration=1.4,actor=b['owner'],playback_scope='settlement')
            for message in self.host.records()['messages']:
                if message['id'] in self.message_ids or message['kind']!='letter':continue
                sender=message['source'];target=message['target'];self.actor=sender
                origin=self.home(sender) or self.initial_homes.get(sender)
                destination=self.home(target)
                self.add('letter',f'{self.names.get(sender,sender)} → {self.names.get(target,target)} · 信件送达',
                         message['payload']['text'],destination,source=f'message/{message["id"]}',duration=3,
                         playback_scope='settlement',letter={'id':message['id'],'source':sender,'target':target,'from':origin,'to':destination})
            self.actor=None
            self.add('settlement','本期结算完成','生产、补给、撤离与市场数据同步至完整保存边界',
                     after=final,source=f'period/{self.host.clock()}',duration=1.4)
            return self.beats
        except Exception as exc:
            self.warning=f'演出结算退回完整快照：{exc}';return []
