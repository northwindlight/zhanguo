"""Diplomacy toolcalls: one explicit action table plus vote and the diplomacy panel."""
from engine.runtime import Action, Tool
from mods.native.diplomacy.objects import ENTITY_TARGET_ACTIONS


ARGUMENTS = {
    'guarantee': {'target': str}, 'cancel_guarantee': {'target': str},
    'propose_defense': {'target': str}, 'break_defense': {'target': str},
    'accept_proposal': {'offer': int}, 'reject_proposal': {'offer': int},
    'bloc_found': {'bloc': str, 'name': str, 'tos': list[str]}, 'bloc_join': {'bloc': str},
    'bloc_leave': {}, 'bloc_dissolve': {}, 'bloc_transfer': {'target': str}, 'bloc_rename': {'name': str},
    'declare_war': {'target': str}, 'offer_peace': {'target': str, 'kind': str, 'gold': int, 'truce': int, 'note': str},
    'accept_peace': {'offer': int}, 'reject_peace': {'offer': int},
    'propose_vassalage': {'target': str}, 'release_vassal': {'target': str}, 'independence_war': {},
    'submit_vassalage': {'target': str},
}

ALLIANCE_COMMITMENT = ('入盟后联盟是一个外交实体：保障、共同防御、宣战、议和须联盟表决，不能个人决定；'
                       '原个人保障/共同防御全部作废，退盟不恢复；盟内互不攻击、共享视野、互通领土。'
                       '任一成员交战，全盟不能退盟或解散；盟主须先转让才能退出。')

DESCRIPTIONS = {
    'guarantee': '本方承诺保障 target：本方是保障人，target 是受保护实体。第三方宣战 target 时，本方承担防守参战义务；target 不因此反向保卫本方，也不替本方主动进攻。双方须和平，联盟须先表决。',
    'cancel_guarantee': '取消本方对目标实体的保障，战时条约冻结；联盟须先表决。',
    'propose_defense': '提出共同防御邀约；双方实体须和平。联盟先表决，接受后才缔结，主动进攻不因此召来伙伴。',
    'break_defense': '解除与目标实体的共同防御，战时不能解除；联盟须先表决。',
    'accept_proposal': '接受 query(panel=foreign) 待回应提议中的共同防御、创始邀约或附庸邀约，offer 为整数编号；创始须所有受邀国接受，共同防御可能先转联盟表决。附庸接受即承担持续上贡与相应权力限制，须先查看邀约类型和当前剧本。',
    'reject_proposal': '拒绝收到的共同防御、创始邀约或附庸邀约，offer 为 query(panel=foreign) 待回应提议的整数编号。',
    'bloc_found': '邀请独立、和平国家创立联盟；bloc 是自定且唯一的命名空间键（如 scenario:union），name 是无空白显示名，tos 是别国国家键数组，不能含自己。全体接受后成立，邀请本身不改条约；发起人为盟主。',
    'bloc_join': '申请加入 query(panel=foreign) 中的联盟，bloc 使用该联盟 id；双方须和平，现成员投票通过后才入盟。',
    'bloc_leave': '退出当前联盟；任一成员交战就禁止退盟，不能靠退盟脱战；盟主须先转让或解散。原个人保障/共同防御不恢复。退出后若向原盟任何成员宣战，仍与其整个联盟交战。',
    'bloc_dissolve': '盟主解散和平时期的联盟；战时不能解散。',
    'bloc_transfer': '盟主把领导权转交给现有其他成员，target 使用成员的国家键；剧本锁定特殊宗主盟主权时不可移交。',
    'bloc_rename': '盟主修改联盟显示名；不能有空白或与其他联盟重名。',
    'declare_war': '向另一外交实体宣战；休战期间不能宣战，本方联盟先表决。攻击联盟任一成员即与全盟交战，防守全盟无需另投票；原防守实体的直接保障/共同防御再传导一跳，不递归。保障不协助主动进攻；之后 attack 不重复传播宣战。',
    'offer_peace': '向敌方战争主导实体代表求和，双方主导者都可发起；普通跟随方不能代谈。kind=white 白和、pay 本方赔款、demand 对方赔款；赔款须 gold>0。kind=submit 本国称臣、subjugate 要求对方称臣，须 gold=0，附庸接受持续上贡和权力限制，其他战线须已结束，不能成环；联盟依正常表决，称臣代表在和平生效后退出原联盟，其他盟友不随之成为附庸。truce 为休战周期数。联盟先表决，接受后整条战线停战；剧本可将指定宗主的附庸自动转为特殊附庸联盟。',
    'accept_peace': '仅 foreign 邀约中 can_accept=true 的战争代表可接受，普通联盟成员应查 vote_ids 后使用 vote 表态。由收到提议的战争代表接受 query(panel=foreign) 待回应提议中的和平提议，offer 为整数编号；联盟可能先表决。',
    'reject_peace': '仅 foreign 邀约中 can_reject=true 的战争代表可拒绝，普通联盟成员应查 vote_ids 后使用 vote 表态。由收到提议的战争代表拒绝和平提议，offer 为 query(panel=foreign) 待回应提议中的整数编号，战争继续；普通跟随方不能代拒。',
    'propose_vassalage': '宗主向独立国家提出附庸邀约，对方免费 accept_proposal 后生效：普通附庸强制上交实际生产15%，失去全部外交权和表决权，随宗主参战，仅宗主可和平释放；附庸只能发动独立战争脱离。剧本可配置特殊附庸联盟，接受前须结合剧本说明确认贡率及权利。',
    'release_vassal': '宗主在和平时释放自己的普通附庸，停止上贡并恢复外交权；特殊附庸使用正常联盟的和平退盟规则。',
    'independence_war': '普通附庸向宗主发动真实独立战争：脱离宗主原有战线、停止上贡，独自对抗宗主及其其他附庸；恢复外交权，战争、补给和伤亡照常，不能和平单方脱离。',
    'submit_vassalage': '向 target 主动称臣。行动期仅提交，回合末结算：普通宗主须接受邀约；特殊宗主联盟可按剧本自动接受并创建联盟、向宗主扣首次建盟费，已有特殊联盟按成员表决入盟。接受贡赋与权力限制前须阅读当前剧本；同回合共同创始者一起处理，不重复收宗主建盟费。宣战在其他外交命令之后结算。',
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
    'propose_vassalage': ('diplomacy.alliance', 'accept.bloc'),
    'release_vassal': ('diplomacy.alliance',),
    'independence_war': ('diplomacy.war',),
    'submit_vassalage': ('diplomacy.alliance','accept.bloc'),
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
        properties = {key: ({'type': 'array', 'items': {'type': 'string'}, 'minItems':1, 'uniqueItems':True} if value == list[str] else {'type': 'integer' if value is int else 'string'})
                      for key, value in arguments.items()}
        for key, prop in properties.items():
            if key in ('offer',):
                prop.update(minimum=1, description='query(panel=foreign) 待回应提议的编号')
            elif key == 'target':
                prop['description'] = ('query(panel=foreign) 中的国家键、联盟 id 或外交实体键（bloc/联盟键、nation/国家键）；系统定位当前代表'
                                       if action in ENTITY_TARGET_ACTIONS else '具体国家键，不能传联盟键或外交实体键')
            elif key == 'bloc':
                prop['description'] = '联盟键：加入时用现有联盟 id；创建时自定唯一命名空间键'
            elif key in ('gold', 'truce'):
                prop['minimum'] = 0
        if action == 'offer_peace':
            properties['kind'].update(enum=['white', 'pay', 'demand','submit','subjugate'])
            properties['note']['description'] = '可选和平提议正文，不改变赔款或休战规则'
        required = [key for key in arguments if not (action == 'offer_peace' and key in ('gold', 'truce', 'note'))]
        description = DESCRIPTIONS[action]
        if action in ENTITY_TARGET_ACTIONS:description+=' target 可用国家键、联盟 id 或 bloc/联盟键、nation/国家键；由系统定位外交实体当前代表。'
        if action=='accept_proposal':description+=' 若接受创始邀约，另适用下列联盟权责；附庸邀约按其普通或剧本特殊附庸条款：'
        if action in ('accept_proposal', 'bloc_found', 'bloc_join'):
            description += ALLIANCE_COMMITMENT
        if action in ('accept_proposal', 'reject_proposal', 'accept_peace', 'reject_peace'):
            description += ' 回应免费，不收外交手续费；议和约定的赔款仍按条款执行。'
        if action == 'bloc_found':
            unit = runtime.units.get('native:diplomacy')
            if unit is not None:
                fee = unit.mod.declaration['Diplomacy']['FoundFee']
                description += f' 发起固定预留{fee}金，不受双方外交中心减免；成功发布邀约即收费，后续被拒不退；发布时失效则退款。'
        result.append(Action(action, submit, owner='native:diplomacy', api=('submit',), argument_types=arguments,
                           parameters={'type': 'object', 'properties': properties, 'required': required, 'additionalProperties': False},
                           groups=GROUPS.get(action, ()), description=description))

    def vote(world, actor, vote: int, accept: bool | None):
        return runtime.player_api(world, 'native:diplomacy').vote(actor, vote, accept)

    result.extend((
        Action('vote', vote, owner='native:diplomacy', api=('vote',), groups=GROUPS['vote'],
             description='对 query(panel=foreign) 投票中本国有资格且当前开放的投票表态：vote 为整数编号，accept=true 赞成、false 反对。仅发起后的下一周期可投票或改票，accept=null 显式弃权；结束回合仍未投票则自动反对；显式弃权保留，赞成多于反对通过；开票前仅自己选票可见，不公开他国是否已投或票数；周期末向全体选民公开记名开票结果，投票通过仍须外交执行生效。'),
    ))
    return tuple(result)


def _diplomacy(data, args, actor, ctx):
    lines = [ctx.text('tool.diplomacy.head')]
    blocs = {'bloc/' + b['id']: b['name'] for b in data.get('alliances', [])}
    name = lambda value: blocs.get(value, ctx.name(value, ctx._names))
    names = lambda values: '、'.join(name(v) for v in values)
    for relation in data.get('vassals',[]):
        lines.append(ctx.text('tool.diplomacy.vassal',subject=name(relation['subject']),overlord=name(relation['overlord']),
                              kind=ctx.text('tool.diplomacy.vassal_'+relation['kind']),percent=relation['tribute_percent']))
    for key, title in (('alliances', 'alliances'), ('pacts', 'pacts'), ('wars', 'wars'), ('truces', 'truces'),
                       ('offers', 'offers'), ('votes', 'votes'), ('pending', 'pending'), ('results', 'results')):
        rows = data.get(key, [])
        lines.append(ctx.text('tool.diplomacy.section_' + title) + (ctx.text('tool.diplomacy.none') if not rows else ''))
        for row in rows:
            if key == 'alliances':
                lines.append(ctx.text('tool.diplomacy.alliance', name=row['name'], id=row['id'], chief=name(row['chief']), members=names(row['members'])))
            elif key == 'pacts':
                if row['kind']=='guarantee':
                    lines.append(ctx.text('tool.diplomacy.guarantee_direction', protector=name(row['a']), protected=name(row['b'])))
                else:
                    lines.append(ctx.text('tool.diplomacy.pact', kind=ctx.text('tool.diplomacy.pact_' + row['kind']), a=name(row['a']), b=name(row['b'])))
            elif key == 'wars':
                lines.append(ctx.text('tool.diplomacy.war', id=row['id'], a=name(row['a']), b=name(row['b']),
                                      attackers=names(row['attackers']), defenders=names(row['defenders'])))
            elif key == 'truces':
                a, b, until = (row['a'], row['b'], row['until']) if isinstance(row, dict) else (row[0], row[1], row[2])
                lines.append(ctx.text('tool.diplomacy.truce', a=name(a), b=name(b), until=until))
            else:
                args_in = dict(row['arguments'])
                action = {'found': 'bloc_found', 'defense': 'propose_defense', 'peace': 'offer_peace','vassal':'propose_vassalage',
                          'submission':'submit_vassalage'}.get(row['action'], row['action'])
                head = ctx.text('tool.diplomacy.offer', id=row['id'], actor=name(row['actor']), action=ctx.label('action', action))
                if key == 'offers':
                    head += ctx.text('tool.diplomacy.offer_target', target=name(row['target']), accepted=names(row['accepted']) or ctx.text('tool.diplomacy.none'))
                    if 'can_accept' in row:
                        head += ctx.text('tool.diplomacy.offer_permissions',representative=name(row['response_actor']) if row.get('response_actor') else ctx.text('tool.diplomacy.none'),
                                         accept=ctx.text('tool.diplomacy.allowed' if row['can_accept'] else 'tool.diplomacy.readonly'),
                                         reject=ctx.text('tool.diplomacy.allowed' if row['can_reject'] else 'tool.diplomacy.readonly'))
                        if row.get('vote_ids'):head += ctx.text('tool.diplomacy.offer_votes',votes=', '.join(map(str,row['vote_ids'])))
                        elif row['action']=='peace' and not row['can_accept'] and not row['can_reject']:
                            head += ctx.text('tool.diplomacy.offer_wait_representative')
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
    if not receipt.get('ok',True):
        if data.get('reason')=='batch_rejected':
            return '❌ '+ctx.text('tool.batch.rejected')+'\n'+'\n'.join(
                ctx.name(row['target'],ctx._names)+'：'+row['message'] for row in data['items'])
        return None
    args = {**(args or {}), **dict(data.get('arguments') or ())}
    fee = _num(data.get('fee', 0))
    target_key=args.get('target','')
    target = ctx.name(target_key, ctx._names)
    if isinstance(target_key,str) and target_key.startswith('bloc/'):
        alliances=getattr(ctx,'_facts',{}).get(actor,{}).get('diplomacy',{}).get('alliances',[])
        target=next((b['name'] for b in alliances if 'bloc/'+b['id']==target_key),target)
    if action == 'vote':
        choice = args.get('accept')
        word = ctx.text('tool.diplomacy.abstain') if choice is None else ctx.text('tool.diplomacy.accept') if choice else ctx.text('tool.diplomacy.reject')
        return ctx.text('tool.diplomacy.vote_done', id=data.get('id', args.get('vote', '?')), word=word)
    if action in ('guarantee', 'cancel_guarantee', 'propose_defense', 'break_defense', 'declare_war', 'bloc_leave', 'bloc_dissolve', 'bloc_rename', 'bloc_transfer',
                  'propose_vassalage','release_vassal','independence_war','submit_vassalage'):
        return ctx.text('tool.diplomacy.' + action, target=target, fee=fee, name=args.get('name', ''))
    if action == 'bloc_found':
        tos = '、'.join(ctx.name(t, ctx._names) for t in (args.get('tos') or []))
        return ctx.text('tool.diplomacy.bloc_found', name=args.get('name', ''), tos=tos or ctx.text('tool.diplomacy.invitees'), fee=fee)+'\n'+'\n'.join(
            ctx.text('tool.diplomacy.invitee_submitted',target=ctx.name(n,ctx._names)) for n in args.get('tos',[]))
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
