"""Redraw the paper's three figures from the two public saves (Pillow).

    python make_figures.py

Writes ../figures/{equity-curves,attribution,baseline}.png
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import extract as E

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / 'figures'
OUT.mkdir(parents=True, exist_ok=True)

FONT = r'C:\Windows\Fonts\msyh.ttc'
FONTB = r'C:\Windows\Fonts\msyhbd.ttc'
COLORS = [(31, 119, 180), (214, 39, 40), (44, 160, 44), (255, 127, 14)]


def font(size, bold=False):
    try:
        return ImageFont.truetype(FONTB if bold else FONT, size)
    except OSError:
        return ImageFont.load_default()


def load_games():
    return {'H': E.load(E.DEFAULTS['hydrogen']), 'F': E.load(E.DEFAULTS['flash'])}


def equity(games):
    out = {}
    for tag, game in games.items():
        accounts = game['data']['modules']['stock:portfolio']['accounts']
        out[tag] = {name: [(e['round'], e['wealth']) for e in accounts[actor]['equity']]
                    + [(60, accounts[actor]['final_cash'])] for actor, name in E.SEATS}
    return out


def figure_equity(games):
    eq = equity(games)
    W, H, L, R, T, B = 1180, 560, 90, 210, 70, 70
    lo, hi = 60000, 135000
    img = Image.new('RGB', (W, H), 'white')
    dr = ImageDraw.Draw(img)
    px = lambda r: L + (r - 1) / 59 * (W - L - R)
    py = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    dr.text((W / 2, 26), '两局四席逐回合净值：Hydrogen（实线）与 3.8 Flash（虚线）',
            font=font(24, True), fill='black', anchor='mm')
    for v in range(60000, 140000, 10000):
        y = py(v)
        dr.line([(L, y), (W - R, y)], fill=(230, 230, 230))
        dr.text((L - 10, y), f'{v // 1000}k', font=font(15), fill=(90, 90, 90), anchor='rm')
    for r in (1, 10, 20, 30, 40, 50, 60):
        x = px(r)
        dr.line([(x, T), (x, H - B)], fill=(242, 242, 242))
        dr.text((x, H - B + 18), str(r), font=font(15), fill=(90, 90, 90), anchor='mm')
    dr.line([(L, py(100000)), (W - R, py(100000))], fill=(150, 150, 150), width=2)
    dr.text((L + 8, py(100000) - 10), '初始 10 万', font=font(15), fill=(120, 120, 120), anchor='lb')
    legend_y = T + 30
    for tag, dashed in (('H', False), ('F', True)):
        for index, (_, name) in enumerate(E.SEATS):
            pts = [(px(r), py(v)) for r, v in eq[tag][name]]
            if dashed:
                for a, b in zip(pts, pts[1:]):
                    for k in range(0, 8, 2):
                        dr.line([(a[0] + (b[0] - a[0]) * k / 8, a[1] + (b[1] - a[1]) * k / 8),
                                 (a[0] + (b[0] - a[0]) * (k + 1) / 8, a[1] + (b[1] - a[1]) * (k + 1) / 8)],
                                fill=COLORS[index], width=3)
            else:
                dr.line(pts, fill=COLORS[index], width=3, joint='curve')
            dr.line([(W - R + 16, legend_y), (W - R + 52, legend_y)], fill=COLORS[index], width=4)
            dr.text((W - R + 58, legend_y), f'{name} {tag}', font=font(16), fill=(40, 40, 40), anchor='lm')
            legend_y += 26
    dr.text((W - R + 10, legend_y + 8), '横轴：回合', font=font(14), fill=(130, 130, 130), anchor='lt')
    dr.text((W - R + 10, legend_y + 28), '纵轴：收盘净值（元）', font=font(14), fill=(130, 130, 130), anchor='lt')
    img.save(OUT / 'equity-curves.png')


def figure_attribution(games):
    accounts = {tag: game['data']['modules']['stock:portfolio']['accounts'] for tag, game in games.items()}
    avg = lambda tag, key: sum(accounts[tag][actor][key] for actor, _ in E.SEATS) / 4
    rows = [('交易净盈亏', avg('H', 'realized_pnl') - avg('F', 'realized_pnl'), (44, 160, 44)),
            ('算力支出', -(avg('H', 'compute_expenses') - avg('F', 'compute_expenses')), (214, 39, 40)),
            ('情报支出', -(avg('H', 'intelligence_expenses') - avg('F', 'intelligence_expenses')), (255, 127, 14)),
            ('借券费', -(avg('H', 'borrow_fees') - avg('F', 'borrow_fees')), (148, 103, 189))]
    total = sum(v for _, v, _ in rows)
    W, H = 1100, 470
    img = Image.new('RGB', (W, H), 'white')
    dr = ImageDraw.Draw(img)
    dr.text((W / 2, 30), '每席平均最终现金差：Hydrogen − Flash（分项拆解）',
            font=font(24, True), fill='black', anchor='mm')
    L, T, mid, scale = 250, 80, 560, 300 / 32000
    y = T
    for name, value, color in rows:
        width = abs(value) * scale
        x = mid if value >= 0 else mid - width
        dr.rectangle([x, y, x + width, y + 44], fill=color)
        tx = mid + width + 12 if value >= 0 else mid - width - 12
        dr.text((tx, y + 22), f'{value:+,.0f}', font=font(19), fill=(20, 20, 20),
                anchor='lm' if value >= 0 else 'rm')
        dr.text((L, y + 22), name, font=font(19), fill=(30, 30, 30), anchor='rm')
        y += 62
    dr.line([(mid, T - 12), (mid, y - 10)], fill=(60, 60, 60), width=2)
    dr.text((mid, y + 6), '0', font=font(14), fill=(120, 120, 120), anchor='ma')
    dr.text((L, y + 26), '合计', font=font(20, True), fill=(30, 30, 30), anchor='rm')
    dr.text((mid + 14, y + 26), f'+{total:,.0f} 元 / 席  = +31.25 个百分点',
            font=font(20, True), fill=(20, 20, 20), anchor='lm')
    dr.text((40, H - 24), '注：右侧为正，表示 Hydrogen 该项比 Flash 多出的金额；左侧为负，表示 Hydrogen 多花（少得）的部分。',
            font=font(14), fill=(120, 120, 120), anchor='lm')
    img.save(OUT / 'attribution.png')


def figure_baseline(games):
    baseline = {}
    for tag, game in games.items():
        history = game['data']['modules']['stock:market']['history']
        closes = {s: history[s][20:] for s in E.INITIAL_STOCKS}  # 20 anchor warm-up values
        baseline[tag] = sum(c[-1] / c[0] - 1 for c in closes.values()) / len(closes) * 100
    W, H, L, R, T, B = 1100, 480, 110, 60, 80, 100
    lo, hi = -40, 45
    img = Image.new('RGB', (W, H), 'white')
    dr = ImageDraw.Draw(img)
    py = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    dr.text((W / 2, 30), '收益对照：模型策略 / 被动持有 / 空仓（种子 2026111303）',
            font=font(24, True), fill='black', anchor='mm')
    for v in (-40, -30, -20, -10, 0, 10, 20, 30, 40):
        y = py(v)
        dr.line([(L, y), (W - R, y)], fill=(235, 235, 235))
        dr.text((L - 12, y), f'{v:+d}%', font=font(15), fill=(90, 90, 90), anchor='rm')
    bars = [('Hydrogen\n四席均值', 7.71, (31, 119, 180)), ('3.8 Flash\n四席均值', -23.54, (214, 39, 40)),
            ('被动等权\n持有 H', baseline['H'], (127, 127, 127)),
            ('被动等权\n持有 F', baseline['F'], (127, 127, 127)), ('空仓\n不动', 0.0, (170, 170, 170))]
    width = 130
    gap = (W - L - R - len(bars) * width) / (len(bars) - 1)
    for index, (name, value, color) in enumerate(bars):
        x = L + index * (width + gap)
        y0, y1 = py(0), py(value)
        dr.rectangle([x, min(y0, y1), x + width, max(y0, y1)], fill=color)
        dr.text((x + width / 2, y1 - 10 if value >= 0 else y1 + 10), f'{value:+.2f}%',
                font=font(18, True), fill=(20, 20, 20), anchor='mb' if value >= 0 else 'mt')
        for row, part in enumerate(name.split('\n')):
            dr.text((x + width / 2, H - B + 22 + row * 22), part, font=font(15), fill=(50, 50, 50), anchor='ma')
    dr.text((40, H - 24), '注：被动等权持有＝第 1 回合收盘等权买入 10 只初始股票并持有到第 59 回合收盘；空仓不动＝全程不交易、不买算力，终局 100,000 元。',
            font=font(13), fill=(120, 120, 120), anchor='lm')
    img.save(OUT / 'baseline.png')


if __name__ == '__main__':
    games = load_games()
    figure_equity(games)
    figure_attribution(games)
    figure_baseline(games)
    print('figures written to', OUT)
