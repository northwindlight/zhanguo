# -*- coding: utf-8 -*-
import sys, random
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")
from scenarios import eight_nations as en
import mp_ai, render

OUT = Path(__file__).resolve().parent / "out"
OUT.mkdir(exist_ok=True)

S = 24
starts = {n: (int(x * S / 36), int(y * S / 36)) for n, (x, y) in en.STARTS.items()}
w = en.build(size=S, starts=starts)
rng = random.Random(11)
SHOTS = {1, 60}
for t in range(1, 61):
    for nm in list(w.alive()):
        mp_ai.dummy_turn(w, nm, rng, rule_ai="v11plus")
    w.resolve_turn(); w.begin_turn()
    if t in SHOTS:
        for who in ("韩",):
            png = render.render(w, who)
            if png is None:
                raise SystemExit("地图渲染失败，请检查 Pillow；render.available()=" + str(render.available()))
            (OUT / f"m_{who}_t{t}.png").write_bytes(png)

            print(f"t{t} {who}: {len(png)/1024:.1f} KB  {who} 国土{len(w.own_tiles(who))}块 视野{len(mp_ai._visible_cells(w,who))}格")
