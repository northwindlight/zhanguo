# -*- coding: utf-8 -*-
"""对拍两份 trunk_linear 日志的三张 AUC 表（逐格）。判据见 rlmail2/to_pi.md 08:46 那条。"""
import re
import sys

ROW = re.compile(r"^\s{2}(\S.*?)\s{2,}(线性探针|自带头|策略 logits)\s+([0-9.]+)\s+([0-9.]+)\s*$")
HDR = re.compile(r"^=== (.+?)：(\d+) 个候选 / (\d+) 个状态")
SUM = re.compile(r"^共 (\d+) 个候选 / (\d+) 个状态，基准可执行率 ([0-9.]+)%")


def parse(path):
    tables, cur, summ = {}, None, None
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        m = SUM.match(line)
        if m:
            summ = (int(m.group(1)), int(m.group(2)), float(m.group(3)))
            continue
        m = HDR.match(line)
        if m:
            cur = m.group(1).strip()
            tables[cur] = {"_hdr": (int(m.group(2)), int(m.group(3))), "_rows": {}}
            continue
        m = ROW.match(line)
        if m and cur:
            tables[cur]["_rows"][(m.group(1).strip(), m.group(2))] = (float(m.group(3)), float(m.group(4)))
    return summ, tables


a_sum, a = parse(sys.argv[1])
b_sum, b = parse(sys.argv[2])
print(f"旧（归档）: {sys.argv[1]}\n新（本次）: {sys.argv[2]}\n")
print(f"汇总行  旧 {a_sum}   新 {b_sum}   {'一致 ✓' if a_sum == b_sum else '★不一致 ✗'}")

worst, ncell, bad = 0.0, 0, []
for t in a:
    if t not in b:
        bad.append(f"新日志缺表 {t}")
        continue
    if a[t]["_hdr"] != b[t]["_hdr"]:
        bad.append(f"{t} 表头计数不同 {a[t]['_hdr']} vs {b[t]['_hdr']}")
    for k, (x1, y1) in a[t]["_rows"].items():
        if k not in b[t]["_rows"]:
            bad.append(f"{t} 缺行 {k}")
            continue
        x2, y2 = b[t]["_rows"][k]
        for lbl, (v1, v2) in (("状态内同种类", (x1, x2)), ("同候选跨状态", (y1, y2))):
            ncell += 1
            d = abs(v1 - v2)
            worst = max(worst, d)
            if d > 0.001:
                bad.append(f"{t} / {k[0]} / {k[1]} / {lbl}: 旧 {v1:.3f} 新 {v2:.3f} 差 {d:.3f}")
for t in b:
    if t not in a:
        bad.append(f"新日志多出表 {t}")

print(f"对拍格数 {ncell}   最大绝对差 {worst:.4f}")
print("判据：≤0.001 ⇒ 探针路径未被动、归档数字仍可比；任一格 >0.003 ⇒ 判量具被同步动了")
if bad:
    print("\n★不一致明细：")
    for x in bad:
        print("  -", x)
    print(f"\n判决：{'量具被动过（>0.003）✗' if worst > 0.003 else '差异在 0.001~0.003 之间，需人工看'}")
else:
    print("\n判决：**逐格一致 ✓**（差 ≤0.001）")
