import subprocess, sys, pathlib
mods = sorted(p.stem for p in pathlib.Path("tests").glob("test_*.py"))
mods = [m for m in mods if m != "test_rule_v10"]
TARGET = "tests.test_rule_v10.TestNoMountainSpecialCase"

def fails(sub):
    r = subprocess.run([sys.executable, "-m", "unittest", "-q"] + [f"tests.{m}" for m in sub] + [TARGET],
                       capture_output=True, text=True, timeout=900)
    return "FAILED" in r.stderr or "FAILED" in r.stdout

lo, hi = 0, len(mods)
if not fails(mods):
    print("全前缀都不触发"); sys.exit()
while lo < hi:
    mid = (lo + hi) // 2
    if fails(mods[:mid]):
        hi = mid
    else:
        lo = mid + 1
print(f"最小触发前缀长度 {lo}：最后一个模块 = {mods[lo-1]}")
print("前缀尾部：", mods[max(0,lo-4):lo])
