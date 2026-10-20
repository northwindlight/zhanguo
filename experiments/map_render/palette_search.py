# -*- coding: utf-8 -*-
"""搜 8 国国土色：**每个都要离四色兵牌足够远**。
不靠眼睛挑——先算色相空间里到底放不放得下。"""
import math, itertools

def srgb_to_lin(c):
    c = c / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def rgb_to_oklab(rgb):
    r, g, b = (srgb_to_lin(c) for c in rgb)
    l = 0.4122214708*r + 0.5363325363*g + 0.0514459929*b
    m = 0.2119034982*r + 0.6806995451*g + 0.1073969566*b
    s = 0.0883024619*r + 0.2817188376*g + 0.6299787005*b
    l_, m_, s_ = l**(1/3), m**(1/3), s**(1/3)
    return (0.2104542553*l_ + 0.7936177850*m_ - 0.0040720468*s_,
            1.9779984951*l_ - 2.4285922050*m_ + 0.4505937099*s_,
            0.0259040371*l_ + 0.7827717662*m_ - 0.8086757660*s_)

def de(a, b):
    la, lb = rgb_to_oklab(a), rgb_to_oklab(b)
    return 100 * math.sqrt(sum((x - y) ** 2 for x, y in zip(la, lb)))

import colorsys
def hsl_hex(h, s, l):
    r, g, b = colorsys.hls_to_rgb(h / 360, l, s)
    return (round(r*255), round(g*255), round(b*255))

SIDE = {"自己-绿": (58,196,96), "友军-蓝": (58,132,232), "敌人-红": (224,62,58), "中立-灰": (142,144,142)}

# 先看：去掉四色所在色相带之后，还剩多少可用的色相
print("=== 四色兵牌占据的色相 ===")
for k, v in SIDE.items():
    h, l, s = colorsys.rgb_to_hls(*[c/255 for c in v])
    print(f"  {k}: 色相 {h*360:5.0f}°  亮度 {l:.2f}  饱和 {s:.2f}")

print("\n=== 网格搜索：8 色，最大化「离四色兵牌的最近距离」的最小值 ===")
best = None
for sat in (0.55, 0.7, 0.85, 1.0):
    for light in (0.42, 0.52, 0.62):
        # 扫 36 个色相起点 × 间隔，取使最小交叉距离最大的组合
        for start in range(0, 360, 5):
            for step in (35, 40, 45, 50):
                cand = [hsl_hex((start + i * step) % 360, sat, light) for i in range(8)]
                worst = min(min(de(c, s) for s in SIDE.values()) for c in cand)
                # 同时要求 8 色内部别太近
                inner = min(de(a, b) for a, b in itertools.combinations(cand, 2))
                score = min(worst * 1.6, inner)
                if best is None or score > best[0]:
                    best = (score, cand, worst, inner, sat, light, start, step)
score, cand, worst, inner, sat, light, start, step = best
print(f"最优：饱和 {sat} 亮度 {light} 起点 {start}° 步长 {step}°")
print(f"  离四色兵牌的最近距离（最小）= {worst:.1f}   （门槛 15）")
print(f"  8 色之间最近距离（最小）    = {inner:.1f}")
print("  配色:", ",".join("#%02X%02X%02X" % c for c in cand))
for i, c in enumerate(cand):
    d = min((de(c, s), k) for k, s in SIDE.items())
    print(f"    #{i+1} #%02X%02X%02X  离最近兵牌色 {d[1]} ΔE={d[0]:.1f}" % c)
