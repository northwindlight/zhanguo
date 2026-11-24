"""内阁首辅：真人玩家的对话式治国助手（原型）。

首辅包揽本国政务（内政、军事、外交），玩家只做三件事：批阅奏折（准／驳）、下诏（口谕与方针）、
亲政（亲手操作，首辅不再自动拟奏）。

流程：主线程从绑定玩家的正式视图整理一份本国简报（不读外国私有事实）→ 后台线程调用一次模型，
让它用 ``submit_memorial`` 工具交回结构化奏折 → 回到主线程逐条核对操作名与参数结构 → 发给界面。
玩家准奏后，界面经原有 ``human_submit`` 通道逐条提交，回执与审计照旧；首辅本身不直接写世界。
宣战等仍走原有的确认窗口。

首辅设置（模型档案）由界面每局传入，不进游戏配置；方针、准驳记录与亲政开关存在存档旁的
``.desktop-chancellor.json``，不改世界状态与存档契约。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from mods.ui.desktop_player import frame as player_frame

# 结束回合由君主亲自决定；其余正式操作首辅都可以奏请。
FORBIDDEN = ('end_turn',)
# 大事：不进「全部准奏」，须君主逐条批阅。
MAJOR = ('declare_war', 'offer_peace', 'accept_peace', 'reject_peace', 'propose_vassalage', 'submit_vassalage',
         'release_vassal', 'independence_war', 'bloc_found', 'bloc_join', 'bloc_leave', 'bloc_dissolve',
         'bloc_transfer', 'borrow', 'demolish', 'disband', 'guarantee', 'cancel_guarantee', 'propose_defense',
         'break_defense', 'accept_proposal')
STATE_FILE = '.desktop-chancellor.json'
MAX_PROPOSALS = 8
MAX_DIRECTIVES = 12


def memorial_tool(operations):
    return {'type': 'function', 'function': {
        'name': 'submit_memorial',
        'description': '向君主呈上本回合奏折。每条建议是一件可以单独准奏或驳回的事，calls 为准奏后依次执行的操作。',
        'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['summary', 'proposals'], 'properties': {
            'summary': {'type': 'string', 'description': '局势摘要，三句以内，先讲最要紧的事'},
            'reply': {'type': 'string', 'description': '对君主诏书的回应；没有诏书时留空'},
            'proposals': {'type': 'array', 'maxItems': MAX_PROPOSALS, 'items': {'type': 'object', 'additionalProperties': False,
                'required': ['title', 'reason', 'calls'], 'properties': {
                    'title': {'type': 'string', 'description': '一句话说明做什么，如「瀚屿建农场」「民兵#3 进驻北岭」'},
                    'reason': {'type': 'string', 'description': '为什么做、代价与风险、预计效果，两句以内'},
                    'calls': {'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
                        'required': ['operation', 'arguments'], 'properties': {
                            'operation': {'type': 'string', 'enum': list(operations)},
                            'arguments': {'type': 'object', 'description': '与该操作的正式参数一致'}}}},
                    'directive': {'type': 'string', 'description': '若这条是建议新增长期方针，写方针原文，calls 留空'}}}}}}}}

SYSTEM = """你是战国时代一国的内阁首辅，总揽本国内政、军事与外交，替君主（真人玩家）拟定每回合要办的事；君主准奏后，朝廷按你写的操作逐条执行。
原则：
- 只用简报与规则书里的事实，不编造。价格、库存、可建项、军队编号与可达目标都以简报为准；地块用 x、y，军队用 ids（简报里的 #编号），国家用 [方括号] 里的 id。
- 每块地每回合只能开工一项；建设与征兵入队即预付，回合末统一开工。军队移动／进攻须是简报列出的可达目标；跨回合远征用 queue_orders。
- 内政、军事、外交都归你管，先办最要紧的事：断粮缺电、边境威胁、闲置的军队与可开工的地块、待复的外交提议。每条建议独立成立，最多 {max} 条，没事就少写，不要凑数。
- 宣战、议和、附庸、结盟、借贷、拆除、遣散、接受提议是大事：理由写清代价与风险，由君主亲自定夺。
- 遵守君主的方针；诏书优先于你的判断。君主说「以后都……」时，可用 directive 建议新增方针。参考近来准驳，揣摩君主偏好。
- 不能结束回合，那由君主决定。
- 必须调用 submit_memorial 呈上奏折。用中文，简短直白。"""


def _num(value):
    value = float(value or 0)
    return str(int(value)) if value == int(value) else f'{value:.1f}'


class Chancellor:
    def __init__(self, control):
        self.control = control
        self.cfg = None
        self.backend = None
        self.busy = None
        self.directives: list[str] = []
        self.log: list[dict] = []
        self.personal = False          # 亲政：首辅不再每回合自动拟奏
        self._load()

    # ── 设置与持久化 ──────────────────────────────────────────────
    @property
    def path(self) -> Path:
        return Path(self.control.save).parent / STATE_FILE

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.directives = [str(d) for d in data.get('directives', [])][:MAX_DIRECTIVES]
            self.log = list(data.get('log', []))[-20:]
            self.personal = bool(data.get('personal', False))
        except (OSError, ValueError, AttributeError):
            pass

    def _store(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({'directives': self.directives, 'log': self.log[-20:], 'personal': self.personal},
                                            ensure_ascii=False, indent=1), encoding='utf-8')
        except OSError as exc:
            self.control.send('notice', message=f'首辅记录未保存：{exc}')

    @property
    def enabled(self):
        return self.cfg is not None

    def configure(self, profile):
        if not profile:
            self.cfg = self.backend = None
        else:
            if not isinstance(profile, dict) or not all(str(profile.get(k) or '').strip() for k in ('base_url', 'api_key', 'model')):
                raise ValueError('首辅模型档案缺少 base_url、api_key 或 model')
            cfg = {k: profile[k] for k in ('provider', 'base_url', 'api_key', 'model') if profile.get(k)}
            extra = profile.get('extra')
            if isinstance(extra, str) and extra.strip():
                extra = json.loads(extra)
            if isinstance(extra, dict):
                cfg.update({k: v for k, v in extra.items() if k not in ('base_url', 'api_key', 'model')})
            cfg.setdefault('thinking', 'disabled')      # 奏折要快；需要推理时在档案附加 JSON 里打开
            cfg.setdefault('max_tokens', 4096)
            cfg.setdefault('temperature', 0.3)
            self.cfg = cfg
            self.backend = None
        self.publish_state()

    def publish_state(self):
        self.control.send('chancellor_state', enabled=self.enabled, busy=self.busy is not None, personal=self.personal,
                          directives=list(self.directives),
                          model=(self.cfg or {}).get('model', ''))

    def set_personal(self, personal):
        self.personal = bool(personal)
        self._store()
        self.publish_state()

    @property
    def auto(self):
        return self.enabled and not self.personal

    def directive(self, op, text='', index=None):
        if op == 'add':
            text = str(text).strip()[:200]
            if not text:
                raise ValueError('方针不能为空')
            if len(self.directives) >= MAX_DIRECTIVES:
                raise ValueError(f'方针最多 {MAX_DIRECTIVES} 条')
            if text not in self.directives:
                self.directives.append(text)
        elif op == 'remove':
            if not isinstance(index, int) or not 0 <= index < len(self.directives):
                raise ValueError('没有这条方针')
            self.directives.pop(index)
        else:
            raise ValueError('未知方针操作')
        self._store()
        self.publish_state()

    def feedback(self, row):
        entry = {'period': int(row.get('period', 0)),
                 'approved': [str(t)[:80] for t in row.get('approved', [])][:MAX_PROPOSALS],
                 'rejected': [str(t)[:80] for t in row.get('rejected', [])][:MAX_PROPOSALS],
                 'failed': [str(t)[:120] for t in row.get('failed', [])][:MAX_PROPOSALS]}
        self.log.append(entry)
        self._store()

    # ── 简报 ─────────────────────────────────────────────────────
    def briefing(self):
        api, actor = self.control.human_api, self.control.viewer
        state = api.state(actor)
        frame = player_frame(self.control.host.ui, state)
        labels = frame['labels']
        name = lambda key: labels.get(key) or str(key).split(':')[-1]
        facts = state['facts']
        nation = frame['nations'][0]
        lines = [f'【本国】{frame["names"][actor]} · 第 {frame["period"] + 1} 回合 · 国土 {nation["land"]} 格']
        flows = dict((nation.get('resource_flow') or {}).get('net') or [])
        stock = []
        for s in nation['stocks']:
            delta = flows.get(s['id'])
            stock.append(f'{s["name"]} {_num(s["amount"])}' + (f'（上期{delta:+g}）' if isinstance(delta, (int, float)) and delta else ''))
        lines.append('库存：' + '、'.join(stock))
        energy = facts['econ']['preview'].get('energy', {})
        lines.append(f'电力预计：发电 {_num(energy.get("generated"))} / 需求 {_num(energy.get("demand"))}'
                     + ('（缺电！）' if energy.get('short') else ''))
        lines.append('市场（买价/卖价）：' + '、'.join(
            f'{p["name"]} {_num(p["buy"]) if p["buy"] is not None else "缺货"}/{_num(p["sell"])}' for p in frame['market']))
        automatic = facts['market'].get('automatic', {}).get('rules') or []
        if automatic:
            lines.append('已设自动交易：' + '、'.join(name(r.get('resource')) for r in automatic))
        lines.extend(self._military(api, actor, facts, frame, name))
        lines.extend(self._foreign(facts, frame))
        lines.append('\n【本国地块】（坐标 x,y；只有「本期可开工」的地块能再建）')
        tiles = {tuple(t['position']): t for t in frame['tiles']}
        for site in facts['building_sites']['sites']:
            x, y = site['position']
            try:
                opts = api.site_options(actor, x=x, y=y)
            except Exception as exc:          # 单格报价失败不影响整份简报
                lines.append(f'- {x},{y}：报价失败（{exc}）')
                continue
            tile = tiles.get((x, y), {})
            space = opts['space']
            built = '、'.join(tile.get('building_labels', [])) or '无'
            head = (f'- {tile.get("name") or ""}({x},{y}) {tile.get("terrain_name", "")} · 建筑位 {space["free"]}/{space["capacity"]}'
                    f' · {"本期可开工" if space.get("order_remaining") else "本期已开工"} · 已有：{built}')
            lines.append(head)
            if not space.get('order_remaining'):
                continue
            estimates = {e['kind']: e for e in opts.get('estimates', [])}
            ranked = []
            for b in opts['buildings']:
                if not b.get('available'):
                    continue
                est = estimates.get(b['kind'], {})
                net = (est.get('net_per_period') or {}).get('total')
                payback = est.get('payback_periods')
                cost = '、'.join(f'{name(k)}{_num(v)}' for k, v in b['price']['costs'].items())
                ranked.append((-(net or -1e9), f'{name(b["kind"])}[{b["kind"]}] 费用 {cost}'
                               + (f' · 估每期净收益 {_num(net)}' if net is not None else '')
                               + (f' · 回本约 {_num(payback)} 期' if payback else '')))
            for _, text in sorted(ranked)[:5]:
                lines.append('    可建 ' + text)
            for u in opts.get('units', []):
                if u.get('has_facility') and all(r.get('met') for r in u.get('requirements', [])):
                    cost = '、'.join(f'{name(k)}{_num(v)}' for k, v in u['costs'].items())
                    lines.append(f'    可征 {name(u["kind"])}[{u["kind"]}] 每支 {cost} · 本期额度 {u.get("quota")}')
        news = [n.get('text') for n in facts.get('news', [])[-5:] if n.get('text')]
        if news:
            lines.append('\n【近讯】' + '；'.join(news))
        lines.append('\n【方针】' + ('；'.join(f'{i + 1}. {d}' for i, d in enumerate(self.directives)) if self.directives else '未定'))
        if self.log:
            last = self.log[-3:]
            lines.append('【近来准驳】' + '；'.join(
                f'第{e["period"] + 1}回合 准：{"、".join(e["approved"]) or "无"} 驳：{"、".join(e["rejected"]) or "无"}'
                + (f' 未成：{"、".join(e["failed"])}' if e.get('failed') else '') for e in last))
        return '\n'.join(lines), state

    def _military(self, api, actor, facts, frame, name):
        tiles = {tuple(t['position']): t for t in frame['tiles']}
        def place(x, y):
            tile = tiles.get((x, y), {})
            owner = tile.get('owner')
            who = '本国' if owner == actor else ('无主' if not owner else frame['names'].get(owner, owner))
            return f'{tile.get("name") or ""}({x},{y})·{who}'
        own = [u for u in facts['army'] if u['owner'] == actor and u['hp'] > 0]
        lines = ['\n【军队】' + ('无' if not own else f'{len(own)} 支（编号 #id，移动／进攻只能选下列可达目标）')]
        catalog = facts.get('unit_catalog', {})
        for u in own[:24]:
            commands = u.get('commands') or {}
            ready = [m for m in ('move', 'attack') if (commands.get(m) or {}).get('available')]
            text = (f'- #{u["id"]} {name(u["kind"])} 在 {place(u["x"], u["y"])} 血 {_num(u["hp"])}/{_num((catalog.get(u["kind"]) or {}).get("hp"))}'
                    + ('（交战中）' if u.get('engaged') else '') + ('' if ready else '（本回合已行动或不能行动）'))
            lines.append(text)
            for mode, label in (('attack', '可攻击'), ('move', '可移动')):
                if mode not in ready:
                    continue
                try:
                    targets = api.targets(actor, [u['id']], mode)['targets']
                except ValueError:
                    continue
                spots = [tg['position'] for tg in targets if not tg.get('uncertain')]
                if mode == 'move':     # 优先列出无主与他国地块，再列本国
                    spots.sort(key=lambda p: (tiles.get(tuple(p), {}).get('owner') == actor, p))
                if spots:
                    lines.append(f'    {label}：' + '、'.join(place(*p) for p in spots[:8]) + (f' 等 {len(spots)} 处' if len(spots) > 8 else ''))
        foreign = [u for u in facts['army'] if u['owner'] != actor and u['hp'] > 0]
        if foreign:
            groups = {}
            for u in foreign:
                key = (u['owner'], u['x'], u['y'])
                groups[key] = groups.get(key, 0) + 1
            lines.append('视野内外军：' + '、'.join(
                f'{"野人" if o == "野人" else frame["names"].get(o, o)}×{n} 在 {place(x, y)}' for (o, x, y), n in list(groups.items())[:12]))
        orders = facts.get('army_orders') or []
        if orders:
            lines.append(f'持续军令 {len(orders)} 条（跨回合自动推进）')
        return lines

    def _foreign(self, facts, frame):
        d = facts['foreign']['diplomacy']
        names = frame['names']
        lines = ['\n【列国】' + '、'.join(f'{n["name"]}[{n["id"]}]' for n in facts['foreign']['nations']
                                         if n['id'] != self.control.viewer and n.get('alive') is not False)]
        def pairs(rows):
            return '、'.join(json.dumps(r, ensure_ascii=False)[:80] for r in rows[:6])
        for key, label in (('wars', '战争'), ('truces', '休战'), ('pacts', '条约'), ('alliances', '联盟'), ('vassals', '附庸')):
            if d.get(key):
                lines.append(f'{label}：{pairs(d[key])}')
        for offer in d.get('offers') or []:
            lines.append(f'待复提议 #{offer.get("id")}：{json.dumps(offer, ensure_ascii=False)[:160]}'
                         '（接受用 accept_proposal／accept_peace，拒绝用 reject_proposal／reject_peace，参数 offer=编号）')
        for vote in d.get('votes') or []:
            lines.append(f'联盟投票 #{vote.get("id")}：{json.dumps(vote, ensure_ascii=False)[:160]}（vote=编号，accept=true/false）')
        return lines

    # ── 请求与结果 ───────────────────────────────────────────────
    def request(self, kind='memorial', text=''):
        if not self.enabled:
            raise ValueError('未设首辅：请在开局时选择首辅的模型档案')
        if self.busy is not None:
            raise ValueError('首辅正在拟奏，请稍候')
        if self.control._human_player is None:
            raise ValueError('尚未轮到本国，首辅无从奏事')
        briefing, state = self.briefing()
        request_id = uuid.uuid4().hex
        prompt = briefing + ('\n\n【君主诏书】' + str(text).strip()[:500] if kind == 'edict' and str(text).strip()
                             else '\n\n请呈上本回合奏折。')
        operations = [o['operation'] for o in state['operations'] if o['operation'] not in FORBIDDEN]
        rules = self.control._human_player.rules('all')
        messages = [{'role': 'system', 'content': SYSTEM.format(max=MAX_PROPOSALS)
                     + '\n\n【规则书】' + json.dumps(rules, ensure_ascii=False, separators=(',', ':'))},
                    {'role': 'user', 'content': prompt}]
        self.busy = {'id': request_id, 'period': self.control.host.clock(), 'kind': kind,
                     'operations': {o['operation']: o for o in state['operations']}, 'started': time.monotonic(),
                     'tool': memorial_tool(operations)}
        self.control.send('chancellor_status', request_id=request_id, state='drafting', kind=kind)
        threading.Thread(target=self._call, args=(request_id, messages, self.busy['tool']), daemon=True).start()
        return request_id

    def _backend(self):
        script = os.environ.get('ZHANGUO_CHANCELLOR_SCRIPT')
        if script:
            return ScriptedBackend(script)
        if self.backend is None:
            from clients.provider import make_backend
            self.backend = make_backend(self.cfg)
        return self.backend

    def _call(self, request_id, messages, tool):
        """后台线程：只做网络调用，结果回到主线程命令队列。"""
        try:
            msg, stats = self._backend().chat_turn(messages, [tool], dict(self.cfg))
            payload = None
            for call in msg.get('tool_calls') or []:
                fn = call.get('function') or {}
                if fn.get('name') == 'submit_memorial':
                    payload = json.loads(fn.get('arguments') or '{}')
                    break
            if payload is None:
                payload = _json_in_text(msg.get('content') or '')
            row = {'command': 'chancellor_result', 'request_id': request_id, 'payload': payload,
                   'seconds': stats.get('wall')}
        except Exception as exc:              # 网络、配额、格式错误都只影响这份奏折
            row = {'command': 'chancellor_result', 'request_id': request_id, 'error': str(exc)[:300]}
        self.control.commands.put(row)

    def receive(self, row):
        busy, self.busy = self.busy, None
        if busy is None or row.get('request_id') != busy['id']:
            return
        seconds = round(time.monotonic() - busy['started'], 1)
        if row.get('error') or not isinstance(row.get('payload'), dict):
            self.control.send('chancellor_memorial', request_id=busy['id'], ok=False, seconds=seconds,
                              message='首辅未能拟奏：' + (row.get('error') or '回复不是有效奏折'))
            return
        if busy['period'] != self.control.host.clock() or self.control._human_player is None:
            self.control.send('chancellor_memorial', request_id=busy['id'], ok=False, seconds=seconds,
                              message='回合已变化，这份奏折作废')
            return
        payload = row['payload']
        proposals, dropped = [], []
        for index, item in enumerate((payload.get('proposals') or [])[:MAX_PROPOSALS]):
            if not isinstance(item, dict):
                continue
            title = str(item.get('title') or '').strip()[:60] or f'第 {index + 1} 条'
            directive = str(item.get('directive') or '').strip()[:200]
            calls, problems = [], []
            for call in item.get('calls') or []:
                problem = _check_call(call, busy['operations'])
                if problem:
                    problems.append(problem)
                else:
                    calls.append({'operation': call['operation'], 'arguments': call['arguments']})
            if problems:
                dropped.append(f'{title}：{"；".join(problems)}')
                continue
            if not calls and not directive:
                continue
            proposals.append({'id': f'{busy["id"][:8]}-{index}', 'title': title,
                              'reason': str(item.get('reason') or '').strip()[:240],
                              'calls': calls, 'directive': directive,
                              'major': any(c['operation'] in MAJOR for c in calls)})
        self.control.send('chancellor_memorial', request_id=busy['id'], ok=True, seconds=seconds,
                          period=busy['period'], kind=busy['kind'],
                          summary=str(payload.get('summary') or '').strip()[:400],
                          reply=str(payload.get('reply') or '').strip()[:400],
                          proposals=proposals, dropped=dropped)


def _check_call(call, operations):
    if not isinstance(call, dict):
        return '操作格式不对'
    op, args = call.get('operation'), call.get('arguments')
    if op in FORBIDDEN:
        return f'不在可奏请范围：{op}（须由君主亲自决定）'
    if op not in operations:
        return f'本国当前没有「{op}」操作'
    if not isinstance(args, dict):
        return '缺少参数'
    schema = operations[op].get('parameters') or {}
    props = schema.get('properties') or {}
    missing = [k for k in schema.get('required', []) if k not in args]
    if missing:
        return '缺少参数 ' + '、'.join(missing)
    extra = [k for k in args if k not in props]
    if extra and schema.get('additionalProperties') is False:
        return '多余参数 ' + '、'.join(extra)
    for key, value in args.items():
        spec = props.get(key) or {}
        kind = spec.get('type')
        if kind == 'integer' and (type(value) is not int or value < spec.get('minimum', value)):
            return f'参数 {key} 应为非负整数'
        if kind == 'string' and not isinstance(value, str):
            return f'参数 {key} 应为文字'
        if 'enum' in spec and value not in spec['enum']:
            return f'参数 {key} 不认识：{value}'
    located = ('x' in args and 'y' in args) or bool(args.get('site') or args.get('place'))
    if op in ('build', 'recruit', 'queue_build', 'queue_recruit', 'move', 'attack') and not located:
        return '须给出地块坐标 x、y'
    if op in ('move', 'attack', 'retreat', 'disband') and not (args.get('ids') or args.get('id')):
        return '须给出军队编号 ids'
    if op in ('buy', 'sell') and (type(args.get('quantity')) is not int or args['quantity'] <= 0):
        return '买卖数量须为正整数'
    return None


def _json_in_text(text):
    start, end = text.find('{'), text.rfind('}')
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


class ScriptedBackend:
    """离线验收：按脚本文件依次返回奏折（JSON 数组，每项是一份 submit_memorial 参数）。"""
    _lock = threading.Lock()
    _cursor: dict = {}

    def __init__(self, path):
        self.path = path

    def chat_turn(self, messages, tools, cfg, on_retry=None, on_delta=None):
        with self._lock:
            text = Path(self.path).read_text(encoding='utf-8')
            index = self._cursor.get(self.path, 0)
            self._cursor[self.path] = index + 1
        # 占位符取自简报：第一块本期可开工地块的坐标与它的第一项可建建筑，脚本因此不依赖具体地图。
        site = re.search(r'\((\d+),(\d+)\)[^\n]*本期可开工[^\n]*\n\s+可建 [^\[]*\[([^\]]+)\]', messages[-1]['content'])
        if site:
            text = text.replace('"$X"', site.group(1)).replace('"$Y"', site.group(2)).replace('$KIND', site.group(3))
        nation = re.search(r'【列国】[^\[\n]*\[([^\]]+)\]', messages[-1]['content'])
        if nation:
            text = text.replace('$NATION', nation.group(1))
        items = json.loads(text)
        item = items[min(index, len(items) - 1)]
        delay = float(os.environ.get('ZHANGUO_CHANCELLOR_SCRIPT_DELAY', '0.2'))
        time.sleep(delay)
        return ({'role': 'assistant', 'content': '', 'tool_calls': [{'id': 'scripted', 'type': 'function',
                 'function': {'name': 'submit_memorial', 'arguments': json.dumps(item, ensure_ascii=False)}}]},
                {'wall': delay})
