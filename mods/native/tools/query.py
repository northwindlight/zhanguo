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
        return runtime.call(world, actor, target.name, arguments)

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
