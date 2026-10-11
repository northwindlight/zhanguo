# -*- coding: utf-8 -*-
"""对照实验，把"分不清"的病因拆开：
   A 斜纹开 · 图例在图里      （现状）
   B 斜纹**关** · 图例在图里   （→ 若 B≫A，凶手是斜纹）
   C 斜纹开 · 图例在**文字里** （→ 若 C≫A，凶手是图例不好读）
只考**有主的格子**（真正要问的是"分得清哪个国家"）。"""
import sys
from pathlib import Path, json, base64, random, re, urllib.request
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
me = "韩"; vis = mp_ai._visible_cells(w, me)
owned = [(x, y) for (x, y) in sorted(vis) if w.owned_by(x, y)]
random.seed(4); sample = random.sample(owned, min(12, len(owned)))
truth = {(x+1, y+1): w.owned_by(x, y) for (x, y) in sample}

TEXT_LEGEND = ("图例（国→颜色）：秦=黄　魏=青　韩=品红(你在的国家)　赵=白　"
               "燕=亮黄　齐=亮青　楚=亮品红　周=亮蓝。")
key = json.load(open(Path(__file__).resolve().parents[2] / "mp_config.json"))["nations"][0]["api_key"]
qs = "、".join(f"({a},{b})" for (a, b) in truth)

def ask(png, extra=""):
    Q = (f"这是一张战国战略地图。{extra}请回答下列坐标各属于哪个国家：\n{qs}\n"
         f"严格按行输出「坐标=国家」，不要解释。")
    body = {"model": "qwen3.8-flash", "max_tokens": 2000, "temperature": 0,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": Q},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()}}]}]}
    req = urllib.request.Request("http://127.0.0.1:5050/v1/chat/completions",
        data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return (json.loads(r.read())["choices"][0]["message"]["content"] or "")

def score(ans):
    got = {}
    for line in ans.split("\n"):
        m = re.search(r"\((\d+),(\d+)\)\s*[=:：]\s*(\S+)", line)
        if m: got[(int(m.group(1)), int(m.group(2)))] = m.group(3).strip("。，,")
    hit = sum(1 for k, v in truth.items() if (got.get(k, "") == v or got.get(k, "").startswith(v)))
    return hit, got

png_hatch = render.render(w, me)
saved = render._hatch
render._hatch = lambda *a, **k: None
png_solid = render.render(w, me)
render._hatch = saved
open("/tmp/zgui/ro_hatch.png","wb").write(png_hatch)
open("/tmp/zgui/ro_solid.png","wb").write(png_solid)

print("考题:", "、".join(f"({a},{b})={v}" for (a,b),v in sorted(truth.items())))
for tag, png, extra in (("A 斜纹开·图例在图里", png_hatch, ""),
                        ("B 斜纹关·图例在图里", png_solid, ""),
                        ("C 斜纹开·图例在文字", png_hatch, TEXT_LEGEND)):
    hit, got = score(ask(png, extra))
    print(f"\n{tag}: {hit}/{len(truth)}")
    for k, v in sorted(truth.items()):
        g = got.get(k, "（没答）")
        print(f"    {k} 真={v:3s} 答={g:4s} {'✅' if g==v or g.startswith(v) else '❌'}", end="")
    print()
