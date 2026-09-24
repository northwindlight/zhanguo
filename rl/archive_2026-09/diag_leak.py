import copy, sys, unittest
sys.path.insert(0, ".")
import game, rl.jitter as jitter
import tests.test_rule_v10 as M

true = {k: copy.deepcopy(getattr(game, k)) for k in ("UNIT_TYPES", "TERRAIN_STATS", "BUILDINGS", "MARKET")}
unittest.TextTestRunner(verbosity=0).run(
    unittest.defaultTestLoader.loadTestsFromTestCase(M.TestJitterRegression))
def diff(k):
    cur, t = getattr(game, k), true[k]
    if isinstance(t, dict) and cur.keys() == t.keys():
        bad = [n for n in t if cur[n] != t[n]]
        return f"{k}: {len(bad)} 处不同 {bad[:3]}" if bad else f"{k}: 干净"
    return f"{k}: 结构不同"
for k in ("UNIT_TYPES", "TERRAIN_STATS", "BUILDINGS", "MARKET"):
    print(" ", diff(k))
print("  jitter.current() =", "None" if jitter.current() is None else "非 None（记录没清）")
