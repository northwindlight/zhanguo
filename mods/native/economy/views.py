from __future__ import annotations
from ..core import *
from ..rules import *
from ..ui_constants import *
from .. import presentation as P
import copy,json,re,threading
from pathlib import Path
from ctx import est_tokens
from console import dw as _dw, pad as _pad


def _cost_text(cost: dict[str, int]) -> str:
    """把一份料单写成"10粮+5装"这样的短说法（面板/工具描述共用，不写死数字）。"""
    short = {'粮食': '粮', '装备': '装', '黄金': '金', '木头': '木', '矿石': '矿', '石油': '油', '补给': '补给'}
    return '+'.join((f'{amt}{short.get(k, k)}' for k, amt in cost.items()))

def _res_line(world, name) -> str:
    r = world.nations[name].res
    et, mt, short = world.energy_report.get(name, (0, 0, False))
    parts = []
    for k in RES_KEYS:
        parts.append(f'{RES_LABEL.get(k, k)}{r.get(k, 0)}')
    grid = '正常' if not short else '⚠停摆'
    if short:
        grid_tail = '（★已停摆：补给厂/装备厂/兵营/市政厅**此刻全部没在转**——立刻补电或停耗电件）'
    else:
        reserve = (et - mt) / mt if mt else None
        if reserve is not None and reserve < GRID_RESERVE_WARN:
            grid = f'正常·⚠余量仅 {reserve:.0%}'
            grid_tail = f'（**低于 {GRID_RESERVE_WARN:.0%} 的安全余量**：敌人一支孤军占掉一格电厂、或燃料一断，余量立刻变负 ⇒ **全国耗电建筑一起停摆**。趁现在补电厂，或在电厂那一格留兵）'
        else:
            grid_tail = '（不足则补给厂/装备厂/兵营/市政厅全部停摆）'
    summ = world.econ_summary.get(name, '')
    upkeep = {k: v for k, v in world.industry_upkeep(name).items() if v}
    kep = ''
    if upkeep:
        short_k = [k for k, v in upkeep.items() if r.get(k, 0) < v]
        kep = '\n工业每回合耗料: ' + ' '.join((f'{RES_LABEL.get(k, k)}{v}' + ('⚠' if k in short_k else '') for k, v in upkeep.items()))
        if short_k:
            kep += '\u3000⚠ **库存已不足一回合**：' + '、'.join((f'{RES_LABEL.get(k, k)}(存 {r.get(k, 0)} < 耗 {upkeep[k]})' for k in short_k)) + '——下回合就要断料：**电厂断料＝全国停电**（耗电建筑全停），工厂断料＝那批工厂停产（**先 `buy` 补料**；补不上就别再加耗这套料的建筑，断了料的厂就是白盖的）'
        if short:
            kep += '\u3000（电网停摆中：工厂此刻根本不投料，只有电厂还在烧燃料）'
    halls = world.nation_building_count(name, '市政厅')
    return f"{'  '.join(parts)}\n电网: 产{et}/需{mt} {grid}{grid_tail}{kep}\n国祚: 市政厅 {halls} 座（★失去全部即亡国，余土沦为无主之地）" + (f'\n上一回合结算: {summ}' if summ else '')

def _fmt_market(world, name) -> str:
    r = world.nations[name].res
    lines = [f"世界市场（黄金是货币；可交易：{' '.join(GOODS_DISPLAY)}）", f'  成交=沿曲线均价结算，含 {MARKET_SPREAD:.0%} 买卖价差；每回合向供需均衡价回归。']
    for g in GOODS_DISPLAY:
        p = world.prices[g]
        base = MARKET[g]
        eq = world.equilibrium.get(g, base)
        tag = '≈基准' if abs(p - base) <= 0.02 * base else '贵' if p > base else '贱'
        bp, _ = world.market_quote(g, 1, 'buy')
        sp, _ = world.market_quote(g, 1, 'sell')
        _, s50 = world.market_quote(g, 50, 'sell', name)
        _, b50 = world.market_quote(g, 50, 'buy', name)
        lines.append(f'  {g} 现价{p:.2f}(基准{base} 均衡{eq:.2f} {tag}) 买{bp:.2f}/卖{sp:.2f} 持有{r.get(g, 0)} ｜ 卖50≈{s50}金 买50≈{b50}金')
    warn = world.churn_brief(name)
    if warn:
        lines.append(f'  {warn} —— 同一批货倒手＝白付两趟买卖价差，别倒回来（详见 rules(经济手册) 第四节）')
    return '\n'.join(lines)

def _econ_manual() -> str:
    """【经济学手册】：规则段讲"**能做**什么"，这一节讲"**为什么**该这么做"。

    用户 2026-09-22 口述整本、要求「**可查询**」——所以它走 `rules` 的章节表：
    `rules(经济手册)` 单独取，`rules` 全文里也有一节。**只做润色与分节，不改主张**；
    里面的数字一律现读 `balance`（价差、地板价、贷款倍数），不手抄第二份。
    六节依次是：①扩张＝买建设的期权 ②兵与钱互为前提 ③军费不能榨干现金流
    ④别做"先卖后买"的转手 ⑤市场是个"没人消费就崩"的循环 ⑥战争是最快的获取资源方式。
    """
    half = MARKET_SPREAD / 2
    return f'规则段讲的是「**能做什么**」，这一节讲「**为什么该这么做**」。\n一、扩张是**买建设的期权**，不是买资源。\n  占下来的地本身**不产一粒粮、不出一块矿**——它是**建筑位**，是一张「以后在这儿盖什么」的期权，兑现还得再掏一次建造钱。所以：**光有兵，只拿得下战略纵深**（防线、缓冲、通路、逼近敌人的位置），**换不来经济增长**；地占着不建设，就是一笔压死在手里的钱。\n二、兵与钱互为前提。\n  **光有资本没有兵** ⇒ 没有投资位置（好地都在别人手里，除非你去打）；**光有兵没有资本** ⇒ 打下来也建设不起，等于拿血换了一堆空地。**资本创造复利，军队创造新的资本投资地块、同时保卫资本。** 两条腿缺一条，都走不远。\n三、军队千万不能榨干现金流。\n  军费是**每回合**持续的补给支出，吃掉的正是你本该拿去建造、再投资的那笔现金。如果你已经被榨干（国库见底、产出全被军费吃掉）：**遣散（disband）不打算再用的军队**——免费、当回合就止损，把每回合的补给负担甩掉，**换回经济增长**。**否则你一定会被滚雪球滚死**：别人在复利，你在给军队发口粮。\n四、同一批货，别先卖后买。\n  市场有 {MARKET_SPREAD:.0%} 买卖价差（买 +{half:.0%} / 卖 −{half:.0%}），同一批货卖了再买回来**必然亏本**——那是纯替市场转手、白送手续费。要买就直接买、要卖就直接卖，别拿市场当仓库周转。\n  ★ 引擎会盯着这条：**同一回合**在同一个商品上又买又卖，成交回执里**当场**报出「空转量与净亏」（那一刻起每笔都报一次，面板与回合摘要也会带上）——倒手量按两边重叠的部分算，多出来的那截是真实仓位、不算空转。**跨回合分批不算**：那是省钱的正确做法。\n五、市场是个循环：没人消费，价格就崩。\n  市价由全世界的产/耗决定（每回合向供需均衡价回归）：\n  · **没有军队、也没有新地块可建** ⇒ 没人消费资源 ⇒ 供大于求 ⇒ 市价一路跌到地板（{PRICE_MIN_RATIO:g}× 基准价，最低 {PRICE_MIN_ABS:g} 金/单位）。**所有资源同时变便宜 = 大家同时变穷**：同样的存货换不回同样的金，更养不起消费，于是更没人消费——这就是**经济危机**：市场吞不下过量的资源，价格触底，谁也卖不出钱。\n  · 反过来：**大家一起大力消费**（建造、征兵、打仗的军需）⇒ 资源变贵 ⇒ 军队也养得起 ⇒ 把赚到的再投出去赚新利润 ⇒ **同时也托住了自己手里资源的价格**。也就是说：**你花钱本身就在替自己维持物价**——都捂着不花，先崩的是你自己的资产。\n六、战争是最快的获取资源方式。\n  吞并一个国家：它的地、地上一砖一瓦、它的产出，一起归你——**你的 GDP 与资产可能瞬间翻倍**，如同**秦王扫六合**，滚起来就不可阻挡。所以能打的仗要敢打；但**开打前先回看第三节**：养不起的军队，先垮的是自己。'

def _bank_rules() -> str:
    """【世界央行】规则段（**只有开行时才返回**——关着就整节不存在，不占 token）。"""
    return f'国库现金**默认就是储蓄**（不用存）：每回合按储蓄利率结息——正=入账，负=扣钱（**扣到 0 为止**：储蓄永远扣不成负的）。\n可向央行借款（`loan`）：**额度与期限都不能自选**，只有一种贷款——**额度 = 你当时的 GDP × {BANK_LOAN_GDP_MULT}**、**期限固定 {BANK_LOAN_TURNS} 回合**；**还清前不能再借**（一国同时只有一笔）。贷款利率 = 储蓄利率 + {BANK_SPREAD:.0%}（**可为负**：那时欠款每回合缩水）。\n每回合自动累计应还额（面板与近讯都会报）；**到期一次性强制扣款**——这一笔允许把国库**扣成负的**（欠债不还，国库先扣穿）。所以借款要拿去用在**回本快于到期日**的地方，否则到期那一下就白干。（期限只有 {BANK_LOAN_TURNS} 回合就是这个道理：拖到 10 回合，利息翻一倍多，大半个期限都在替央行打工。）\n借钱**算外交动作**，按外交费计（成功才扣；有外交中心照样减半）。\n央行还**卖别国已公布的经济报表**：{BUY_REPORT_COST} 金一份（`buy_report`，目标用 to=，可选 turn= 指定期；当场到手，进 query panel=spy）。报表是每国每 {REPORT_EVERY} 回合自动结一期、**生成时全世界都收到过一行公告**的公开账，央行卖的只是**明细全文**——要偷**当下**的国库/储备/每块地建设，仍得用 spy。\n利率由**世界央行**设定，变更时**全世界播报**：上调＝抑制通货膨胀、下调＝减少紧缩。利率为负时囤现金每回合缩水——那是央行在逼你把钱花出去或投出去。'

def _fmt_bank(world, name) -> str:
    """【央行】面板（开行才进常驻状态）：利率 + 你的借款状况 + 你的授信额度。"""
    r, lr = (world.bank_rate(), world.bank_loan_rate())
    ln = world.bank['loans'].get(name)
    gdp, credit = (world.nation_gdp(name), world.bank_credit(name))
    if ln:
        st = f"你欠央行 {ln['due']} 金（本金 {ln['principal']}、利率 {ln['rate']:+.1%}、还剩 {ln['turns_left']} 回合到期）——到期**强制扣款**，还清前不能再借"
    elif credit > 0:
        st = f'你的授信 {credit} 金（= 当前 GDP {gdp:.1f} × {BANK_LOAN_GDP_MULT}）：loan 一次借满、期限 {BANK_LOAN_TURNS} 回合（**都不能自选**）；目前无欠款'
    else:
        st = f'你还没有授信：额度 = 当前 GDP × {BANK_LOAN_GDP_MULT}，而你现在 GDP 为 0——GDP 是**上一回合的产出**（市价），先去建设/扩张，产出过东西才有授信'
    if r < 0:
        tip = '⚠ 储蓄利率为负：囤现金每回合都在缩水——央行在逼你把钱花出去或投出去。'
    elif r > 0:
        tip = '储蓄利率为正：现金每回合自动生息（别把它当成免费的——市场那本账另算）。'
    else:
        tip = '储蓄利率为 0：现金不生息也不被扣。'
    return f'储蓄利率 {r:+.1%}（国库现金默认就是储蓄，每回合结息）｜贷款利率 {lr:+.1%}（= 储蓄 + {BANK_SPREAD:.0%}）\n  {st}\n  {tip}\n  央行也卖别国已公布的经济报表：{BUY_REPORT_COST} 金一份（buy_report to=目标国）；要偷当下的底细仍得 spy。'

def _pct(v: float | None, sign: bool=True) -> str:
    """增长率/占比显示：None（无上期）→「—」。"""
    if v is None:
        return '—'
    return f'{v * 100:+.1f}%' if sign else f'{v * 100:.1f}%'

def _fmt_report_one(rep: dict) -> str:
    """单期经济报表。口径：GDP 与军费都是**结报那一回合**的实际值（run-rate，不平均、
    不攒期累计）；投资/资产/贸易仍是整期值。覆盖回合数只在表头交代时间跨度。"""
    days = rep.get('span', REPORT_EVERY)
    start = rep.get('period_start', rep['period_end'] - days + 1)
    span = f"第 {start}–{rep['period_end']} 回合" + ('' if days == REPORT_EVERY else f'（{days} 回合）')
    gdp, mil = (rep['gdp'], rep['military'])
    fiscal = gdp - mil
    L = [f"【经济报表 · 报表回合 {rep['report_turn']} · 覆盖{span}】"]
    L.append(f"  GDP（本回合，市价）      {gdp:>8.1f} 金   {_pct(rep['gdp_growth'])}")
    L.append(f'  财政收入（GDP−军费）     {fiscal:>8.1f} 金' + ('   ⚠ 本回合军费已超过 GDP，靠卖库存/吃老本维持' if fiscal < 0 else ''))
    L.append(f"  军费（本回合补给消耗）   {mil:>8.1f} 金   占 GDP {_pct(rep['military_ratio'], sign=False)}")
    L.append(f"  国家总资产               {rep['assets']:>8.0f} 金   {_pct(rep['assets_growth'])}   （含夺地所得）")
    L.append(f"  本期投资                 {rep['invest']:>8.0f} 金   {_pct(rep['invest_growth'])}")
    L.append(f"  外贸 / 内循环            外贸 {_pct(rep['trade_ratio'], sign=False)} · 内循环 {_pct(1 - rep['trade_ratio'], sign=False)}   （外贸=买卖总额/(自产+进口)）")
    L.append(f"  （本回合军队吃掉补给 {rep.get('supply_eaten_turn', 0)} 单位、折 {mil:.1f} 金；整期累计吃掉 {rep['supply_eaten']} 单位；市场买入 {rep['import_gold']:.0f} 金、卖出 {rep['export_gold']:.0f} 金）")
    L.append('')
    L.append(MIL_WARNING)
    return '\n'.join(L)

def _fmt_report_trend(world, name) -> str:
    """跨期趋势表：一行一期，便于比较不同时期。"""
    reps = world.econ_reports.get(name, [])
    header = ['期', '报表回合', 'GDP/回合', 'GDP增长', '军费/回合', '军费占GDP', '投资(期)', '投资增长', '总资产', '资产增长', '外贸占比']
    rows = [header]
    for i, r in enumerate(reps, 1):
        rows.append([str(i), str(r['report_turn']), f"{r['gdp']:.1f}", _pct(r['gdp_growth']), f"{r['military']:.1f}", _pct(r['military_ratio'], sign=False), f"{r['invest']:.0f}", _pct(r['invest_growth']), f"{r['assets']:.0f}", _pct(r['assets_growth']), _pct(r['trade_ratio'], sign=False)])
    widths = [max((_dw(row[c]) for row in rows)) for c in range(len(header))]
    lines = [f"【经济报表 · 跨期趋势】共 {len(reps)} 期（报表回合 {'、'.join((str(r['report_turn']) for r in reps))}）"]
    lines += ['  ' + '  '.join((_pad(row[c], widths[c]) for c in range(len(header)))) for row in rows]
    lines += ['', MIL_WARNING]
    return '\n'.join(lines)

def _fmt_report(world, name, turn: int | None=None, all_: bool=False) -> str:
    """本国经济报表（只读）：不传参数=最新一期；all_=跨期趋势表；turn=指定报表回合。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return f'（尚无经济报表：第 {REPORT_EVERY + 1} 回合起每 {REPORT_EVERY} 回合自动出一期——第 {REPORT_EVERY + 1}/{2 * REPORT_EVERY + 1}/{3 * REPORT_EVERY + 1}… 回合开局可查。这是自动生成的，不能手动运行。）'
    if all_:
        return _fmt_report_trend(world, name)
    if turn is None:
        return _fmt_report_one(reps[-1])
    for r in reps:
        if r['report_turn'] == turn:
            return _fmt_report_one(r)
    return f'没有第 {turn} 回合的报表。已出：' + '、'.join((f"第{r['report_turn']}回合" for r in reps)) + f'（每 {REPORT_EVERY} 回合自动出一期，不能手动运行；趋势看 report all=true）'

def _report_of(world, to: str, turn: int | None=None):
    """取**任意国家**某期经济报表的快照：返回 dict，取不到则返回一句错误文案（str）。

    给 `buy_report`（向央行买别国报表）用——与本国 `report` 工具读的是同一份快照，
    所以买家看到的明细与卖主自己看到的一模一样。不传 turn = 最新一期。
    """
    if to not in world.nations:
        return f"没有这个国家：{to or '（空）'}（现存：" + '、'.join(world.nations) + '）'
    reps = world.econ_reports.get(to) or []
    if not reps:
        return f'{to} 还没有任何经济报表：每 {REPORT_EVERY} 回合自动结一期，第 {REPORT_EVERY + 1} 回合起才有（现第 {world.turn} 回合）'
    if turn is None:
        return reps[-1]
    for r in reps:
        if r['report_turn'] == turn:
            return r
    return f'{to} 没有第 {turn} 回合的报表。它已出的期：' + '、'.join((f"第{r['report_turn']}回合" for r in reps))

def _fmt_report_panel(world, name) -> str:
    """状态面板里的经济报表：**只在出表那一回合（第 11/21/31…）显示全文**，
    其余回合只给一行摘要——省上下文，需要时用 report 取全文/历史/趋势。"""
    reps = world.econ_reports.get(name, [])
    if not reps:
        return f'尚无（第 {REPORT_EVERY + 1} 回合起每 {REPORT_EVERY} 回合自动出一期，出表那回合自动进本面板，不能手动运行）'
    r = reps[-1]
    if world.turn == r['report_turn']:
        text = _fmt_report_one(r)
        if len(reps) > 1:
            text += f'\n（共 {len(reps)} 期；历史期 report turn=N，跨期趋势 report all=true）'
        return text
    return f"最新第 {r['report_turn']} 回合（GDP {r['gdp']:.1f}/回合（{_pct(r['gdp_growth'])}）、军费占 GDP {_pct(r['military_ratio'], sign=False)}、总资产 {r['assets']:.0f}）——全文 report、跨期趋势 report all=true"

def _econ_building(world, building: str) -> str:
    """单个建筑的经济核算文案。**数字全部来自 mp.build_econ**——与规则 AI v9 同一份
    计算，LLM 看到的回本和 bot 排序用的回本不会有第二套口径。
    （文案与旧版逐字一致；仅加工厂「回本」从"毛利不含电"改为按含电的 per_turn 计，
      与 build_econ/`payback` 对齐——旧版这里本来自相矛盾：毛利行标了电耗、回本行不含。）"""
    info = BUILDINGS[building]
    e = build_econ(world, building)
    cost, capex, k = (e['cost'], e['capex'], info['kind'])
    if k == 'castle':
        return f"{building}: L1造价 {cost}金+{info['wood']}木(折{capex:.0f}金) · 每级+{building_effect('城堡', 'defense_per_level')}%防御，不产金"
    if k == 'militia_camp':
        return f"{building}: 造价折{capex:.0f}金 · **不产粮**（纯民兵编制，无产出）；可征民兵（{_cost_text(UNIT_TYPES['民']['recruit'])}/支，每座{info['effects'].get('militia_per_turn', 1)}支/回合、可驻{info['effects'].get('militia_cap', 1)}支，全国民兵总数≤军屯数×{info['effects'].get('militia_cap', 1)}），驻本格不耗补给（每座覆盖这么多支）（需本地{info['cap_resource']}≥1、每地块限{info.get('limit', 1)}座）"
    if k in ('extract', 'gold'):
        net = e['detail']['net']
        if k == 'gold':
            tag = '固定+金'
        else:
            tag = '外销(卖价)'
        pb = f"{e['payback']:.0f}回合" if e['payback'] else '—'
        return f'{building}: 造价折{capex:.0f}金 · 每回合产出{tag}≈{net:.0f}金 · 回本≈{pb}'
    if k == 'energy':
        fuel = e['detail']['fuel']
        return f"{building}: 造价折{capex:.0f}金 · 每回合烧燃料现值≈{fuel:.0f}金 → 产{info['energy_out']}电（电不交易，供高级建筑维持）"
    if k == 'factory':
        d = e['detail']
        inv, out_self, out_sell, ec = (d['inputs_value'], d['outputs_buy'], d['outputs_sell'], d['energy_cost'])
        net = d['net']
        pb = f"{e['payback']:.0f}回合" if e['payback'] else '—'
        return f"{building}: 造价折{capex:.0f}金 · 每回合投{inv:.0f}金(买价)料→产{out_self:.0f}金(买价=自用替代；纯外销只值{out_sell:.0f}金)（毛利{net:+.0f}金；另耗{info.get('energy', 0)}电≈{ec:.0f}金） · 回本≈{pb}"
    if k == 'barracks':
        return f"{building}: 造价折{capex:.0f}金 · 不自动产金，每兵营每回合可征1军（步{_cost_text(UNIT_TYPES['步']['recruit'])} / 骑{_cost_text(UNIT_TYPES['骑']['recruit'])}，耗兵料另计）"
    if k == 'townhall':
        return f"{building}: 造价折{capex:.0f}金 · 每回合 = {building_effect('市政厅', 'gold_base')}金基础 + 该地块每座建筑×{building_effect('市政厅', 'gold_per_slot')}金（不含自身；{MAX_SLOTS}格满城≈{building_effect('市政厅', 'gold_base') + MAX_SLOTS * building_effect('市政厅', 'gold_per_slot')}金/回合） · 需本地已用位≥{info['min_slots']}、每地块限{info.get('limit', 1)}座、耗{info.get('energy', 0)}电"
    if k == 'tower':
        return f"{building}: 造价折{capex:.0f}金 · 不产金：事件视野 +{building_effect('瞭望塔', 'vision_radius')} 圆（情报投入）"
    if k == 'diplomat':
        return f"{building}: 造价折{capex:.0f}金 · 不产金：外交费每座再减半（{'→'.join(P._diplo_chain())}）；写信起步价吃减免、超字费不吃；自建全国限{info['limit_nation']}座，第2座只能抢"
    if k == 'academy':
        return f"{building}: 造价折{capex:.0f}金 · 不产金：本地块一切建造金价 -{building_effect('工程院', 'build_discount')}%（需本地已用位≥{info['min_slots']}，后续建筑越贵回得越多）"
    return f'{building}: 无核算'

def _fmt_econ(world, name: str | None=None) -> str:
    """当前市价经济表：各建筑造价(折金)/毛利/回本，供建设决策。
    采集/金矿按「卖价」折算产出（外销口径）；加工厂按「买价」折算（自用替代口径）。"""
    L = ['【经济核算 · 当前市价】单位建筑投入产出（木头按现价折金入造价，市政厅=5金基础+本地建筑×1金/回合）：']
    L.append('现价: ' + '  '.join((f'{g}={world.prices.get(g):.1f}' for g in GOODS_DISPLAY)) + f'（买卖另有 {MARKET_SPREAD:.0%} 价差）')
    for b in BUILDINGS:
        L.append('  ' + _econ_building(world, b))
    if name:
        ps = world.nation_armies(name)
        need = sum((unit_supply(a) for a in ps))
        if need:
            buy_p = world.market_quote('补给', 1, 'buy')[0]
            L.append(f'  【你的口径】{len(ps)} 支军队每回合吃 {need} 补给；全按现买价 {buy_p:.2f} 买 ≈ {need * buy_p:.0f} 金/回合——补给厂/装备厂的价值随军队规模摊薄，别只盯外销价。')
    L.append('  注：粮/木是内需品（便宜、回本慢）；靠外贸赚钱卖 矿/油/装备。加工厂产出按「买价」折算，因为它的产出是替你去市场上买。')
    return '\n'.join(L)

EXPORTS = {n: v for n,v in globals().copy().items() if callable(v) and getattr(v,"__module__",None)==__name__}

def panel_res(world,actor,**args):return _res_line(world,actor)

def panel_market(world,actor,**args):return _fmt_market(world,actor)

def panel_econ(world,actor,**args):return _fmt_econ(world,actor)
