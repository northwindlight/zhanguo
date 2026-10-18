"""Nation toolcalls: public identities, the current nation's sites, and the one
combined foreign-affairs panel (own nation + all nations + relations + treaties)."""
from engine.runtime import Tool, unit_context

from .diplomacy import _diplomacy
from .intelligence import _countries


def tools(runtime):
    def foreign(world, actor):
        # 组合面板直接读各模块的玩家 API，不再为四块数据各注册一个工具。
        # 每块都在自己的模块上下文里读，跨模块接口声明仍按各模块校验。
        def read(capability, method):
            if capability not in runtime.capabilities:
                return None
            with unit_context(None):
                return getattr(runtime.player_api(world, capability), method)(actor)
        data = {'nation': read('native:nations', 'overview'),
                'nations': read('native:nations', 'snapshot'),
                'countries': read('native:intelligence', 'countries'),
                'diplomacy': read('native:diplomacy', 'snapshot')}
        return {key: value for key, value in data.items() if value is not None}

    return (
        Tool('foreign', foreign, read_only=True, fact=True, panel=True,
             owner='native:nations', api=('snapshot', 'overview'),
             covers=tuple((capability, method) for capability, method in
                          (('native:intelligence', 'countries'), ('native:diplomacy', 'snapshot'))
                          if capability in runtime.capabilities),
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
    'foreign': lambda args, actor, receipt, ctx: ctx.text('tool.caption.foreign'),
}
