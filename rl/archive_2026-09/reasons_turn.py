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
SEGS = [(1, 35), (36, 70), (71, 105), (106, 140), (141, 175), (176, 200)]
seg_rej = collections.Counter(); seg_step = collections.Counter()
bld = collections.Counter(); bld_seg = collections.Counter()
while True:
    w = tokenize(env, obs)
    i, _lp, _v = act(m, obs, deterministic=det, win=w)
    a = obs.cand["actions"][i]
    obs, r, done, info = env.step(a)
    t = info["turn"]
    seg = next((s for s in SEGS if s[0] <= t <= s[1]), None)
    if seg:
        seg_step[seg] += 1
        if not info["ok"]:
            seg_rej[seg] += 1
            if a.kind == "build":
                bld[a.sub] += 1
                bld_seg[seg] += 1
    if done:
        break
print(f"\n=== {os.path.basename(ckpt)} seed={seed} {'贪心' if det else '采样'} ===")
print(f"{'回合段':<14}{'动作数':>8}{'被拒':>7}{'被拒率':>9}{'其中非法build':>14}")
for s in SEGS:
    lab = f"{s[0]}~{s[1]}"
    print(f"{lab:<14}{seg_step[s]:>8}{seg_rej[s]:>7}{seg_rej[s]/max(seg_step[s],1):>8.0%}"
          f"{bld_seg[s]:>14}")
print("\n非法 build 按建筑：", " ".join(f"{k}:{v}" for k, v in bld.most_common(6)))
