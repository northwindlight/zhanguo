"""Model-facing command umbrellas.

Each action keeps its own registered tool — schema, policy gate, owner, receipt
and hidden name. The umbrella is only the model's entry point: it exposes
`action` plus the union of every action's arguments and forwards to the action
tool through ``Runtime.call``, so gating and receipts stay per action.

Hidden action names remain callable by name (scripts, rule AI, tests); they are
simply not part of the model tool table.
"""
from copy import deepcopy

from engine.runtime import Tool, unit_context


COMMANDS = (
    ('military', '军队行动',
     ('recruit', 'move', 'attack', 'retreat', 'disband')),
    ('economy', '经济行动',
     ('buy', 'sell', 'borrow', 'buy_report')),
    ('relations', '外交行动',
     ('guarantee', 'cancel_guarantee', 'propose_defense', 'break_defense',
      'accept_proposal', 'reject_proposal', 'bloc_found', 'bloc_join', 'bloc_leave',
      'bloc_dissolve', 'bloc_transfer', 'bloc_rename', 'declare_war', 'offer_peace',
      'accept_peace', 'reject_peace', 'vote')),
    ('intelligence', '对外通信与谍报',
     ('send_letter', 'gift', 'share_map', 'spy')),
)

SUMMARY = {
    'military': '军队命令：action 选动作，其余参数按该动作需要。征召材料、军队编号、路径与战斗信息用 query 查（army/unit_catalog/reachable/battle）。',
    'economy': '经济命令：action 选动作，其余参数按该动作需要。行情用 query(panel=market)，库存与报表用 query(panel=econ/report)。',
    'relations': '外交命令：action 选动作，其余参数按该动作需要。当前联盟、条约、战争、提议编号与投票用 query(panel=foreign) 查。',
    'intelligence': '对外通信与谍报：action 选动作，其余参数按该动作需要。各国 id 用 query(panel=foreign) 查，结果在 intel 面板。',
}


def _python_type(prop):
    if not isinstance(prop, dict):
        return object
    if 'anyOf' in prop:
        branch = next((row for row in prop['anyOf'] if row.get('type') not in (None, 'null')), None)
        return _python_type(branch) if branch else object
    return {'array': list, 'integer': int, 'number': float, 'boolean': bool,
            'string': str, 'object': dict}.get(prop.get('type'), object)


def _merge_properties(target, prop, shared, key):
    if key not in target:
        target[key] = deepcopy(prop)
        return
    if target[key] == prop:
        return
    # Same name, different meaning across actions: keep the first and do not let
    # per-actor customisation of one action overwrite the other.
    shared.add(key)
    first, second = target[key], prop
    if 'enum' in first and 'enum' in second:
        first['enum'] = list(dict.fromkeys([*first['enum'], *second['enum']]))


def action_tool(runtime, name, summary, actions):
    """Build one visible umbrella from ``((label, tool), ...)``; None if empty."""
    actions = tuple(actions)
    if not actions:
        return None
    properties, types, content, shared = {}, {'action': str}, set(), set()
    for _, spec in actions:
        params = spec.schema()['function']['parameters']
        for key, prop in params.get('properties', {}).items():
            if key == 'action':
                raise ValueError(f'动作 {spec.name} 已有 action 参数，不能并入 {name}')
            _merge_properties(properties, prop, shared, key)
            types.setdefault(key, _python_type(prop))
        content.update(spec.content_parameters)
    lines = []
    for label, spec in actions:
        required = spec.schema()['function']['parameters'].get('required') or ()
        suffix = '（需 ' + '、'.join(required) + '）' if required else ''
        lines.append(label + suffix + '：' + spec.description)
    properties['action'] = {'type': 'string', 'enum': [label for label, _ in actions],
                            'description': '\n'.join(lines)}
    mapping = {label: spec.name for label, spec in actions}

    def handler(world, actor, **arguments):
        label = arguments.pop('action', None)
        target = mapping.get(label)
        if target is None:
            raise ValueError('未知动作：' + str(label) + '；可用：' + '、'.join(mapping))
        # Leave the umbrella's owner context: Runtime.call re-checks the inner
        # action's gate before entering its own unit context, and that gate may
        # legitimately belong to another module.
        with unit_context(None):
            return runtime.call(world, actor, target, arguments)

    def schema_for_actor(world, actor, schema):
        params = schema['function']['parameters']
        allowed = [label for label, spec in actions if runtime.tool_available(world, actor, spec.name)]
        params['properties']['action']['enum'] = allowed
        for _, spec in actions:
            if spec.schema_for_actor is None:
                continue
            customized = spec.schema_for_actor(world, actor, spec.schema())
            for key, prop in customized['function']['parameters'].get('properties', {}).items():
                if key in params['properties'] and key not in shared:
                    params['properties'][key] = deepcopy(prop)
        return schema

    owner = actions[0][1].owner
    # Coverage is checked per owning module; the hidden action tools already
    # cover their own module, so the umbrella only claims its owner's methods.
    api = tuple(dict.fromkeys(method for _, spec in actions if spec.owner == owner
                              for method in spec.api))
    return Tool(name, handler, owner=owner, api=api,
                parameters={'type': 'object', 'properties': properties,
                            'required': ['action'], 'additionalProperties': False},
                argument_types=types, content_parameters=tuple(sorted(content)),
                description=summary, schema_for_actor=schema_for_actor)


def wrap(runtime, tools):
    """Hide the listed action tools and prepend one umbrella per family.

    Returns ``(tools, commands)`` where ``commands`` maps umbrella name to
    ``{action label: tool name}``, so the tool layer can render the inner
    action's receipt (script labels differ from their tool names).
    """
    specs = {tool.name: tool for tool in tools if not tool.read_only}
    replaced = {}
    umbrellas = []
    commands = {}
    for name, summary, labels in COMMANDS:
        actions = [(label, specs[label]) for label in labels if label in specs]
        umbrella = action_tool(runtime, name, SUMMARY[name] + summary, actions)
        if umbrella is None:
            continue
        umbrellas.append(umbrella)
        for label, _ in actions:
            replaced[label] = True
        commands[name] = {label: spec.name for label, spec in actions}
    result = [umbrella for umbrella in umbrellas]
    result.extend(spec if spec.name not in replaced else _hidden(spec) for spec in tools)
    return result, commands


def _hidden(tool):
    from dataclasses import replace
    return replace(tool, hidden=True)
