"""Experimental tool layer: native tools plus the parliament-only plan explanation."""
from dataclasses import replace

from engine.runtime import Tool
from mods.native.tools import build as native_build
from mods.native.tools import commands as command_layer


def build(runtime):
    layer = native_build(runtime)

    def explain_plan(world, actor, text: str):
        return runtime.player_api(world, 'native:government').explain(actor, text)

    def explain_receipt(args, receipt, actor, ctx):
        data = receipt.get('data') or {}
        return data.get('text') or receipt.get('message') or ctx.format(data)

    tools = [Tool('explain_plan', explain_plan, owner='native:government', api=('explain',),
                  description='议会拒绝国策后说明原因和修正打算，text 为解释正文。解释后方可结束本期；解释不替代新版国策，不会触发本期第二次审核。')]
    receipts = {'explain_plan': explain_receipt}
    captions = {'explain_plan': lambda args, actor, receipt, ctx: ctx.text('tool.caption.explain_plan')}

    if 'sandbox:programs' in runtime.capabilities:
        def script_write(world, actor, source: str):
            return runtime.player_api(world, 'sandbox:programs').write(actor, source)

        def script_patch(world, actor, old: str, new: str):
            return runtime.player_api(world, 'sandbox:programs').patch(actor, old, new)

        def script_check(world, actor, source: str | None = None):
            return runtime.player_api(world, 'sandbox:programs').check(actor, source)

        def script_status(world, actor, include_source: bool = False):
            return runtime.player_api(world, 'sandbox:programs').status(actor, include_source)

        def script_run(world, actor, inputs: dict | None = None):
            return runtime.player_api(world, 'sandbox:programs').claim(actor, inputs)

        script_specs = (
            Tool('script_write', script_write, owner='sandbox:programs', api=('write',),
                 description='保存本国 Python 短脚本，不执行。运行时可用 api.query/observe/submit、inputs、print；适合整类查询、计算收益、按预算执行重复操作。源码可跨回合复用。例：stocks=api.query("econ")["stocks"]; print({s["id"]:s["amount"] for s in stocks})'),
            Tool('script_patch', script_patch, owner='sandbox:programs', api=('patch',),
                 description='精确替换已保存源码中的一处内容，不执行'),
            Tool('script_check', script_check, owner='sandbox:programs', api=('check',), read_only=True,
                 description='免费检查 Python 语法和安全规则，不执行、不消耗本回合脚本执行额度；保存后先检查'),
            Tool('script_status', script_status, owner='sandbox:programs', api=('status',), read_only=True, fact=True,
                 description='查询本国脚本、剩余脚本执行额度、预算及上次结果；include_source=true 查看源码'),
            Tool('script_run', script_run, owner='sandbox:programs', api=('claim',),
                 description='手动执行已保存 Python 脚本，inputs 为本回合 JSON 参数。每国每回合有一次脚本执行额度，计算与只读脚本也消耗该额度；失败只消耗脚本执行额度，不消耗普通行动机会，之后仍可调用国家工具。成功动作保留。返回 print 输出、执行统计及错误位置。api 使用标准事实结构，资源/兵种参数接受唯一短键值；无需每回合重发源码。'),
        )
        umbrella = command_layer.action_tool(
            runtime, 'script',
            '本国脚本：action=write 保存源码、patch 精确替换一处、check 免费查语法、status 查状态、run 执行。脚本在沙盒内用 api.query/observe/submit、inputs、print；每回合一次执行额度。',
            (('write', script_specs[0]), ('patch', script_specs[1]), ('check', script_specs[2]),
             ('status', script_specs[3]), ('run', script_specs[4])))
        command_map = {'script': {'write': 'script_write', 'patch': 'script_patch',
                                  'check': 'script_check', 'status': 'script_status', 'run': 'script_run'}}
        tools.append(umbrella)
        tools.extend(replace(spec, hidden=True) for spec in script_specs)
        receipts.update({
            'script_write': _write_receipt,
            'script_patch': _patch_receipt,
            'script_check': lambda args, receipt, actor, ctx: ctx.format(receipt.get('data')),
            'script_status': lambda args, receipt, actor, ctx: ctx.format(receipt.get('data')),
            'script_run': _run_receipt,
        })
        captions.update({name: (lambda args, actor, receipt, ctx, name=name: ctx.text('tool.caption.' + name))
                         for name in ('script_write', 'script_patch', 'script_check', 'script_status', 'script_run')})
    else:
        command_map = {}
    return layer.extend(tools=tools, receipts=receipts, captions=captions, commands=command_map)


def _write_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return ctx.text('tool.script.write', revision=data.get('revision', '?'), source=args.get('source', ''))


def _patch_receipt(args, receipt, actor, ctx):
    data = receipt.get('data') or {}
    return ctx.text('tool.script.patch', revision=data.get('revision', '?'))


def _run_receipt(args, receipt, actor, ctx):
    data = receipt.get('data')
    if not isinstance(data, dict) or 'steps' not in data:
        return None
    result = ctx.text('tool.script.run', status=ctx.label('status', data['status']),
                      steps=data['steps'], calls=data['calls'], actions=data['actions'])
    error = data.get('error')
    if error:
        line = error.get('line')
        result += '\n' + ctx.text('tool.script.error', line=f'第{line}行：' if line else '', message=error['message'])
    if data.get('output'):
        result += '\n' + data['output']
    return result
