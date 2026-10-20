"""Panel names, arguments and constraints all come from the panel tools."""
from copy import deepcopy
from engine.runtime import Tool

def _parameters(runtime, panels, world=None, actor=None):
    properties, branches, names = {}, [], []
    for name, tool in panels.items():
        if world is not None and not runtime.tool_available(world, actor, tool.name):continue
        schema = tool.schema()
        if world is not None and tool.schema_for_actor:
            schema = tool.schema_for_actor(world, actor, schema)
        branch = deepcopy(schema['function']['parameters'])
        for key in tool.content_parameters:
            prop = branch.get('properties', {}).get(key, {})
            if 'enum' in prop:prop['enum'] = [runtime.content.key(k) for k in prop['enum']]
        for key, prop in branch.get('properties', {}).items():
            if key not in properties:
                properties[key] = deepcopy(prop)
            elif properties[key] != prop:
                current = properties[key]
                variants = current.get('anyOf', [current])
                if prop not in variants:properties[key] = {'anyOf': [*variants, deepcopy(prop)]}
        branch.setdefault('properties', {})['panel'] = {'enum': [name]}
        branch['required'] = list(dict.fromkeys(('panel', *branch.get('required', ()))))
        # 顶层是 panel 参数的并集；分支不再用更严的 additionalProperties
        # 与顶层自相矛盾。运行时只校验顶层，query() 再按目标 panel 过滤。
        branch.pop('additionalProperties', None)
        branches.append(branch)
        names.append(name)
    properties['panel'] = {'type': 'string', 'enum': names,
                          'description': '\n'.join(name + '：' + panels[name].description for name in names)}
    return {'type': 'object', 'properties': properties, 'required': ['panel'],
            'oneOf': branches, 'additionalProperties': False}


def build(runtime, specs):
    panels = {('map' if tool.name == 'view' else tool.name): tool for tool in specs if tool.panel}

    def query(world, actor, **arguments):
        panel = arguments.pop('panel', None)
        target = panels.get(panel)
        if target is None:
            raise ValueError('未知面板：' + str(panel) + '；可用：' + '、'.join(sorted(panels)))
        schema = target.schema()
        if target.schema_for_actor:
            schema = target.schema_for_actor(world, actor, schema)
        accepted = set(schema['function']['parameters'].get('properties') or ())
        # 并集广告、按选中 panel 过滤：不是该 panel 的参数静默丢弃；
        # 顶层 truly unknown 参数仍由 query 自己的 schema 拒绝。
        forwarded = {key: value for key, value in arguments.items() if key in accepted}
        return runtime.call(world, actor, target.name, forwarded)

    def schema_for_actor(world, actor, schema):
        schema['function']['parameters'] = _parameters(runtime, panels, world, actor)
        return schema

    return Tool('query', query, owner='native:query', read_only=True,
                parameters=_parameters(runtime, panels), argument_types={'panel': str}, schema_for_actor=schema_for_actor,
                description='只读查询统一入口：panel 选面板，其余参数、必填项和说明见对应分支。'
                            '已有资料无需重复查询；唯一按单格查询的是 tile。')


RECEIPTS = {}
CAPTIONS = {'query': lambda args, actor, receipt, ctx: ctx.text('tool.caption.query')}
PANELS = {}
