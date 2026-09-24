import collections, os, sys
sys.path.insert(0, "/home/northwind/.claude/jobs/556a2faa/tmp/wt_eval")
import torch
from rl.env import ACT_SAFETY, KINDS, ZhanguoEnv
from rl.features import reject_reason
from rl.hw import set_threads
from rl.ppo import act
from rl.tokenize import GROUPS, tokenize
from rl.transformer import WindowTransformer

ckpt, seed = sys.argv[1], int(sys.argv[2])
det = "--det" in sys.argv
set_threads(int(os.environ.get("ZHANGUO_THREADS", "2")))
env = ZhanguoEnv(map_size=16, max_turns=200, max_actions_per_turn=ACT_SAFETY,
                 invalid_penalty=2.0)
env.reset(0)
w0 = tokenize(env, env._obs())
m = WindowTransformer({g: w0.feats[g].shape[1] for g in GROUPS},
                      d_model=192, n_layer=4, n_head=4)
m.set_sub_sizes([len(env.sub_tables[k]) for k in KINDS])
m.load_state_dict(torch.load(ckpt, map_location="cpu", weights_only=False)["model"],
                  strict=False)
m.eval()
torch.manual_seed(0x5EED)
obs = env.reset(seed)
tab, msgs, att = collections.Counter(), collections.Counter(), collections.Counter()
while True:
    w = tokenize(env, obs)
    i, _lp, _v = act(m, obs, deterministic=det, win=w)
    a = obs.cand["actions"][i]
    obs, r, done, info = env.step(a)
    att[a.kind] += 1
    if not info["ok"]:
        tab[(a.kind, reject_reason(info["msg"]))] += 1
        if a.kind == "build":
            msgs[str(info["msg"])[:70]] += 1
    if done:
        break
print(f"\n=== {os.path.basename(ckpt)} seed={seed} {'贪心' if det else '采样'} ===")
print(f"{'动作':<9}{'次数':>7}{'被拒':>7}{'被拒率':>9}   被拒原因分布")
for k, n in att.most_common():
    rej = sum(v for (kk, _), v in tab.items() if kk == k)
    dist = " ".join(f"{rr}:{v}" for (kk, rr), v in tab.most_common() if kk == k)
    print(f"{k:<9}{n:>7}{rej:>7}{rej/max(n,1):>8.0%}   {dist}")
if msgs:
    print("\nbuild 的原始文案前 6：")
    for msg, n in msgs.most_common(6):
        print(f"  {n:>5}  {msg}")
