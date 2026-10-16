"""Actor-bound API exposed by a distribution session."""
from copy import deepcopy
from functools import wraps
from .state import unit_context
from .player import API_VERSION, BUILTINS, wire


def api_entry(method):
    """A bound request has its own permissions, independent of caller modules."""
    @wraps(method)
    def invoke(*args,**kwargs):
        with unit_context(None):return method(*args,**kwargs)
    return invoke


class BoundPlayer:
    api_version = API_VERSION

    def __init__(self, session, actor, generation):
        self.__session = session
        self.__actor = actor
        self.__generation = generation

    @property
    def actor(self):return self.__actor
    @property
    def generation(self):return self.__generation

    @api_entry
    def action_state(self):
        with self.__session.lock:
            current = self.__session.generations[self.actor]
            active = current == self.generation and self.actor in self.__session.actors()
            can_act = active and self.__session.world.phase == '行动' and self.__session.victory_event is None
            gate = self.__session.runtime.distribution.get('ActionGate')
            if can_act and gate:
                can_act = bool(self.__session.runtime.object_call(self.__session.world,gate['Capability'],gate['Method'],self.actor))
            return {'generation':self.generation, 'current_generation':current,
                    'active':active, 'can_act':bool(can_act),
                    'context':wire(self.__session.contexts[self.actor])}

    @api_entry
    def observe(self):
        with self.__session.lock:
            observation = self.__session.raw_player(self.actor).observe()
            observation['action_state'] = self.action_state()
            observation['can_act'] = observation['action_state']['can_act']
            if self.__session.victory_event is not None:
                observation['victory_event']=deepcopy(self.__session.victory_event)
            return observation

    @api_entry
    def actions(self):return self.__session.raw_player(self.actor).actions()
    @api_entry
    def events(self, cursor=0):return self.__session.raw_player(self.actor).events(cursor)
    @api_entry
    def rules(self, topic=''):return self.__session.raw_player(self.actor).rules(topic)
    @api_entry
    def query(self, name, arguments=None):
        if name == 'player_state':
            if arguments is not None and not isinstance(arguments,dict):raise ValueError('Arguments must be an object')
            if arguments:raise ValueError('player_state takes no arguments')
            return self.observe()
        return self.__session.raw_player(self.actor).query(name, arguments)

    @api_entry
    def submit(self, action, arguments=None):
        session = self.__session
        with session.lock:
            receipt = None
            invoked = False
            detached = deepcopy(arguments)
            def stale_receipt():
                return {'api':API_VERSION,'actor':self.actor,'action':action,'period':session.clock(),
                        'ok':False,'status':'rejected','code':'unavailable_action',
                        'message':'行动控制权已变更，旧句柄的后续命令未执行。',
                        'data':self.action_state()}
            def execute():
                nonlocal invoked, receipt
                if invoked:raise RuntimeError('A submission can execute only once')
                invoked = True
                if not self.action_state()['active']:receipt=stale_receipt()
                else:
                    receipt = session.raw_player(self.actor).submit(action, detached)
                    if action=='player_state' and receipt['ok']:receipt['data']=self.observe()
                return wire(receipt)
            try:
                tool = session.runtime.tools.get(action) if isinstance(action,str) else None
                read_only=bool(tool and tool.read_only) or isinstance(action,str) and action in BUILTINS
                if session.victory_event is not None and not read_only:
                    receipt={'api':API_VERSION,'actor':self.actor,'action':action,'period':session.clock(),
                             'ok':False,'status':'rejected','code':'unavailable_action',
                             'message':'对局已结束，胜利事件已记录；不能继续行动。','data':deepcopy(session.victory_event)}
                elif not self.action_state()['active']:
                    receipt = stale_receipt()
                else:
                    canonical = tool.name if tool else action
                    receipt = wire(session.submit(self, canonical, detached, execute))
                    if not isinstance(receipt,dict) or receipt.get('actor')!=self.actor or receipt.get('action')!=action:
                        raise ValueError('Invalid distribution submission receipt')
            except Exception as exc:
                session._dirty()
                session.record(self, action, detached, receipt, error=exc)
                raise
            session.record(self, action, detached, receipt)
            return receipt

    @api_entry
    def record_dialogue(self, messages):
        with self.__session.lock:
            if self.action_state()['active']:
                self.__session.record_dialogue(self, messages)
