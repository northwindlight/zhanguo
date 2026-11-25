"""参照对局录制（纯读取）：新引擎 2 国规则 AI 对局，逐回合记录全部动作与结果。"""
import json, os, sys, collections
sys.path.insert(0, '/root/zhanguo-rl-economy'); os.chdir('/root/zhanguo-rl-economy')
from host import GameHost
seed, size, turns, out = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
cfg = {'map_size': size, 'seed': seed, 'nations': [
    {'id': f'scenario:n{i}', 'name': f'国{i}', 'client': 'rule', 'rule_ai': 'v13plus', 'rule_seed': seed + i} for i in range(int(sys.argv[5]) if len(sys.argv) > 5 else 2)]}
host = GameHost.new(cfg)
sys.path.insert(0,'/root'); from snap_new import snap
init = snap(host)
log = []; cur = []
orig = host.cycle
def cycle(*a, **k):
    inner = k.get('on_result')
    def on_result(actor, action, args, result):
        cur.append(dict(actor=actor, action=action, args=args, ok=bool(result.get('ok')), res={x: result[x] for x in result if x != 'ok'} if isinstance(result, dict) else str(result)))
        return inner(actor, action, args, result) if inner else None
    k['on_result'] = on_result
    r = orig(*a, **k)
    log.append(dict(turn=len(log) + 1, actions=list(cur), after=snap(host))); cur.clear()
    return r
host.cycle = cycle
for t in range(turns):
    host.cycle()
json.dump(dict(init=init, log=log), open(out, 'w'), ensure_ascii=False, default=str)
c = collections.Counter(x['action'] for r in log for x in r['actions'])
print(c)
bad = collections.Counter(x['action'] for r in log for x in r['actions'] if not x['ok'])
print('fail', bad)
