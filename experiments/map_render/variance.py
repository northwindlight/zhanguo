# -*- coding: utf-8 -*-
"""同一张图连测 3 次：先量清楚模型自己在这个任务上的抖动，再谈 A/B 差异可不可信。"""
import sys
from pathlib import Path, json, base64, random, re, urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.stdout.reconfigure(encoding="utf-8")
from scenarios import eight_nations as en
import mp_ai, render
S=24; starts={n:(int(x*S/36),int(y*S/36)) for n,(x,y) in en.STARTS.items()}
w=en.build(size=S,starts=starts); rng=random.Random(11)
for t in range(1,61):
    for nm in list(w.alive()): mp_ai.dummy_turn(w,nm,rng,rule_ai="v11plus")
    w.resolve_turn(); w.begin_turn()
me="韩"; vis=mp_ai._visible_cells(w,me)
owned=[(x,y) for (x,y) in sorted(vis) if w.owned_by(x,y)]
random.seed(4); sample=random.sample(owned,min(12,len(owned)))
truth={(x+1,y+1):w.owned_by(x,y) for (x,y) in sample}
key=json.load(open(Path(__file__).resolve().parents[2] / "mp_config.json"))["nations"][0]["api_key"]
qs="、".join(f"({a},{b})" for (a,b) in truth)
Q=(f"这是一张战国战略地图。{render.legend_text(w,me)}\n请回答下列坐标各属于哪个国家：\n{qs}\n"
   f"严格按行输出「坐标=国家」，不要解释。")
png=render.render(w,me); b64=base64.b64encode(png).decode()
def run():
    body={"model":"qwen3.8-flash","max_tokens":2000,"temperature":0,
          "messages":[{"role":"user","content":[{"type":"text","text":Q},
            {"type":"image_url","image_url":{"url":"data:image/png;base64,"+b64}}]}]}
    req=urllib.request.Request("http://127.0.0.1:5050/v1/chat/completions",data=json.dumps(body).encode(),
        headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=300) as r: a=json.loads(r.read())["choices"][0]["message"]["content"] or ""
    got={}
    for line in a.split("\n"):
        m=re.search(r"\((\d+),(\d+)\)\s*[=:：]\s*(\S+)",line)
        if m: got[(int(m.group(1)),int(m.group(2)))]=m.group(3).strip("。，,")
    return sum(1 for k,v in truth.items() if got.get(k,"")==v or got.get(k,"").startswith(v)), got
res=[run() for _ in range(3)]
for i,(h,g) in enumerate(res): print(f"  第{i+1}次: {h}/12")
print(f"\n同一张图三次: {[h for h,_ in res]}  → 抖动幅度 {max(h for h,_ in res)-min(h for h,_ in res)} 题")
# 逐题稳定性
from collections import Counter
for k,v in sorted(truth.items()):
    ans=[g.get(k,"?") for _,g in res]
    ok=[a==v or a.startswith(v) for a in ans]
    print(f"  {k} 真={v:3s} 三次答={ans}  {'稳对' if all(ok) else ('稳错' if not any(ok) else '摇摆')}")
