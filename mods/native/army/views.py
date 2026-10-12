from __future__ import annotations
from ..core import *
from ..rules import *
from .rules import *
from .content import native_unit_definitions
from ..localization import resource_cost
from ..ui_constants import *
from .. import presentation as P
import copy,json,re,threading
from pathlib import Path
from ctx import est_tokens
from console import dw as _dw, pad as _pad


def _move_brief(kind: str, definition=None) -> str:
    """某兵种"能走几格"的说法——**从 `MOVE_COST` / `UNIT_TYPES` 现算**，不写死数字。

    规则：移动力 = `UNIT_TYPES[kind]["speed"]`；一步的代价 = max(出发格, 目标格)，
    所以"开阔地形能走几格 = 移动力 ÷ 开阔代价"、"崎岖能走几格 = 移动力 ÷ 崎代价"。
    """
    definition = definition if definition is not None else native_unit_definitions()[kind]
    budget = definition['speed']
    costs = definition['move_cost']
    open_cost = min(costs.values())
    open_cells = budget // max(1, open_cost)
    slow = [t for t in TERRAIN_STATS if costs.get(t, 1) > open_cost]
    if not slow:
        return f'动{open_cells}格/回合（任何地形）'
    slow_cells = budget // max(costs.values())
    return f"动{open_cells}格/回合（{'/'.join(slow)}只 {slow_cells} 格）"

def _move_cost_text() -> str:
    """地形代价那一句——**由 `MOVE_COST` 现算**（改表 ⇒ 文案跟着变）。

    捷径/崎岖两类地形名、以及"崎岖能走几格"都从表里推，不写死。
    """
    costs = MOVE_COST['native:cavalry']
    open_t = [t for t in TERRAIN_STATS if costs.get(t, 1) == min(costs.values())]
    slow_t = [t for t in TERRAIN_STATS if t not in open_t]
    budget = UNIT_TYPES['native:cavalry']['speed']
    return f"每走一格按地形扣（出发格与目标格取更贵的那个）：骑兵在{'/'.join(open_t)}便宜、在{'/'.join(slow_t)}贵一倍（⇒ 只能走 {budget // max(costs.values())} 格，且**穿不过去**）；步兵/民兵任何地形每格 1"

def _move_rule_text() -> str:
    """移动规则的完整说法——**全部由 `MOVE_COST` / `UNIT_TYPES` 现算**。

    2026-09-15 起：速度=移动力，逐格按地形扣（出发格与目标格取更贵的那个），
    多格移动逐格判定 ⇒ 崎岖地形"减速 + 穿不过去"。
    """
    rows = [f"{unit_label(k)}{_move_brief(k)}" for k in UNIT_TYPES]
    return f"**移动**：{'、'.join(rows)}；{_move_cost_text()}。多格移动**逐格判定**：隔着一道山地/森林、或借道别人的地界，都冲不过去。"

def _reach_brief() -> str:
    """`atk` 够得着多远的短说法——**由 `MOVE_COST` / `UNIT_TYPES` 现算**。

    （这里原来手写着"步1格/骑2格"：移动代价表一改就成假话，而工具描述是 LLM 玩家
    做决策时唯一的规则来源。改表 ⇒ 这句跟着变。）
    """
    return '、'.join((f"{unit_label(k)}{_move_brief(k)}" for k in UNIT_TYPES))

def _recruit_desc(costs: dict[str, dict], definitions=None) -> str:
    """征召工具的说明：费用/补给/移动**全部现算**（`UNIT_TYPES` + 政体表）。

    `costs` = {兵种: 料单}，由「该国实际征召价」（`World.recruit_cost`）或
    `UNIT_TYPES[*]['recruit']`（无政体）给出 ⇒ 匈奴的骑兵特价自动体现在文案里。
    """
    rows = []
    for k, info in (definitions if definitions is not None else native_unit_definitions()).items():
        extra = '；驻本格军屯不耗补给' if k == 'native:militia' else ''
        rows.append(f"{k}={unit_label(k, definition=info)}({resource_cost(costs.get(k, info['recruit']))}、耗补给{info['supply']}/回合、{_move_brief(k, info)}{extra})")
    return '在自己有兵营且电网正常的地块征召军队，每兵营每回合1支。兵种 kind：' + '；'.join(rows) + f"——民兵是廉价驻守军队，每军屯每回合{building_effect('军屯', 'militia_per_turn')}支、每座可驻{building_effect('军屯', 'militia_cap')}支" + f"（全国民兵总数≤军屯数×{building_effect('军屯', 'militia_cap')}；阵亡或遣散后才能补员）。" + '注意补给仓必须跟上：补给不足时**吃补给的**军队按缺口比例扣血' + f'（满缺 -{ARMY_STARVE_DAMAGE}HP/军/回合，交战中也照扣），饿毙不复活。'

def _fmt_armies(world, name, brief: bool=False) -> str:
    """逐军明细；`brief=False`（`query panel=army`）**尾部附军事图**——谁在哪儿一眼看得出。

    ★ 2026-09-20 用户：「军队面板很不详细，应该展现军队地图，地图专门做了面板没用」——
    军事图（`_fmt_mil_map`）早就做好了，却只挂在 `panel=grid` 里，而 AI 查军队看的是
    `panel=army` ⇒ 那份图基本没人看。现在接进军队面板；常驻状态里仍只给列表 + 一句指路
    （免得每回合都把整张图塞进上下文）。
    """
    mine = [a for a in world.armies if a['owner'] == name]
    if not mine:
        return '（无军队——先建兵营，再用 recruit 征兵）'
    lines = []
    for a in sorted(mine, key=lambda x: x['id']):
        t = world.tiles.get((a['x'], a['y']))
        where = t['name'] if t else '野外'
        st = '交战中' if a.get('engaged') else '已移动' if a.get('moved_turn') == world.turn else '可行动'
        mv = _move_brief(a['type'], a.definition)
        reach = len(world._reachable(name, a)) - 1 if st == '可行动' else 0
        cl = world.visible_buildings(name, a['x'], a['y']).get('城堡', 0)
        lines.append(f"{a['name']}(#{a['id']}) | {a['hp']}HP | ({a['x'] + 1},{a['y'] + 1}) {where}" + (f' 城L{cl}' if cl else '') + f' | {st} | {mv}' + (f'｜本回合可及 {reach} 格' if st == '可行动' else ''))
    body = '\n'.join(lines)
    if brief:
        return body + '\n  （想看「谁在哪儿」的军队位置图：query panel=army）'
    return body + '\n\n' + _fmt_mil_map(world, name) + '\n  （军队管理：move 挪位 / attack 进攻 / **disband 遣散**——不打算再用的军就地解散，当回合起即不再吃军费与补给；★不返还、交战中不可用）'

def _reach_cells(world, name) -> set:
    """本回合**有军队够得着**的格（能直接 atk 入驻/开打的）。与 land 面板同口径。"""
    reach: set = set()
    for a in world.armies:
        if a['owner'] == name and a['hp'] > 0 and (not a.get('engaged')) and (a.get('moved_turn') != world.turn):
            reach |= set(world._reachable(name, a, for_attack=True))
    return reach

def _fmt_mil_map(world, name) -> str:
    """**军事图**（常驻）：谁在哪儿——自己全部军队 + 视野内的他国军队。

    · 格内是**归属国的国别代码**（和地形图同一套），不是逐军编号；
    · **一格只画一个**（重叠时画优先级最高的：自家优先 → 国序 → 番号），
      跨国的重叠位置会另起一行列出（同格混编），同国叠兵不重复标；
    · **野人守军不在此图**：它们驻在每一块无主地上（静态属性），见地形图的 `*`；
    · 逐军明细（番号/血量/状态/坐标）在【军队】/【威胁】面板，本图不重复。
    """
    vis = P._visible_cells(world, name)
    if not vis:
        return '（无视野）'
    x0, x1, y0, y1, cropped = P._map_frame(world, name, vis)
    letters = P._nation_letters(world)
    order = {n: i for i, n in enumerate(world.order)}
    mine = sorted((a for a in world.armies if a['owner'] == name), key=lambda a: a['id'])
    foreign = sorted((a for a in world.armies if a['owner'] != name and a['owner'] != '野人' and ((a['x'], a['y']) in vis)), key=lambda a: (order.get(a['owner'], 99), a['id']))
    by_tile: dict = {}
    for a in mine + foreign:
        if x0 <= a['x'] <= x1 and y0 <= a['y'] <= y1:
            by_tile.setdefault((a['x'], a['y']), []).append(a)

    def pick(lst: list) -> dict:
        return sorted(lst, key=lambda a: (0 if a['owner'] == name else 1, order.get(a['owner'], 99), a['id']))[0]
    lines = [f'军事图 x {x0 + 1}→{x1 + 1}、y {y0 + 1}→{y1 + 1}（与地形图同框；一格只画一个国家符号）']
    lines += P._axis_lines(x0, x1, y0, y1)
    for y in range(y0, y1 + 1):
        cells = []
        for x in range(x0, x1 + 1):
            lst = by_tile.get((x, y))
            cells.append((letters.get(pick(lst)['owner'], '?') if lst else '.') + ' ')
        lines.append(f'  {y + 1:3d} ' + ''.join(cells))
    counts = {}
    for a in mine + foreign:
        counts[a['owner']] = counts.get(a['owner'], 0) + 1
    lines.append('自家军 %d 支 · 视野内他国军 %d 支（%s）· 国别代码同地形图' % (len(mine), len(foreign), ' '.join((f"{letters.get(o, '?')}×{c}" for o, c in sorted(counts.items(), key=lambda kv: order.get(kv[0], 99)) if o != name)) or '无'))
    mixed = [p for p in sorted(by_tile) if len({a['owner'] for a in by_tile[p]}) > 1]
    if mixed:
        lines.append('同格混编：' + ' '.join(('+'.join(sorted({letters.get(a['owner'], '?') for a in by_tile[p]})) + f'@({p[0] + 1},{p[1] + 1})' for p in mixed[:8])) + (f' …另 {len(mixed) - 8} 处' if len(mixed) > 8 else ''))
    left = [a for a in mine if (a['x'], a['y']) not in by_tile]
    if left:
        lines.append(f'（另有 {len(left)} 支自家军在框外：' + ' '.join((f"{a['name']}@{a['x'] + 1},{a['y'] + 1}" for a in left[:6])) + '——见 query panel=army）')
    if cropped:
        lines.append('（视野里有远离本土的格，未画进图）')
    lines.append(MIL_LEGEND)
    return '\n'.join(lines)

def war_manual() -> str:
    """【战争手册】：**只要在打仗，每回合强行挂载**（`World.at_war` 为真时由 system_prompt 追加）。

    用户 2026-10-06 口述要点、要求写成"战时必看"：「必须要求战争速战速决，快速夺厅，
    打兵没有用，消灭军队是服务于战略意图，对工业国磨死一两个士兵总是可以增援上的」
    ＋「防御条约不是安全网……多次重申联盟才安全可靠」。

    ★ 2026-10-07 追加（用户口述）：「**战略比战术更重要**——不要老是想着必赢的战斗，
    那实际上是给对手调兵和恢复的战略时间；机会丢失只会无限慢性失血；一旦某处触发强制
    和平，就是浪费时间换不来任何战果」，以及兵力比的经验值（**以打平原为基准**）：
    高出三成＝决定性歼灭战、五成＝稳操胜券、一倍＝一回合零阵亡全歼。
    ★ 2026-10-07 再追加（用户原话）：「**不是不堆到一格，不集中意味着你根本无法防守**」
    ＋「**不是更要有兵，是你的安全假设必须成立**」
    ＋「**而且不是什么常驻，为什么要把根本不可能被进攻的地守一堆军队，等于学燕，被运动战
    早晚打烂**」——所以第一节那段的落点是
    「你的进攻计划建立在『后方安全』这个假设上，**它必须成立、每回合都要验**」，
    **不是**「别把建筑堆在一格」（集中是对的：§四「要守的点少而厚」），
    **不是**「厅上要多堆几支」，更**不是**「每座厅常驻一支」——
    **警报只在该假设已破时响**：那格没人 **且** 看得见的敌军**本回合就够得着**
    （`_reachable(for_attack=True)`）。够不着的厅**不需要兵**。
    ★★ 那三档是**模糊值**，用户明确「模糊值够了」，并且**立意是劝它早打、不是劝它等**：
    实盘里燕国多次手握巨大优势却按兵不动（"等双倍"），所以 **30% 是动手门槛**、
    **100% 只是奢侈品**——这一节的落点是「够三成就打」，不是「堆够两倍再上」。
    不要把它改成精确公式/查表，也不要为了更准去接战斗 DP（用户 2026-10-07 已否掉 DP：
    「有点作弊了」，`tests/test_battle_panel.py::TestNoProbabilitySolver` 是那条禁令的守卫）。

    ★ 每一条都是**引擎事实**，不是劝告，落点全在代码里：
    · 亡国判据 = 市政厅尽失 —— `World._eliminate_if_dead` / `has_townhall`；
    · 厅与城堡同档、只在**你视野内**露出 —— `World.visible_buildings`（视野内的他国地只报城堡/市政厅）；
      **不是全球广播**：视野外一概看不见 ⇒ 想拔谁的厅先把视野推过去（面板「视野内市政厅」那一行）；
    · 伤害 = Σ各军攻击、按人头分摊 —— `World._resolve_battles`（`_combat_power`/`_spread`）；
    · 灭国收线 + 余土变无主 + 强制作战休战 —— `_eliminate_if_dead` 的 wars 清场 + `FALL_TRUCE_TURNS`；
    · 条约不给通行/不给视野 —— `World.allied_between`（只认同一实体）与 `World.visible_to`（只认 bloc）。
    数字一律现读 balance，不手抄第二份。停战后可用 `rules(战争手册)` 取回。
    """
    inf, cav = (UNIT_TYPES['native:infantry']['recruit'], UNIT_TYPES['native:cavalry']['recruit'])
    hall = BUILDINGS['市政厅']
    return f"**你正在打仗，这本册子每回合都要读一遍。**\n★ **推荐阅读 `rules(战略手册)`**：这本只讲「这一仗怎么打」；**该不该打、打完谁做大、会不会被别人趁虚围剿**——那是盘面问题，全在战略手册里（均势 / 主动干预他国征服 / 围魏救赵 / 弱国也要主动出手 / 外交是必须的 / 僵持招来均势战争）。\n一、战争的**唯一得分动作**是拔厅。\n  亡国的**唯一**条件是**市政厅被拔光**——不是丢光领土。还握着大片土地、市政厅却被拔光的国家**照样亡国**。⚠ **但厅不是全球广播的**：它和城堡同档，**只在「你视野内」的格子上自动露出**（面板里那行「视野内市政厅」就是它；视野外一概看不见）⇒ **想拔谁的厅，先把视野推到它头上**。**占地本身不判生死，拔厅才判。**\n  ⚠ 但市政厅**可以重建**（{hall['cost']} 金 + {hall['wood']} 木，需该格已用建筑位 ≥6）⇒ **你每慢一回合，它就把厅补回来。**\n  ★★ **把这一节反过来再读一遍：你的厅也是对手唯一的得分动作。** 空着的厅不是「后方」，是**一格白送的国祚**——敌人一支孤军 `atk` 进无军格就是**零战斗直接进驻**（引擎里没有守军就没有仗可打，它连减速都不会）。\n  ⇒ **时刻保证你的后方安全：被换家意味着战略完全失败。** 前线赢得再多，家里被走进来一次，国祚和战争工业一起归零——**这不是「打得不好」，是战略本身散了**。\n  ⇒ 推论①：**把兵全压出去之前，先问一句：「我的后方安全，靠什么成立？」**靠的**不是**「每个厅都钉一支兵」——那是**学燕**：把兵摊在自己家里，正面遇上运动战早晚被打烂（要守的点**少而厚**，见第四节）。集中的资产**只需要守一处**，那正是集中的好处；但**那一处在敌人够得着的时候，不能是空的**。\n  ⇒ 推论②：**你所有的进攻计划都建立在一个假设上——「我的后方是安全的」。**这个假设**必须成立**。它一旦不成立，你前线打得再好也全部作废（实盘：有人按时间表拔下了对手的国祚，同一时间自己的国祚连同战争工业被一格端走，整场战役的成果当场归零）。\n  ⇒ 所以每回合**验一遍**它「现在还成不成立」，而不是假定它还在。尤其是集中的资产被端掉是**连锁**的：那格若还堆着补给厂/装备厂/兵营，一格易主就一起停摆（耗电建筑**全有全无**，电网一破全部停产）。\n  ★ 状态面板顶部的【国祚警报】就是替你验这条假设的：**当你有厅没驻军、而且看得见的敌军本回合就够得着**，它会点名是哪几处、谁够得着——**它一出现，就说明假设已经破了，立刻回兵**。\n  ★ 它平时**不响**：够不着的厅**不需要**放兵——为那种地方驻军，是**被运动战拖死的第一步**（这也是「集中」的意义：让你只有一处需要守）。\n  ★ **落锤前先 `query panel=battle`**：它会摊开这一格每一方的**支数·兵种构成·合计 HP·输出基数**，以及**仅防御方**吃的地形/城堡/合计减伤——那几样就是你算「这一拳打不打得动」的全部数据（引擎不替你算胜负）。**你的军队站在哪一格，那一格就列得出来**；不在你视野内的格标「盲战」，只报我方、减伤不明——\n  ⚠ 盲战的代价是**真的**：引擎照收那格地形与城堡的减伤，只是不告诉你。\n二、**打兵不得分。** 兵是产能的产物。\n  一支步兵 = {inf['native:food']} 粮 + {inf['native:equipment']} 装（骑兵 {cav['native:food']} 粮 + {cav['native:equipment']} 装）。对一个还在运转的工业国，你磨掉它一两支兵，它下回合就补回来 ⇒ **净收益约等于零**；而你为这两支兵付出的是**回合数**，回合数**不可再生**。\n  ⇒ 消灭敌军的**唯一**用处是**清掉守厅的路**。除此之外的杀兵，都是拿自己的回合数买对面的可再生产。\n  ⇒ 战果该这样记：**拔了几座厅、净拿了多少块站得住的地**——不是「杀了它几支兵」。\n三、**拖 = 纯亏。拖长战争得不到任何优势，只会让你在全世界落后。**\n  ★★ 先把这句钉死：**时间本身不产生任何优势。** 它不给你加成、不给你资源、不给你位置——它只做一件事：**把你每一回合的军费烧掉，同时让别人的复利多滚一圈。**\n  · **你不动，别人在动。** 战场之外没有「暂停」键：你在前线「稳住阵线」的每一个回合，后方各国都在占空地、盖建筑、滚复利。**你拖的不是敌人，是你自己。**\n  · **军费是每回合的固定支出**，而这些钱本可以拿去投资复利。⇒ **兵力占优还故意拖延，就是被自己的军费拖死**——你的优势正被你自己的开销一回合、一回合地吃掉。\n  · ⇒ 结论：**拖延是这局里最贵的动作。** 它什么也买不到，只买到一个「全世界都比你大了」的结局。**有优势而不动手，是极其愚蠢的一种输法。**\n  ⇒ 判据：**N 回合内没让对手少一座厅、或少一片站得住的地 ⇒ 这 N 回合你是纯亏的。**\n  ⇒ 所以**速战速决**：开战就写下「几回合内拔哪几座厅」，然后**按这个期限调兵**，不要按「哪格挨打救哪格」调兵。\n  ★★ **战略大于战术——别把力气花在「这一仗我一定能赢」上。** 必胜的小仗**不是战果，是慢性失血**：你赢一次，掉一批血、回去修几个回合；而对手这几个回合在调兵、在回血、在补厅、在把兵往你身上堆。**反复啃必胜的小仗＝节奏握在对手的增援速度手里**，你只是在替它把战线拖长。\n  ⇒ 你要的不是「每仗都赢」，是「**这条战线几个回合内结束**」：看准了就**一击拔厅**，然后收兵。**别为了「更稳」再等几回合——等来的通常不是更稳，是对方的援军。** 机会窗口不会一直开着——\n  ⚠ **战场之外任何一处灭国，都会终止全天下所有战线**——**在本回合结束时统一结算**        （不是灭国那一刻当场生效；判亡国、清战线、压休战都是同一步），        并把 {FALL_TRUCE_TURNS} 回合压给每一对国家——这 {FALL_TRUCE_TURNS} 回合里**谁也不能宣战谁、谁也入不了盟**。★ **正在打的仗也一起停**（不只是与亡国者有关的那几条），停在哪个位置就是哪个位置。\n  ⇒ 所以**你算好的那一拳要是拖到那天，就白算了**：战线没了，兵还得养，对手白得一段喘息。**要么在别人亡国之前打完，要么就别指望那条线还留着。**\n  ★★ **但「拖」治的是「连续几十回合啃不动的僵持」——它的启动前提是：你已经花掉大约 20 回合、真的试过并证明了「打不动」**（它的厅没少一座、你的地没多一块、它越守越厚、你的兵在掉血）。**没走完这 20 回合就签和收兵，那不叫止损——那叫在必赢的一击前收手。**收手之前把这三条算完，**一条都不是「它盟友多」**：\n  · ① **兵力够不够一次拔掉它**（够了就该打——兵力是平方级的，见第四节）；\n  · ② **它的援军最短第几回合能到**——**援军也要走格子**（步兵每回合 1 格、骑兵平地 2 格，地形还吃移动力）。**隔着 N 格就是 N 回合；N 大于你拔厅要用的回合数 ⇒ 那支援军不存在。** 「它盟友多」「它的盟友已经宣战了」都不是理由：**宣战不等于到场**，到场要按格子算。\n  · ③ **这一刀本身值多少**：**灭掉它＝你亲手打出**上面那 {FALL_TRUCE_TURNS} 回合全天下休战（所有战线当场终止，含与你无关的那些），**外加它的余土当场变无主、先到先得**。对方若是**盟主或当前第一**，这一刀还要再翻一倍。\n  ⇒ **你想要的那段「休战期」，亲手灭掉对方就能拿到，还顺手拿走它的地。**拿一纸和约去换同一段时间、却把它留在世上——是这笔账里最亏的一种算法。\n四、**集中，永远集中。**\n  伤害 = 该方**各军攻击之和**；受击伤害**按人头分摊**。⇒ 兵力是**平方级**的：堆在一起不只打得更疼，还**每人挨得更少**。\n  ⇒ **1 支军单独守一格 = 送。** 分散驻防不是「守土」，是把兵力拆成一份份礼物。\n  ⇒ 摊在无关紧要地方的兵：**够得着就收回来堆厚**，够不着（守不动、也用不上）的**直接 `disband` 遣散**——留在那儿既不守土、又每回合白吃你的军费（遣散不返还，所以要拿定主意：这是止损，不是换钱）。\n  ⇒ **守土靠往同一格堆 2~3 支**（要守的点**少而厚**，不要多而薄）；**进攻同理**——要打就一次堆够，别「先打再补」。\n  ★ **打之前先看倍数**：**你的输出基数 ÷ 它的输出基数**（步/骑各 {UNIT_TYPES['native:infantry']['atk']}、民兵 {UNIT_TYPES['native:militia']['atk']}；撤退中的军打两折）。下面是**打平原**的基准（打丘陵/城堡要再往上抬，见下）：\n    · **高出 30%** ⇒ **决定性的歼灭战**——守军被全歼。**这就是动手的门槛，不是「还差一点」。**\n    · **高出 50%** ⇒ **稳操胜券**：两回合全歼，自己还剩三成血，翻不了盘。\n    · **高出 100%** ⇒ 一回合零阵亡全歼——**那是奢侈品，不是门槛。别拿「等双倍」当拖延的借口。**\n  ★★ **「等双倍」是本作最贵的错误。** 你等的每一个回合，对方都在**往回调兵、补厅、回血、修工事**——**你的优势会自己过期作废**。分明已经占优、却因为「还不够稳」而不动手，是这局反复出现的一种输法：等到最后，优势没了，兵也白养了。\n  ⇒ **够三成就打。** 打两回合、掉一半血，也远好过把优势拖成平手——**打掉的是它的兵和它的厅，不打掉的是你自己的回合数。**\n  ⇒ 一倍左右是**同归于尽**；贴着 1 打就是赌命（每回合双方各掷骰 ±{max(COMBAT_DIE_MOD.values())}%）。所以要的是「**明显**高出一截」，不是「吓人的倍数」。\n  ⇒ 守方吃**地形+城堡**减伤（**相乘**叠加，只给格主/守方，沙漠 −10% 反而更好打）：丘陵 +25%、森林 +10%、城堡每级 +10%。**同一拳打到丘陵上，要多堆两三成兵**才算数。\n五、**三种军队：先看清你买的是什么。**\n  步兵：攻 {UNIT_TYPES['native:infantry']['atk']} / 血 {UNIT_TYPES['native:infantry']['hp']} / 速 {UNIT_TYPES['native:infantry']['speed']} / 口粮 {UNIT_TYPES['native:infantry']['supply']}/回合 / 征兵 {inf['native:food']} 粮 + {inf['native:equipment']} 装\n  骑兵：攻 {UNIT_TYPES['native:cavalry']['atk']} / 血 {UNIT_TYPES['native:cavalry']['hp']} / **速 {UNIT_TYPES['native:cavalry']['speed']}** / 口粮 **{UNIT_TYPES['native:cavalry']['supply']}**/回合 / 征兵 {cav['native:food']} 粮 + {cav['native:equipment']} 装\n  民兵：攻 **{UNIT_TYPES['native:militia']['atk']}** / 血 **{UNIT_TYPES['native:militia']['hp']}** / 速 {UNIT_TYPES['native:militia']['speed']} / 口粮 {UNIT_TYPES['native:militia']['supply']}（驻自家军屯格**免费**）/ 征兵 {UNIT_TYPES['native:militia']['recruit']['native:gold']} 金 + {UNIT_TYPES['native:militia']['recruit']['native:food']} 粮\n  ★ **正规军只吃粮和装备，不吃金**——只要农场/补给厂/装备厂还在转，兵就是**可再生产的**。所以「打兵」打不掉对手的战争能力，**只有拔厅能**。\n  ★ **骑兵强在哪：不是单挑，是位置。** 它的攻击和血**和步兵完全一样**，一对一它一点不占优。\n    ① **速度 {UNIT_TYPES['native:cavalry']['speed']}**：平地上**一回合跨 2 格**⇒堆得比谁都快。伤害＝各军攻击之和、受击按人头摊 ⇒ 谁先在**同一格**堆出明显优势谁赢。**骑兵是「把集中变成可能」的兵种。**\n    ② **进得去也出得来**：撤退固定只退 1 格（所有人），但**平时移动按兵种速度** ⇒ 骑兵撤下来一回合就能回前线，步兵要爬两回合。\n    ③ **它能在对方看不见的地方穿插**——这条最容易被忽略。视野是「自家地及其相邻一圈」，所以**别人视野之外就是你的盲飞区**：骑兵一回合 2 格，可以贴着野地/自家地绕过去，**出现在它后方，而它直到你踏进它的视野才看得见你**。步兵办不到：1 格/回合，绕同样的路要多花一倍回合，**而且它追不上你**（同向跑，速度差恒为 1）。\n       ⚠ 市政厅同理（**只在视野内露出**，见第一节）：**你的厅也藏不住**——推进到你家门口的敌人一样看得见它；反过来，想直取它的厅就**先把视野推过去**。\n       ⇒ 这就是「直取市政厅」的战术形态：**不要一层层推前线——绕开它的军队，去砸它的厅。**\n    ⚠ 代价：**口粮 ×2、征兵贵**（装备 {cav['native:equipment']} vs {inf['native:equipment']}），且**崎岖地形吃满移动力**（森林/山地一步花光 2 点）⇒ **在森林/山地里骑兵和步兵没区别，却还在付双倍口粮**。**骑兵的主场是平地。**\n  ★ **民兵是守备兵，不是野战兵**：攻击 {UNIT_TYPES['native:militia']['atk']} ＝步骑**一半**（2026-10-10 从 20 抬到 25）、血 {UNIT_TYPES['native:militia']['hp']}。它的价值是**零维持**（驻自家军屯格不耗口粮、回血不吃装备）。**用它钉格子，别用它进攻。**\n六、**灭国能一夜终结整条战线。**\n  一方**主导者亡国 ⇒ 整条战线当场消失**（不用议和、不用赢下每一场）。亡国的连锁后果**全世界都会收到广播**（都在**本回合末**一并落地）：① 它的**全部余土变成无主空地**，地上的建筑（含市政厅/城堡/兵营）**原样留着、先到先得**；② 它的军队**一并解散**；③ **全天下强制休战 {FALL_TRUCE_TURNS} 回合**——**所有战线一并终止**（含与你无关的那些），" + '期满各自回到中立，要再打就得重新宣战、**重新触发一遍全部条约传导**（防连环征服）。\n  ★ **休战期不是外交冻结期：不得宣战，但条约照签、盟照结。**全天下同时停火的这段窗口，**是唯一一段谁都打不了谁、可以放心谈的时候**——趁它把盟立起来；一旦那个刚跟你签了和约的对手也进了同一个联盟，**那纸和约就并入联盟了**（盟内本就互不攻击，比和约更强）。\n  ⇒ 如果对方是这条战线的**主导者**，**直取它的厅，比在边境换一百次地都快**。\n七、**防御条约不是安全网——只有联盟是。**\n  「保障独立」「共同防御」**不是保险**：\n  · **只在「宣战」那一刻结算一次**，此后这条战线的名单就**冻结**了——不是持续保护。\n  · **不给军事通行**：它们的领土**不是你的地**，你的军队**走不进去、撤退也退不进去**。\n  · **不共享视野**：你不知道它的军队在哪，它也不知道你哪一格吃紧。\n  · ⇒ **协同防御在引擎里做不到。** 它会被义务拖进来，然后**站在自己家里**；**你无从得知它会不会来**——「参战」不等于「来救你」。反过来，你也救不了它。\n  ★ **只有联盟是安全的**：联盟 = **同一个外交实体**——互通领土（自由通行 + 合法撤退地）、**共享视野**、互不攻击、**任一成员被打全盟自动参战（防守不需投票）**、同战线**自动归还你的核心领土**。\n  ⇒ **要安全，就去结盟。靠条约，等于没穿衣服。**\n八、**兵比对方多、却一直在吃败仗？立刻停下，把这四问答完再动手。**\n  「兵多」本身不是优势；**「兵多、集中、并且用出去」才是优势**。数量占优还在输，只有一种解释：**你的兵根本没被打出去。**\n  ① **为什么兵多还输？** 把最近三场交战的双方兵力列出来。若每一场你都是 1 打 2，那就不是运气——**是你自己把兵摊开了**。\n  ② **为什么我在故意分散？** 你是在「守土」，还是在**把兵力拆成一份份礼物**？要守的点必须**少而厚**：占着一堆格子、每格一支，等于每格都守不住。\n  ③ **为什么不直接消灭对手？** 你算过双方总量吗？如果你有 2:1 以上的兵力，却还在「稳住阵线」——那你不是打不赢，**是没打**。\n  ④ **为什么兵多还被动？** 被动是**选择**，不是处境。有优势却等对方先动手，等于把主动权白送：对方每回合挑你最薄的那格打，你只能到处救火。\n  ⇒ **兵多还挨打，答案永远在自己这边，不在对面。**\n  ⇒ **纠正动作**：把散出去的兵收拢成 2~3 个拳头，挑一个**一步够得到、且对方救不到**的目标，**一次堆够打掉它**；打完再收拢、再挑下一个。**别再做「哪格挨打救哪格」的救火队。**'

def _fmt_threats(world, name) -> str:
    """视野内的**他国军队**。

    ★ 野人守军**一律不列**（2026-09-19 用户：「顺便野人也不应该列威胁，全过滤算了」）：
    它们从不主动攻击、HP 恒 100、每块无主地都有一只，而**坐标地图每格已经用「，野人」标了它们**。
    实测第 120 回合：原本 1668 字符 / 64 行里 **59 行（90%）是野人条目**，全砍掉省 ≈750 token/回合。
    想看某个守军的血量/位置：地图那一行（或 `query panel=tile x= y=`）。
    """
    rows = []
    for a in world.armies:
        if a['owner'] == name or a['owner'] == '野人':
            continue
        if not world.visible_to(name, a['x'], a['y']):
            continue
        cl = world.visible_buildings(name, a['x'], a['y']).get('城堡', 0)
        rows.append(f"{a['name']}({a['owner']}) {a['hp']}HP @({a['x'] + 1},{a['y'] + 1})" + (f' 城L{cl}' if cl else ''))
    return '视野内的他国军队:\n  ' + '\n  '.join(rows) if rows else '视野内没有他国军队（野人守军不列在此——它们从不主动进攻，见坐标地图里每格的「，野人」）'

def _battle_cells(world, name, cap: int):
    """**交战格的唯一取数门禁** —— `_fmt_battle` 与 `battle_brief` 都只走这里。

    一格入选当且仅当 **`可见 ∪ 我有活军在场`**：
      · 纯视野（别国互殴）：看得到就报；**看不到的连计数都不给**——计数本身就是
        泄漏"别处正在打"；
      · **我有军在场**：必须出现。`attack` 允许打视野外的格，而军是**站在那格上**的，
        自己不出现的话 AI 连"我正在盲打"都不知道（2026-10-07 的起因）。

    候选集走 `world.troops`（缓存过的**非野人**名单）而不是 `world.armies`：
    一局里野人常有 1500+ 支，而野人永不是交战方（`battle_sides` 的 `attacker` 不含野人）。

    返回 `(rows, extra_cells)`；`rows = [(x, y, sides, seen)]`，`seen` = 该格是否在我视野内
    （决定报不报敌方与减伤——盲战只报我方）；`extra_cells` = 入选但超出 `cap` 的坐标。
    """
    vis = P._visible_cells(world, name)
    mine = {(a['x'], a['y']) for a in world.troops if a['owner'] == name and a['hp'] > 0}
    cells = sorted({(a['x'], a['y']) for a in world.troops if a.get('engaged')})
    rows, extra_cells = ([], [])
    for x, y in cells:
        if (x, y) not in vis and (x, y) not in mine:
            continue
        sides = world.battle_sides(x, y)
        if sides is None:
            continue
        if len(rows) >= cap:
            extra_cells.append((x, y))
            continue
        rows.append((x, y, sides, (x, y) in vis))
    return (rows, extra_cells)

def _side_line(world, F: str, units: list[dict], sides: dict) -> str:
    """一方的一行汇总：支数 · 兵种构成 · 合计 HP · 输出基数。"""
    kinds: dict[str, int] = {}
    for a in units:
        k = a.get('type') or '野人'
        kinds[k] = kinds.get(k, 0) + 1
    comp = ' '.join((f'{unit_label(k, definition=world.units.definition(k))}{v}' if k != '野人' else '野人' for k, v in sorted(kinds.items())))
    hp = sum((a['hp'] for a in units))
    return f"{len(units)} 支 · {comp} · 合计 {hp}HP · 输出基数 {sides['atk_base'][F]}"

def _fmt_battle(world, name) -> str:
    """当前交战的**事实**面板：谁在攻、谁在守、双方投入多少、吃几档减伤。

    ★ **只给事实，不给判断**——不算胜率、不预判胜负（用户 2026-10-07：接战斗 DP
      「有点作弊了」；项目元规律「给事实不给判断」）。AI 要算自己算：燕 T121 的原话
      就是「按伤害模型再打两轮就是全歼」——它缺的是数据，不是求解器。

    ★ **盲战**：该格不在我视野内、但**我有军在场** ⇒ 该格出现（否则不知道自己在盲打），
      但**只报我方**，敌方与减伤一律「敌情不明」。**尤其不报合计减伤**：它含城堡分量，
      报出去等于让 AI 用算术把雾里的城防反推出来。
    """
    rows, extra_cells = _battle_cells(world, name, BATTLE_CELL_CAP)
    if not rows:
        return '（当前没有交战——你没有军队在打，视野内也没有别国在打。\n  要开打：`query panel=army` 选军 → `attack` 目标格；开打后这里会列出各方的\n  兵种构成、逐支 HP、输出基数，以及**仅防御方**吃的地形/城堡/合计减伤。）'
    out = [f'【交战】{len(rows) + len(extra_cells)} 处（只列你视野内、或有你军队在场的格；视野外且与你无关的仗不在此列）', '']
    order_hint = {'攻': 0, '守': 1, '旁观': 2}
    for x, y, sides, seen in rows:
        t = world.tiles.get((x, y))
        hall = world.visible_buildings(name, x, y).get('市政厅', 0)
        head = f'⚔ ({x + 1},{y + 1}){world.ter_char(x, y)}' + (f" 城L{world.visible_buildings(name, x, y).get('城堡', 0)}" if world.visible_buildings(name, x, y).get('城堡', 0) else '') + ('【市政厅·国祚】' if hall else '')
        if not seen:
            head += ' 【**盲战**·该格不在你视野内】'
        nm = (t or {}).get('name')
        head += '｜**无主野地**' if sides['owner'] is None else f"｜{sides['owner']} 的领土"
        head += f'「{nm}」' if nm else ''
        out.append(head)
        fs = sorted(sides['forces'], key=lambda F: (order_hint[sides['role'][F]], F))
        shown = fs if seen else [F for F in fs if F == name]
        for F in shown[:BATTLE_PARTY_CAP]:
            role = sides['role'][F]
            tag = {'攻': '攻方', '守': '守方', '旁观': '在场未参战'}[role]
            if F == name:
                tag += '（你）'
            if F == '野人':
                tag += '（野人）'
            elif role == '攻' and sides['soak_elig'].get(F):
                tag += '（兼格主，仍吃本格减伤）'
            out.append(f"   {tag} {F}：{_side_line(world, F, sides['forces'][F], sides)}")
            for a in sides['forces'][F][:BATTLE_UNIT_CAP]:
                extra_u = ''
                if F == name and a.get('retreat_to'):
                    tx, ty = a['retreat_to']
                    extra_u = f" ⚑撤退中→({tx + 1},{ty + 1})｜{retreat_note(a.get('retreat_role') != '攻', a.get('retreat_cover', 100))}"
                out.append(f"     · {a['name']}(#{a['id']}) {a['hp']}HP{extra_u}")
            if len(sides['forces'][F]) > BATTLE_UNIT_CAP:
                out.append(f"     …另 {len(sides['forces'][F]) - BATTLE_UNIT_CAP} 支")
        if len(shown) > BATTLE_PARTY_CAP:
            out.append(f'   …另 {len(shown) - BATTLE_PARTY_CAP} 方')
        if seen:
            terrain = t['terrain'] if t else world.tile_terrain(x, y)
            per_lv = building_effect('城堡', 'defense_per_level') or 1
            out.append('   减伤（**仅防御方**吃，进攻方恒 0）：')
            for F in fs:
                if sides['role'][F] == '攻':
                    continue
                td, cd, total = sides['soak_parts'][F]
                castle = f'城堡 L{cd // per_lv} +{cd}%' if cd else '城堡 —'
                note = '（格主）' if F == sides['owner'] else '（非格主，无城堡加成）'
                if td < 0:
                    note += ' ★负地形：反而多挨打'
                out.append(f'     {F}{note}：地形 {terrain} {td:+d}% × {castle} ⇒ 合计减伤 {total}%')
        else:
            out.append('   敌方：**敌情不明**（该格不在你视野内——守军构成、地形与城堡减伤均不可知，结算照常进行）')
        if name in sides['forces']:
            need, short, per = world.supply_shortfall(name)
            if short:
                out.append(f'   ⚠ 你补给断粮：仓不够，缺 {short} ⇒ **吃补给的每军 −{per}HP/回合**（交战中也照扣；驻自家军屯的免供民兵不在内）')
        out.append('   · 本格在交战 ⇒ 双方**本回合都不回血**（回血的前提是「不在交战格」）')
        out.append('')
    out.append('   · 本引擎无「阵营」：攻/守按每方自己的 engaged 与宣战关系**逐方**判定，同盟方各打各的。')
    out.append('   · 输出基数是 Σ兵种攻击的基数，**不含骰子**——实际结果看回合末结算。')
    if extra_cells:
        rest = '、'.join((f'({x + 1},{y + 1})' for x, y in extra_cells))
        out.append(f'   …另 {len(extra_cells)} 处：{rest}（用 `query panel=tile x= … y= …` 单看）')
    return '\n'.join(out)

def battle_brief(world, name) -> str:
    """一行交战摘要（每次行动后自动挂）—— 只答"有没有仗、这场仗读起来是不是在赢"。

    ★ 与 `_fmt_battle` **共用同一个门禁**：不可见的别国互殴既不进列表也不计数。
    ★ 尺寸有界：最多 `BATTLE_BRIEF_CAP` 格，其余只给个数（明细让 AI 自己 `query`）。
    """
    rows, extra_cells = _battle_cells(world, name, BATTLE_BRIEF_CAP)
    if not rows:
        return ''
    parts = []
    for x, y, sides, seen in rows:
        mine = name if name in sides['forces'] else None
        if mine is None:
            parts.append(f'别国交战({x + 1},{y + 1})' if seen else '')
            continue
        role = sides['role'][mine]
        hp_me = sum((a['hp'] for a in sides['forces'][mine]))
        seg = f"你{('攻' if role == '攻' else '守')}({x + 1},{y + 1}) 你{len(sides['forces'][mine])}支{hp_me}HP"
        if not seen:
            parts.append(seg + '/敌情不明【盲战】')
            continue
        foes = [F for F in sides['forces'] if F != mine and sides['role'][F] != '旁观']
        if foes:
            F = foes[0]
            seg += f"/{('守' if role == '攻' else '攻')}{len(sides['forces'][F])}支{sum((a['hp'] for a in sides['forces'][F]))}HP"
            if role == '攻' and sides['soak_elig'].get(F):
                seg += f"·守方减伤{sides['soak_pct'][F]}%"
            elif role == '守' and sides['soak_elig'].get(mine):
                seg += f"·你方减伤{sides['soak_pct'][mine]}%"
        parts.append(seg)
    parts = [p for p in parts if p]
    if not parts:
        return ''
    n = len(rows) + len(extra_cells)
    tail = f'＋另{len(extra_cells)}处' if extra_cells else ''
    return f'⚔交战{n}处：' + '；'.join(parts[:BATTLE_BRIEF_CAP]) + tail + '｜明细 query panel=battle'

EXPORTS = {n: v for n,v in globals().copy().items() if callable(v) and getattr(v,"__module__",None)==__name__}

def panel_army(world,actor,**args):return _fmt_armies(world,actor)

def panel_threats(world,actor,**args):return _fmt_threats(world,actor)

def panel_battle(world,actor,**args):return _fmt_battle(world,actor)
