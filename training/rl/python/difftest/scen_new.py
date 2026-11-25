"""脚本化对局：v13plus 规则 AI 照常经营与作战，脚本在每回合开头注入额外动作（外交/银行/拆除/退兵…）。
脚本 = 函数 script(turn, actor, P)；P.do(name, args) 提交；全部动作（含被拒的）经 on_result 钩子按时间序录下。"""
import json, os, sys
sys.path.insert(0, '/root/zhanguo-rl-economy'); os.chdir('/root/zhanguo-rl-economy')
sys.path.insert(0, '/root')
import host as H
from ruleai.v11plus import apimap
from snap_new import snap

class P:
    def __init__(self, player, turn, actor):
        self.player, self.turn, self.actor = player, turn, actor
    def obs(self): return self.player.observe()['facts']
    def q(self, name, args=None): return apimap.query(self.player, name, args)
    EXTRA_DIPLO = ('propose_vassalage', 'submit_vassalage', 'release_vassal', 'independence_war', 'break_defense')
    EXTRA_API = {'auto_trade': ('economy:queries', 'auto_trade'), 'remove_auto_trade': ('economy:queries', 'remove_auto_trade')}
    def do(self, name, args=None):
        args = dict(args or {})
        if name in self.EXTRA_API: return self.player.call_api(*self.EXTRA_API[name], args, action_label=name)
        if name in self.EXTRA_DIPLO: return self.player.call_api('native:diplomacy', 'submit', {**args, 'action': name}, action_label=name)
        return apimap.submit(self.player, name, args)
    def tool(self, name, args=None): return self.player.submit(name, dict(args or {}))

class Wrapper:
    kind = 'rule'; version = 'script'
    def __init__(self, rc, script, ctl): self.rc, self.script, self.ctl = rc, script, ctl
    def play(self, player, max_actions=None, **kw):
        if self.script and player.action_state()['can_act']:
            self.script(self.ctl['turn'], player.actor, P(player, self.ctl['turn'], player.actor))
        return self.rc.play(player, max_actions=max_actions, **kw)
    def to_save(self): return self.rc.to_save()
    def __getattr__(self, n): return getattr(self.rc, n)

def run(seed, size, nations, script, turns, out, rule='v13plus', rule_for=None):
    ctl = {'turn': 0}
    orig = H.create_client
    H.create_client = lambda cfg, record, entry, services: Wrapper(orig(cfg, record, entry, services), script, ctl)
    cfg = {'map_size': size, 'seed': seed, 'nations': [{'id': f'scenario:n{i}', 'name': f'国{i}', 'client': 'rule', 'rule_ai': (rule_for or {}).get(i, rule), 'rule_seed': seed + i} for i in range(nations)]}
    host = H.GameHost.new(cfg)
    init = snap(host); log = []; cur = []
    orig_cycle = host.cycle
    def cycle(*a, **k):
        inner = k.get('on_result')
        def on_result(actor, action, args, result):
            cur.append(dict(actor=actor, action=action, args=args, ok=bool(result.get('ok')), code=result.get('code'),
                            msg=result.get('message'), res={x: result[x] for x in result if x != 'ok'}))
            return inner(actor, action, args, result) if inner else None
        k['on_result'] = on_result
        ctl['turn'] += 1
        r = orig_cycle(*a, **k)
        log.append(dict(turn=ctl['turn'], actions=list(cur), after=snap(host))); cur.clear()
        return r
    host.cycle = cycle
    for t in range(turns): host.cycle()
    json.dump(dict(init=init, log=log), open(out, 'w'), ensure_ascii=False, default=str)
    return host
