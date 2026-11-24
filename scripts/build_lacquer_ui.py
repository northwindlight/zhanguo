"""生成「漆金」界面素材：九宫格面板、按钮、题签、印章与图标（原创 SVG）。

    python3 scripts/build_lacquer_ui.py

输出到 mods/ui/desktop/assets/ui-lacquer/，同时写 Godot 导入配置和一张图录
（docs/design/ui-lacquer/catalog.svg）。色板与 Scripts/UiSkin.cs 保持一致：
漆黑底、青铜描边与回纹角饰、朱漆题签，数据区保持素净。
SVG 只用路径、矩形与线性/径向渐变，不用滤镜、外部图片或文字，ThorVG 可直接导入。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'mods' / 'ui' / 'desktop' / 'assets' / 'ui-lacquer'
DOC = ROOT / 'docs' / 'design' / 'ui-lacquer'

# 色板（与 UiSkin.cs 同步）
INK0, INK1, INK2, INK3 = '#0c0907', '#18110b', '#22190f', '#33261a'
BRONZE, BRONZE_LT, BRONZE_DK, BRONZE_DEEP = '#b48d4e', '#e2c27e', '#7a5c34', '#4a3822'
LACQUER, LACQUER_LT, LACQUER_DK = '#8e2b1e', '#b63c27', '#5a1a10'
PAPER, PAPER_MUTED = '#efe2c4', '#b9aa8b'
JADE = '#6fa38f'

ASSETS: dict[str, tuple[str, dict]] = {}


def svg(w: int, h: int, body: str, defs: str = '') -> str:
    d = f'<defs>{defs}</defs>' if defs else ''
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">{d}{body}</svg>\n'


def vgrad(id_: str, *stops: tuple[float, str], opacity: float = 1) -> str:
    s = ''.join(f'<stop offset="{o}" stop-color="{c}" stop-opacity="{opacity}"/>' for o, c in stops)
    return f'<linearGradient id="{id_}" x1="0" y1="0" x2="0" y2="1">{s}</linearGradient>'


def add(name: str, text: str, **meta):
    ASSETS[name] = (text, meta)


def mirror4(w: int, h: int, corner: str) -> str:
    """Repeat a top-left corner ornament in all four corners."""
    return (f'<g>{corner}</g><g transform="translate({w},0) scale(-1,1)">{corner}</g>'
            f'<g transform="translate(0,{h}) scale(1,-1)">{corner}</g><g transform="translate({w},{h}) scale(-1,-1)">{corner}</g>')


# ── 面板 ─────────────────────────────────────────────────────────────
FRET = (f'<path d="M2.5 21 V2.5 H21" fill="none" stroke="{BRONZE_LT}" stroke-width="2.4" stroke-linecap="square"/>'
        f'<path d="M8.5 18 V8.5 H18 V14 H13 V13" fill="none" stroke="{BRONZE}" stroke-width="1.6" stroke-linecap="square"/>'
        f'<rect x="0.5" y="0.5" width="6" height="6" fill="{BRONZE_LT}"/><rect x="2.5" y="2.5" width="2" height="2" fill="{INK0}"/>')


def panel(name: str, alpha: float, ornament: bool = True):
    w = h = 96
    body = (f'<rect x="1" y="1" width="94" height="94" rx="2" fill="url(#f)"/>'
            f'<rect x="1" y="1" width="94" height="94" rx="2" fill="none" stroke="{INK0}" stroke-width="2"/>'
            f'<rect x="4.5" y="4.5" width="87" height="87" fill="none" stroke="{BRONZE}" stroke-width="1.4"/>'
            f'<rect x="7.5" y="7.5" width="81" height="81" fill="none" stroke="{BRONZE_DEEP}" stroke-width="1"/>')
    if ornament:
        body += mirror4(w, h, FRET)
    add(name, svg(w, h, body, vgrad('f', (0, '#241a10'), (1, '#160f0a'), opacity=alpha)), margin=24)


panel('panel', .97)
panel('panel-glass', .88)
# 朴素内框：数据卡片、列表条目
add('card', svg(48, 48,
    f'<rect x="0.5" y="0.5" width="47" height="47" rx="2" fill="url(#f)" stroke="{BRONZE_DEEP}"/>'
    f'<path d="M3 1.5 H45" stroke="{BRONZE_DK}" stroke-opacity=".6"/>',
    vgrad('f', (0, '#21180f'), (1, '#1a130d'))), margin=8)
add('tooltip', svg(48, 48,
    f'<rect x="0.5" y="0.5" width="47" height="47" rx="2" fill="#140e09" fill-opacity=".97" stroke="{BRONZE}"/>'
    f'<rect x="3.5" y="3.5" width="41" height="41" fill="none" stroke="{BRONZE_DEEP}"/>'
    f'<rect x="1" y="1" width="4" height="4" fill="{BRONZE_LT}"/><rect x="43" y="1" width="4" height="4" fill="{BRONZE_LT}"/>'
    f'<rect x="1" y="43" width="4" height="4" fill="{BRONZE_LT}"/><rect x="43" y="43" width="4" height="4" fill="{BRONZE_LT}"/>'), margin=10)

# 底部政务栏：上沿双青铜条，左右回纹
dock_fret = (f'<path d="M3 30 V3 H30" fill="none" stroke="{BRONZE_LT}" stroke-width="2.4"/>'
             f'<path d="M9 22 V9 H22 V16 H15 V15" fill="none" stroke="{BRONZE}" stroke-width="1.6"/>')
add('dock', svg(128, 80,
    f'<rect x="1" y="1" width="126" height="78" rx="3" fill="url(#f)" stroke="{INK0}" stroke-width="2"/>'
    f'<path d="M4 4.5 H124" stroke="{BRONZE_LT}" stroke-width="1.6"/><path d="M4 8 H124" stroke="{BRONZE_DK}"/>'
    f'<path d="M4 75.5 H124" stroke="{BRONZE_DK}"/>'
    f'<g>{dock_fret}</g><g transform="translate(128,0) scale(-1,1)">{dock_fret}</g>',
    vgrad('f', (0, '#2a1e13'), (1, '#150f0a'), opacity=.96)), margin=[32, 14, 32, 10])

# ── 题签与标题线 ─────────────────────────────────────────────────────
add('title', svg(128, 36,
    f'<path d="M0 2 H128 L120 18 L128 34 H0 L8 18 Z" fill="url(#r)"/>'
    f'<path d="M1 3 H127" stroke="{BRONZE_LT}" stroke-width="1.6"/><path d="M1 33 H127" stroke="{BRONZE_LT}" stroke-width="1.6"/>'
    f'<path d="M10 6.5 H118" stroke="#e7a38c" stroke-opacity=".28"/>',
    vgrad('r', (0, '#a53624'), (.55, '#80271a'), (1, '#5a1a10'))), margin=[22, 6, 22, 6])
add('heading', svg(96, 16,
    f'<path d="M14 10.5 H96" stroke="url(#l)" stroke-width="1.4"/>'
    f'<path d="M5 6 L9.5 10.5 L5 15 L0.5 10.5 Z" fill="{LACQUER_LT}" stroke="{BRONZE_LT}" stroke-width="1"/>'
    f'<path d="M9.5 10.5 H14" stroke="{BRONZE_LT}" stroke-width="1.4"/>',
    f'<linearGradient id="l" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="{BRONZE_LT}"/>'
    f'<stop offset="1" stop-color="{BRONZE}" stop-opacity=".25"/></linearGradient>'), margin=[16, 0, 16, 10])

# ── 按钮 ─────────────────────────────────────────────────────────────
CHAMFER = 'M4 0.5 H44 L47.5 4 V44 L44 47.5 H4 L0.5 44 V4 Z'


def button(name: str, top: str, bottom: str, stroke: str, width: float = 1.2, highlight: str = '', extra: str = '', fill_opacity: float = 1):
    body = f'<path d="{CHAMFER}" fill="url(#f)" stroke="{stroke}" stroke-width="{width}"/>'
    if highlight:
        body += f'<path d="M5 2.6 H43" stroke="{highlight}" stroke-width="1.2"/>'
    add(name, svg(48, 48, body + extra, vgrad('f', (0, top), (1, bottom), opacity=fill_opacity)), margin=8)


button('button-normal', '#2c2117', '#1c150f', BRONZE_DK, highlight='#4a3a28')
button('button-hover', '#3d2e1f', '#281d13', BRONZE, highlight='#6b5435')
button('button-pressed', '#120d09', '#1d1610', BRONZE_LT)
button('button-disabled', '#1b1510', '#17120d', '#3b2f23')
button('button-selected', '#40301d', '#2a1d12', BRONZE_LT, width=1.8, highlight='#7a6138',
       extra=f'<path d="M12 44.5 H36" stroke="{BRONZE_LT}" stroke-width="2.2"/>')
add('button-focus', svg(48, 48, f'<path d="{CHAMFER}" fill="none" stroke="{BRONZE_LT}" stroke-width="1.6" stroke-dasharray="3 2"/>'), margin=8)
button('button-primary', '#a83a27', '#6c2015', BRONZE_LT, highlight='#d0664a')
button('button-primary-hover', '#c0462f', '#7f271a', '#f3d797', highlight='#e07a5c')
button('button-primary-pressed', '#5a190f', '#7a2417', '#f3d797')
button('button-danger', '#4a1c14', '#2b100b', '#c4553c', highlight='#6c2c20')
# 结束回合：朱印式主按钮，左侧留出印面
add('seal-button', svg(96, 56,
    f'<path d="M5 1 H91 L95 5 V51 L91 55 H5 L1 51 V5 Z" fill="url(#f)" stroke="{BRONZE_LT}" stroke-width="1.6"/>'
    f'<path d="M6 4.5 H90" stroke="#e48a6a" stroke-opacity=".45" stroke-width="1.2"/>'
    f'<path d="M7 51.5 H89" stroke="{INK0}" stroke-opacity=".35" stroke-width="1.2"/>',
    vgrad('f', (0, '#b23f29'), (.6, '#86281a'), (1, '#621c11'))), margin=[14, 12, 14, 12])
add('seal-button-hover', svg(96, 56,
    f'<path d="M5 1 H91 L95 5 V51 L91 55 H5 L1 51 V5 Z" fill="url(#f)" stroke="#f7e0a6" stroke-width="2"/>'
    f'<path d="M6 4.5 H90" stroke="#f0a080" stroke-opacity=".55" stroke-width="1.2"/>',
    vgrad('f', (0, '#c94c33'), (.6, '#972f1f'), (1, '#6e2014'))), margin=[14, 12, 14, 12])
add('seal-button-disabled', svg(96, 56,
    f'<path d="M5 1 H91 L95 5 V51 L91 55 H5 L1 51 V5 Z" fill="url(#f)" stroke="{BRONZE_DEEP}" stroke-width="1.4"/>',
    vgrad('f', (0, '#3d2019'), (1, '#2a1611'))), margin=[14, 12, 14, 12])

# 复选与开关
add('check-off', svg(24, 24, f'<rect x="3.5" y="3.5" width="17" height="17" rx="1.5" fill="{INK1}" stroke="{BRONZE}" stroke-width="1.4"/>'))
add('check-on', svg(24, 24,
    f'<rect x="3.5" y="3.5" width="17" height="17" rx="1.5" fill="{LACQUER}" stroke="{BRONZE_LT}" stroke-width="1.4"/>'
    f'<path d="M7.5 12 L10.8 15.5 L16.8 8.5" fill="none" stroke="{PAPER}" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>'))
add('check-disabled', svg(24, 24, f'<rect x="3.5" y="3.5" width="17" height="17" rx="1.5" fill="{INK1}" stroke="#3b2f23" stroke-width="1.4"/>'))
add('toggle-off', svg(44, 24,
    f'<rect x="1.5" y="4.5" width="41" height="15" rx="7.5" fill="{INK1}" stroke="{BRONZE_DK}" stroke-width="1.4"/>'
    f'<circle cx="11" cy="12" r="6.5" fill="{PAPER_MUTED}" stroke="{INK0}"/>'))
add('toggle-on', svg(44, 24,
    f'<rect x="1.5" y="4.5" width="41" height="15" rx="7.5" fill="{LACQUER}" stroke="{BRONZE_LT}" stroke-width="1.4"/>'
    f'<circle cx="33" cy="12" r="6.5" fill="{PAPER}" stroke="{INK0}"/>'))
add('radio-off', svg(24, 24, f'<circle cx="12" cy="12" r="8" fill="{INK1}" stroke="{BRONZE}" stroke-width="1.4"/>'))
add('radio-on', svg(24, 24, f'<circle cx="12" cy="12" r="8" fill="{INK1}" stroke="{BRONZE_LT}" stroke-width="1.4"/>'
                            f'<circle cx="12" cy="12" r="4.2" fill="{LACQUER_LT}"/>'))
add('arrow-down', svg(24, 24, f'<path d="M7 10 L12 15 L17 10" fill="none" stroke="{BRONZE_LT}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'))

# ── 图标 48×48（显示 24–32） ────────────────────────────────────────
P, A, R = PAPER, BRONZE_LT, '#c84a32'
LINE = f'fill="none" stroke="{P}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"'
THIN = f'fill="none" stroke="{P}" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"'
OUT_ = f'stroke="{INK0}" stroke-width="1.4" stroke-linejoin="round"'

ICONS = {
    # 版筑：夯土墙与木夯
    'build': (f'<rect x="5" y="31" width="38" height="12" rx="1" fill="{A}" {OUT_}/>'
              f'<path d="M5 37 H43 M15 31 V37 M29 37 V43 M36 31 V37" stroke="{INK0}" stroke-width="1.6"/>'
              f'<path d="M12 27 L29 10" {LINE}/>'
              f'<rect x="23" y="1.5" width="18" height="10" rx="1.5" transform="rotate(45 32 6.5)" fill="{P}" {OUT_}/>'),
    # 戈
    'army': (f'<path d="M9 43 L38 6" {LINE}/>'
             f'<path d="M34 15 L10 6 L7.5 11 L30 21 Z" fill="{A}" {OUT_}/>'
             f'<path d="M35.5 10.5 L42 13" {LINE}/><path d="M12 34 L18 38" stroke="{R}" stroke-width="3" stroke-linecap="round"/>'),
    # 布币
    'market': (f'<path d="M19 4 H29 V11 L36 14 V33 L31.5 35.5 L31 44 H27 L24 37.5 L21 44 H17 L16.5 35.5 L12 33 V14 L19 11 Z" fill="{A}" {OUT_}/>'
               f'<circle cx="24" cy="7.5" r="1.8" fill="{INK0}"/><path d="M24 16 V32 M18.5 20 V28 M29.5 20 V28" stroke="{INK0}" stroke-width="1.6" stroke-linecap="round"/>'),
    # 圆钱（方孔）
    'bank': (f'<circle cx="31" cy="17" r="11" fill="{BRONZE}" {OUT_}/><rect x="27.5" y="13.5" width="7" height="7" fill="{INK0}"/>'
             f'<circle cx="18" cy="29" r="13" fill="{A}" {OUT_}/><rect x="14" y="25" width="8" height="8" fill="{INK0}"/>'
             f'<circle cx="18" cy="29" r="9.5" fill="none" stroke="{INK0}" stroke-opacity=".45" stroke-width="1.2"/>'),
    # 使节之节（旄）
    'diplomacy': (f'<path d="M24 4 V45" {LINE}/>'
                  + ''.join(f'<path d="M24 {y} C18 {y+1} 15 {y+5} 15 {y+9} C19 {y+7} 22 {y+6} 24 {y+6} C26 {y+6} 29 {y+7} 33 {y+9} C33 {y+5} 30 {y+1} 24 {y} Z" fill="{R}" {OUT_}/>'
                            for y in (8, 18, 28))
                  + f'<circle cx="24" cy="4.5" r="2.6" fill="{A}" {OUT_}/>'),
    # 会盟：交叉旌旗
    'alliance': (f'<path d="M9 44 L33 7 M39 44 L15 7" {LINE}/>'
                 f'<path d="M33 7 L44 10.5 L35 15.5 Z" fill="{R}" {OUT_}/><path d="M15 7 L4 10.5 L13 15.5 Z" fill="{A}" {OUT_}/>'
                 f'<circle cx="24" cy="25" r="3" fill="{P}" {OUT_}/>'),
    # 竹简
    'intelligence': (''.join(f'<rect x="{x}" y="8" width="5.6" height="32" rx="1.4" fill="{A}" {OUT_}/>' for x in (7, 14, 21, 28, 35))
                     + f'<path d="M5 16 H43 M5 32 H43" stroke="{R}" stroke-width="2.2"/>'),
    # 鼎
    'nation': (f'<path d="M13 16 V7 H17 V16 M31 16 V7 H35 V16" fill="none" stroke="{P}" stroke-width="2.6" stroke-linejoin="round"/>'
               f'<path d="M6 16 H42 Q42 35 24 35 Q6 35 6 16 Z" fill="{A}" {OUT_}/>'
               f'<path d="M9 21 H39" stroke="{INK0}" stroke-width="1.5"/><path d="M13 25 L16 22 L19 25 L22 22 L25 25 L28 22 L31 25 L34 22" fill="none" stroke="{INK0}" stroke-width="1.3"/>'
               f'<path d="M14 32 L10 44 M24 35 V44 M34 32 L38 44" {LINE}/>'),
    # 朱印 + 循环箭头
    'end-turn': (f'<rect x="5" y="5" width="38" height="38" rx="3" fill="{R}" {OUT_}/>'
                 f'<rect x="9.5" y="9.5" width="29" height="29" rx="1.5" fill="none" stroke="{P}" stroke-width="2.2"/>'
                 f'<path d="M30.5 18.5 A8.5 8.5 0 1 0 32 26" fill="none" stroke="{P}" stroke-width="3" stroke-linecap="round"/>'
                 f'<path d="M31.5 12 L31.5 19.5 L24.5 19" fill="none" stroke="{P}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'),
    # 舆图
    'map': (f'<rect x="9" y="11" width="30" height="26" fill="{P}" {OUT_}/>'
            f'<rect x="4" y="8" width="7" height="32" rx="3" fill="{A}" {OUT_}/><rect x="37" y="8" width="7" height="32" rx="3" fill="{A}" {OUT_}/>'
            f'<path d="M12 33 L19 22 L24 28 L28 23 L36 33 Z" fill="{JADE}" stroke="{INK0}" stroke-width="1.2" stroke-linejoin="round"/>'
            f'<circle cx="30" cy="16.5" r="2.6" fill="{R}"/>'),
    # 典籍
    'guide': (f'<path d="M24 12 C18 8 10 8 5 10 V39 C10 37 18 37 24 41 C30 37 38 37 43 39 V10 C38 8 30 8 24 12 Z" fill="{A}" {OUT_}/>'
              f'<path d="M24 12 V41" stroke="{INK0}" stroke-width="1.6"/>'
              f'<path d="M10 17 H19 M10 23 H19 M10 29 H17 M29 17 H38 M29 23 H38 M29 29 H36" stroke="{INK0}" stroke-width="1.4" stroke-linecap="round"/>'),
    'pin': (f'<circle cx="24" cy="24" r="11" {LINE}/><circle cx="24" cy="24" r="3.6" fill="{R}"/>'
            f'<path d="M24 4 V12 M24 36 V44 M4 24 H12 M36 24 H44" {LINE}/>'),
    'move': (f'<path d="M7 24 H35" {LINE}/><path d="M26 13 L38 24 L26 35" {LINE}/>'
             f'<circle cx="8" cy="24" r="3.4" fill="{A}" {OUT_}/>'),
    'attack': (f'<path d="M9 9 L37 37 M39 9 L11 37" {LINE}/>'
               f'<path d="M31 41 L41 31 M7 31 L17 41" stroke="{A}" stroke-width="3.6" stroke-linecap="round"/>'
               f'<path d="M9 9 L6 6 M39 9 L42 6" stroke="{R}" stroke-width="3" stroke-linecap="round"/>'),
    'retreat': (f'<path d="M36 36 V22 Q36 14 28 14 H11" {LINE}/><path d="M19 6 L10 14 L19 22" {LINE}/>'),
    'disband': (f'<path d="M14 44 V6" {LINE}/><path d="M14 7 H38 L32 15 L38 23 H14" fill="{A}" {OUT_}/>'
                f'<path d="M24 30 L40 44 M40 30 L24 44" stroke="{R}" stroke-width="3.4" stroke-linecap="round"/>'),
    'close': f'<path d="M12 12 L36 36 M36 12 L12 36" {LINE}/>',
    'check': f'<path d="M9 25 L19 35 L39 13" {LINE}/>',
    'report': (f'<rect x="9" y="5" width="30" height="38" rx="2" fill="{P}" {OUT_}/>'
               f'<path d="M15 33 V27 M21 33 V20 M27 33 V24 M33 33 V14" stroke="{LACQUER}" stroke-width="3.2" stroke-linecap="round"/>'
               f'<path d="M14 37 H34" stroke="{INK0}" stroke-width="1.4"/>'),
    'time': (f'<circle cx="24" cy="24" r="17" fill="{A}" {OUT_}/><circle cx="24" cy="24" r="12.5" fill="none" stroke="{INK0}" stroke-width="1.3"/>'
             f'<path d="M24 24 L24 13 M24 24 L31 28" stroke="{INK0}" stroke-width="2.6" stroke-linecap="round"/>'),
    'menu': f'<path d="M9 14 H39 M9 24 H39 M9 34 H39" {LINE}/>',
    'pause': f'<path d="M17 11 V37 M31 11 V37" {LINE}/>',
    'play': f'<path d="M15 10 L37 24 L15 38 Z" fill="{A}" {OUT_}/>',
    'save': (f'<path d="M8 6 H34 L41 13 V42 H8 Z" fill="{A}" {OUT_}/><rect x="14" y="6" width="16" height="11" fill="{INK0}"/>'
             f'<rect x="14" y="26" width="20" height="12" fill="{P}" stroke="{INK0}" stroke-width="1.2"/>'),
    'filter': f'<path d="M7 9 H41 L28 25 V39 L20 35 V25 Z" fill="{A}" {OUT_}/>',
    'search': f'<circle cx="21" cy="21" r="11" {LINE}/><path d="M29 29 L40 40" {LINE}/>',
    'prev': f'<path d="M29 10 L15 24 L29 38" {LINE}/>',
    'next': f'<path d="M19 10 L33 24 L19 38" {LINE}/>',
    'recruit': (f'<path d="M15 44 L33 6" {LINE}/><path d="M33 6 L36 15 L29 12 Z" fill="{A}" {OUT_}/>'
                f'<path d="M30 30 H44 M37 23 V37" stroke="{R}" stroke-width="3.6" stroke-linecap="round"/>'),
    'manage': (f'<rect x="8" y="6" width="32" height="36" rx="2" fill="{A}" {OUT_}/>'
               f'<path d="M19 16 H34 M19 24 H34 M19 32 H34" stroke="{INK0}" stroke-width="2.2" stroke-linecap="round"/>'
               f'<path d="M12 16 h2 M12 24 h2 M12 32 h2" stroke="{LACQUER}" stroke-width="3" stroke-linecap="round"/>'),
    'tile': (f'<path d="M24 5 L43 15 V33 L24 43 L5 33 V15 Z" fill="{JADE}" {OUT_}/>'
             f'<path d="M5 15 L24 25 L43 15 M24 25 V43" fill="none" stroke="{INK0}" stroke-width="1.6"/>'
             f'<path d="M14 12 L20 7 L24 11" fill="none" stroke="{P}" stroke-width="2"/>'),
    'power': f'<path d="M27 4 L11 27 H22 L19 44 L37 19 H26 Z" fill="{A}" {OUT_}/>',
    'warning': (f'<path d="M24 5 L44 41 H4 Z" fill="{A}" {OUT_}/><path d="M24 17 V29" stroke="{INK0}" stroke-width="3.4" stroke-linecap="round"/>'
                f'<circle cx="24" cy="35" r="2.2" fill="{INK0}"/>'),
}
for key, body in ICONS.items():
    add('icon-' + key, svg(48, 48, body))

# 标题印章（主菜单）
add('title-seal', svg(120, 120,
    f'<rect x="6" y="6" width="108" height="108" rx="6" fill="{LACQUER_LT}"/>'
    f'<rect x="14" y="14" width="92" height="92" rx="3" fill="none" stroke="{PAPER}" stroke-width="4"/>'))

# ── 主菜单山水 ───────────────────────────────────────────────────────
def ridge(points: list[tuple[int, int]], base: int, fill: str, opacity: float, gid: str) -> tuple[str, str]:
    """Smooth ink-wash ridge (Catmull-Rom → Bézier) fading into mist at its foot."""
    pts = [(-40, points[0][1])] + points + [(1640, points[-1][1])]
    d = f'M{pts[0][0]} {pts[0][1]}'
    for i in range(len(pts) - 1):
        p0, p1, p2 = pts[max(i - 1, 0)], pts[i], pts[i + 1]
        p3 = pts[min(i + 2, len(pts) - 1)]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f' C{c1[0]:.0f} {c1[1]:.0f} {c2[0]:.0f} {c2[1]:.0f} {p2[0]} {p2[1]}'
    top = min(y for _, y in points)
    grad = (f'<linearGradient id="{gid}" x1="0" y1="{top}" x2="0" y2="{base}" gradientUnits="userSpaceOnUse">'
            f'<stop offset="0" stop-color="{fill}" stop-opacity="{opacity}"/><stop offset=".55" stop-color="{fill}" stop-opacity="{opacity*.85}"/>'
            f'<stop offset="1" stop-color="#b89a74" stop-opacity="{opacity*.35}"/></linearGradient>')
    if opacity >= 1:
        return f'<path d="{d} L1640 900 L-40 900 Z" fill="{fill}"/>', ''
    return f'<path d="{d} L1640 900 L-40 900 Z" fill="url(#{gid})"/>', grad


pines = ''.join(
    f'<g transform="translate({x} {y}) scale({s})"><path d="M0 0 V-34" stroke="#0b0806" stroke-width="3"/>'
    f'<path d="M-16 -18 L0 -30 L16 -18 Z M-13 -26 L0 -38 L13 -26 Z M-19 -9 L0 -22 L19 -9 Z" fill="#0b0806"/></g>'
    for x, y, s in ((1230, 812, 1.6), (1268, 826, 1.25), (1302, 818, 1.4), (140, 842, 1.5), (176, 852, 1.1)))
birds = ''.join(f'<path d="M{x} {y} q7 -6 14 0 q7 -6 14 0" fill="none" stroke="#2a1c12" stroke-width="2.4" stroke-linecap="round"/>'
                for x, y in ((1040, 230), (1080, 210), (1110, 246), (1150, 222)))
clouds = ''.join(
    f'<path d="M{x} {y} q20 -22 46 -6 q16 -20 40 -4 q26 -8 30 14 z" fill="#f3e3c2" fill-opacity=".10"/>' for x, y in ((240, 300), (420, 250), (1270, 330), (980, 380)))
RIDGES = [
    ([(60, 430), (170, 330), (250, 400), (330, 360), (420, 300), (520, 410), (640, 380), (760, 420), (880, 350), (990, 300),
      (1090, 390), (1220, 360), (1330, 410), (1450, 330), (1560, 400)], 560, '#6a5444', .55),
    ([(0, 560), (110, 470), (200, 520), (300, 430), (380, 500), (520, 560), (640, 480), (720, 420), (820, 520), (960, 560),
      (1080, 470), (1180, 520), (1300, 450), (1420, 540), (1600, 500)], 700, '#3f3026', .85),
    ([(0, 700), (140, 640), (280, 690), (400, 600), (520, 680), (700, 720), (860, 640), (980, 600), (1100, 690), (1260, 700),
      (1400, 630), (1600, 690)], 820, '#251a12', .95),
    ([(0, 830), (220, 790), (440, 830), (640, 805), (860, 850), (1060, 800), (1280, 835), (1480, 800), (1600, 820)], 900, '#0e0a07', 1),
]
_ridges = [ridge(points, base, color, alpha, f'r{i}') for i, (points, base, color, alpha) in enumerate(RIDGES)]
add('menu-landscape', svg(1600, 900,
    '<rect width="1600" height="900" fill="url(#sky)"/>'
    '<circle cx="1120" cy="300" r="150" fill="url(#glow)"/><circle cx="1120" cy="300" r="92" fill="url(#sun)"/>'
    + clouds + birds + ''.join(path for path, _ in _ridges) + pines,
    '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#110c09"/><stop offset=".42" stop-color="#342216"/>'
    '<stop offset=".66" stop-color="#6e4428"/><stop offset="1" stop-color="#2a1a10"/></linearGradient>'
    '<radialGradient id="sun"><stop offset="0" stop-color="#ef7a45"/><stop offset=".85" stop-color="#c2442b"/>'
    '<stop offset="1" stop-color="#b8402a" stop-opacity=".9"/></radialGradient>'
    '<radialGradient id="glow"><stop offset=".55" stop-color="#c2442b" stop-opacity=".35"/><stop offset="1" stop-color="#c2442b" stop-opacity="0"/></radialGradient>'
    + ''.join(g for _, g in _ridges)))


# ── 输出 ─────────────────────────────────────────────────────────────
IMPORT = '''[remap]

importer="texture"
type="CompressedTexture2D"
uid="uid://{uid}"
path="res://.godot/imported/{name}.svg-{h}.ctex"
metadata={{
"vram_texture": false
}}

[deps]

source_file="res://assets/ui-lacquer/{name}.svg"
dest_files=["res://.godot/imported/{name}.svg-{h}.ctex"]

[params]

compress/mode=0
compress/high_quality=false
compress/lossy_quality=0.7
compress/uastc_level=0
compress/rdo_quality_loss=0.0
compress/hdr_compression=1
compress/normal_map=0
compress/channel_pack=0
mipmaps/generate={mip}
mipmaps/limit=-1
roughness/mode=0
roughness/src_normal=""
process/channel_remap/red=0
process/channel_remap/green=1
process/channel_remap/blue=2
process/channel_remap/alpha=3
process/fix_alpha_border=true
process/premult_alpha=false
process/normal_map_invert_y=false
process/hdr_as_srgb=false
process/hdr_clamp_exposure=false
process/size_limit=0
detect_3d/compress_to=1
svg/scale={scale}
editor/scale_with_editor_scale=false
editor/convert_colors_with_editor_theme=false
'''


def uid(name: str) -> str:
    n = int.from_bytes(hashlib.sha256(('ui-lacquer/' + name).encode()).digest()[:8], 'big') & ((1 << 62) - 1)
    chars = 'abcdefghijklmnopqrstuvwxy0123456789'
    out = ''
    while n:
        n, r = divmod(n, len(chars))
        out = chars[r] + out
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    DOC.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (text, meta) in ASSETS.items():
        path = OUT / f'{name}.svg'
        path.write_text(text, encoding='utf-8')
        h = hashlib.md5(f'res://assets/ui-lacquer/{name}.svg'.encode()).hexdigest()
        icon = name.startswith('icon-')
        (OUT / f'{name}.svg.import').write_text(
            IMPORT.format(uid=uid(name), name=name, h=h, mip='true' if icon else 'false', scale='1.0'), encoding='utf-8')
        manifest[name] = {'sha256': hashlib.sha256(text.encode()).hexdigest(), **meta}
    (OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    # 图录：所有素材平铺，便于审阅
    cells, x, y, row = [], 16, 16, 0
    for name, (text, _) in ASSETS.items():
        if name == 'menu-landscape':
            continue
        w = int(text.split('width="')[1].split('"')[0]); hgt = int(text.split('height="')[1].split('"')[0])
        scale = 1 if w > 64 else 1.5
        cw, ch = max(w * scale, 96), hgt * scale + 22
        if x + cw > 1200:
            x, y = 16, y + row + 12
            row = 0
        inner = text.split('>', 1)[1].rsplit('</svg>', 1)[0]
        inner = inner.replace('id="', f'id="{name}-').replace('url(#', f'url(#{name}-')
        cells.append(f'<g transform="translate({x} {y})"><svg width="{w*scale}" height="{hgt*scale}" viewBox="0 0 {w} {hgt}">{inner}</svg>'
                     f'<text x="0" y="{hgt*scale+15}" font-size="11" fill="{PAPER_MUTED}" font-family="sans-serif">{name}</text></g>')
        x += cw + 14
        row = max(row, ch)
    total = y + row + 16
    (DOC / 'catalog.svg').write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1216" height="{total}" viewBox="0 0 1216 {total}">'
        f'<rect width="1216" height="{total}" fill="#2b2620"/>' + ''.join(cells) + '</svg>\n', encoding='utf-8')
    print(f'{len(ASSETS)} 个素材 → {OUT.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
