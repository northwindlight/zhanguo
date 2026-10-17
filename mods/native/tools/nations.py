"""Nation toolcalls: public identities, the current nation's sites, and the one
combined foreign-affairs panel (own nation + all nations + relations + treaties)."""
from engine.runtime import Tool

from .diplomacy import _diplomacy
from .intelligence import _countries


def tools(runtime):
    def nations(world, actor):
        return runtime.player_api(world, 'native:nations').snapshot(actor)

    def nation(world, actor):
        return runtime.player_api(world, 'native:nations').overview(actor)

    def foreign(world, actor):
        # 组合面板走已注册的只读工具，而不是在 nations 的上下文里直接调别家 API：
        # Runtime.call 会把每个子工具放进它自己的 owner 上下文，跨模块接口声明
        # 因此仍按各自模块校验。
        def read(name):
            return runtime.call(world, actor, name, {}) if name in runtime.tools else None
        data = {'nation': read('nation'), 'nations': read('nations'),
                'countries': read('countries'), 'diplomacy': read('diplomacy')}
        return {key: value for key, value in data.items() if value is not None}

    return (
        Tool('nations', nations, read_only=True,
             owner='native:nations', api=('snapshot',),
             description='全部国家的公开身份与登场/灭亡记录；无需参数。'),
        Tool('nation', nation, read_only=True,
             owner='native:nations', api=('overview',),
             description='本国身份与当前拥有的站点；无需参数，站点明细见 building_sites。'),
        Tool('foreign', foreign, read_only=True, fact=True, panel=True,
             owner='native:nations', api=('snapshot', 'overview'),
             description='本国与全部对外关系一次查完：本国身份与站点、各国身份与存续、关系/条约/接壤/来信、联盟与战争、待回应提议、投票与待结算命令。无需参数；代价是比单独查一块长。'),
    )


def _nations(data, args, actor, ctx):
    # 在世是常态，不逐行写「在世」；只有亡国才单独标记。
    lines = []
    for row in data:
        if row['alive']:
            lines.append(ctx.text('tool.nations.row_alive', name=row['name'], identity=row['id']))
        else:
            lines.append(ctx.text('tool.nations.row', name=row['name'], identity=row['id'],
                                  status=ctx.text('tool.nations.extinct')) +
                         ('｜' + row['extinction_reason'] if row.get('extinction_reason') else ''))
    return '\n'.join(lines) or ctx.text('tool.nations.none')


def _nation(data, args, actor, ctx):
    nation = data['nation']
    sites = data.get('sites', [])
    return ctx.text('tool.nations.head', name=nation['name'], identity=nation['id'], count=len(sites)) + (
        '\n' + '、'.join(ctx.pos(s, actor=actor) for s in sites) if sites else '')


def _foreign(data, args, actor, ctx):
    parts = []
    if data.get('nation'):
        parts.append(_nation(data['nation'], args, actor, ctx))
    if data.get('nations'):
        parts.append(_nations(data['nations'], args, actor, ctx))
    if data.get('countries'):
        parts.append(_countries(data['countries'], args, actor, ctx))
    if data.get('diplomacy'):
        parts.append(_diplomacy(data['diplomacy'], args, actor, ctx))
    return '\n\n'.join(parts)


PANELS = {'nations': _nations, 'nation': _nation, 'foreign': _foreign}
RECEIPTS = {}
CAPTIONS = {
    'nations': lambda args, actor, receipt, ctx: ctx.text('tool.caption.nations'),
    'nation': lambda args, actor, receipt, ctx: ctx.text('tool.caption.nation'),
    'foreign': lambda args, actor, receipt, ctx: ctx.text('tool.caption.foreign'),
}
