"""Seat-scoped presentation capture. It has no observer fallback."""
from copy import deepcopy


class DiscardHumanCycle(Exception):
    """The transport closed or the player explicitly discarded unsaved progress."""


class PlayerCapture:
    def __init__(self, control):
        self.control=control;self.period=control.host.clock();self.actor=None
        self.warning=None;self.beats=[]
        self.event_cursor=max((e['id'] for e in self.state()['facts']['news']),default=0)

    def state(self):
        return self.control.human_api.state(self.control.viewer)

    def capture(self,actor,action,arguments,receipt):
        try:self._capture(actor,action,arguments,receipt)
        except Exception:
            self.warning='本国演出采集失败，退回玩家快照';self.beats=[]

    def _capture(self,actor,action,arguments,receipt):
        if self.control.viewer is None or actor!=self.control.viewer:return
        spec=self.control.host.runtime.tools.get(action)
        if spec and spec.read_only:return
        self.actor=actor
        text=self.control.host.ui.console.action(self.period+1,actor,action,arguments,receipt,
            {actor:self.control.host.roster[actor]['name']})
        self.beats.append({'phase':'human-action','actor':actor,'client_kind':'human',
            'playback_scope':'actor','title':'本国操作','text':text,'duration':1.5,
            'source':f'audit/{self.control.host.audit[-1]["id"]}',
            'position':None,'delta':{},'receipt':deepcopy(receipt)})

    def drain(self):
        beats,self.beats=self.beats,[]
        return beats

    def finish(self):
        if self.control.viewer is None:return []
        try:news=self.state()['facts']['news']
        except ValueError:return []
        beats=self.drain()
        for event in news:
            if event['id']<=self.event_cursor or event['kind']=='action':continue
            beats.append({'phase':'player-event','actor':None,'client_kind':None,
                'playback_scope':'settlement','title':'本国可见消息','text':event['text'],
                'duration':1.5,'position':None,'delta':{},'source':f'event/{event["id"]}'})
        beats.append({'phase':'settlement','actor':None,'client_kind':None,
            'playback_scope':'settlement','title':'本期结算完成','text':'本国可见事实已同步至完整保存边界',
            'duration':1.4,'position':None,'delta':{},'source':f'period/{self.control.host.clock()}'})
        return beats
