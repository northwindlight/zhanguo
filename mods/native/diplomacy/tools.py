from __future__ import annotations
from ..core import *
from engine.runtime import Tool

def tool_countries(world, actor, **args):
    from ..presentation import _fmt_countries
    tool = 'countries'
    return _fmt_countries(world, actor)

def tool_send_letter(world, actor, **args):
    tool = 'send_letter'
    return world.send_mail(actor, str(args.get('to', '')), str(args.get('content', '')))[1]

def tool_gift(world, actor, **args):
    from ..presentation import GOODS_DISPLAY, GOOD_ALIAS
    tool = 'gift'
    to = str(args.get('to', ''))
    g = GOOD_ALIAS.get(str(args.get('good', '')).lower())
    if g is None:
        g = {'黄金': '黄金', '金': '黄金', 'gold': '黄金'}.get(str(args.get('good', '')).lower())
    if g is None:
        return '物资名无效（可赠：' + '、'.join(GOODS_DISPLAY) + ' 或 黄金）'
    try:
        n = int(args.get('qty', args.get('amount', 0)))
    except (TypeError, ValueError):
        return '数量需为整数'
    return world.gift(actor, to, g, n)[1]

def tool_share_map(world, actor, **args):
    tool = 'share_map'
    to = str(args.get('to', ''))
    return world.share_map(actor, to)[1]

def tool_spy(world, actor, **args):
    tool = 'spy'
    return world.spy(actor, str(args.get('to', '')))[1]

def tool_propose(world, actor, **args):
    from ..presentation import PACT_MAP
    tool = 'propose'
    to = str(args.get('to', ''))
    kind = PACT_MAP.get(str(args.get('kind', '')).lower(), args.get('kind'))
    return world.propose_pact(kind, actor, to)[1]

def tool_respond_proposal(world, actor, **args):
    from ..presentation import _diplo_cost
    tool = 'respond_proposal'
    pid = int(args.get('proposal_id', args.get('id', 0)))
    accept = str(args.get('accept', '')).lower() in ('true', 'yes', '1', '接受', '是')
    p = next((x for x in world.proposals if x['id'] == pid), None)
    to = p['a'] if p and p['kind'] == '联盟' else p.get('asker') if p else None
    cost = _diplo_cost(world, actor, to)
    if accept:
        return world.accept_pact(actor, pid)[1]
    return world.reject_pact(actor, pid)[1]

def tool_break_alliance(world, actor, **args):
    tool = 'break_alliance'
    to = str(args.get('to', ''))
    return world.break_pact('同盟', actor, to)[1]

def tool_bloc_found(world, actor, **args):
    tool = 'bloc_found'
    tos = args.get('tos', args.get('to', []))
    if isinstance(tos, str):
        tos = [tos]
    tos = [str(t).strip() for t in tos or [] if str(t).strip()]
    hub = next((t for t in tos if world.nation_building_count(t, '外交中心') > 0), None)
    return world.propose_bloc(actor, str(args.get('name', '')), tos)[1]

def tool_bloc_join(world, actor, **args):
    tool = 'bloc_join'
    return world.bloc_join(actor, str(args.get('name', args.get('bloc', ''))))[1]

def tool_bloc_leave(world, actor, **args):
    tool = 'bloc_leave'
    return world.bloc_leave(actor)[1]

def tool_bloc_rename(world, actor, **args):
    tool = 'bloc_rename'
    return world.bloc_rename(actor, str(args.get('name', '')))[1]

def tool_bloc_transfer(world, actor, **args):
    tool = 'bloc_transfer'
    return world.bloc_transfer(actor, str(args.get('to', '')))[1]

def tool_bloc_dissolve(world, actor, **args):
    tool = 'bloc_dissolve'
    return world.bloc_dissolve(actor)[1]

def tool_vote(world, actor, **args):
    tool = 'vote'
    vid = int(args.get('vote_id', args.get('id', 0)))
    raw = args.get('choice', args.get('vote', args.get('approve', args.get('accept'))))
    if isinstance(raw, bool):
        choice = raw
    else:
        s = str(raw or '').strip().lower()
        if s in ('yes', 'y', 'true', '1', '赞成', '同意', '接受', '是'):
            choice = True
        elif s in ('no', 'n', 'false', '0', '反对', '否决', '拒绝'):
            choice = False
        elif s in ('abstain', 'abstention', '弃权', '中立', '不表态'):
            choice = None
        else:
            return 'vote 需要 choice=yes/no/abstain（你要怎么表态？）'
    return world.cast_vote(actor, vid, choice)[1]

def tool_break_defense(world, actor, **args):
    tool = 'break_defense'
    to = str(args.get('to', ''))
    return world.break_pact('共同防御', actor, to)[1]

def tool_guarantee(world, actor, **args):
    tool = 'guarantee'
    to = str(args.get('to', ''))
    return world.declare_guarantee(actor, to)[1]

def tool_cancel_guarantee(world, actor, **args):
    tool = 'cancel_guarantee'
    to = str(args.get('to', ''))
    return world.cancel_guarantee(actor, to)[1]

def tool_declare_war(world, actor, **args):
    tool = 'declare_war'
    to = str(args.get('to', ''))
    return world.declare_war(actor, to)[1]

def tool_offer_peace(world, actor, **args):
    from ..presentation import KIND_MAP
    tool = 'offer_peace'
    to = str(args.get('to', ''))
    kind = KIND_MAP.get(str(args.get('kind', '')).lower(), args.get('kind'))
    gold = int(args.get('gold', 0) or 0)
    note = str(args.get('note', '') or '')
    truce = int(args.get('truce', 0) or 0)
    return world.offer_peace(actor, to, kind, gold, note, truce)[1]

def tool_accept_peace(world, actor, **args):
    tool = 'accept_peace'
    return world.accept_peace(actor, int(args.get('offer_id', 0)))[1]

def tool_reject_peace(world, actor, **args):
    tool = 'reject_peace'
    return world.reject_peace(actor, int(args.get('offer_id', 0)))[1]

def definitions():
    return (
        Tool('countries', tool_countries, aliases=('外交对象', '国家列表', '对手'), argument_types={}),
        Tool('send_letter', tool_send_letter, aliases=('写信', 'letter'), argument_types={'to': str, 'content': str}),
        Tool('gift', tool_gift, aliases=('赠送', '赠予', '馈赠'), argument_types={'to': str, 'good': str, 'qty': int}),
        Tool('share_map', tool_share_map, aliases=('交换地图', '送图', '发地图'), argument_types={'to': str}),
        Tool('spy', tool_spy, aliases=('间谍', '刺探'), argument_types={'to': str}),
        Tool('propose', tool_propose, aliases=('提议',), argument_types={'to': str, 'kind': str}),
        Tool('respond_proposal', tool_respond_proposal, aliases=('回应邀约',), argument_types={'proposal_id': int, 'accept': bool}),
        Tool('break_alliance', tool_break_alliance, aliases=('断盟',), argument_types={}),
        Tool('bloc_found', tool_bloc_found, aliases=('结盟', '发起结盟'), argument_types={'name': str, 'tos': list[str]}),
        Tool('bloc_join', tool_bloc_join, aliases=('入盟', '申请入盟'), argument_types={'name': str}),
        Tool('bloc_leave', tool_bloc_leave, aliases=('退盟', '退出联盟'), argument_types={}),
        Tool('bloc_rename', tool_bloc_rename, aliases=('改盟名', '联盟改名'), argument_types={'name': str}),
        Tool('bloc_transfer', tool_bloc_transfer, aliases=('移交盟主',), argument_types={'to': str}),
        Tool('bloc_dissolve', tool_bloc_dissolve, aliases=('解散联盟',), argument_types={}),
        Tool('vote', tool_vote, aliases=('投票',), argument_types={'vote_id': int, 'choice': str}),
        Tool('break_defense', tool_break_defense, aliases=('解除共同防御',), argument_types={'to': str}),
        Tool('guarantee', tool_guarantee, aliases=('保障独立',), argument_types={'to': str}),
        Tool('cancel_guarantee', tool_cancel_guarantee, aliases=('撤回保障',), argument_types={'to': str}),
        Tool('declare_war', tool_declare_war, aliases=('宣战',), argument_types={'to': str}),
        Tool('offer_peace', tool_offer_peace, aliases=('求和',), argument_types={'to': str, 'kind': str, 'gold': int, 'truce': int, 'note': str}),
        Tool('accept_peace', tool_accept_peace, aliases=('接受议和',), argument_types={'offer_id': int}),
        Tool('reject_peace', tool_reject_peace, aliases=('拒绝议和',), argument_types={'offer_id': int}),
    )
