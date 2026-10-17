"""Diplomacy toolcalls: one explicit action table plus vote and the diplomacy panel."""
from engine.runtime import Tool


ARGUMENTS = {
    'guarantee': {'target': str}, 'cancel_guarantee': {'target': str},
    'propose_defense': {'target': str}, 'break_defense': {'target': str},
    'accept_proposal': {'offer': int}, 'reject_proposal': {'offer': int},
    'bloc_found': {'bloc': str, 'name': str, 'tos': list[str]}, 'bloc_join': {'bloc': str},
    'bloc_leave': {}, 'bloc_dissolve': {}, 'bloc_transfer': {'target': str}, 'bloc_rename': {'name': str},
    'declare_war': {'target': str}, 'offer_peace': {'target': str, 'kind': str, 'gold': int, 'truce': int, 'note': str},
    'accept_peace': {'offer': int}, 'reject_peace': {'offer': int},
}

ALLIANCE_COMMITMENT = ('入盟后联盟是一个外交实体：保障、共同防御、宣战、议和须联盟表决，不能个人决定；'
                       '原个人保障/共同防御全部作废，退盟不恢复；盟内互不攻击、共享视野、互通领土。'
                       '任一成员交战，全盟不能退盟或解散；盟主须先转让才能退出。')

DESCRIPTIONS = {
    'guarantee': '保障对方外交实体：对方被宣战时承担直接防守参战义务；双方须和平，联盟须先表决。',
    'cancel_guarantee': '取消本方对目标实体的保障，战时条约冻结；联盟须先表决。',
    'propose_defense': '提出共同防御邀约；双方实体须和平。联盟先表决，接受后才缔结，主动进攻不因此召来伙伴。',
    'break_defense': '解除与目标实体的共同防御，战时不能解除；联盟须先表决。',
    'accept_proposal': '接受 query(panel=foreign) 待回应提议中的共同防御或创始邀约，offer 为整数编号；创始须所有受邀国接受，共同防御可能先转联盟表决。',
    'reject_proposal': '拒绝收到的共同防御或创始邀约，offer 为 query(panel=foreign) 待回应提议的整数编号。',
    'bloc_found': '邀请独立、和平国家创立联盟；bloc 是自定且唯一的命名空间键（如 scenario:union），name 是无空白显示名，tos 是别国国家键数组，不能含自己。全体接受后成立，邀请本身不改条约；发起人为盟主。',
    'bloc_join': '申请加入 query(panel=foreign) 中的联盟，bloc 使用该联盟 id；双方须和平，现成员投票通过后才入盟。',
    'bloc_leave': '退出当前联盟；任一成员交战就禁止退盟，不能靠退盟脱战；盟主须先转让或解散。原个人保障/共同防御不恢复。退出后若向原盟任何成员宣战，仍与其整个联盟交战。',
    'bloc_dissolve': '盟主解散和平时期的联盟；战时不能解散。',
    'bloc_transfer': '盟主把领导权转交给现有其他成员，target 使用成员的国家键。',
    'bloc_rename': '盟主修改联盟显示名；不能有空白或与其他联盟重名。',
    'declare_war': '向另一外交实体宣战；休战期间不能宣战，本方联盟先表决。攻击联盟任一成员即与全盟交战，防守全盟无需另投票；原防守实体的直接保障/共同防御再传导一跳，不递归。保障不协助主动进攻；之后 attack 不重复传播宣战。',
    'offer_peace': '向敌方战争主导实体代表求和，双方主导者都可发起；普通跟随方不能代谈。kind=white 白和、pay 本方赔款、demand 对方赔款；后两种 gold>0，白和 gold=0；truce 为休战周期数。联盟先表决，接受后整条战线停战。',
    'accept_peace': '由收到提议的战争代表接受 query(panel=foreign) 待回应提议中的和平提议，offer 为整数编号；联盟可能先表决。',
    'reject_peace': '由收到提议的战争代表拒绝和平提议，offer 为 query(panel=foreign) 待回应提议中的整数编号，战争继续；普通跟随方不能代拒。',
}


# 策略组：政体可用 group.<名> 整组禁用；组名同时是领域对象做能力检查时用的键。
GROUPS = {
    'guarantee': ('diplomacy.treaty', 'accept.treaties'),
    'cancel_guarantee': ('diplomacy.treaty', 'accept.treaties'),
    'propose_defense': ('diplomacy.treaty', 'accept.treaties'),
    'break_defense': ('diplomacy.treaty', 'accept.treaties'),
    'accept_proposal': ('diplomacy.treaty', 'accept.treaties'),
    'reject_proposal': ('diplomacy.treaty', 'accept.treaties'),
    'bloc_found': ('diplomacy.alliance', 'accept.bloc'),
    'bloc_join': ('diplomacy.alliance', 'accept.bloc'),
    'bloc_leave': ('diplomacy.alliance', 'accept.bloc'),
    'bloc_dissolve': ('diplomacy.alliance', 'accept.bloc'),
    'bloc_transfer': ('diplomacy.alliance', 'accept.bloc'),
    'bloc_rename': ('diplomacy.alliance', 'accept.bloc'),
    'vote': ('diplomacy.vote',),
    'declare_war': ('diplomacy.war',),
    'offer_peace': ('diplomacy.war',),
    'accept_peace': ('diplomacy.war',),
    'reject_peace': ('diplomacy.war',),
}


def _num(value):
    if value is None:
        return '—'
    if isinstance(value, (int, float)):
        return f'{value:.2f}'.rstrip('0').rstrip('.') if isinstance(value, float) else str(value)
    return str(value)


def tools(runtime):
    result = []
    for action, arguments in ARGUMENTS.items():
        def submit(world, actor, _action=action, **args):
            return runtime.player_api(world, 'native:diplomacy').submit(actor, _action, **args)
        properties = {key: ({'type': 'array', 'items': {'type': 'string'}} if value == list[str] else {'type': 'integer' if value is int else 'string'})
                      for key, value in arguments.items()}
        for key, prop in properties.items():
            if key in ('offer',):
                prop.update(minimum=1, description='query(panel=foreign) 待回应提议的编号')
            elif key == 'target':
                prop['description'] = 'query(panel=foreign) 中的别国 id；求和须使用其战争代表'
            elif key == 'bloc':
                prop['description'] = '联盟键：加入时用现有联盟 id；创建时自定唯一命名空间键'
            elif key in ('gold', 'truce'):
                prop['minimum'] = 0
        if action == 'offer_peace':
            properties['kind'].update(enum=['white', 'pay', 'demand'])
            properties['note']['description'] = '可选和平提议正文，不改变赔款或休战规则'
        required = [key for key in arguments if not (action == 'offer_peace' and key in ('gold', 'truce', 'note'))]
        description = DESCRIPTIONS[action]
        if action in ('accept_proposal', 'bloc_found', 'bloc_join'):
            description += ALLIANCE_COMMITMENT
        if action in ('accept_proposal', 'reject_proposal', 'accept_peace', 'reject_peace'):
            description += ' 回应免费，不收外交手续费；议和约定的赔款仍按条款执行。'
        if action == 'bloc_found':
            unit = runtime.units.get('native:diplomacy')
            if unit is not None:
                fee = unit.mod.declaration['Diplomacy']['FoundFee']
                description += f' 发起固定预留{fee}金，不受双方外交中心减免；成功发布邀约即收费，后续被拒不退；发布时失效则退款。'
        result.append(Tool(action, submit, owner='native:diplomacy', api=('submit',), argument_types=arguments,
                           parameters={'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
                           groups=GROUPS.get(action, ()), description=description))

    def vote(world, actor, vote: int, accept: bool | None):
        return runtime.player_api(world, 'native:diplomacy').vote(actor, vote, accept)

    def diplomacy(world, actor):
        return runtime.player_api(world, 'native:diplomacy').snapshot(actor)

    result.extend((
        Tool('vote', vote, owner='native:diplomacy', api=('vote',), groups=GROUPS['vote'],
             description='对 query(panel=foreign) 投票中本国有资格且当前开放的投票表态：vote 为整数编号，accept=true 赞成、false 反对。仅发起后的下一周期可投票或改票，accept=null 显式弃权；结束回合仍未投票则自动反对；显式弃权保留，赞成多于反对通过；投票不等于条约已生效。'),
        Tool('diplomacy', diplomacy, owner='native:diplomacy', api=('snapshot',), read_only=True, fact=True,
             description='本国相关联盟、条约、战争、休战、提议、投票、待结算命令及结果；模型统一查 query(panel=foreign)。offers/votes 的 id 用于回应和投票；submitted 不代表关系已改变。'),
    ))
    return tuple(result)


def _diplomacy(data, args, actor, ctx):
    lines = [ctx.text('tool.diplomacy.head')]
    blocs = {'bloc/' + b['id']: b['name'] for b in data.get('alliances', [])}
    name = lambda value: blocs.get(value, ctx.name(value, ctx._names))
    names = lambda values: '、'.join(name(v) for v in values)
    for key, title in (('alliances', 'alliances'), ('pacts', 'pacts'), ('wars', 'wars'), ('truces', 'truces'),
                       ('offers', 'offers'), ('votes', 'votes'), ('pending', 'pending'), ('results', 'results')):
        rows = data.get(key, [])
        lines.append(ctx.text('tool.diplomacy.section_' + title) + (ctx.text('tool.diplomacy.none') if not rows else ''))
        for row in rows:
            if key == 'alliances':
                lines.append(ctx.text('tool.diplomacy.alliance', name=row['name'], id=row['id'], chief=name(row['chief']), members=names(row['members'])))
            elif key == 'pacts':
                lines.append(ctx.text('tool.diplomacy.pact', kind=ctx.text('tool.diplomacy.pact_' + row['kind']), a=name(row['a']), b=name(row['b'])))
            elif key == 'wars':
                lines.append(ctx.text('tool.diplomacy.war', id=row['id'], a=name(row['a']), b=name(row['b']),
                                      attackers=names(row['attackers']), defenders=names(row['defenders'])))
            elif key == 'truces':
                a, b, until = (row['a'], row['b'], row['until']) if isinstance(row, dict) else (row[0], row[1], row[2])
                lines.append(ctx.text('tool.diplomacy.truce', a=name(a), b=name(b), until=until))
            else:
                args_in = dict(row['arguments'])
                action = {'found': 'bloc_found', 'defense': 'propose_defense', 'peace': 'offer_peace'}.get(row['action'], row['action'])
                head = ctx.text('tool.diplomacy.offer', id=row['id'], actor=name(row['actor']), action=ctx.label('action', action))
                if key == 'offers':
                    head += ctx.text('tool.diplomacy.offer_target', target=name(row['target']), accepted=names(row['accepted']) or ctx.text('tool.diplomacy.none'))
                elif key == 'votes':
                    head += ctx.text('tool.diplomacy.vote_line', deadline=row['deadline'], voters=names(row['voters']),
                                     ballots='、'.join(name(n) + (ctx.text('tool.diplomacy.abstain') if choice is None else ctx.text('tool.diplomacy.accept') if choice else ctx.text('tool.diplomacy.reject')) for n, choice in row['ballots']) or ctx.text('tool.diplomacy.no_ballots'))
                else:
                    head += ctx.text('tool.diplomacy.result', status=ctx.label('status', row['status']), fee=row['fee'])
                lines.append(head)
                if args_in:
                    lines.append('  ' + ctx.format(args_in, ctx._names).replace('\n', '；'))
                if row.get('message'):
                    lines.append('  ' + ctx.label('receipt', row['message']))
    return '\n'.join(lines)


def _action_receipt(action, args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, dict):
        return None
    args = {**(args or {}), **dict(data.get('arguments') or ())}
    fee = _num(data.get('fee', 0))
    target = ctx.name(args.get('target', ''), ctx._names)
    if action == 'vote':
        choice = args.get('accept')
        word = ctx.text('tool.diplomacy.abstain') if choice is None else ctx.text('tool.diplomacy.accept') if choice else ctx.text('tool.diplomacy.reject')
        return ctx.text('tool.diplomacy.vote_done', id=data.get('id', args.get('vote', '?')), word=word)
    if action in ('guarantee', 'cancel_guarantee', 'propose_defense', 'break_defense', 'declare_war', 'bloc_leave', 'bloc_dissolve', 'bloc_rename', 'bloc_transfer'):
        return ctx.text('tool.diplomacy.' + action, target=target, fee=fee, name=args.get('name', ''))
    if action == 'bloc_found':
        tos = '、'.join(ctx.name(t, ctx._names) for t in (args.get('tos') or []))
        return ctx.text('tool.diplomacy.bloc_found', name=args.get('name', ''), tos=tos or ctx.text('tool.diplomacy.invitees'), fee=fee)
    if action == 'bloc_join':
        return ctx.text('tool.diplomacy.bloc_join', bloc=args.get('bloc', ''), fee=fee)
    if action in ('accept_proposal', 'reject_proposal', 'accept_peace', 'reject_peace'):
        return ctx.text('tool.diplomacy.' + action, offer=args.get('offer', '?'), fee=fee)
    if action == 'offer_peace':
        kind = args.get('kind', 'white')
        terms = ctx.text('tool.diplomacy.terms_' + kind, gold=_num(args.get('gold', 0)))
        line = ctx.text('tool.diplomacy.offer_peace', target=target, terms=terms)
        if args.get('truce') is not None:
            line += ctx.text('tool.diplomacy.truce_line', turns=_num(args['truce']))
        line += ctx.text('tool.diplomacy.reserve', fee=fee)
        if args.get('note'):
            line += '\n' + args['note']
        return line
    return None


RECEIPTS = {action: (lambda args, receipt, actor, ctx, action=action: _action_receipt(action, args, receipt, actor, ctx))
            for action in (*ARGUMENTS, 'vote')}
PANELS = {'diplomacy': _diplomacy}
CAPTIONS = {action: (lambda args, actor, receipt, ctx, action=action: ctx.text('tool.caption.' + action))
            for action in (*ARGUMENTS, 'vote', 'diplomacy')}
