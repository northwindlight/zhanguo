"""Government toolcalls: plan, end turn, and the government panel."""
from engine.runtime import Tool


def tools(runtime):
    def plan(world, actor, text: str):
        return runtime.player_api(world, 'native:government').plan(actor, text)

    def end_turn(world, actor, summary: str = ''):
        return runtime.player_api(world, 'native:government').end_turn(actor, summary)

    def government(world, actor):
        return runtime.player_api(world, 'native:government').snapshot(actor)

    return (
        Tool('plan', plan, owner='native:government', api=('plan',),
             description='提交持久国策：写未来多回合的经济、军事、情报与外交战略，明确目标、优先级、资源约束和调整条件，依据事实而非编造。不是本回合流水账。到期或战略变化时修订，已有有效国策无需反复提交。'),
        Tool('end_turn', end_turn, owner='native:government', api=('end_turn',),
             description='结束本国行动。AI 的 summary 只用一两句概括本回合实际执行的结果与关键待结算事项；详细行动已有回执，不重复国策、逐格清单或下回合战略。AI 必须先有有效国策；正式绑定的真人可省略 summary、无需国策。结束成功后，未回应的已收到外交提议提交自动拒绝、回合末生效，开放的联盟投票未表态则自动反对。'),
        Tool('government', government, read_only=True, fact=True, panel=True,
             owner='native:government', api=('snapshot',),
             description='查看本国有效国策、到期周期、结束状态与回合小结；无需参数。'),
    )


def _government(data, args, actor, ctx):
    plan = data.get('plan')
    period = data['period']
    lines = [ctx.text('tool.government.head', period=period,
                      status=ctx.text('tool.government.ended' if data['ended'] else 'tool.government.acting'))]
    if plan:
        lines.append(ctx.text('tool.government.plan_head', start=plan['period'], end=plan['valid_until'],
                              status=ctx.text('tool.government.expired' if plan['valid_until'] < period else 'tool.government.valid')))
        lines.append(plan['text'])
    else:
        lines.append(ctx.text('tool.government.no_plan'))
    return '\n'.join(lines)


def _plan(args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, dict):
        return None
    body = data.get('text', args.get('text', ''))
    if 'approved' in data:
        if data['approved']:
            return ctx.text('tool.plan.approved', body=body)
        return ctx.text('tool.plan.rejected', reason=data.get('reason', receipt.get('message', '')), body=body)
    until = ctx.text('tool.plan.until', period=data['valid_until']) if 'valid_until' in data else ''
    return ctx.text('tool.plan.created', until=until, body=data.get('text', args.get('text', '')))


def _end_turn(args, receipt, actor, ctx):
    if not receipt.get('ok'):
        return None  # 失败交给通用错误渲染，不能把提交的小结当作成功回执。
    data = receipt.get('data')
    text = data.get('text', args.get('summary', '')) if isinstance(data, dict) else args.get('summary', '')
    result = ctx.text('tool.end_turn.summary', text=text)
    rejected = data.get('auto_rejected', {}) if isinstance(data, dict) else {}
    for kind in ('offers', 'votes'):
        if rejected.get(kind):
            result += '\n' + ctx.text(f'tool.end_turn.auto_{kind}', ids='、'.join('#' + str(i) for i in rejected[kind]))
    automatic=data.get('auto_trades',{}) if isinstance(data,dict) else {}
    for order in automatic.get('orders',[]):
        result+='\n自动交易 '+ctx.label('content',order['resource'])+' '+('买入' if order['side']=='buy' else '卖出')+f' {order["quantity"]}/{order["requested"]}'
        result+=f'；实际金币 {order["gold"]}' if order['quantity'] else '；'+order.get('reason','未成交')
    return result


PANELS = {'government': _government}
RECEIPTS = {'plan': _plan, 'end_turn': _end_turn}
CAPTIONS = {
    'plan': lambda args, actor, receipt, ctx: ctx.text('tool.caption.plan'),
    'end_turn': lambda args, actor, receipt, ctx: ctx.text('tool.caption.end_turn'),
    'government': lambda args, actor, receipt, ctx: ctx.text('tool.caption.government'),
}
