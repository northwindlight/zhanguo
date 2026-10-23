"""从源码与 mod.toml 反射出 Mod 开发文档的 API 参考页。

文档站的内容页是手写的，**API 参考页是生成的**：跑一次这个脚本，
参考页就跟当前代码同步，不靠人肉抄字段。

    python3 scripts/api_doc.py            # 写出 docs/site/content/reference/*.md
    python3 scripts/api_doc.py --check    # 只检查是否与代码同步（CI 用）

字段说明取自源码里的行内注释与 docstring：注释写在字段上方就会被收进来。
"""
from __future__ import annotations

import argparse
import ast
import dataclasses
import inspect
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONTENT = ROOT / 'docs' / 'site' / 'content' / 'reference'

# 发行版入口：文件名 → 站点里的标题。
DISTRIBUTIONS = [
    ('mods.toml', 'native', 'native 发行版'),
    ('mods.experimental.toml', 'experimental', 'experimental 发行版'),
    ('mods.minigame.toml', 'minigame', 'minigame 开发模式'),
    ('mods.stock.toml', 'stock', 'stock 发行版（模拟炒股）'),
]

# 顶层目录名 → 模块目录（发行版清单在 mods/ 下，成员目录各不相同）。
DISTRIBUTION_MEMBERS = {
    'native': 'mods/native',
    'experimental': 'mods/experimental',
    'minigame': 'mods/examples/minigame',
    'stock': 'mods/stock',
}


# ── 源码注释抓取 ──────────────────────────────────────────────────────
def member_docs(path: Path, class_name: str) -> dict[str, str]:
    """取类里每个字段/方法上方的行内注释：注释就是它的说明。"""
    source = path.read_text(encoding='utf-8')
    lines = source.splitlines()
    tree = ast.parse(source)
    docs: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                name = item.target.id
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = item.name
            else:
                continue
            collected: list[str] = []
            index = item.lineno - 2
            while index >= 0:
                line = lines[index].strip()
                if line.startswith('#'):
                    collected.insert(0, line.lstrip('#').strip())
                    index -= 1
                else:
                    break
            if collected:
                docs[name] = ' '.join(collected)
    return docs


def dataclass_fields(cls, path: Path) -> list[dict]:
    """数据类字段：名字、类型、默认值、源码注释。"""
    docs = member_docs(path, cls.__name__)
    rows = []
    for item in dataclasses.fields(cls):
        default = item.default if item.default is not dataclasses.MISSING else None
        if item.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = 'factory'
        kind = 'list' if str(item.type).startswith('tuple[str') else str(item.type)
        rows.append({'name': item.name, 'type': kind, 'default': default,
                     'doc': docs.get(item.name, '')})
    return rows


def public_methods(cls, *, path: Path | None = None, skip: tuple[str, ...] = ()) -> list[dict]:
    """公开方法：签名、docstring 与源码行内注释。"""
    comments = member_docs(path, cls.__name__) if path else {}
    rows = []
    for name, member in sorted(vars(cls).items()):
        if name.startswith('_') or name in skip or not callable(member):
            continue
        try:
            signature = str(inspect.signature(member))
        except (TypeError, ValueError):
            signature = '(…)'
        doc = GAMEMOD_DOC.get(name) or inspect.getdoc(member) or comments.get(name, '')
        rows.append({'name': name, 'signature': f'{name}{signature}', 'doc': doc})
    return rows


# GameMod 的方法说明：源码里没有逐条注释，这里是模块作者真正要看的契约。
GAMEMOD_DOC = {
    'mixins': '必实现。返回混入 World 的类（Mixin）；Mixin 的方法会变成世界对象的属性。',
    'tools': '返回本模块暴露给模型的工具（Tool）。工具要写清 description：模型只看得见它。',
    'player_api': '返回玩家 API 对象；工具层通过它调用动作与只读事实。',
    'schema_for_actor': '按玩家改写工具 schema（比如按权限隐藏参数）。',
    'stages': '返回结算阶段（Stage）；阶段挂在发行版声明的挂点上。',
    'state_keys': '本模块独占的状态键；别人要写必须先在本模块的 State.Requests 里申请。',
    'needs': '本模块用到的其他能力名；能力名就是「模块ID:能力名」。',
    'optional': '可选依赖能力名：对方不在也不报错。',
    'provides': '本模块提供的能力名。',
    'numbers': '本模块提供的数值；规则正文里的 {{number:…}} 从这里取，改参数规则正文就跟着变。',
    'presentations': '本模块提供的呈现部件（面板、回执渲染等）。',
    'objects': '返回「能力名 → 对象」的映射；能力方法就在这些对象上。',
    'content_definitions': '返回「内容 ID → 定义」的映射。注册过的内容才有短键与显示名。',
    'apply_state_change': '替换状态对象前的钩子；默认检查写入所有权。',
    'before_tool': '工具执行前的钩子：校验参数、限流、记账。',
    'after_tool': '工具执行后的钩子：改写结果或记日志。',
    'configure': '装载时读声明文件；不要在这里做重活。',
    'rule_sections': '返回规则正文节（RuleSection）；模型用 player_rules 按主题查。',
    'initialize_world': '建世界对象：把状态挂到 world 上。',
    'save_state': '导出本模块的存档状态。',
    'load_state': '读回本模块的存档状态。',
}


# ── mod.toml 字段：契约表 + 漂移检查 ──────────────────────────────────
MANIFEST_CONTRACT = {
    'Mod': [('Id', '必填', '模块唯一 ID；能力名就是「模块ID:能力名」'),
            ('Version', '必填', '模块版本；存档兼容按它判断'),
            ('Entry', '必填', '`模块:类名`，类要实现 GameMod'),
            ('Requires', '选填', '必须存在的依赖模块'),
            ('After', '选填', '结算顺序：本模块在这些模块之后'),
            ('Before', '选填', '结算顺序：本模块在这些模块之前'),
            ('Kind', '选填', '模块种类'),
            ('Aliases', '选填', '别名列表（政体查找用）')],
    'State': [('Owns', '选填', '本模块独占的状态键；别人要写必须先在 Requests 里申请'),
              ('Requests', '选填', '本模块要写别的模块的状态时，在这里申请')],
    'Capabilities': [('Provides', '选填', '本模块提供的能力名'),
                     ('Needs', '选填', '本模块用到的其他能力名'),
                     ('Optional', '选填', '可选依赖：对方不在也不报错')],
    'Interfaces': [('（能力名）', '选填', '用到的其他能力的方法名清单，形如 `"mini:army" = ["seed"]`')],
    'PlayerAPI': [('Methods', '选填', '本模块对玩家开放的方法'),
                  ('ReadOnly', '选填', '其中只读的方法'),
                  ('Actions', '选填', '方法名 → 动作名 的别名表'),
                  ('ContentParameters', '选填', '哪些参数是内容键值')],
    'Load': [('Packages', '必填', '发行版入口文件：模块目录列表')],
    'Distribution': [('Id', '必填', '发行版 ID'),
                     ('Version', '必填', '发行版版本'),
                     ('Members', '必填', '成员模块清单（相对发行版目录的路径）'),
                     ('RequiredState', '选填', '必须有人拥有的状态键'),
                     ('ActorCollection', '选填', '玩家集合在世界上的属性名'),
                     ('ActionGate', '选填', '行动资格：能力名 + 方法名'),
                     ('PlayerClock', '选填', '回合时钟：能力名 + 方法名'),
                     ('PlayerEvents', '选填', '玩家事件来源：只读工具名'),
                     ('PlayerState', '选填', '每回合自动发给模型的事实：只读工具名列表'),
                     ('PlayerStateInclude', '选填', '额外并入 PlayerState 的面板'),
                     ('PlayerStateExclude', '选填', '从 PlayerState 里排除的面板'),
                     ('ReadOnlyPanel', '选填', '只读面板名列表'),
                     ('ManualOrder', '选填', '规则正文的章节顺序'),
                     ('Scenarios', '选填', '剧本声明'),
                     ('Tools', '选填', '工具层：`Entry` 指向工具层入口'),
                     ('Session', '必填', '会话：`Entry` 指向发行版的会话类'),
                     ('Configuration', '选填', '配置归一化：`Entry` 指向函数'),
                     ('ClientFactories', '选填', '客户端工厂：模型客户端、规则客户端等')],
    'Hooks': [('Id', '必填', '挂点 ID'),
              ('Name', '选填', '挂点显示名'),
              ('Description', '选填', '挂点说明')],
    'Distribution.Tools': [('Entry', '必填', '工具层入口：`模块:函数`'),
                           ('Version', '选填', '工具层版本')],
    'Distribution.ClientFactories.<名字>': [('Entry', '选填', '客户端类：`模块:类名`'),
                                            ('Configuration', '选填', '该客户端的配置归一化函数'),
                                            ('Services', '选填', '授予该客户端的能力方法')],
    'Distribution.Session': [('Entry', '必填', '会话类：`模块:类名`'),
                             ('Services', '选填', '会话服务（议会、脚本等）'),
                             ('Observer', '选填', '观察者能力：记录、广播、送信')],
    'Distribution.Configuration': [('Entry', '必填', '配置归一化函数')],
}


def manifest_drift() -> list[str]:
    """装载器读了、但契约表里没有的字段——有就说明契约表该更新了。"""
    source = (ROOT / 'engine' / 'runtime.py').read_text(encoding='utf-8')
    known = {key for fields in MANIFEST_CONTRACT.values() for key, _, _ in fields}
    known |= set(MANIFEST_CONTRACT)
    used = set(re.findall(r"declaration(?:\.get\(|\[)(?:'Mod'\])?\.?(?:get\()?['\"]([A-Za-z]+)['\"]", source))
    used |= set(re.findall(r"declaration\['Mod'\]\.get\('([A-Za-z]+)'", source))
    return sorted(used - known - {'Policy'})


# ── 模型面向的事实词汇 ────────────────────────────────────────────────
def fact_vocabulary() -> list[dict]:
    """模型面向层实际读取的事实名：从简报与仪表盘源码里扫出来。"""
    rows: dict[str, set[str]] = {}
    for relative in ('presentation/briefing.py', 'clients/model_view.py'):
        source = (ROOT / relative).read_text(encoding='utf-8')
        for name in re.findall(r"facts\.get\('([a-z_]+)'", source):
            rows.setdefault(name, set()).add(relative)
        for name in re.findall(r"\(\s*facts\.get\('([a-z_]+)'\)\s*or\s*\{\}\)", source):
            rows.setdefault(name, set()).add(relative)
    return [{'name': name, 'read_by': sorted(where)} for name, where in sorted(rows.items())]


def builtin_contract() -> list[dict]:
    """引擎内建工具：任何发行版都有，模型直接可调。"""
    from engine.player import BUILTINS
    return [{'name': name, 'description': description,
             'parameters': sorted(parameters)} for name, (description, parameters) in BUILTINS.items()]


# ── 发行版反射 ────────────────────────────────────────────────────────
def reflect_distribution(entry: str) -> dict:
    """装起一个发行版，把模块、工具、阶段、规则节都反射出来。"""
    from engine.runtime import Runtime
    runtime = Runtime.load(ROOT / entry)
    modules = []
    for name in runtime.order:
        unit = runtime.units[name]
        mod = unit.mod
        modules.append({
            'id': name,
            'entry': mod.declaration.get('Mod', {}).get('Entry', ''),
            'requires': list(unit.requires),
            'after': list(unit.after),
            'before': list(unit.before),
            'state_keys': list(mod.state_keys()),
            'provides': list(mod.provides()),
            'needs': list(mod.needs()),
            'rules': [{'title': section.title, 'body': section.body,
                       'system': getattr(section, 'system', False)}
                      for section in mod.rule_sections(None)],
        })
    # tool_owners 存的是模块对象；反查回模块 ID，别把对象 repr 写进参考页。
    owner_ids = {id(runtime.units[unit].mod): unit for unit in runtime.order}
    tools = []
    for name, tool in runtime.tools.items():
        tools.append({
            'name': name,
            'owner': owner_ids.get(id(runtime.tool_owners.get(name)), ''),
            'description': tool.description,
            'read_only': tool.read_only,
            'fact': tool.fact,
            'panel': tool.panel,
            'hidden': tool.hidden,
            'parameters': tool.parameters or {},
        })
    stages = []
    for name, stage in runtime.stage_map.items():
        stages.append({
            'name': name, 'hook': stage.hook, 'title': stage.title,
            'description': stage.description, 'requires': list(stage.requires),
            'after': list(stage.after), 'before': list(stage.before),
            'provides': list(stage.provides), 'needs': list(stage.needs),
        })
    return {
        'entry': entry,
        'distribution': runtime.distribution,
        'hooks': list(runtime.hooks),
        'modules': modules,
        'tools': sorted(tools, key=lambda row: row['name']),
        'stages': sorted(stages, key=lambda row: (row['hook'], row['name'])),
        'content': sorted(runtime.content.manifest()),
    }


# ── 页面渲染 ──────────────────────────────────────────────────────────
def table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '_（无）_\n'
    out = ['| ' + ' | '.join(headers) + ' |',
           '|' + '|'.join('---' for _ in headers) + '|']
    for row in rows:
        out.append('| ' + ' | '.join(str(cell).replace('|', '\\|').replace('\n', ' ') for cell in row) + ' |')
    return '\n'.join(out) + '\n'


def page(title: str, summary: str, body: str) -> str:
    return f'---\ntitle: {title}\nsection: API 参考\nsummary: {summary}\n---\n\n{body}'


def engine_page() -> str:
    from engine import player as player_module
    from engine import runtime as runtime_module
    from engine.player import PlayerSession
    from engine.runtime import GameMod, RuleSection, Runtime, Stage, Tool

    runtime_path = ROOT / 'engine' / 'runtime.py'
    body = ['# 引擎 API\n',
            '引擎是发行版之上的通用运行时：装 Mod、跑回合、存档、给模型发工具。\n',
            '## GameMod：模块要实现的东西\n',
            '`mixins` 必实现；其余按需覆写。说明取自源码里的行内注释。\n',
            table(['方法', '签名', '说明'],
                  [[row['name'], f"`{row['signature']}`", row['doc'].split('\n')[0]]
                   for row in public_methods(GameMod, path=runtime_path, skip=('configure',))]),
            '\n## Tool：暴露给模型的工具\n',
            table(['字段', '类型', '默认', '说明'],
                  [[f"`{row['name']}`", row['type'], row['default'], row['doc']]
                   for row in dataclass_fields(Tool, runtime_path)]),
            '\n## Stage：结算阶段\n',
            table(['字段', '类型', '默认', '说明'],
                  [[f"`{row['name']}`", row['type'], row['default'], row['doc']]
                   for row in dataclass_fields(Stage, runtime_path)]),
            '\n## RuleSection：规则正文\n',
            table(['字段', '类型', '默认', '说明'],
                  [[f"`{row['name']}`", row['type'], row['default'], row['doc']]
                   for row in dataclass_fields(RuleSection, runtime_path)]),
            '\n## PlayerSession：模型能调的玩家 API\n',
            table(['方法', '签名', '说明'],
                  [[row['name'], f"`{row['signature']}`", row['doc'].split('\n')[0]]
                   for row in public_methods(PlayerSession, path=ROOT / 'engine' / 'player.py')]),
            '\n## Runtime：运行时\n',
            table(['方法', '签名', '说明'],
                  [[row['name'], f"`{row['signature']}`", row['doc'].split('\n')[0]]
                   for row in public_methods(Runtime, path=runtime_path)]),
            '\n## 配置层\n',
            '配置层（`engine/config.py`）声明每个字段的类型、取值范围与是否必填：\n',
            table(['名字', '说明'],
                  [['`Field`', '一个配置字段：类型元组、choices、minimum/maximum、nullable'],
                   ['`Schema`', '一组字段的集合；`normalize` 会校验并保留未声明的键'],
                   ['`component`', '调用发行版自己声明的归一化函数'],
                   ['`prepare`', '把整份配置按发行版契约归一化']]),
            ]
    return page('引擎 API', '运行时、模块基类、工具与阶段、玩家 API。', '\n'.join(body))


def manifest_page() -> str:
    body = ['# 模块声明（mod.toml）\n',
            '每个模块一个 `mod.toml`，声明它是谁、依赖谁、拥有哪些状态与能力。\n',
            '发行版入口文件（`mods.toml`）用 `[Load]` 声明装哪些模块。\n']
    for section, fields in MANIFEST_CONTRACT.items():
        body.append(f'\n## [{section}]\n')
        body.append(table(['字段', '必填', '说明'],
                          [[f"`{key}`", required, doc] for key, required, doc in fields]))
    body.append('\n## 模块自己的表\n')
    body.append('模块可以在自己的 `mod.toml` 里加任意表（`[Bank]`、`[Policy]`、`[Limits]` 等），'
                '这些表原样挂在 `mod.declaration` 上，模块自己读。引擎只认识上面这些通用段。\n')
    drift = manifest_drift()
    body.append('\n## 装载器读到的其它字段\n')
    if drift:
        body.append('以下字段装载器会读，但契约表里还没写说明（新增字段时补上）：\n')
        body.append(table(['字段'], [[f"`{name}`"] for name in drift]))
    else:
        body.append('装载器读到的字段都在上面的契约表里。\n')
    return page('模块声明（mod.toml）', '模块清单的全部字段与约定。', '\n'.join(body))


def distribution_page() -> str:
    body = ['# 发行版清单\n',
            '发行版是「装哪些模块、按什么顺序、给模型看什么」的总装配。\n']
    for entry, key, title in DISTRIBUTIONS:
        data = reflect_distribution(entry)
        body.append(f'\n## {title}（`{entry}`）\n')
        body.append(f'模块 {len(data["modules"])} 个 · 工具 {len(data["tools"])} 个 · '
                    f'阶段 {len(data["stages"])} 个 · 挂点 {len(data["hooks"])} 个\n')
        body.append('\n### 清单字段\n')
        body.append(table(['字段', '值'], [
            ['`Id`', data['distribution'].get('Id', '')],
            ['`Version`', data['distribution'].get('Version', '')],
            ['`Members`', '、'.join(data['distribution'].get('Members', []))],
            ['`RequiredState`', '、'.join(data['distribution'].get('RequiredState', []))],
            ['`ActorCollection`', data['distribution'].get('ActorCollection', '')],
            ['`ActionGate`', json.dumps(data['distribution'].get('ActionGate', {}), ensure_ascii=False)],
            ['`PlayerClock`', json.dumps(data['distribution'].get('PlayerClock', {}), ensure_ascii=False)],
            ['`PlayerState`', '、'.join(data['distribution'].get('PlayerState', []))],
            ['`PlayerEvents`', data['distribution'].get('PlayerEvents', '')],
        ]))
        body.append('\n### 模块顺序\n')
        body.append(table(['#', '模块', '状态键', '提供能力'],
                          [[index, f"`{row['id']}`", '、'.join(row['state_keys']),
                            '、'.join(row['provides'])]
                           for index, row in enumerate(data['modules'], 1)]))
        body.append('\n### 结算阶段\n')
        body.append(table(['阶段', '挂点', '说明'],
                          [[f"`{row['name']}`", row['hook'], row['description'] or row['title']]
                           for row in data['stages']]))
    return page('发行版清单', '发行版的字段、模块顺序与结算阶段。', '\n'.join(body))


def model_contract_page() -> str:
    body = ['# 模型面向契约\n',
            '模型客户端不认识任何发行版：它只按这份契约办事。\n',
            '发行版只要满足这份契约，同一个模型客户端就能玩它。\n',
            '## 引擎内建工具\n',
            '任何发行版都有，模型直接可调：\n',
            table(['工具', '说明', '参数'],
                  [[f"`{row['name']}`", row['description'], '、'.join(row['parameters']) or '—']
                   for row in builtin_contract()]),
            '\n## 事实词汇\n',
            '模型面向的简报与仪表盘按这些事实名取数；发行版给哪些，简报就出哪些节。\n',
            table(['事实名', '被谁读取'],
                  [[f"`{row['name']}`", '、'.join(row['read_by'])] for row in fact_vocabulary()]),
            '\n## 回合小结契约\n',
            '`end_turn` 必须接收 `summary`，并在回执数据里返回 `text`：\n',
            '```\nend_turn(summary="本期建了兵营并宣战")\n→ data.text = "本期建了兵营并宣战"\n```\n',
            '\n模型客户端会把这个 `text` 存进本回合小结，作为下一回合的记忆。\n',
            '\n## 交战事实形状\n',
            '战争事实可以有三种写法，模型面向层都认：\n',
            '```\n{"attackers": ["a"], "defenders": ["b"]}\n{"parties": ["a", "b"]}\n["a", "b"]\n```\n',
            '\n## 工具描述就是给模型看的说明书\n',
            '工具的 `description` 直接进模型工具表：写清参数、约束与失败原因，'
            '模型才知道该传什么、什么时候会失败。\n']
    return page('模型面向契约', '模型客户端与发行版之间的通用约定。', '\n'.join(body))


def tools_page() -> str:
    body = ['# 工具表\n',
            '每个发行版实际暴露给模型的工具。模型看到的工具表就是这里这份。\n']
    for entry, key, title in DISTRIBUTIONS:
        data = reflect_distribution(entry)
        body.append(f'\n## {title}\n')
        body.append(table(['工具', '归属', '只读', '说明'],
                          [[f"`{row['name']}`", row['owner'], '是' if row['read_only'] else '否',
                            row['description'] or '—']
                           for row in data['tools'] if not row['hidden']]))
    return page('工具表', '各发行版暴露给模型的工具清单。', '\n'.join(body))


def native_modules_page() -> str:
    body = ['# native 模块清单\n',
            'native 是最完整的发行版：它覆盖了所有玩法领域，是写新模块时的参照。\n']
    for entry, key, title in DISTRIBUTIONS:
        if key != 'native':
            continue
        data = reflect_distribution(entry)
        for row in data['modules']:
            body.append(f'\n## `{row["id"]}`\n')
            body.append(table(['项', '值'], [
                ['入口', f"`{row['entry']}`"],
                ['状态键', '、'.join(row['state_keys']) or '—'],
                ['提供能力', '、'.join(row['provides']) or '—'],
                ['需要能力', '、'.join(row['needs']) or '—'],
                ['依赖模块', '、'.join(row['requires']) or '—'],
            ]))
            if row['rules']:
                body.append('\n规则正文：\n')
                for section in row['rules']:
                    body.append(f'\n**{section["title"]}**\n\n{section["body"]}\n')
    return page('native 模块清单', 'native 每个模块的能力、状态与规则正文。', '\n'.join(body))


PAGES = {
    '引擎.md': engine_page,
    '模块声明.md': manifest_page,
    '发行版清单.md': distribution_page,
    '模型面向契约.md': model_contract_page,
    '工具表.md': tools_page,
    'native模块.md': native_modules_page,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='只检查是否与代码同步')
    args = parser.parse_args()
    CONTENT.mkdir(parents=True, exist_ok=True)
    stale = []
    for name, builder in PAGES.items():
        text = builder()
        path = CONTENT / name
        if args.check:
            if not path.is_file() or path.read_text(encoding='utf-8') != text:
                stale.append(name)
        else:
            path.write_text(text, encoding='utf-8')
            print(f'写出 {path.relative_to(ROOT)}')
    if args.check:
        if stale:
            print('以下参考页与代码不同步：' + '、'.join(stale))
            print('跑 python3 scripts/api_doc.py 重新生成。')
            return 1
        print('参考页与代码同步。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
