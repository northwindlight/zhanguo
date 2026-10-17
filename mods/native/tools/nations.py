"""Nation toolcalls: public identities and the current nation's sites."""
from engine.runtime import Tool


def tools(runtime):
    def nations(world, actor):
        return runtime.player_api(world, 'native:nations').snapshot(actor)

    def nation(world, actor):
        return runtime.player_api(world, 'native:nations').overview(actor)

    return (
        Tool('nations', nations, read_only=True, fact=True, panel=True,
             owner='native:nations', api=('snapshot',),
             description='全部国家的公开身份与登场/灭亡记录；无需参数。'),
        Tool('nation', nation, read_only=True, fact=True, panel=True,
             owner='native:nations', api=('overview',),
             description='本国身份与当前拥有的站点；无需参数，站点明细见 building_sites。'),
    )


def _nations(data, args, actor, ctx):
    return '\n'.join(
        ctx.text('tool.nations.row', name=row['name'], identity=row['id'],
                 status=ctx.text('tool.nations.alive' if row['alive'] else 'tool.nations.extinct')) +
        ('｜' + row['extinction_reason'] if row.get('extinction_reason') else '')
        for row in data) or ctx.text('tool.nations.none')


def _nation(data, args, actor, ctx):
    nation = data['nation']
    sites = data.get('sites', [])
    return ctx.text('tool.nations.head', name=nation['name'], identity=nation['id'], count=len(sites)) + (
        '\n' + '、'.join(ctx.pos(s, actor=actor) for s in sites) if sites else '')


PANELS = {'nations': _nations, 'nation': _nation}
RECEIPTS = {}
CAPTIONS = {
    'nations': lambda args, actor, receipt, ctx: ctx.text('tool.caption.nations'),
    'nation': lambda args, actor, receipt, ctx: ctx.text('tool.caption.nation'),
}
