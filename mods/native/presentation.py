"""发行版的只读展示能力转口；实现由领域模块注册。"""
from .core import *
from .rules import *
from .ui_constants import *
_EXPORTS = {}
def register(exports):
    _EXPORTS.update(exports)
def __getattr__(name):
    if name == 'HUNS_DOCTRINE': return _EXPORTS['_huns_doctrine']()
    if name == 'HUNS_BLOCKED': return _EXPORTS.get('HUNS_BLOCKED', set())
    if name in _EXPORTS:
        from functools import wraps
        @wraps(_EXPORTS[name])
        def dispatch(*args, **kwargs):
            world = args[0] if args else None
            runtime = getattr(world, 'runtime', None)
            fn = runtime.presentations.get(name) if runtime else None
            return (fn or _EXPORTS[name])(*args, **kwargs)
        return dispatch
    raise AttributeError(name)

_SCHEMA_CACHE = {}
TOOL_SCHEMAS = []
BANK_TOOL_SCHEMAS = ()
BANK_TOOL_SCHEMA = BUY_REPORT_TOOL_SCHEMA = None
TOOL_SCHEMAS_TOKENS = 0

def assemble(runtime):
    global TOOL_SCHEMAS, BANK_TOOL_SCHEMAS, BANK_TOOL_SCHEMA, BUY_REPORT_TOOL_SCHEMA, TOOL_SCHEMAS_TOKENS, __all__
    import json
    schemas = runtime.schemas()
    TOOL_SCHEMAS = [s for s in schemas if s['function']['name'] not in ('loan','buy_report','expand','break_alliance')]
    BANK_TOOL_SCHEMAS = tuple(s for s in schemas if s['function']['name'] in ('loan','buy_report'))
    BANK_TOOL_SCHEMA = next((s for s in BANK_TOOL_SCHEMAS if s['function']['name']=='loan'),None)
    BUY_REPORT_TOOL_SCHEMA = next((s for s in BANK_TOOL_SCHEMAS if s['function']['name']=='buy_report'),None)
    TOOL_SCHEMAS_TOKENS = (len(json.dumps(TOOL_SCHEMAS,ensure_ascii=False)) + 3) // 4
    from . import core,rules,ui_constants
    __all__ = sorted(set(core.__all__) | set(rules.__all__) | set(ui_constants.__all__) | set(_EXPORTS) |
        {'TOOL_SCHEMAS','BANK_TOOL_SCHEMAS','BANK_TOOL_SCHEMA','BUY_REPORT_TOOL_SCHEMA','TOOL_SCHEMAS_TOKENS','tool_schemas','_SCHEMA_CACHE','HUNS_DOCTRINE','HUNS_BLOCKED'})

def tool_schemas(world, actor):
    import copy
    schemas = world.runtime.schemas()
    tag = world.polity.get(actor,'')
    if not tag and not world.bank_on() and schemas == [*TOOL_SCHEMAS, *BANK_TOOL_SCHEMAS]:
        return TOOL_SCHEMAS
    result = []
    for schema in schemas:
        name = schema['function']['name']
        if name in ('expand','break_alliance') or name in ('loan','buy_report') and not world.bank_on():continue
        if not world.runtime.tool_available(world,actor,name):continue
        schema=copy.deepcopy(schema)
        owner=world.runtime.tool_owners[name]
        result.append(owner.schema_for_actor(world,actor,schema))
    # 无政体、关行时保留稳定 schema 对象供前缀缓存。
    if not tag and not world.bank_on() and result == TOOL_SCHEMAS:return TOOL_SCHEMAS
    key=(id(world.runtime),tag,world.bank_on())
    return _SCHEMA_CACHE.setdefault(key,result)
