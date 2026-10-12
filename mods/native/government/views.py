from __future__ import annotations
from ..core import *
from ..rules import *
from ..ui_constants import *
from .. import presentation as P
import copy,json,re,threading
from pathlib import Path
from ctx import est_tokens
TURN_STATE_HEAD = "以上为过往回合记录"
from console import dw as _dw, pad as _pad


def observer_board(world) -> str:
    """Observer 全景大面板：各国国库/储备/国土/军队/关系/近期通信，一屏尽览。"""
    alive = world.alive()
    L = [f"══════ 世界全景 · 第 {world.turn} 回合 · 现存 {'、'.join(alive)} ══════"]
    L.append('世界市场: ' + '  '.join((f'{g}{world.market_price(g)}' for g in GOODS_DISPLAY)))
    if world.blocs:
        L.append('联盟: ' + world.bloc_desc())
    for n in alive:
        r = world.nations[n].res
        L.append(f"◆ {n}：国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} 油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | {world.econ_summary.get(n, '')}")
        L.append(f'   国土 {len(world.own_tiles(n))} 块 | 军队 {len(world.nation_armies(n))} 支 | {world.rel_desc(n)}')
        msgs = []
        for m in [m for m in world.mailbox.get(n) or [] if m.get('turn') == world.turn][-1:]:
            msgs.append(f"收[{m['from']}]「{m['text'][:50]}」")
        for m in [m for m in world.mail_pending if m['from'] == n and m['arrive'] == world.turn + 1][-1:]:
            msgs.append(f"寄[{m['to']}]「{m['text'][:50]}」")
        if msgs:
            L.append('   ' + ' ⏐ '.join(msgs))
    dead = [n for n in world.order if n not in world.nations]
    if dead:
        L.append('亡国: ' + '、'.join(dead))
    return '\n'.join(L)

def observer_map(world) -> str:
    """Observer 整张世界地图：大写字母=该国占领并**按国别着色**，小写=无人地形（不着色）。

    颜色只落在"国家占领区"，好一眼看出势力版图；荒野/野人维持字母不着色。
    纯观感（各国 agent 看不到这张图），用支持 ANSI 的终端看 mp_map.txt 即为彩色。
    """
    palette = ['91', '32', '94', '35', '96', '33', '92', '34', '95', '93', '36', '97']
    ch = {}
    col = {}
    j = 0
    for i, n in enumerate(world.order):
        if n in world.nations:
            ch[n] = chr(65 + i) if i < 26 else str(i - 25)
            col[n] = palette[j % len(palette)]
            j += 1

    def cell(x: int, y: int) -> str:
        o = world.owned_by(x, y)
        if o:
            return f'\x1b[{col[o]}m{ch[o]}\x1b[0m'
        return world.ter_char(x, y).lower()
    rows = [''.join((cell(x, y) for x in range(world.size))) for y in range(world.size)]
    legend = ' '.join((f'\x1b[{col[n]}m{ch[n]}\x1b[0m={n}' for n in world.order if n in ch))
    return '\n'.join(rows) + f'\n地图例：{legend} | 小写p/f/h/m/d=平原/森林/丘陵/山地/沙漠（无人）野人亦在其上'

def strategy_manual() -> str:
    """【战略手册】：**盘面层**的判断——谁该被打、什么时候必须出手、怎么才不被拖死。

    ★ 用户 2026-10-09 口述要点，要求写成一本讲义：均势（不允许任何一国做大）／坐山观虎斗
      的结果是出现超级大虎、必须主动干预他国征服、及时围魏救赵／弱国也必须主动出手让强国
      四面受敌／外交是必须的，不外交就是肥肉／抢地断经济饿死守城军队是好手段，但拖时间会让
      后方变危险、招来均势战争／建筑集中好守、分散会被各个击破或被占领饿死／电厂要与当地
      工业配套／战争中资源集中才能坚守，不集中就必须出门打，静态防御不是运动战的对手，
      运动战配骑兵极强。

    ★ 挂法（用户 2026-10-09 选「**完全按需取**」）：**不挂进任何 system prompt**，
      只在 `rules(战略手册)` 按需返回；但《战争手册》里挂了一句"推荐阅读"把人引过来。

    ★ 与另三本的边界：经济手册讲「钱怎么滚」、基础指南讲「基础功」、
      战争手册讲「这一仗怎么打」；这本讲**盘面**——该不该打、打完谁做大、僵持值不值、
      守还是出门。**所以它没有逐条数值**，数字一律指回那三本与 `rules`。

    ★ **签名零参数、正文逐字节稳定**（与 `_econ_manual()`/`war_manual()` 同规矩）：
      不写回合号、市价、国名、任何对手数据。
    """
    return '一、默认立场：**均势**——不允许任何一国做大，包括你的盟友。\n  这局比的不是「谁打得赢一仗」，是「**最后还站着的是谁**」。吞掉一个国家的赢家，会连它的地、地上的建筑、产能一起拿走 ⇒ **别人变强不需要你输，只需要你不看**。\n  ⇒ 你的默认立场是：**谁是当前最大、增速最快的那个，谁就是你真正的对手**——它对你友不友好**不在考虑之内**；今天的朋友，就是明天的第一。\n  ⇒ 每局都要问自己一次：**谁在连胜？谁在吞并小国？谁的地与产能明显领先？**答案就是你的下一个战略目标。\n  ★★ **「第一」是算出来的，不是感觉出来的——而且算的时候必须把联盟当一国算**：盟主加全部成员的经济、军队、资产、产能**合在一起**才是它的体量。**一个四国联盟，永远大于其中任何一个成员**——**联盟正在吞人，你却把枪口对准它里面的一个中等成员，就是把「均势」读成了「先卖队友」**。\n  ★★ 所以本条**只有一个方向，别读反**：「今天的朋友，就是明天的第一」是叫你把盟友**列入观察名单**，不是叫你现在就动手。**顺序是先解决当前最大的那个**：盟友今天不是第一，它今天就不是你的目标。第一正在吞人时你去卖盟友，**等于替第一把遗产先清好场**——它吃地、你吃骂名，吃完它比你更大。\n  ⇒ 反过来看更直白：**你和第一之间的那个国家，是你最便宜的防线**。它活着，第一得先过它；它死了，下一个就是你——而且第一已经比原来更大。\n二、坐山观虎斗，观出来的是一只**超级大虎**。\n  两强相争时旁观最舒服：不费兵、不树敌、还能两边卖人情。但两强相争**不会自动两败俱伤**——赢的一方拿走失败者的全部。**你的中立，只是替它把这场仗变成一次低风险的收购。**\n  ⇒ 所以：**他国正在征服时，必须主动干预**。干预不等于出兵——外交围堵、参战、断它的外援、抢它还没吃完的地，都算。\n  ⇒ **别拿「我打不出去」当旁观的借口**：和约挡着、共同防御网挡着、一打就是一打多——那是**出兵**这条路上的障碍，不是**干预**这条路上的障碍。送钱让对方多撑几回合、把情报给它、逼它把兵分到第二处、抢在赢家前面去占那些即将无主的地——**这些一个宣战权都不需要**。\n  ⇒ **围魏救赵是最便宜的干预**：小国被围攻时，**去打围攻者的后方**（核心格、产金地、补给厂，乃至市政厅）——它必须回兵，围自然就解了。**你救的是均势，不是那个小国**：它多半还是活不长，但那不关你的事。\n三、**越弱越要主动出手**：坐视 = 排队等死。\n  弱国的死法通常不是「被打死」，是「**等死**」——强国一个个吃掉别人，你只是名单上的下一个。\n  ⇒ 弱国唯一的活法是**让强国四面受敌**：结盟、请援、给它的敌人送钱/送图/送情报、在它背后开第二战场。**任何能让它分兵的事都值得做**——包括花钱。\n  ⇒ 你不需要赢它，你只需要**让它赢得不够快**。强国最怕的不是输，是**同时打两场**。\n四、**不要长期僵持**。\n  僵持是纯支出：你每回合照付军费，而全世界都在复利（见 `rules(经济手册)` 第三节）。\n  更贵的是：僵持**会招来均势战争**——你把兵钉在一条没有战果的战线上，全世界都看见了。那正是别人眼里最肥的下手时机：**你在看别人，别人也在看你**。\n  ⇒ 开战就写下**期限与目标**（几回合内拔哪几座厅 / 拿哪几块产金地 / 逼它赔多少），到点没拿到就换手段——议和、换目标、撤兵重组——**别把「没输」当成果**。\n五、**外交不是善意，是工具**；不外交的代价，就是你当那块肥肉。\n  没人知道你、没人怕你、没人替你说话 ⇒ 打你**没有成本**，你就是送到嘴边的那块肉。\n  ⇒ 外交按用途排序：① **让别人不敢先动你**（保障独立 / 共同防御 / 联盟）；② **让别人去打别人**（给它的对手送钱、送图、送情报）；③ **把战争成本推给第三方**（买和约、拉人入伙参战）。\n  ⇒ 条约的价值不在于「它保证和平」，而在于**它让对手多犹豫的那几个回合**——你要的就是那几个回合。\n六、**抢地断经济**：好手段，但要算时间。\n  占住或拔掉产金地、补给厂，让它的军队**断供**——断供是按缺口比例**吃补给的军队一起扣血**（驻自家军屯的免供民兵不在此列），比硬啃要塞便宜得多。**围而不打是正经战法。**\n  但它有两个价钱：① 兵钉在那里，**你的后方就空了**；② 时间越长，**别人越可能对你发动均势战争**（见第四节）。\n  ⇒ 断经济是「短促手术」，不是「长期围困」：几回合内不见效，就改成直接拔厅收线，或者干脆撤。\n七、**集中**：建筑、电、兵，都要集中。\n  集中的意义只有一个：**你只有一处需要守**。资产四处分散 ⇒ 守军被**各个击破**，地**被占领后饿死**（产补给的地一丢，全军一起断供）。\n  ★★ **电要和工业配套，而且要跟工业待在同一个守得住的地方**：电网是**全国一口账**（全国发电 ≥ 全国耗电，否则**所有**耗电建筑——补给厂/装备厂/兵营/市政厅——**一起停摆**，不是停一部分）。\n  ⇒ 于是**一台电厂就是一个全国总闸**：电厂单独建在**你没兵守**的格子上，敌人一支孤军占掉它，**全国一起黑**——这比抢你的工业还便宜。**所以电厂要和工业同格（或同一簇）、跟着工业一起守**；发电量再**留出余量**（丢一格工业，不该让全国跟着黑）。\n  ⇒ **电厂本身另有一笔账**（木电与油电的造价、燃料差多少）：见 `rules(基础指南)` 第三节。\n  ⇒ 别把工业摊成「哪儿有资源就盖哪儿」的一片：那种布局守不住，也救不回来。\n八、战争里：**守得住的前提是「你的资源确实集中」**；不集中就必须出门打。\n  资源集中 ⇒ **可以坚守不出**：往同一格堆厚、吃地形与城堡减伤（守方减伤相乘），让进攻撞墙——这是「坚守」唯一成立的前提。\n  资源分散 ⇒ **坚守就是等死**：它绕开你守的那格去打别的格，你的兵被钉在城里，被一格一格拔掉。⇒ 这时必须**主动出门**：迎击它的落单分队、打它的补给线与后方，**逼它回防**——把「它选在哪打」变成「你选在哪打」。\n  ★★ **任何静态防御都不是运动战的对手**：运动战决定**在哪打、什么时候打、打你哪一格**，你只能应；**它常能击败几倍兵力的静态防御**——因为它每次打的都是你分兵后的一角，**局部以多打少**。\n  ⇒ **骑兵是运动战的核心**：平地每回合 2 格 ⇒ 堆得快、撤得快、绕得过（数值见 `rules(战争手册)` 第五节）。它给运动战三样东西：**快速集中、快速脱离、绕到对手视野之外再出现在它后方**。\n九、**附：孙子兵法摘抄**（春秋·孙武《孙子兵法》）。\n  ⚠ **这不是本作规则**——是两千多年前打仗的常识，**每条后面注明它在「本作」里对应哪件事**（对不上的老话我没收）。看不懂机制就先看前面八节。\n  · 「兵者，国之大事，死生之地，存亡之道，不可不察也。」（始计）\n    ⇒ 出兵前先算清「几回合拔几座厅」——亡国只认**市政厅尽失**，一场仗赌的是国运。\n  · 「知彼知己，百战不殆。」（谋攻）\n    ⇒ 你只看得见自家国土 + 相邻一圈。**别国的底细只能靠 `spy` / `buy_report` / 来信 / 视野推过去**：不开视野就打，是蒙着眼下注。\n  · 「兵贵胜，不贵久。」「兵闻拙速，未睹巧之久也。」「久暴师则国用不足。」（作战）\n    ⇒ 军费是**每回合**照付的现金（`rules(经济手册)` 第三节）：拖的每一回合都在烧钱，而对手在复利。**速战速决不是风格，是账**。\n  · 「攻其无备，出其不意。」（始计）\n    ⇒ 视野之外就是**盲飞区**：骑兵一回合 2 格，贴着野地/自家地绕过去，**它直到你踏进它视野才看得见你**（战争手册 §五）。\n  · 「百战百胜，非善之善者也；不战而屈人之兵，善之善者也。」（谋攻）\n    ⇒ 本作**杀兵不得分**：战果只记「拔了几座厅、净拿几块站得住的地」（战争手册 §二）。\n  · 「上兵伐谋，其次伐交，其次伐兵，其下攻城。」（谋攻）\n    ⇒ 本作的四种手段**优先级一模一样**：**伐谋**＝让它的计划不成立（保障/联盟锁死它）；**伐交**＝拆它的外援（策反、断盟、抢它没吃完的地）；**伐兵**＝野战；**攻城最贵**（守方吃地形 × 城堡**相乘**减伤）。\n  · 「胜兵先胜而后求战，败兵先战而后求胜。」（军形）\n    ⇒ 落锤前先 `query panel=battle` 把支数/兵种/HP/减伤摊开算，**算出来是「稳操」再动手**（战争手册 §四那三档）。\n  · 「故形人而我无形，则我专而敌分。」（虚实）\n    ⇒ 集中是本作第一原则：伤害＝各军攻击**之和**、受击**按人头分摊** ⇒ 堆在一格既打得更疼、每人又挨得更少。\n  · 「我专为一，敌分为十，是以十攻其一也。」（虚实）\n    ⇒ **运动战的全部秘密：局部以多打少**。它要守十个点，你只打其中一个点。\n  · 「善战者，致人而不致于人。」（虚实）\n    ⇒ 主动权：把「它选在哪打」变成「**你**选在哪打」——没主动权的坚守就是等死。\n  · 「凡先处战地而待敌者佚，后处战地而趋战者劳。」（虚实）\n    ⇒ 抢位置：骑兵平地 2 格就是为了**先到**——先占格的人吃地形减伤，后到的只能撞。\n  · 「知可以战与不可以战者胜。」（谋攻）\n    ⇒ 动手门槛：**输出高出三成**就是「决定性的歼灭战」（战争手册 §四），别拿「等双倍」当拖延的借口。\n  · 「无恃其不来，恃吾有以待也。」（九变）\n    ⇒ 后方安全是**假设**，每回合都要验：有厅没驻军、且看得见的敌军本回合够得着 ⇒ 假设已破（战争手册 §一）。\n  · 「善用兵者，役不再籍，粮不三载。」（作战）\n    ⇒ 补给线就是命：断它的补给厂与产金地 ⇒ 它**吃补给的军队**按缺口一起掉血（第六节）。\n'

def _chapter_key(title: str) -> str:
    """章标题 → 短名（去掉「一、二、…」序号）。"""
    return re.sub('^[一二三四五六七八九十]+、', '', title).strip()

def _clean_manual(text: str) -> str:
    """章内清理：去 HTML 注释、去外链（留文字）、去**设计/实测注记**。

    ★ 只删不增，且只删"明显是写给开发者看的"那几种形状——**宁可漏删**：
    漏删的后果是 AI 多读一句；误删的后果是**规则没了**。
    """
    t = re.sub('<!--.*?-->', '', text, flags=re.DOTALL)
    t = re.sub('\\[([^\\]]*)\\]\\([^)]*\\)', '\\1', t)
    t = re.sub('（[^（）]*(?:\\.py\\b|_[a-z_]{3,}\\b|[A-Z][A-Z_]{3,}\\b|[A-Z][a-z]+\\.[a-z_]+|20\\d\\d-\\d\\d|实测|补记|用户)[^（）]*）', '', t)
    return re.sub('\\n{3,}', '\n\n', '\n'.join((ln.rstrip() for ln in t.splitlines()))).strip()

def manual_sections() -> list[tuple[str, str]] | None:
    """doc 目录《规则纲要》→ `[(章标题, 正文)]`（`###` 子节并入所属章）。

    读不到文件返回 `None`——调用方负责**优雅降级**，绝不让一次读盘失败崩掉整个回合。
    """
    try:
        raw = MANUAL_PATH.read_text(encoding='utf-8')
    except OSError:
        return None
    blocks: list[tuple[str, str, list[str]]] = []
    for ln in raw.splitlines():
        if ln.startswith('## '):
            blocks.append(('h2', ln[3:].strip(), []))
        elif ln.startswith('### '):
            blocks.append(('h3', ln[4:].strip(), []))
        elif blocks:
            blocks[-1][2].append(ln)
    merged: list[tuple[str, str]] = []
    for kind, title, lines in blocks:
        if kind == 'h2':
            merged.append((title, '\n'.join(lines)))
        elif not any((title.startswith(p) for p in MANUAL_SKIP)) and merged:
            head, body = merged[-1]
            merged[-1] = (head, f'{body}\n\n〔{title}〕\n' + '\n'.join(lines))
    out: list[tuple[str, str]] = []
    for title, body in merged:
        if any((title.startswith(p) for p in MANUAL_SKIP)):
            continue
        b = _clean_manual(body)
        if b:
            out.append((title, b))
    return out

def _lectures() -> list[tuple[str, str]]:
    """四本**讲义**（在代码里，各有独立 doc 与 sync 通道）：经济 / 基础 / 战争 / 战略手册。"""
    return [('经济手册', P._econ_manual()), ('基础指南', P.opening_guide()), ('战争手册', P.war_manual()), ('战略手册', strategy_manual())]

def rules_text(world, topic: str='') -> str:
    """主题、关键字及正文均来自加载的 Mod。"""
    return _clean_manual(world.runtime.rules(world, topic))

def _fmt_alerts(world, name) -> str | None:
    """**领土警报**——钉在状态面板**最前**（用户 2026-09-21：「领土变更，被侵略是大事，得强调」）。

    原先这些只落在面板**最末尾**的【近讯】里，前面压着几百行地图与十来个面板 ⇒ 一屏滚过去
    就没了紧迫感；而"我刚丢了地/敌人正踩在我地上"是**要立刻改国策**的事。

    只报**自上次行动以来**（上一轮结算 + 本回合，`turn >= world.turn - 1`）真正发生的：
      ✖ **失地**：被夺走的自己的地（♥ 标核心——那种"同战线盟友夺回会自动归还"，口径不同；
        带市政厅的格另标「市政厅」——2026-09-21 起它是**国祚**，丢光了当场亡国）；
      ＋ **得地**：自己夺来/拓来的地（进项也一并报，免得只报坏消息）；
    两样都没有 ⇒ 返回 None，**整段不出现**（不白占 token）。

    ★ 不列"敌军站在我地上"（用户 2026-09-21：「**不存在踩上去没丢地，脱裤子放屁**」）：
    进我的地只能靠 `atk`，那一脚就已经进交战了——回合末结算要么这格易主（上面 ✖ 失地
    那行会报）、要么它被打退；而"视野内他国军队"本来就在【威胁】面板里（含对角/盟国
    共享视野/瞭望塔半径）。单列一行纯属重复。
    ★ 也不按"国境/境内"说话：本引擎没有这种多边形模型，领土是**一格一格的地块**，
    看见口径只有 `visible_to`（该格本身或含对角的相邻格里有自家/盟国地）。

    ★ 全部按**结构化字段**查（`phase/nation/lost_by/x/y`），**不解析文本**：
    措辞是给人读的，解析它等于把面板钉死在文案上。
    """
    since = world.turn - 1
    lost: list[str] = []
    gain: list[str] = []
    for h in world.history:
        if h.get('phase') != '领土' or int(h.get('turn', 0)) < since:
            continue
        x, y = (h.get('x'), h.get('y'))
        if x is None or y is None:
            continue
        t = world.tiles.get((x, y)) or {}
        where = f"「{t.get('name') or '?'}」({x + 1},{y + 1})"
        if h.get('lost_by') == name:
            tags = '，'.join((x for x in ('♥核心' if t.get('core') == name else '', '市政厅' if (t.get('buildings') or {}).get('市政厅') else '') if x))
            lost.append(where + f" 被 {h.get('nation')} 夺去" + (f'（{tags}）' if tags else ''))
        elif h.get('nation') == name:
            gain.append(where + (f" 夺自 {h['lost_by']}" if h.get('lost_by') else ' 拓疆'))
    rows: list[str] = []
    if lost:
        rows.append(f'  ✖ **失地 {len(lost)} 块**：' + '；'.join(lost))
    if gain:
        rows.append(f'  ＋ 得地 {len(gain)} 块：' + '；'.join(gain))
    if not rows:
        return None
    return '⚠ 【领土警报】\n' + '\n'.join(rows)

def _fmt_news(world, name) -> str:
    ev = world.events_for(name, limit=10)
    return '近讯:\n  ' + '\n  '.join(ev) if ev else '近讯: 暂无'

def _fmt_memory(world, name, since: int | None=None) -> str:
    """本国回合小结纪事（私有记忆，别国不可见）。

    since=已进 replay 的最早回合 → 只保留 replay 覆盖不到的更早小结，避免和完整记录重复。
    """
    mem = world.summaries.get(name, [])
    if since is not None:
        mem = [m for m in mem if int(m['turn']) < since]
    if not mem:
        return '（无——近况见上文完整记录）' if since is not None else '（尚无往回合小结）'
    return '\n'.join((f"  [第{m['turn']}回合] {m['text']}" for m in mem[-10:]))

def _fmt_plan(world, name) -> str:
    """国策规划（常驻上下文）：无 plan 不能结束回合；每 PLAN_MAX_TURNS 回合须修订。"""
    pl = world.plans.get(name)
    if not pl or not str(pl.get('text', '')).strip():
        return '（尚未制定）——结束回合(end_turn)前必须先 plan(content=…) 制定国策；可从 经济发展 / 军事规划 / 情报管理 / 外交方向 四方面写明目标'
    since = world.turn - pl.get('turn', world.turn)
    flag = ''
    if since >= PLAN_MAX_TURNS:
        flag = f' ⚠ 已 {since} 回合未修订（每 {PLAN_MAX_TURNS} 回合必须修订一次才能结束）'
    elif PLAN_MAX_TURNS - since == 1:
        flag = f' ⚠ 本回合必须修订（下回合即满 {PLAN_MAX_TURNS} 回合）'
    return f"[第{pl.get('turn')}回合制定/修订]{flag}\n  {pl['text']}"

def _fmt_intel_hint(world, name) -> str:
    """收到的地图情报摘要（简短提示，完整见 query panel=intel）。"""
    ms = world.maps.get(name, [])
    if not ms:
        return f'无（可用 share_map 与别国互发地图：对方下回合到；或 spy 间谍偷地图：{SPY_TURNS} 回合后到）'
    last = ms[-1]
    return f"收到 {len(ms)} 张，最新来自 {last['from']}（第{last['turn']}回合）；完整内容见 query panel=intel"

def _fmt_intel(world, name) -> str:
    """完整地图情报：最近收到的别国地图。"""
    ms = world.maps.get(name, [])
    if not ms:
        return '（你尚未收到别国的地图情报）'
    out = [f'收到 {len(ms)} 张地图情报（按到达先后）:']
    for m in ms[-3:]:
        out.append(f"── 第{m['turn']}回合 {m['from']} 的地图 ──")
        out.append(m['text'])
    return '\n'.join(out)

def _fmt_spy_hint(world, name) -> str:
    """收到的间谍情报摘要（完整见 query panel=spy）。"""
    es = world.econ_intel.get(name, [])
    alt = f'；或 buy_report 花 {BUY_REPORT_COST} 金买它**已公布**的经济报表（明细，当场到手）' if world.bank_on() else ''
    if not es:
        return f'无（可用 spy 花{SPY_COST}金刺探别国，{SPY_TURNS}回合后到手经济底细+外交关系+粗略军情数量+地图{alt}）'
    last = es[-1]
    return f"{len(es)} 份，最新 {last['from']}（第{last['turn']}回合）；完整见 query panel=spy{alt}"

def _fmt_spy(world, name) -> str:
    """完整间谍情报：最近拿到的别国经济底细 + 外交关系 + 粗略军情（各兵种数量）。"""
    es = world.econ_intel.get(name, [])
    if not es:
        return '（你尚未拿到任何间谍情报）'
    return '\n'.join((m['text'] for m in es[-2:]))

def full_state(world, name, replay_since: int | None=None, map_text: str | None=None) -> str:
    """本回合的新鲜状态（上下文尾部）。replay_since 见 turn_state。

    ★ **【威胁】排最上 + 【领土警报】紧随**（用户 2026-09-21：「威胁排最上」；丢地/被侵略
    是"立刻改国策"的事）：原先【威胁】压在第 5 段（国力/国策/地图/军队之后）、丢地在最末尾的
    近讯里，一屏滚过去就没了紧迫感。警报没事时整段不出现。
    """
    others = '、'.join((n for n in world.alive() if n != name)) or '（只剩你）'
    alerts = _fmt_alerts(world, name)
    hall_alert = P._fmt_hall_alert(world, name)
    return '\n'.join([f'你（{name}）现在进行第 {world.turn} 回合的行动。其余国家：{others}。', f'【威胁】\n{P._fmt_threats(world, name)}', *([hall_alert] if hall_alert else []), *([alerts] if alerts else []), world.decision_overview(name), f'【国力】\n{P._res_line(world, name)}', f'【国策规划】\n{_fmt_plan(world, name)}', f'【国土/视野】\n{(P._fmt_atlas(world, name) if map_text is None else map_text)}', f'【军队】\n{P._fmt_armies(world, name, brief=True)}', f'【市场】\n{P._fmt_market(world, name)}', *([f'【央行】\n{P._fmt_bank(world, name)}'] if world.bank_on() else []), f'【经济报表】\n{P._fmt_report_panel(world, name)}', f'【纪事(近10回合)】\n{_fmt_memory(world, name, replay_since)}', f'【地图情报】\n{_fmt_intel_hint(world, name)}', f'【间谍情报】\n{_fmt_spy_hint(world, name)}', f'【信箱】\n{P._fmt_mail(world, name, brief=True)}', f'【外交】\n{P._fmt_diplomacy(world, name)}', f'【近讯】\n{_fmt_news(world, name)}'])

def compact_state(world, name) -> str:
    r = world.nations[name].res
    n_army = len([a for a in world.armies if a['owner'] == name])
    box = world.mailbox.get(name, [])
    b = P.battle_brief(world, name)
    return f"【刷新】你{name} 国库{r['黄金']} 粮{r['粮食']} 木{r['木头']} 矿{r['矿石']} 油{r['石油']} 装{r['装备']} 补给仓{r['补给']} | 军队{n_army}" + (f' | {b}' if b else '') + f' | 收信{len(box)} | 关系:{world.rel_desc(name)} | 近讯见 events。继续你的行动，做完了调 end_turn。'

def _mem_cut(text: str, n: int) -> str:
    text = ' '.join(str(text or '').split())
    return text if len(text) <= n else text[:n] + '…'

def _memory_search(world, name: str, args: dict) -> str:
    """按关键词搜本国历史记忆的正文，返回命中回合号 + 相邻上下文。

    只搜 `turn_memory`/`summaries`/`summary_blocks`/`long_memory`/`plans` 的
    **content**（assistant 的 reasoning_content 刻意不索引——那是思考不是事实）；
    只搜得到该国自己的记忆，符合迷雾纪律。免费、只读。
    """
    query = str(args.get('query', '') or '').strip()
    try:
        limit = max(1, min(8, int(args.get('limit') or 3)))
    except (TypeError, ValueError):
        limit = 3
    if not query:
        return '用法：memory_search(query=关键词 用空格分隔多个词, limit=条数)，例：memory_search(query=盟约 楚)'
    _sep = {',', '，', '、', ';', '；', '\t'}
    kws = [k for k in ''.join((c if c not in _sep else ' ' for c in query)).split() if k]
    if not kws:
        return f'记忆检索「{query}」：空关键词。'
    units: list[tuple[str, str, str, str]] = []
    for rec in world.turn_memory.get(name) or []:
        msgs = rec.get('messages') or []
        for i, m in enumerate(msgs):
            is_state = i == 0 and str(m.get('content') or '').startswith(TURN_STATE_HEAD)
            parts = []
            if m.get('role') == 'tool':
                parts.append(str(m.get('content') or ''))
            elif not is_state:
                if m.get('content'):
                    parts.append(str(m['content']))
                for tc in m.get('tool_calls') or []:
                    fn = tc.get('function') or {}
                    parts.append(f"{fn.get('name')}({fn.get('arguments')})")
            text = ' '.join((p for p in parts if p))
            if not text:
                continue
            prev = _mem_cut(msgs[i - 1].get('content'), _MEM_NEIGH) if i > 0 else ''
            nxt = _mem_cut(msgs[i + 1].get('content'), _MEM_NEIGH) if i + 1 < len(msgs) else ''
            units.append((str(rec.get('turn', '?')), text, prev, nxt))
    for it in world.summaries.get(name) or []:
        units.append((str(it['turn']), '小结：' + str(it.get('text') or ''), '', ''))
    for b in world.summary_blocks.get(name) or []:
        units.append((f"{b['from']}-{b['to']}", '阶段总结：' + str(b.get('text') or ''), '', ''))
    lm = (world.long_memory.get(name) or '').strip()
    if lm:
        units.append(('长期记忆', lm, '', ''))
    pl = (world.plans.get(name) or {}).get('text', '')
    if pl:
        units.append((str((world.plans.get(name) or {}).get('turn', '') or 0), '国策：' + str(pl), '', ''))
    scored = [(sum((1 for k in kws if k in u[1])), u) for u in units if any((k in u[1] for k in kws))]
    if not scored:
        return f'记忆检索「{query}」：没有命中（正文检索，不含思考过程）。'

    def _tkey(x):
        try:
            return -int(x[1][0])
        except (TypeError, ValueError):
            return 1
    scored.sort(key=lambda x: (-x[0], _tkey(x)))
    L = [f'📇 记忆检索「{query}」命中 {len(scored)} 处（正文，不含思考），列出前 {limit}：']
    for i, (_, u) in enumerate(scored[:limit], 1):
        tag, text, prev, nxt = u
        head = f'第{tag}回合' if tag.isdigit() else f'【{tag}】'
        L.append(f'{i}. {head} ▸ {_mem_cut(text, _MEM_CUT)}')
        if prev or nxt:
            L.append(f"    └ 相邻：{prev}{(' … ' if prev and nxt else '')}{nxt}")
    return '\n'.join(L)

EXPORTS = {n: v for n,v in globals().copy().items() if callable(v) and getattr(v,"__module__",None)==__name__}


def panel_overview(world,actor,**args):return world.decision_overview(actor)

def panel_state(world,actor,**args):
    return json.dumps(world.player_state(actor,since_event=int(args.get('since_event',0))),ensure_ascii=False)

def panel_pending(world,actor,**args):return json.dumps(world.player_state(actor)['pending'],ensure_ascii=False)
def panel_targets(world,actor,**args):return json.dumps(world.player_state(actor)['current']['visible_townhalls'],ensure_ascii=False)
def panel_results(world,actor,**args):return json.dumps(world.player_state(actor)['results'],ensure_ascii=False)

def panel_all(world,actor,**args):return full_state(world,actor)

def panel_plan(world,actor,**args):return _fmt_plan(world,actor)

def panel_news(world,actor,**args):return _fmt_news(world,actor)

def panel_intel(world,actor,**args):return _fmt_intel(world,actor)

def panel_spy(world,actor,**args):return _fmt_spy(world,actor)
