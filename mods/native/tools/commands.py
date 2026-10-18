"""Model-facing command umbrellas over tool-layer ActionSpec records.

Actions are not engine Tools: the engine registers only their name/capability/
method/read_only/groups so the umbrella can dispatch through
``Runtime.call_action``. Each Action keeps its own schema, policy gate, adapter
and receipt; the umbrella exposes ``action`` plus the union of every action's
arguments. Scripts and the rule AI never use these names — they call the player
API (``call_api``).
"""
from copy import deepcopy

from engine.runtime import Action, Tool, unit_context


COMMANDS = (
    ('military', '军队行动',
     ('recruit', 'move', 'attack', 'retreat', 'disband')),
    ('economy', '经济行动',
     ('buy', 'sell', 'borrow', 'buy_report')),
    ('relations', '外交行动',
     ('guarantee', 'cancel_guarantee', 'propose_defense', 'break_defense',
      'accept_proposal', 'reject_proposal', 'bloc_found', 'bloc_join', 'bloc_leave',
      'bloc_dissolve', 'bloc_transfer', 'bloc_rename', 'declare_war', 'offer_peace',
      'accept_peace', 'reject_peace', 'vote', 'propose_vassalage','release_vassal','independence_war','submit_vassalage')),
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
        types = []
        for branch in prop['anyOf']:
            types.append(type(None) if branch.get('type') == 'null' else _python_type(branch))
        if not types:
            return object
        result = types[0]
        for extra in types[1:]:
            try:
                result = result | extra
            except TypeError:
                return object
        return result
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
    lines, branches = [], []
    for label, spec in actions:
        params = spec.schema()['function']['parameters']
        for key, prop in params.get('properties', {}).items():
            if key == 'action':
                raise ValueError(f'动作 {spec.name} 已有 action 参数，不能并入 {name}')
            _merge_properties(properties, prop, shared, key)
            types.setdefault(key, _python_type(prop))
        content.update(spec.content_parameters)
        required = list(params.get('required') or ())
        suffix = '（需 ' + '、'.join(required) + '）' if required else ''
        lines.append(label + suffix + '：' + spec.description)
        # 伞工具只有一个 action 必填；每个动作真正需要的参数放在分支里，
        # 让模型在第一次调用时就看到必填项，而不是等内层拒绝再补。
        branch = {'properties': {'action': {'enum': [label]}},
                  'required': list(dict.fromkeys(('action', *required)))}
        for key in ('oneOf', 'anyOf', 'allOf'):
            if key in params:
                branch[key] = deepcopy(params[key])
        branches.append(branch)
    properties['action'] = {'type': 'string', 'enum': [label for label, _ in actions],
                            'description': '\n'.join(lines)}
    mapping = {label: spec.name for label, spec in actions}
    specs = {spec.name: spec for _, spec in actions}

    def handler(world, actor, **arguments):
        label = arguments.pop('action', None)
        target = mapping.get(label)
        if target is None:
            raise ValueError('未知动作：' + str(label) + '；可用：' + '、'.join(mapping))
        # Leave the umbrella's owner context: Runtime.call re-checks the inner
        # action's gate before entering its own unit context, and that gate may
        # legitimately belong to another module.
        spec = specs[target]
        with unit_context(None):
            if isinstance(spec, Action):
                return runtime.call_action(world, actor, spec.name, arguments)
            return runtime.call(world, actor, target, arguments)

    def schema_for_actor(world, actor, schema):
        params = schema['function']['parameters']
        allowed = [label for label, spec in actions
                   if (runtime.action_available(world, actor, spec.name)
                       if isinstance(spec, Action) else runtime.tool_available(world, actor, spec.name))]
        params['properties']['action']['enum'] = allowed
        params['oneOf'] = [branch for branch in params.get('oneOf', ())
                           if branch['properties']['action']['enum'][0] in allowed]
        for _, spec in actions:
            if spec.schema_for_actor is None:
                continue
            customized = spec.schema_for_actor(world, actor, spec.schema())
            for key, prop in customized['function']['parameters'].get('properties', {}).items():
                if key in params['properties'] and key not in shared:
                    params['properties'][key] = deepcopy(prop)
        return schema

    owner = actions[0][1].owner
    return Tool(name, handler, owner=owner,
                parameters={'type': 'object', 'properties': properties,
                            'required': ['action'], 'oneOf': branches,
                            'additionalProperties': False},
                argument_types=types, content_parameters=tuple(sorted(content)),
                description=summary, schema_for_actor=schema_for_actor)


def wrap(runtime, tools):
    """Split ActionSpec records out of the engine tool table and add umbrellas.

    Returns ``(visible_tools, actions, commands)``. Actions are registered by
    the runtime as API methods, not Tools; read-only Tools named by an umbrella
    (e.g. script_status) stay registered as facts/panels and are dispatched
    through ``Runtime.call``.
    """
    specs = {tool.name: tool for tool in tools
             if isinstance(tool, Action) or not tool.read_only}
    umbrellas = []
    commands = {}
    for name, summary, labels in COMMANDS:
        actions = [(label, specs[label]) for label in labels if label in specs]
        umbrella = action_tool(runtime, name, SUMMARY[name] + summary, actions)
        if umbrella is None:
            continue
        umbrellas.append(umbrella)
        commands[name] = {label: spec.name for label, spec in actions}
    visible = [umbrella for umbrella in umbrellas]
    visible.extend(tool for tool in tools if not isinstance(tool, Action))
    actions = [tool for tool in tools if isinstance(tool, Action)]
    covered = {label for _, _, labels in COMMANDS for label in labels if label in specs}
    missing = sorted(spec.name for spec in actions if spec.name not in covered)
    if missing:
        raise ValueError('动作未归入任何伞工具：' + '、'.join(missing))
    return visible, actions, commands
