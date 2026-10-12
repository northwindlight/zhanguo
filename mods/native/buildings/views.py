from __future__ import annotations
from ..core import *
from ..rules import *
from ..ui_constants import *
from .. import presentation as P
import copy,json,re,threading
from pathlib import Path
from ctx import est_tokens
from console import dw as _dw, pad as _pad


def opening_guide() -> str:
    """【基础指南】：**功底的账**——开局怎么下手（前 20 回合强行挂载），外加电与工业怎么配。

    来源是八国局（size 36 / 第 184 回合）的尸检结论，用户 2026-09-26 拍板：
    「他们一开始居然不知道把建筑放在首都的市政厅好，会产生额外利润，这次得写明白点，
    写一本开局指南，强行挂开局 20 回合」。

    ★ **2026-10-09 改名**：原名《开局指南》。用户原话「开局手册也可以查，改成基础指南」
      ——它本来就是**随时可查**的基础功（不是只在开局有用），名字别再把人劝退。
      函数名/开关名 `opening_guide` **保留**：那是"开局前 N 回合强行挂载"这个**挂法**的名字，
      `mp_config` 的 `opening_guide` 开关与旧档都靠它，不动。

    ★ **2026-10-09 加第七节「电」**（用户口述）：**木电厂 ROI 低、油电厂高得多，也不容易断供**。
      数字**现读 `balance`**（造价/木料/燃料/出电），所以平衡一改这节自己跟着走。

    两件事要说清：
    ① **它不是新规则**——每一条都能在 `rules(建筑)` / `rules(地形)` 里查到；
       它做的是把"规则"变成"该做的动作"，并点名最贵的那几个坑；
    ② **正文里的数字一律现读 `balance`/`game`**（厅金公式、门槛位、损耗、城墙、电厂账），不手抄。

    挂法：`World.opening_guide` 为真时，由 `system_prompt` 在**前 EXTRA_PROMPT_TURNS 回合**
    追加；过期后仍可用 `rules(基础指南)` 取回（走 `_lectures()` 的讲义表）。
    六节：①核心格最便宜 ②资源位 vs 建筑位 ③生产链 ④野人是静态的 ⑤攻城的价 ⑥开局次序。

    ★ **签名必须保持零参数、正文必须逐字节稳定**（与 `_econ_manual()` 同规矩，见 `ctx.py`
    开头那条"system 必须逐字节稳定"）：它进的是 system 前缀，随回合变一个字就是**每回合**
    整段前缀连同 replay 一起失效。窗口到期那一次失效（第 EXTRA_PROMPT_TURNS 回合）是有意的，
    也只该付那一次——所以这里**不许**取 world/name，不许写"第 N 回合"、价格、国名。
    ★ 另一条硬约束：正文里**不许出现 `世界央行` / `buy_report`**——`rules_text(w, "")` 无主题时
    会把全部章节（含本节）拼起来，而 tests/test_bank.py 断言"央行关着时全文不含这两个词"。
    """
    hall = BUILDINGS['市政厅']
    hall_base = building_effect('市政厅', 'gold_base')
    hall_slot = building_effect('市政厅', 'gold_per_slot')
    hall_min = hall['min_slots']
    bar_min = BUILDINGS['兵营']['min_slots']
    eng_min = BUILDINGS['工程院']['min_slots']
    dip_min = BUILDINGS['外交中心']['min_slots']
    eng_disc = building_effect('工程院', 'build_discount')
    castle_def = building_effect('城堡', 'defense_per_level')
    gold_mine = BUILDINGS['黄金矿场']['outputs']['黄金'] * MARKET['黄金']
    sup_in = BUILDINGS['补给厂']['inputs']
    sup_out = BUILDINGS['补给厂']['outputs']['补给']
    ar_in = BUILDINGS['装备厂']['inputs']
    ar_out = BUILDINGS['装备厂']['outputs']['装备']
    wood_e = BUILDINGS['木材能源厂']['energy_out']
    oil_e = BUILDINGS['石油能源厂']['energy_out']
    wp, op = (BUILDINGS['木材能源厂'], BUILDINGS['石油能源厂'])
    n_w = oil_e // wood_e
    wood_capex = n_w * wp['cost'] + n_w * wp['wood'] * MARKET['木头']
    oil_capex = op['cost'] + op['wood'] * MARKET['木头']
    wood_fuel_gold = n_w * wp['fuel']['木头'] * MARKET['木头']
    oil_fuel_gold = op['fuel']['石油'] * MARKET['石油']
    terr = '、'.join((f"{k} {v['defense']:+d}%" for k, v in TERRAIN_STATS.items()))
    elec = '、'.join((f"{k}×{v['energy']}" for k, v in BUILDINGS.items() if v.get('energy')))
    return f"开局前 {EXTRA_PROMPT_TURNS} 回合，这一节会**直接挂在你的 system prompt 里**（所以你现在读得到它；过期之后仍可用 `rules(基础指南)` 随时取回）。\n  下面**没有一条是新规则**——每一条都能在 `rules(建筑)`/`rules(地形)` 里查到；它做的事只有一件：把那些规则变成**该做的动作**，并点出最贵的几个坑。\n一、你脚下那一格，是全场最便宜的地。\n  你的**核心格必为平原**（建造 0 惩罚），且**开局自带一座市政厅**（＝国祚：被拔光就亡国）。市政厅每回合给你 **{hall_base} + 本格其他建筑数×{hall_slot}** 金 ⇒ **往这一格上放的每一座建筑，除了它自己的产出，还白送 {hall_slot} 金/回合**。所以开局最赚的一件事：**把「不占资源位」的建筑往核心格上堆**（兵营、补给厂、装备厂、电厂、工程院、瞭望塔、城堡）。两条限制：**每地块每回合只能下 1 座单**（堆格子是慢工，要趁早起步）、**每格最多 {MAX_SLOTS} 个建筑位**（城堡级数也占位）。\n  （八国局实测：秦、魏、韩、周前 60 回合在自家核心格上一座都没盖；燕盖了，14 座厅平均每格 3.3 座 ⇒ 厅金 119 金/回合，若每格堆到 6 座则是 154——**同一张图、同一条规则，差出来的全是白捡的**。）\n二、采集类看资源位，工厂类只看建筑位。\n  林场/农场/矿场/石油厂/黄金矿场**受本地块资源量限制**（格子写着「木头×3」就只能盖 3 座林场）；军屯限耕地且每格限 1 座。兵营/补给厂/装备厂/电厂/市政厅/工程院/瞭望塔/外交中心**不受资源位限制** ⇒ 它们才是堆核心格的主力。\n  这两类的**成本结构也是分开的**：基础楼**料贵钱贱**（木材要得多、金要得少），高级楼**钱贵料不变**——所以「光有钱」铺不出一片采集楼，「光有木」也上不了工厂，两条腿都得有（查 `rules(建筑)` 看现价）。\n  **黄金矿场**（{BUILDINGS['黄金矿场']['cost']} 金 + {BUILDINGS['黄金矿场']['wood']} 木）每回合产 1 黄金 = **{gold_mine} 金/回合**，是最肥的金源——见到黄金位就占。\n  另记一条容易漏的：**军屯本身不产东西**，它是**民兵编制**——每屯每回合可征 {building_effect('军屯', 'militia_per_turn')} 支、可驻 {building_effect('军屯', 'militia_cap')} 支（全国民兵总数 ≤ 军屯数×{building_effect('军屯', 'militia_cap')}），且同格那几支**不吃补给**（民兵比正规军便宜、驻自家军屯格免维持），开局的廉价守备就靠它。\n  有门槛的建筑：兵营需本格已用 ≥{bar_min} 位、工程院 ≥{eng_min}、外交中心 ≥{dip_min}、市政厅 ≥{hall_min}。\n三、生产链（这条最常被记错）。\n  补给厂吃 {'+'.join((f'{k}{v}' for k, v in sup_in.items()))} → 产补给 {sup_out}；装备厂吃 {'+'.join((f'{k}{v}' for k, v in ar_in.items()))} → 产装备 {ar_out}；木材能源厂烧 木头1 → 电 {wood_e}，石油能源厂烧 石油1 → 电 {oil_e}。\n  ⇒ **木头只去两个地方：建材、发电——它不是任何工厂的原料**。\n  耗电建筑（{elec}）在**电网不足时全部停摆**（不是停一部分）⇒ 上耗电件之前先算电网余量（= (发电−耗电)/耗电）——**余量低于 {GRID_RESERVE_WARN:.0%} 时，国力栏每回合都会提醒你**，因为那时「刚好够」已经等于悬崖边。\n  ★ **电本身：木电厂是过渡品，油电厂是主力。** 拿到同样多的电，两边差得离谱（按基准价折金）：\n    · 木电 {oil_e} 度 = **{n_w} 座**木材能源厂 = {n_w}×{wp['cost']} 金 + {n_w * wp['wood']} 木 ≈ **{wood_capex:.0f} 金**，每回合烧 {n_w} 木 ≈ {wood_fuel_gold:.0f} 金；\n    · 油电 {oil_e} 度 = **1 座**石油能源厂 = {op['cost']} 金 + {op['wood']} 木 ≈ **{oil_capex:.0f} 金**，每回合烧 1 石油 ≈ {oil_fuel_gold:.0f} 金。\n    ⇒ **同样的电，油电的造价只有木电的约 {oil_capex / wood_capex:.0%}**，**光木料就省 {n_w * wp['wood'] - op['wood']}**——而木头是你盖每一座楼都要花的建材。\n  ★★ 更要紧的是**断供**：木头**同时是建材**，你一边盖楼、一边发电，木材被建造抽干、电厂就烧不动，而**电网一停是全国停摆**；石油只有发电与装备厂两个出口、还有石油厂专门产它 ⇒ **不容易被别的用途抢走**。\n  ⇒ 结论：**早期用木电把电网顶起来**（便宜、木头近在手边），**一旦有油（占石油位或买油）就尽快换油电**——省木料、省燃料、还不容易全城停电。\n  ★ **你的工业每回合要吃多少料，【国力】栏一直替你算着**：「工业每回合耗料」那一行列出电厂燃料与工厂投料，**任何一项库存掉到「不够一回合」就会标 ⚠**——那是「下回合就断」的意思，别等它发生：断料的那批厂当回合就白盖了。\n四、野人是静态的：开局的扩张零风险、不要钱。\n  全图每一格无主地都站着**一支野人**，它们**永不移动、永不增援、死了不重生**。实测：**2 支满血步兵**打一格野人**稳拿**，代价随地形递增——平原合计损 ~70 HP、森林 ~79、丘陵 ~94、山地 ~120 ⇒ **别拿伤兵去打山地**（残兵上去会全灭）。另外两条硬规矩：进攻目标**必须与军队所在格相邻**（隔 2 格会被拒），且**每支军每回合只能动一次**。\n五、攻城没那么可怕，但也确实要付钱。\n  守方吃**地形减伤 × 城堡减伤**（相乘）：{terr}，城堡每级 {castle_def}%；**谁挨打谁是守方**（交战中的进攻方不吃加成）。实测（4 支满血守军、平原城 L1）：**6 支攻方稳拿**（约 2 回合、损 4 支军），8 支损 3 支出头 ⇒ **2:1 兵力就够**。「打不下来」多半只是没凑够兵。\n  另记一条常被忽略的成本：**伤兵回血不是免费的**——每支回血的军队每回合吃 {HEAL_EQUIP_COST} 件装备（**民兵除外**）。所以「打完一仗」的账要连修兵一起算。\n六、前 20 回合的次序（建议，不是规则）。\n  ① **第 1~3 回合**：核心格先落 **{bar_min - 1} 座不占资源的建筑**（够了 {bar_min} 位才能盖**兵营**）；同时在资源最密的自己格上铺采集楼；**开工前先买料**（木价低于均衡时囤木）。\n  ② **第 4~10 回合**：征兵 2 支，开始吃相邻野地；核心格继续一座一格地堆，堆到 {eng_min} 位补**工程院**（本格建造 −{eng_disc}%）。\n  ③ **第 10~20 回合**：补电网（**早期木电顶，有油就换油电**——见第三节的账）→ 上**补给厂**（军队每回合吃补给，断供掉血）；挑一块资源密的格堆到 {hall_min} 位，盖**第 2 座市政厅**（多一条国祚、多一份厅金）。\n  ④ 三条底线：**电网别欠**（欠了工厂和厅一起停）、**补给仓别空**、**国库别囤着**——钱只有变成建筑和军队才算数。"

def _fmt_hall_alert(world, name) -> str | None:
    """**国祚警报**：我有厅的格上没人守，**且看得见的敌军本回合就够得着** ⇒ 假设已破。

    来由（用户 2026-10-07，实盘秦魏之战）：秦把国祚连同**全部战争工业**（补给厂×7 /
    装备厂×3 / 兵营 / 能源厂 / 市政厅）堆在晴桥一格，又为一场"闪击战"把兵全压到前线
    ——自家南翼一兵未留。魏一支步军 `atk` 进空格：**敌人为 0、零战斗直接进驻**，
    秦当场丢了工业首都；那两格能源厂一掉**电网停摆**，其余全部停产。

    ★★ 触发条件**不是**"哪座厅上没有兵"（第一版就是那么写的，被用户当场驳回两次）：

      · 「**不是不堆到一格，不集中意味着你根本无法防守**」——集中是对的（手册 §四
        「要守的点少而厚」），错的是集中了却空着；
      · 「**为什么要把根本不可能被进攻的地守一堆军队，等于学燕，被运动战早晚打烂**」
        ——要求"每座厅常驻"就是把兵钉死在家里挨运动战。

    口径落成用户那句话：**你的安全假设必须成立**。所以这条警报的语义是
    「**你这个假设，此刻已经不成立了**」——那里没人 **且** 有人**本回合就能走进去**。
    都够不着 ⇒ 安全假设成立 ⇒ 返回 None，整段不出现（不白占 token）。

    够得着用引擎自己的 `World._reachable(for_attack=True)` 算（含交战判定与逐格移动代价），
    不另抄一份移动规则；只在**看得见**的敌军里找（迷雾就是迷雾，看不见的算不出来）。

    ★ 在建（`pending`）的厅还不是国祚 ⇒ 不算；口径与 `nation_building_count` 一致。
    """
    naked: set[tuple[int, int]] = set()
    for (x, y), t in world.tiles.items():
        if t['owner'] != name:
            continue
        if (t.get('buildings') or {}).get('市政厅', 0) <= 0:
            continue
        naked.add((x, y))
    if not naked:
        return None
    naked -= {(a['x'], a['y']) for a in world.armies if a['owner'] == name}
    if not naked:
        return None
    threats: list[tuple[tuple[int, int], dict]] = []
    for a in world.troops:
        enemy = a['owner']
        if enemy == name or not world.war_between(name, enemy):
            continue
        if not world.visible_to(name, a['x'], a['y']):
            continue
        for cell in sorted(naked & set(world._reachable(enemy, a, for_attack=True))):
            threats.append((cell, a))
    if not threats:
        return None

    def _where(x: int, y: int) -> str:
        t = world.tiles.get((x, y)) or {}
        return f"「{t.get('name') or '?'}」({x + 1},{y + 1})"
    cells: dict[tuple[int, int], list[str]] = {}
    for cell, a in threats:
        cells.setdefault(cell, []).append(a['name'])
    rows = [f"  {_where(x, y)} ← {'、'.join(who[:3])}" for (x, y), who in sorted(cells.items())[:4]]
    more = f'\n  （另 {len(cells) - 4} 处）' if len(cells) > 4 else ''
    return f'⚠ 【国祚警报】**你的后方安全假设已经不成立**：{len(cells)} 座没有驻军的市政厅，敌军**本回合就够得着**。\n' + '\n'.join(rows) + more + '\n  那里没人守，它一步 `atk` 进去就是**零战斗直接进驻**——丢的不只是那一格。\n  ★★ **被换家意味着战略完全失败**：前线赢得再多，家里被走进来一次，国祚和战争工业一起归零。**要么回兵，要么承认这格是丢的——别让它在敌人够得着时空着。**'

EXPORTS = {n: v for n,v in globals().copy().items() if callable(v) and getattr(v,"__module__",None)==__name__}
