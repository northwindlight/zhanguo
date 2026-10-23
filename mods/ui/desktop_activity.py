"""Observer-only, bounded presentation of real action receipts and settlement.

Capture runs on the session thread. The renderer never drives a rule action;
only the next complete cycle waits for its acknowledgement.
"""
from copy import deepcopy


def key(collection, row):
    if collection in ('armies','guards'):return f'{row["owner"]}/{row["id"]}'
    if collection=='ownership':return f'{row["x"]},{row["y"]}'
    return str(row['id'])


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
    def __init__(self,host):
        self.host=host;self.period=host.clock();self.beats=[];self.warning=None;self.actor=None
        self.previous=self.scene()
        self.actual=deepcopy(self.previous)
        self.marches=[];self.held_units={};self.held_guards={};self.held_owners={}
        self.event_cursor=max((e['id'] for e in host.records()['events']),default=0)
        self.names={a:host.roster[a]['name'] for a in host.roster}

    def scene(self):return self.host.ui.desktop_scene(self.host.observer_scene())

    def add(self,phase,title,text,position=None,*,after=None,source='',duration=1.5,**extra):
        if len(self.beats)>=self.LIMIT:raise ValueError('本期演出超过2048片段，改用完整快照同步')
        delta=difference(self.previous,after) if after is not None else {}
        turn_title=f'{self.names.get(self.actor,self.actor)} · 第 {self.period+1} 回合' if self.actor else f'第 {self.period+1} 回合 · 结算'
        self.beats.append({'phase':phase,'title':title,'turn_title':turn_title,'actor':self.actor,'text':text[:180],'position':position,
                           'duration':duration,'source':source,'delta':delta,**extra})
        if after is not None:self.previous=deepcopy(after)

    def home(self,actor):
        b=next((b for b in self.previous['buildings'] if b['owner']==actor and b['kind']=='native:town-hall'),None)
        return [b['x'],b['y']] if b else None

    def planning_scene(self,after):
        """Keep issued movements at their camps until the end-of-turn parade."""
        shown=deepcopy(after)
        shown['armies']=[deepcopy(self.held_units.get(key('armies',u),u)) for u in shown['armies']
                         if key('armies',u) not in self.held_guards]
        shown['guards']=[g for g in shown['guards'] if key('guards',g) not in self.held_guards]+deepcopy(list(self.held_guards.values()))
        shown['ownership']=[r for r in shown['ownership'] if (r['x'],r['y']) not in self.held_owners]
        shown['ownership'].extend(deepcopy(r) for r in self.held_owners.values() if r is not None)
        return shown

    def play_marches(self):
        living={key('armies',u) for u in self.actual['armies']}
        for order in self.marches:
            stage=deepcopy(self.previous)
            rows=[u for u in order['units'] if key('armies',u) in living]
            keys={key('armies',u) for u in rows}
            if not keys:continue
            def apply_units(records):
                stage['armies']=[u for u in stage['armies'] if key('armies',u) not in keys]+deepcopy(records)
            arrival=deepcopy(rows)
            if order['action']=='attack':
                for u in arrival:
                    if u['owner']==order['actor']:u['engaged']=False
            apply_units(arrival)
            stage['guards']=[g for g in stage['guards'] if key('guards',g) not in keys]
            motions=[m for m in order['motions'] if m['unit'] in keys]
            if motions:
                self.add('march',order['name']+' · 行军',f'{len(motions)}支军队沿合法路线行进',order['position'],
                         after=stage,source=order['source'],duration=2.4,motions=motions)
            apply_units(rows)
            if order['owner'] is not None:
                at=tuple(order['position']);stage['ownership']=[r for r in stage['ownership'] if (r['x'],r['y'])!=at]
                stage['ownership'].append(deepcopy(order['owner']))
            self.add('engage' if order['action']=='attack' else 'arrival',order['name']+(' · 接敌' if order['action']=='attack' else ' · 抵达'),
                     order['result'],order['position'],after=stage,source=order['source'],duration=1.8)
        if self.marches:
            self.add('mobilized','各军调动完成','军队已到达各自位置，进入本期生产、军需与战斗结算',after=self.actual,duration=1)

    def capture(self,actor,action,arguments,receipt):
        # Session.record treats callback exceptions as rule faults. Presentation
        # errors must instead fall back to the already valid cycle boundary.
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
            version=getattr(self.host.clients[actor],'version','rule')
            self.add('actor',f'{name}开始行动',f'规则 AI {version} · 逐项展示本期行动',self.home(actor),source=source,duration=2.2)
        if not receipt.get('ok'):
            self.add('rejected',f'{name} · 行动未执行',text,position,source=source,duration=1.4)
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
                    cache[route_key]=self.host.observer_route(actor,u['id'],[old['x'],old['y']],[u['x'],u['y']],action=='attack')
                route=cache[route_key]
                if route:motions.append({'unit':key('armies',u),'points':route})
        if action in ('move','attack'):
            data=receipt.get('data') or {};ids=set(data.get('units',args.get('ids') or [args.get('aid')]))
            selected=[u for u in after['armies'] if u['owner']==actor and u['id'] in ids]
            for u in selected:
                k=key('armies',u)
                if k not in self.held_units:self.held_units[k]=deepcopy(old_units[k])
            old_guards={key('guards',g):g for g in before['guards']}
            defenders=[u for u in after['armies'] if key('armies',u) in old_guards and [u['x'],u['y']]==position]
            for u in defenders:self.held_guards[key('armies',u)]=deepcopy(old_guards[key('armies',u)])
            at=tuple(position)
            if at not in self.held_owners:self.held_owners[at]=next((deepcopy(r) for r in before['ownership'] if (r['x'],r['y'])==at),None)
            owner=next((r for r in after['ownership'] if (r['x'],r['y'])==at),None)
            self.marches.append({'action':action,'actor':actor,'name':name,'units':deepcopy(selected+defenders),'motions':motions,
                                 'position':position,'owner':deepcopy(owner),'source':source,'result':text})
            self.add('order',name+(' · 进攻军令' if action=='attack' else ' · 调动军令'),
                     f'{len(selected)}支军队指向 ({position[0]},{position[1]})；回合末展示行军与抵达',position,
                     after=self.planning_scene(after),source=source,duration=1.5)
        else:
            phase={'build':'build','recruit':'recruit','move':'arrival','retreat':'retreat',
                   'buy':'trade','sell':'trade','plan':'plan','end_turn':'turn-end'}.get(action,'action')
            captions={'build':'动工','recruit':'征召','move':'抵达','retreat':'提交撤退','buy':'采购','sell':'出售','plan':'本期国策','end_turn':'行动结束'}
            new_buildings=[b for b in after['buildings'] if b['id'] not in {v['id'] for v in self.previous['buildings']}]
            if new_buildings:position=[new_buildings[0]['x'],new_buildings[0]['y']]
            self.add(phase,f'{name} · {captions.get(action,"行动")}',text,position,after=self.planning_scene(after),source=source,
                     duration=2 if phase=='build' else 1.4)

    def finish(self):
        if self.warning:return []
        try:
            self.actor=None
            self.play_marches()
            final=self.scene()
            facilities={b['id']:b for b in self.previous['buildings']}
            grouped={}
            for fact in final.get('production',[]):
                if fact['period']!=self.period or not fact['id'].startswith('native:building-production/'):continue
                b=facilities.get(int(fact['id'].rsplit('/',1)[-1]))
                if not b:continue
                grouped.setdefault((b['x'],b['y']),[]).append((b,fact))
            for position,rows in grouped.items():
                pops=[]
                for b,fact in rows:
                    for resource,amount in fact['inputs']:pops.append({'building':b['id'],'resource':resource,'amount':-amount})
                    for resource,amount in fact['outputs']:pops.append({'building':b['id'],'resource':resource,'amount':amount})
                self.add('production','采集与生产',f'{len(rows)}处设施完成本期实际投入与产出',list(position),
                         source=','.join(f['id'] for _,f in rows),duration=2.2,pops=pops)
            events=[e for e in self.host.records()['events'] if e['id']>self.event_cursor]
            for event in events:
                if event['kind']!='maintenance':continue
                d=event.get('details',{});positions={}
                for u in d.get('before',[]):positions.setdefault((u['x'],u['y']),[]).append(u)
                for index,(position,units) in enumerate(positions.items()):
                    stage=deepcopy(self.previous);ids={key('armies',u) for u in units}
                    updates=[u for u in d['after'] if key('armies',u) in ids]
                    stage['armies']=[u for u in stage['armies'] if key('armies',u) not in ids]+updates
                    pops=[]
                    if index==0:
                        pops.extend({'position':list(position),'resource':r,'amount':-amount} for r,amount in d['costs'].items())
                    elif d['used']:pops.append({'position':list(position),'resource':'native:supply','amount':None})
                    for heal in d['healing']:
                        if any(u['id']==heal['id'] for u in units):pops.append({'position':list(position),'resource':'hp','amount':heal['hp']})
                    self.add('maintenance','补给与恢复',event['text'],list(position),after=stage,source=f'event/{event["id"]}',duration=2,pops=pops)
            for event in events:
                if event['id']<=self.event_cursor or event['kind']!='battle':continue
                detail=event.get('details',{})
                if not {'before','after'}<=detail.keys():continue
                position=event['position'];x,y=position;source=f'event/{event["id"]}'
                stage=deepcopy(self.previous)
                stage['armies']=[u for u in stage['armies'] if (u['x'],u['y'])!=(x,y)]+deepcopy(detail['before'])
                self.add('battle','交锋 · 本期战斗结算','双方接战；伤亡以本次结算为准',position,after=stage,
                         source=source,duration=2.4,combat=detail)
                stage['armies']=[u for u in stage['armies'] if (u['x'],u['y'])!=(x,y)]+deepcopy(detail['after'])
                self.add('casualties','战果 · 伤亡确认',event['text'],position,after=stage,
                         source=source,duration=2,combat=detail)
                if detail['owner']!=detail['previous_owner']:
                    stage['ownership']=[r for r in stage['ownership'] if (r['x'],r['y'])!=(x,y)]
                    stage['ownership'].append({'x':x,'y':y,'owner':detail['owner']})
                    self.add('occupation','易帜 · 归属变更',f'{self.names.get(detail["owner"],detail["owner"])}取得 ({x},{y})',
                             position,after=stage,source=source,duration=1.6)
            pending={b['id']:b for b in self.previous['buildings'] if b['remaining']>0}
            for b in final['buildings']:
                if b['id'] not in pending or b['remaining']>0:continue
                stage=deepcopy(self.previous)
                stage['buildings']=[deepcopy(b) if old['id']==b['id'] else old for old in stage['buildings']]
                self.add('complete',f'{self.names.get(b["owner"],b["owner"])} · 落成',f'{b["label"]}竣工，下期起按规则运营',
                         [b['x'],b['y']],after=stage,source=f'building/{b["id"]}',duration=1.4)
            self.add('settlement','本期结算完成','生产、补给、撤离与市场数据同步至完整保存边界',
                     after=final,source=f'period/{self.host.clock()}',duration=1.4)
            return self.beats
        except Exception as exc:
            self.warning=f'演出结算退回完整快照：{exc}';return []
