from __future__ import annotations
from ..core import *
from ..rules import *
from ..ui_constants import *
from .. import presentation as P
import copy,json,re,threading
from pathlib import Path
from ctx import est_tokens
from console import dw as _dw, pad as _pad


def _diplo_chain() -> list[str]:
    """外交中心叠加减半后的外交费序列（10→5→2→1…，下限 DIPLO_CENTER_MIN_COST）。"""
    c = DIPLO_COST
    out = []
    while True:
        out.append(str(c))
        if c <= DIPLO_CENTER_MIN_COST:
            break
        c = max(DIPLO_CENTER_MIN_COST, c // 2)
    return out

def _fmt_mail(world, name, brief: bool=False) -> str:
    """信箱。brief=True（每回合状态面板用）：只完整展示最新几封，旧信压成摘要行；
    brief=False（query panel=mail 按需查询用）：列出全部信件全文——面板里"旧信见
    query"的提示必须真能取回，否则老信件对 AI 永久丢失。"""
    box = world.mailbox.get(name, [])
    if not box:
        return '（收件箱为空）'
    if not brief:
        lines = [f'收件箱 {len(box)} 封（寄出后下回合到）:']
        for m in reversed(box):
            lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
        return '\n'.join(lines)
    if len(box) <= MAIL_BRIEF_FULL:
        lines = [f'收件箱 {len(box)} 封（寄出后下回合到）:']
        for m in reversed(box):
            lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
        return '\n'.join(lines)
    lines = [f'收件箱 {len(box)} 封（寄出后下回合到；旧信只列摘要，全文用 query panel=mail）:']
    for m in reversed(box[-MAIL_BRIEF_FULL:]):
        lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{m['text']}")
    old = box[:-MAIL_BRIEF_FULL]
    for m in reversed(old[-MAIL_BRIEF_ROWS:]):
        t = ' '.join(str(m['text']).split())
        lines.append(f"  [第{m['turn']}回合] {m['from']} → 你：{t[:28]}{('…' if len(t) > 28 else '')}")
    if len(old) > MAIL_BRIEF_ROWS:
        lines.append(f'  （更早 {len(old) - MAIL_BRIEF_ROWS} 封见 query panel=mail）')
    return '\n'.join(lines)

def _fmt_public_affairs(world, me: str) -> str:
    """【公开条约与战线】：**全世界公开**的国家行为——谁与谁缔约/保障、谁在打谁、有哪些联盟。

    口径（2026-09-19 用户：「**必须知情**」）：条约与战争是**公开行为**，第三方有权知道；
    **商议过程**（联盟投票与计票、求和提议的来回、写信）仍然只有当事人看得见。
    这些条目走 `World.proclaim` → `broadcast`（`seen`=全体，不受视野过滤），
    所以这里可以放心把全世界的条约/战线一次列全——它跟各国近讯里收到的播报是同一份事实。
    """
    parts = []
    for b in world.blocs:
        mem = [m for m in b['members'] if m in world.nations]
        if mem:
            parts.append(f"🤝 联盟「{b['name']}」（盟主 {world.bloc_chief(b)}）成员：{'、'.join(mem)}")
    for p in world.pacts:
        arrow = '→' if p['kind'] == '保障' else '↔'
        parts.append(f"🕊 {p['kind']} {world.entity_label(p['a'])}{arrow}{world.entity_label(p['b'])}")
    for w in world.wars:
        fls = list(w['followers']) + list(w.get('atk_followers', []))
        parts.append(f"⚔ {w['atk']} ↔ {w['def']}" + (f"（跟随：{'、'.join(fls)}）" if fls else ''))
    for pair, until in list(world.truce.items()):
        if world.turn > until:
            continue
        a, b = sorted(pair)
        if world.entity_of(a) == world.entity_of(b):
            continue
        if a.startswith('国:') and world.bloc_of(a[2:]) is not None:
            note = f'（{a[2:]} 的**个人旧约·盟内冻结**：退出即恢复，不随盟主变）'
        elif b.startswith('国:') and world.bloc_of(b[2:]) is not None:
            note = f'（{b[2:]} 的**个人旧约·盟内冻结**：退出即恢复，不随盟主变）'
        else:
            note = ''
        parts.append(f'🕊 {world.entity_label(a)} ↔ {world.entity_label(b)} 休战至第 {until} 回合{note}')
    head = '  【公开条约与战线】（全世界可见：签了什么、谁打谁——商议过程不公开）'
    return head + '\n' + ('    ' + '\n    '.join(parts) if parts else '暂无：全世界还没有联盟/条约/战争')

def _fmt_diplomacy(world, name) -> str:
    lines = [f'国家关系: {world.rel_desc(name)}']
    bloc = world.bloc_of(name)
    if bloc is not None:
        chief = world.bloc_chief(bloc)
        me_chief = '（你是盟主：无否决权、可改盟名、可移交盟主、可解散联盟）' if chief == name else f'（盟主 {chief} 无否决权）'
        fighting = [m for m in bloc['members'] if world.at_war(m)]
        lock = f" ⚠ 战时锁死：{'、'.join(fighting)} 正在交战——不退盟、不解散，只能议和。" if fighting else ''
        lines.append(f"  🤝 你的联盟「{bloc['name']}」（盟主 {chief}）成员：{'、'.join(bloc['members'])} —— 盟内互通领土/互不攻击/共享视野；**对外以「联盟」这个外交实体出面**：保障独立/共同防御/宣战/议和一律须联盟投票通过（vote 表态），成员个人签不了任何条约；议和由盟主出面并经投票；同战线盟友会自动归还你的核心领土（land 面板 ♥ 标记）。{lock}\n     {me_chief}")
    wars_in = []
    for w in world.wars:
        sides = world._war_sides(w)
        if name not in sides[0] and name not in sides[1]:
            continue
        fls = list(w['followers']) + list(w.get('atk_followers', []))
        side = 'atk' if name in sides[0] else 'def'
        rep = world._peace_rep(w, side)
        role = f"★{('进攻' if side == 'atk' else '防御')}主导，**谈判代表=你，可直接 offer_peace**" if name == rep else f'本方谈判代表={rep}（你不能单独议和）'
        wars_in.append(f"{w['atk']}↔{w['def']}" + (f"(跟随 {'、'.join(fls)})" if fls else '(无跟随)') + f'｜你的身份：{role}')
    if wars_in:
        lines.append('  交战战线: ' + '；'.join(wars_in))
        lines.append('     ★ **反击无罪**：这条战线上你们已经开打了——atk 夺地、打人**不会再触发任何**保障/共同防御/联盟条约（传导只在「宣战」那一刻结算过**一次**、且**一对实体只走一跳**，此后参战名单冻结，不会再拉进任何国家）。**放手打，缩手才是亏。**')
    for v in world.votes:
        vb = world.bloc_by_name(v['bloc'])
        mine = bloc is not None and vb is bloc
        cand = v['kind'] == '入盟' and v['payload'].get('candidate') == name
        if not (mine or cand):
            continue
        members = [m for m in (vb['members'] if vb else []) if m in world.nations]
        yes = sum((1 for m in members if v['votes'].get(m) is True))
        no = sum((1 for m in members if v['votes'].get(m) is False))
        abst = sum((1 for m in members if m in v['votes'] and v['votes'][m] is None))
        pending = len(members) - yes - no - abst
        pl = v['payload']
        if v['kind'] == '宣战':
            desc = f"对 {world.entity_label(pl.get('target'))} 宣战"
        elif v['kind'] == '入盟':
            desc = f"{pl.get('candidate')} 申请入盟"
        elif v['kind'] == '缔约':
            act = '撤回/解除' if pl.get('cancel') else '对外提议' if pl.get('offer') else '接受对方邀约'
            desc = f"{act}{pl.get('pact')}（{world.entity_label(pl.get('A'))} ↔ {world.entity_label(pl.get('B'))}）"
        elif pl.get('type') == 'offer':
            desc = f"向 {pl.get('to')} 求和（{pl.get('kind')}{(' ' + str(pl.get('gold')) + '金' if pl.get('gold') else '')}）"
        else:
            desc = f"接受议和#{pl.get('offer_id')}"
        tail = ''
        if mine and v['kind'] != '入盟':
            if name not in v['votes']:
                tail = f" —— vote {v['id']} choice=yes/no/abstain 表态"
            else:
                tail = '（你已投' + {True: '赞成', False: '反对', None: '弃权'}[v['votes'][name]] + '）'
        lines.append(f"  🗳 投票#{v['id']}（{v['bloc']}·{v['kind']}，发起 {v['proposer']}）{desc}" + (f' 赞成{yes}/反对{no}/弃权{abst}/未投{pending}' if mine else '') + f"；第{v['voting_turn']}回合表决并于末尾计票" + f'（需赞成>反对；盟主无否决权）{tail}')
    my_ent = world.entity_of(name)
    incoming = [p for p in world.proposals if p['kind'] == '联盟' and name in p.get('invitees', []) or (p['kind'] != '联盟' and p.get('B') == my_ent)]
    if incoming:
        for p in incoming:
            if p['kind'] == '联盟':
                lines.append(f"  📨 邀约#{p['id']}: {p['a']} 提议结盟「{p['name']}」（创始成员：{'、'.join(p['invitees'])}；respond_proposal {p['id']} true/false）")
            else:
                how = f"——回应即提交联盟表决（respond_proposal {p['id']} true/false）" if bloc is not None else f"（respond_proposal {p['id']} true/false）"
                lines.append(f"  📨 邀约#{p['id']}: {world.entity_label(p['A'])} 提议 {p['kind']} {how}")
    offers = [p for p in world.peace_offers if p['b'] == name]
    if offers:
        for p in offers:
            k = {'pay': f"{p['a']}愿赔{p['gold']}金", 'demand': f"{p['a']}要你赔{p['gold']}金", 'white': '白和'}[p['kind']]
            if p.get('truce'):
                k += f"（休战{p['truce']}回合）"
            lines.append(f"  🕊 求和#{p['id']}（{p['a']}→你）: {k} —— {p.get('note', '')}（accept_peace/reject_peace {p['id']}）")
    my_e = world.entity_of(name)
    if world.guaranteed_by(my_e):
        lines.append('  你的实体（' + world.entity_label(my_e) + '）保障: ' + '、'.join((world.entity_label(e) for e in world.guaranteed_by(my_e))))
    if world.guarantors_of(my_e):
        lines.append('  保障你的实体: ' + '、'.join((world.entity_label(e) for e in world.guarantors_of(my_e))))
    own_offers = [p for p in world.peace_offers if p['a'] == name]
    for p in own_offers:
        k = {'pay': f"你愿赔{p['gold']}金", 'demand': f"你索{p['gold']}金", 'white': '白和'}[p['kind']]
        if p.get('truce'):
            k += f"（休战{p['truce']}回合）"
        lines.append(f"  你提的求和#{p['id']}（给{p['b']}）: {k}")
    lines.append(_fmt_public_affairs(world, me=name))
    return '\n'.join(lines)

def _fmt_countries(world, name) -> str:
    """外交对象面板：先把『不是自己』的国家列出来选目标。所有 to= 参数只能填这里面的国名。"""
    lines = [f'可选外交对象（其余国家；外交/通信的 to= 必须是其中之一，不能是自己）:']
    for n in world.alive():
        if n == name:
            continue
        my, them = (world.entity_of(name), world.entity_of(n))
        tags = []
        if them == my:
            tags.append(f'联盟成员(同属{world.entity_label(my)})')
        if world.has_pact('共同防御', my, them):
            tags.append('共同防御')
        if world.has_pact('保障', my, them):
            tags.append('本实体保障它')
        if world.has_pact('保障', them, my):
            tags.append('它保障本实体')
        if world.war_between(name, n):
            tags.append('交战')
        if not tags:
            tags.append('中立')
        border = False
        for x, y in world.own_tiles(name):
            if any((world.owned_by(*p) == n for p in world.neighbors(x, y))):
                border = True
                break
        letters = sum((1 for m in world.mailbox.get(name, []) if m['from'] == n))
        vis = [a for a in world.armies if a['owner'] == n and world.visible_to(name, a['x'], a['y'])]
        lines.append(f"  {n}：{'、'.join(tags)}{('，接壤' if border else '，不接壤')} 你收其来信 {letters} 封" + (f'，视野内有其军队 {len(vis)} 支' if vis else ''))
    return '\n'.join(lines)

def _diplo_cost(world, actor: str, to: str | None=None, *, incoming: bool=False) -> int:
    """外交基础费：盟内免费；向他国提议 结盟/联盟/议和 且对方有外交中心 → 免费（incoming）；
    否则每座本国（含抢来的）外交中心使费用再减半（10→5→2→1，下限 1 金）。"""
    if to and world.allied_between(actor, to):
        return 0
    if incoming and to and (world.nation_building_count(to, '外交中心') > 0):
        return 0
    c = DIPLO_COST
    for _ in range(world.nation_building_count(actor, '外交中心')):
        c = max(DIPLO_CENTER_MIN_COST, c // 2)
    return c

def _charge(world, actor: str, cost: int, fn, *a, **k) -> str:
    """外交/换图/馈赠/信件统一收费：先验国库，动作成功后扣费。

    动作合法失败（已同盟/已交战/物资不足等）不烧金，避免模型空试浪费；
    国库 < 费用时动作直接不发。

    ★ **`cost == 0` 的动作绝不受国库闸门管**（2026-10-09 用户报的真实 bug：
      「投票免费，但是负资产不能投」）。原先写成 `if 黄金 < cost`，于是**负国库**时
      `-45 < 0` 成立 ⇒ **免费**动作（投票 / 联盟改名 / 移交盟主 / 解散联盟，以及
      有外交中心/同盟成员免外交费的那些）**全被"国库不足：此操作需 0 金"挡死**。
      负国库的代价是"买不起东西"（借贷、买报表、派间谍照样瘫痪，那是设计），
      但**不该连一句话都说不出来**——穷国尤其需要投票和外交，这是它仅剩的武器。
    """
    if cost > 0 and world.res(actor, '黄金') < cost:
        return f"国库不足：此操作需 {cost} 金（你现 {world.res(actor, '黄金')}）"
    ok, msg = fn(*a, **k)
    if ok:
        world.add_res(actor, '黄金', -cost)
    return msg

EXPORTS = {n: v for n,v in globals().copy().items() if callable(v) and getattr(v,"__module__",None)==__name__}

def panel_mail(world,actor,**args):return _fmt_mail(world,actor)

def panel_diplomacy(world,actor,**args):return _fmt_diplomacy(world,actor)

def panel_countries(world,actor,**args):return _fmt_countries(world,actor)
