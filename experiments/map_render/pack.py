# -*- coding: utf-8 -*-
import os, sys, random, zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")
from scenarios import eight_nations as en
import mp_ai, render

S = 24
starts = {n: (int(x*S/36), int(y*S/36)) for n, (x, y) in en.STARTS.items()}
w = en.build(size=S, starts=starts); rng = random.Random(11)
for t in range(1, 61):
    for nm in list(w.alive()): mp_ai.dummy_turn(w, nm, rng, rule_ai="v11plus")
    w.resolve_turn(); w.begin_turn()
print("回合", w.turn, "存活", w.alive())

OUT = Path(__file__).resolve().parent / "out"; OUT.mkdir(exist_ok=True)
out = str(OUT)
files = []
png = render.render_world(w)
open(f"{out}/00_世界地图_全体第{w.turn}回合.png","wb").write(png)
files.append(f"{out}/00_世界地图_全体第{w.turn}回合.png")
print(f"世界地图 {len(png)/1024:.0f} KB")
for i, nm in enumerate(w.alive(), 1):
    p = render.render(w, nm)
    if p is None: continue
    fn = f"{out}/{i:02d}_{nm}_视角第{w.turn}回合.png"
    open(fn,"wb").write(p); files.append(fn)
    print(f"  {nm} {len(p)/1024:.0f} KB")
zp = str(Path(__file__).resolve().parent / "zhanguo-map-preview.zip")
with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as z:
    for f in files: z.write(f, os.path.basename(f))
print("打包:", zp, f"{os.path.getsize(zp)/1024:.0f} KB, {len(files)} 张")
