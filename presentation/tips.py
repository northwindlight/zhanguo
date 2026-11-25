"""每回合随机提示：从手册先验和常见失误里抽一句，放在战情末尾。"""
import random
import hashlib

TIPS = (
    '随时随地读读战略手册总是没错的。',
    '经济手册是有利的：先算投入、产出和当前市价，再决定建什么。',
    '战争手册会告诉你如何战争：目标、兵力、后勤、退路，缺一项都别开打。',
    '你了解你的邻国吗？你的视野似乎很狭窄——一座瞭望塔或一份 20 金报表就能补上。',
    '你考虑过 ROI 吗？有些工厂原材料上涨的时候，会变得亏本。',
    '读《战略手册》不用等到打输：每回合抽一句，胜过临阵翻书。',
    '《经济学手册》讲的是为什么该这么做；规则只讲能做什么。',
    '《战争手册》的第一页不是战术，是战前自检。',
    '如果连邻国有几座市政厅都不知道，先别宣战。',
    '你的视野只有土地周围一格；瞭望塔能把半径扩到四格。',
    '报表是十期前的账；用它判断当前兵力，等于闭眼开车。',
    '谍报延迟三期，而且不提供军队位置；别把旧谍报当成当前部署。',
    '同回合先卖后买同一商品，必亏价差；要买就买，要卖就卖。',
    '借款是流动性工具，不是收入；到期强制扣款能把国库扣成负数。',
    '工厂的 ROI 随原料价格变；今天赚钱的厂，原料涨了就可能亏本。',
    '补给厂和装备厂的收益要分开算，别用总产出掩盖一类亏损。',
    '防御比反攻便宜：地形和城堡减伤相乘，先算守方再算攻方。',
    '主力出战时，后方至少留一支能挡住偏师的守军。',
    '总兵力和投入进攻的兵力是两回事；差距里的军队要说明为什么闲置。',
    '进攻队伍里没有预备队，就等于把胜负押在第一波。',
    '市政厅全失即亡国；第二座厅比一座满级城堡更值钱。',
    '可见 0 支不等于全国 0 支；未知要写上界，不要当零。',
    '经济报表的第一产业是农林矿油，第二产业是补给和装备；分开看。',
    '市场价由库存和基准库存决定；大家都捂着不买，价格会崩。',
    '大单会推动价格；下单前先看买卖 50 的试算。',
    '军队每回合吃补给；完全断粮会全军掉血，而且不回血。',
    '伤兵回血耗装备；民兵回血不耗装备。',
    '新征的兵本回合不能动；移动过的兵赶不到第二个战场。',
    '撤退提交后仍参加本期战斗，结算后才撤离。',
    '攻城前先算守方的地形×城堡减伤，再算你的到场兵力。',
    '盟友的扩张也会改变均势；共享视野不等于共享国库。',
    '联盟是完整外交实体，入盟后个人保障作废，退盟也不恢复。',
    '向联盟任何一国宣战，就是与整个联盟交战。',
    '求和只能找战争主导实体的代表；普通跟随方不能代谈。',
    '长期等待要写清等什么、等到哪期；没有条件的等待就是放弃。',
    '扩张是买建设的期权；占了地不建设，就是压死在手里的钱。',
    '现金不是全部资产；库存和建筑也是资产，但变现要打折。',
    '先算情报成本与行动窗口；不知道就写未知、上界、最坏情形。',
    '你的邻国也在复利；你不扩张，就是把选择权交给别人。',
    '不要为了过审堆砌精确措辞；把战略、兵力、后勤、退路写清楚。',
    '每回合先问：我的总兵力、可调度兵力、投入进攻的兵力，分别是多少？',
    '后方、首都、预备队，三者至少要有一项在开战前想清楚。',
    '贸易、建造、征兵都在花现金；先确认下一期的开工料和军饷。',
    '地图上的空白不是安全区，是还没被你看见的风险。',
    '加入联盟也许会暂时违背兼并天下的目标，但那可能是你比较弱小时活到最终争霸的关键工具。',
    '弱小时先买生存：联盟能挡刀、共享视野、争取发展时间；强大了再评估退盟或换边。',
    '不入盟也是战略，但别把“不结盟”当成目的；它只是手段之一。',
    '成为盟主，击溃强大得多的对手！',
    '联盟不只是盾牌：当上盟主，就能把单挑打不过的强敌变成联盟战争。',
)


TIPS_EN = (
    'Reading the Strategy manual is never a mistake.',
    'The Economics manual pays off: work out inputs, outputs and current market prices before deciding what to build.',
    'The Warfare manual tells you how to wage war: objective, forces, logistics and a line of retreat. Do not attack if any one is missing.',
    'Do you know your neighbours? Your view looks narrow; a watchtower or a 20-gold report would fill the gap.',
    'Have you considered ROI? Some factories lose money when their raw materials get more expensive.',
    'You need not lose a war before reading the Strategy manual: one line per turn beats leafing through it on the eve of battle.',
    'The Economics manual explains why to act; the rules only say what you can do.',
    'The first page of the Warfare manual is not tactics; it is the pre-war checklist.',
    'If you do not even know how many town halls your neighbour has, do not declare war yet.',
    'You can see only one tile around your land; a watchtower widens the radius to four tiles.',
    'Reports are ten turns old; judging current forces from them is driving with your eyes closed.',
    'Intelligence lags by three turns and shows no army positions; do not mistake old intelligence for current deployment.',
    'Selling and buying the same goods in one turn always loses the spread; buy or sell, not both.',
    'A loan is a liquidity tool, not income; forced repayment at maturity can push the treasury negative.',
    "A factory's ROI moves with input prices; a profitable plant today may lose money once its inputs rise.",
    'Count supply-depot and equipment-factory returns separately; do not let total output hide a losing line.',
    'Defending is cheaper than counterattacking: terrain and castle damage reduction multiply, so work out the defender before the attacker.',
    'When the main army marches out, leave at least one garrison able to stop a flanking force.',
    'Total forces and forces committed to an attack are different numbers; explain why the difference is idle.',
    'An attack without a reserve stakes everything on the first wave.',
    'Losing every town hall ends the nation; a second town hall is worth more than a fully upgraded castle.',
    'Seeing 0 units is not the same as a nation having 0 units; write an upper bound for the unknown instead of zero.',
    'In economic reports, primary industry is farming, forestry, mining and oil; secondary industry is supplies and equipment. Read them separately.',
    'Market prices follow stock against the reference stock; if everyone hoards and nobody buys, prices collapse.',
    'Large orders move the price; check the buy-and-sell-50 quote before placing one.',
    'Armies consume supplies every turn; a complete shortage makes every unit lose HP without healing.',
    'Healing wounded soldiers consumes equipment; militia heal without it.',
    'Newly recruited units cannot move this turn; units that have moved cannot reach a second battlefield.',
    "A unit that submits a retreat still fights this turn's battle and leaves only after settlement.",
    "Before a siege, work out the defender's terrain × castle damage reduction, then the forces you can bring.",
    "An ally's expansion also shifts the balance of power; shared vision is not a shared treasury.",
    'An alliance is a full diplomatic entity: joining voids your individual guarantees, and leaving does not restore them.',
    'Declaring war on any member of an alliance means war with the whole alliance.',
    'Peace can be sought only from the representative of the leading entity in a war; ordinary followers cannot negotiate for it.',
    'When you wait for a long time, state what you are waiting for and until which turn; waiting without conditions is giving up.',
    'Expansion buys an option on construction; land you take but do not build on is money locked away.',
    'Cash is not your only asset; stock and buildings are assets too, but they sell at a discount.',
    'Weigh the cost of intelligence and the window for action first; when you do not know, write unknown, an upper bound or the worst case.',
    'Your neighbours compound too; if you do not expand, you hand the choice to someone else.',
    'Do not pile up precise wording to pass review; state the strategy, forces, logistics and retreat clearly.',
    'Ask every turn: what are my total forces, my deployable forces and the forces committed to attack?',
    'Rear, capital and reserve: think through at least one of the three before war starts.',
    "Trade, construction and recruitment all spend cash; first secure next turn's construction materials and army pay.",
    'A blank on the map is not a safe zone; it is a risk you have not seen yet.',
    'Joining an alliance may for a while run against the goal of conquering the realm, but while you are weak it can be the tool that keeps you alive for the final contest.',
    'When weak, buy survival first: an alliance takes blows for you, shares vision and buys time to develop. Once strong, reconsider leaving or switching sides.',
    'Staying out of alliances is also a strategy, but do not treat non-alignment as the goal; it is only one of the means.',
    'Become the alliance leader and defeat a far stronger rival!',
    'An alliance is more than a shield: as its leader you can turn a strong enemy you cannot beat alone into an alliance war.',
)
"""English tips, index-aligned with `TIPS`: the same seeded choice picks the same tip in both languages."""


def tips_for(language='zh-CN'):
    return TIPS_EN if str(language or '').split('-')[0].lower()=='en' else TIPS


def random_tip(key=None, language='zh-CN'):
    tips=tips_for(language)
    if key is None:return random.choice(tips)
    # Briefings are persisted in model memory. The same turn and facts must
    # replay the same cosmetic tip after loading, without consuming game RNG.
    value=int.from_bytes(hashlib.sha256(str(key).encode('utf-8')).digest()[:8],'big')
    return tips[value % len(tips)]
