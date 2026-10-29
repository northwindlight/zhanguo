"""Player-scoped action and settlement choreography, with no observer fallback."""
from copy import deepcopy
from .desktop_player import frame as player_frame, flow
from .desktop_activity import difference
from .desktop_defense import project
from mods.native.army.candidates import candidates


class DiscardHumanCycle(Exception):
    """The transport closed or the player explicitly discarded unsaved progress."""


class PlayerCapture:
    def __init__(self, control):
        self.control=control;self.period=control.host.clock();self.actor=None
        self.warning=None;self.beats=[]
        state=self.state();self.previous=player_frame(control.host.ui,state);self.previous_state=state
        self.event_cursor=max((e['id'] for e in state['facts']['news']),default=0)
        self.message_ids={m['id'] for m in state['facts']['intel']}

    def state(self):return self.control.human_api.state(self.control.viewer,controls=False)
    def visual(self):return player_frame(self.control.host.ui,self.state())
    def home(self,visual=None):
        visual=visual or self.previous;actor=self.control.viewer
        halls=sorted((b for b in visual['scene']['buildings'] if b['owner']==actor and b['kind']=='native:town-hall' and not b['remaining']),key=lambda b:b['id'])
        if halls:return [halls[0]['x'],halls[0]['y']]
        tile=next((t for t in visual['tiles'] if t.get('owner')==actor),None)
        return tile['position'] if tile else None

    def add(self,phase,title,text,visual,position=None,*,source='',actor=None,**extra):
        project(visual['scene'])
        owner=self.control.viewer
        self.beats.append({'phase':phase,'actor':actor,'client_kind':'human' if actor==owner else None,
            'playback_scope':'actor' if actor==owner else 'settlement','title':title,'text':text,
            'turn_title':f'{visual["names"][owner]} · 第 {self.period+1} 回合'+(' · 结算' if actor is None else ''),
            'duration':2 if phase=='march' else 4.6 if phase=='battle' else 2.6 if phase in ('production','maintenance') else 1.4,
            'source':source,'position':position,'delta':{},'visual':deepcopy(visual),**extra})
        self.previous=deepcopy(visual)

    def capture(self,actor,action,arguments,receipt):
        try:self._capture(actor,action,arguments,receipt)
        except Exception:
            self.warning='本国演出采集失败，退回玩家快照';self.beats=[]

    def _capture(self,actor,action,arguments,receipt):
        if self.control.viewer is None:return
        spec=self.control.host.runtime.tools.get(action)
        if spec and spec.read_only:return
        state=self.state();after=player_frame(self.control.host.ui,state);before=self.previous
        prior_state=self.previous_state;self.previous_state=state
        if actor!=self.control.viewer:
            # Actual visible changes only. Private calls and public market
            # price changes never disclose a foreign action or camera target.
            changed=difference({k:before['scene'][k] for k in ('armies','buildings','ownership')},
                               {k:after['scene'][k] for k in ('armies','buildings','ownership')})
            if changed:self.add('observe','视野内变化','当前权限内的军队或地块已更新',after,source=f'view/{self.period}',
                client_kind=self.control.host.clients[actor].kind,playback_scope='actor')
            return
        self.actor=actor
        resolve=self.control.host.runtime.tool_layer.action_name
        canonical=resolve(action,arguments or {})
        args=arguments or {};source=f'audit/{self.control.host.audit[-1]["id"]}'
        text=self.control.host.ui.console.action(self.period+1,actor,action,args,receipt,{actor:after['names'][actor]})
        position=[args['x'],args['y']] if type(args.get('x')) is int and type(args.get('y')) is int else self.home(after)
        live={tuple(t['position']) for t in after['tiles'] if t['visibility']=='live'}
        if position is not None and tuple(position) not in live:position=None
        phase={'buy':'trade','sell':'trade','build':'build','demolish':'demolish','recruit':'recruit','move':'march','attack':'engage','retreat':'order','buy_map':'map','send_letter':'order'}.get(canonical,'human-action')
        if canonical in ('queue_build','queue_recruit') and receipt.get('ok') and receipt.get('data',{}).get('started'):
            phase='build' if canonical=='queue_build' else 'recruit'
        effective=receipt.get('ok') and receipt.get('status') not in ('confirmation_required','rejected','failed')
        if not effective:phase='human-action'
        if receipt.get('status')=='confirmation_required':text='请查看宣战警告，核对参战方与条约后确认。'
        extra={}
        automatic=(receipt.get('data') or {}).get('auto_trades',{}) if canonical=='end_turn' else {}
        auto_fills=[o for o in automatic.get('orders',[]) if o['quantity']>0]
        if canonical=='demolish' and effective:
            removed=next((b for b in before['scene']['buildings'] if b['id']==args.get('id') and b['owner']==actor),None)
            if removed:position=[removed['x'],removed['y']]
        if (canonical in ('buy','sell') or auto_fills) and effective:
            old={s['id']:s['amount'] for s in before['scene']['accounts'][0]['stocks']}
            changes=[{'id':s['id'],'name':s['name'],'amount':s['amount']-old.get(s['id'],0)} for s in after['scene']['accounts'][0]['stocks'] if s['amount']!=old.get(s['id'],0)]
            extra={'market_actor':actor,'trade':{'side':'auto' if auto_fills else canonical,'goods':[{'resource':s['id'],'amount':s['amount']} for s in changes if s['id']!='native:gold'],
                'gold':next((s['amount'] for s in changes if s['id']=='native:gold'),0)}}
            if auto_fills:
                self.add('trade','本国自动交易',f'按库存与价格限制实际成交 {len(auto_fills)} 笔，详情见市场',after,self.home(after),source=source,actor=actor,**extra)
                extra={}
        if canonical in ('move','attack') and effective:
            previous={u['id']:u for u in before['armies'] if u['owner']==actor}
            motions=[]
            selected=(receipt.get('data') or {}).get('units',[])
            preview=candidates(prior_state,list(selected),canonical,prior_state['map']['size']) if selected else {'targets':[]}
            for u in after['armies']:
                old=previous.get(u['id']) if u['owner']==actor else None
                if old and (old['x'],old['y'])!=(u['x'],u['y']) and (old['x'],old['y']) in live and (u['x'],u['y']) in live:
                    target=next((t for t in preview['targets'] if t['position']==[u['x'],u['y']]),None)
                    route=next((r for r in target['routes'] if r['aid']==u['id']),None) if target else None
                    if route and not route['uncertain']:
                        motions.append({'unit':f'{actor}/{u["id"]}','points':route['points'],'schematic':False})
            if motions:extra['motions']=motions;phase='march'
            if canonical=='attack' and position is not None:
                sources={(u['x'],u['y']) for u in before['armies'] if u['owner']==actor and u['id'] in (receipt.get('data') or {}).get('units',[])}
                extra['approaches']=[{'actor':actor,'from':list(p),'to':position} for p in sorted(sources) if p in live]
        if canonical=='queue_orders' and effective:
            motions=[];approaches=[]
            position=(receipt['data']['started'][0]['position'] if receipt['data']['started'] else
                      args['nodes'][-1]['position'] if args.get('nodes') else None)
            previous={u['id']:u for u in before['armies'] if u['owner']==actor}
            for event in receipt['data']['started']:
                aid=event['aid'];old=previous.get(aid);destination=event['position']
                if not old:continue
                if event['mode']=='attack' and (event['receipt'].get('engaged') or event['receipt'].get('occupied')):
                    approaches.append({'actor':actor,'from':[old['x'],old['y']],'to':destination})
                if not event['receipt'].get('position_changed'):continue
                preview=candidates(prior_state,[aid],event['mode'],prior_state['map']['size'])
                target=next((t for t in preview['targets'] if t['position']==destination),None)
                route=next((r for r in target['routes'] if r['aid']==aid),None) if target else None
                if route and not route['uncertain']:motions.append({'unit':f'{actor}/{aid}','points':route['points'],'schematic':False})
            if motions:extra['motions']=motions;phase='march'
            elif approaches:phase='engage'
            if approaches:extra['approaches']=approaches
        self.add(phase,'本国操作',text,after,position,source=source,actor=actor,receipt=deepcopy(receipt),**extra)

    def drain(self):
        beats,self.beats=self.beats,[]
        return beats

    def finish(self):
        if self.control.viewer is None or self.warning:return []
        try:state=self.state();final=player_frame(self.control.host.ui,state)
        except ValueError:return []
        owner=self.control.viewer;news=[e for e in state['facts']['news'] if e['id']>self.event_cursor]
        actual=flow(state)
        for phase in ('production','maintenance'):
            stage=deepcopy(self.previous);ledger=deepcopy(actual);ledger['stage']=phase
            for name in ('production_in','production_out','tribute_in','tribute_out','military_out'):
                if (name=='military_out')!=(phase=='maintenance'):ledger[name]=[]
            records=[e for e in news if e['kind']=='maintenance' and (e.get('details') or {}).get('owner')==owner]
            if phase=='maintenance' and not records:continue
            net={}
            for name in ('production_in','production_out','tribute_in','tribute_out','military_out'):
                for key,amount in ledger[name]:net[key]=net.get(key,0)+amount*(1 if name.endswith('_in') else -1)
            for s in stage['scene']['accounts'][0]['stocks']:s['amount']+=net.get(s['id'],0)
            ledger.update(net=list(net.items()),facilities=None,supply_need=0,supply_used=0,healed_hp=0,starvation_hp=0,units=0)
            for e in records:
                d=e['details']
                ids={(u['owner'],u['id']) for u in d['before']}
                stage['scene']['armies']=[u for u in stage['scene']['armies'] if (u['owner'],u['id']) not in ids]+deepcopy(d['after'])
                stage['armies']=deepcopy(stage['scene']['armies'])
                ledger['starvation_hp']+=max(0,sum(u['hp'] for u in d['before'])+sum(h['hp'] for h in d['healing'])-sum(u['hp'] for u in d['after']))
                ledger['supply_need']+=d['need'];ledger['supply_used']+=d['used'];ledger['healed_hp']+=sum(h['hp'] for h in d['healing']);ledger['units']+=len(d['before'])
            self.add(phase,'本国采集与生产' if phase=='production' else '本国补给与恢复',
                     '只展示本国这一阶段的实际收入与消耗',stage,self.home(stage),source=f'period/{self.period}/{phase}',ledger=ledger,actor=owner,playback_scope='settlement',
                     turn_title=f'{final["names"][owner]} · 第 {self.period+1} 回合结算',
                     anchor_building=next((b['id'] for b in stage['scene']['buildings'] if b['owner']==owner and b['kind']=='native:town-hall' and not b['remaining']),None))
        for event in news:
            if event['kind']=='battle' and event.get('position'):
                position=event['position'];details=deepcopy(event.get('details') or {})
                if {'before','after'}<=details.keys() and any(t['position']==position and t['visibility']=='live' for t in self.previous['tiles']):
                    stage=deepcopy(self.previous)
                    stage['scene']['guards']=[u for u in stage['scene']['guards'] if [u['x'],u['y']]!=position]
                    stage['scene']['armies']=[u for u in stage['scene']['armies'] if [u['x'],u['y']]!=position]+deepcopy(details['before'])
                    stage['armies']=deepcopy(stage['scene']['armies'])
                    self.add('battle','视野内交战','按本次实际战斗结果演出',stage,position,source=f'event/{event["id"]}',combat=details)
                    stage=deepcopy(stage)
                    stage['scene']['armies']=[u for u in stage['scene']['armies'] if [u['x'],u['y']]!=position]+deepcopy(details['after'])
                    stage['armies']=deepcopy(stage['scene']['armies'])
                    self.add('casualties','战斗结果',event['text'],stage,position,source=f'event/{event["id"]}',combat=details)
                    if details.get('owner')!=details.get('previous_owner'):
                        tile=next((t for t in stage['tiles'] if t['position']==position),None)
                        if tile is not None:tile['owner']=details.get('owner')
                        for row in stage['scene']['ownership']:
                            if [row['x'],row['y']]==position:row['owner']=details.get('owner')
                        for row in stage['scene']['buildings']:
                            if [row['x'],row['y']]==position:row['owner']=details.get('owner')
                        self.add('occupation','归属变更','战斗结束后按实际结果易帜',stage,position,source=f'event/{event["id"]}')
                    continue
            if event['kind'] not in ('action','maintenance','healing','production-detail'):
                self.add('player-event','本国可见消息',event['text'],self.previous,source=f'event/{event["id"]}')
        pending={b['id'] for b in self.previous['scene']['buildings'] if b['owner']==owner and b['remaining']>0}
        for building in final['scene']['buildings']:
            if building['id'] not in pending or building['remaining']>0:continue
            stage=deepcopy(self.previous)
            stage['scene']['buildings']=[deepcopy(building) if b['id']==building['id'] else b for b in stage['scene']['buildings']]
            position=[building['x'],building['y']]
            for tile in stage['tiles']:
                if tile['position']!=position:continue
                original=next(t for t in final['tiles'] if t['position']==position)
                for field in ('building_instances','buildings','building_labels'):tile[field]=deepcopy(original[field])
            self.add('complete','本国建筑落成',building['label']+'竣工，下期起按规则运营',stage,position,source=f'building/{building["id"]}')
        for message in state['facts']['intel']:
            if message['id'] not in self.message_ids and message['kind'] in ('letter','observer-letter'):
                source=message['source'];home=self.home()
                origin=next(([b['x'],b['y']] for b in self.previous['scene']['buildings'] if b['owner']==source and b['kind']=='native:town-hall'),home)
                self.add('letter','信件送达 · 投递示意',message['payload']['text'],self.previous,home,source=f'message/{message["id"]}',
                    letter={'id':message['id'],'source':source,'target':owner,'from':origin,'to':home,'schematic':True})
        self.add('settlement','本期结算完成','本国视野与完整存档已同步',final,source=f'period/{self.control.host.clock()}')
        return self.drain()
