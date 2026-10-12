from ..core import *
from ..rules import *
from ..ui_constants import *
from ..presentation import _cost_text,_move_rule_text,_reach_brief,_recruit_desc

def _props(schema: dict) -> dict:
    """把每个属性里的 `required: True` 提到顶层（属性内不允许出现 required）。"""
    props, req = ({}, [])
    for k, v in schema.items():
        pv = dict(v)
        if pv.pop('required', False):
            req.append(k)
        props[k] = pv
    d = {'type': 'object', 'properties': props}
    if req:
        d['required'] = req
    return d

def definitions():
    result = [
        {"type": "function", "function": {
            "name": "countries", "description": "列出所有可选外交对象（除你之外的每个国家：关系/是否接壤/有无来信）。外交动作前先用它选一个目标，再以 to=该国家 行动；绝不能对自己用外交工具。",
            "parameters": _props({})}},
        {"type": "function", "function": {
            "name": "send_letter", "description": f"给别国写信。**按字数计价：起步价联盟内 {LETTER_COST_ALLY} 金 / 非联盟 {LETTER_COST} 金（含前 {LETTER_FREE_CHARS} 字，吃外交中心减免：每座 {LETTER_CENTER_DISCOUNT}、下限 {LETTER_COST_MIN}）；超出 {LETTER_FREE_CHARS} 字的部分每 {LETTER_CHARS_PER_GOLD} 字 1 金、不吃任何减免**。成功即扣、下回合送达；联盟成员不再免费。写长信就是花钱——写信前先算账：值不值？预期收益（贡品/结盟/情报/逼降）明显大于信价才写，说不出收益就别写，更别拿写信闲聊；国库紧张时短写或不写。诉求能并进一次正式外交提议（外交费）就别单独写信。to 必须用 countries 选出的别国，不能是自己。",
            "parameters": _props({"to": {"type": "string", "description": "收信国名", "required": True},
                                  "content": {"type": "string", "description": "信件正文", "required": True}})}},
        {"type": "function", "function": {
            "name": "gift", "description": f"把本国储备赠给别国（to=countries 里的别国，不能是自己）：good=粮食/木头/矿石/石油/装备/补给 或 黄金，qty=数量。本回合垫支扣出、下回合到账；另扣外交手续费（基准 {DIPLO_COST} 金；有外交中心则减半；收件人是联盟成员则免费）。示好/资助盟国/买通可用。",
            "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                                  "good": {"type": "string", "description": "物资名", "required": True},
                                  "qty": {"type": "integer", "description": "数量", "required": True}})}},
        {"type": "function", "function": {
            "name": "share_map", "description": f"把你的整张已知地图（全部国土块+边界外可见块，含坐标）发给别国，对方下一回合在 query panel=intel 收到（外交费，基准 {DIPLO_COST} 金，成功才扣；对象是联盟成员则免费）。换情报/亮家底/协同步调可用。to=countries 里的别国，不能是自己。",
            "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
        {"type": "function", "function": {
            "name": "spy", "description": f"不想开口问（懒得谈、钱多）时派间谍刺探别国：花 {SPY_COST} 金（国库不足会被拒），{SPY_TURNS} 回合后拿回该国全部经济情报（query panel=spy 看——国库/储备、上回合收入、每一块地的建筑与在建）**、它的外交关系（该国视角：与谁 交战/共同防御/保障、在哪个联盟、哪条战线）**、粗略军情（仅各兵种数量，军队位置/血量/番号不外泄），以及它的整张已知地图（进 query panel=intel）。目标不能是自己。",
            "parameters": _props({"to": {"type": "string", "description": "刺探对象国", "required": True}})}},
        {"type": "function", "function": {
            "name": "bloc_found", "description": f"发起结盟（多边联盟）：**必须给联盟起名**（1~{BLOC_NAME_MAX} 字、不含空格、全局唯一）并邀请创始成员。全体创始成员 respond_proposal 接受后联盟成立（任一拒绝即流产），**发起方自动成为盟主**。★ 联盟本身就是**外交实体**：此后保障独立/共同防御/宣战/议和都由联盟出面、且须联盟投票通过，成员个人签不了任何条约；**入盟即放弃个人条约**（成员国原有的保障/共同防御一律作废）。盟内效果：互通领土/互不攻击/共享视野；同战线自动归还核心领土。**战争期间不能缔结同盟**；★ **休战期（和约在身）不拦结盟**——你与盟友之间那纸和约会被联盟**合并**（不再独立成立；联盟解散/退盟则按原到期回合自动恢复）。发起扣外交费（基准 {DIPLO_COST} 金；受邀方有外交中心则免费）。",
            "parameters": _props({"name": {"type": "string", "description": "联盟名（1~{BLOC_NAME_MAX}字，全局唯一）", "required": True},
                                  "tos": {"type": "array", "items": {"type": "string"}, "description": "创始成员国名数组（至少1个，须为 countries 里的别国）", "required": True}})}},
        {"type": "function", "function": {
            "name": "bloc_join", "description": f"申请加入指定联盟：现成员投票，**赞成 > 反对**即通过（盟主无否决权）；一国同时只属一个联盟；与该联盟成员交战、或自己正在交战 → 不能申请。★ 有和约在身**不拦入盟**（和约会被联盟合并）。扣外交费（基准 {DIPLO_COST} 金）。",
            "parameters": _props({"name": {"type": "string", "description": "联盟名", "required": True}})}},
        {"type": "function", "function": {
            "name": "bloc_leave", "description": f"退出所在联盟：普通成员单方面退出、立即生效（滞留前盟友领土的军队回合末自动遣返）。★ **战争期间一律不准退盟**——任一成员在交战即被拒，盟员在战时被锁死，先议和。**盟主不能退盟**——请先 bloc_transfer 移交，或 bloc_dissolve 解散。扣外交费（基准 {DIPLO_COST} 金）。",
            "parameters": _props({})}},
        {"type": "function", "function": {
            "name": "bloc_rename", "description": f"【盟主专属】给联盟改名（1~{BLOC_NAME_MAX} 字、不含空格、全局唯一）。免费。",
            "parameters": _props({"name": {"type": "string", "description": "新联盟名", "required": True}})}},
        {"type": "function", "function": {
            "name": "bloc_transfer", "description": "【盟主专属】把盟主之位移交给本联盟另一成员（移交后你变成普通成员，从此可自由退盟）。盟主身份由发起方自动获得，只有现任盟主能移交。免费。",
            "parameters": _props({"to": {"type": "string", "description": "接任盟主的成员国名（须在本盟成员里）", "required": True}})}},
        {"type": "function", "function": {
            "name": "bloc_dissolve", "description": "【盟主专属】解散你的联盟：全体成员恢复独立（各自重新成为外交实体）、条约作废、进行中的联盟投票一并作废。**战争期间不能解散**（任一成员正在交战即被拒，先议和停战）。盟主不能退盟，想脱身就用解散或移交。免费。",
            "parameters": _props({})}},
        {"type": "function", "function": {
            "name": "vote", "description": "对所在联盟进行中的投票表态（宣战/议和/入盟/缔约）。choice=yes 赞成 / no 反对 / abstain 弃权（不投=到期算弃权）。**赞成 > 反对即通过**（弃权不计入分母）；发起后下一回合投票，该回合末统一计票；**盟主反对仅计一票，无否决权**。可改票；联盟成员投票免费。★「缔约」表决是本作**唯一**的签约通道：保障独立、共同防御都只能这样签或撤。",
            "parameters": _props({"vote_id": {"type": "integer", "description": "投票id（diplomacy 面板有）", "required": True},
                                  "choice": {"type": "string", "enum": ["yes", "no", "abstain"], "description": "你的表态", "required": True}})}},
        {"type": "function", "function": {
            "name": "propose", "description": f"提议『共同防御』（仅守：它被打才自动并肩参战，你主动开战它不上）。**签约方是外交实体**：你所在的联盟出面与你**是独立国家**出面，效果不同——你在联盟里 → 调用先转为**联盟投票**，通过后由联盟出面提议；对方是联盟 → 它接受时同样要过它的联盟投票（一盟一票）。全面结盟请用 bloc_found。to 必须用 countries 选出的别国，不能是自己。**双方实体都必须在和平状态**（任一方在交战 → 不能缔结，先议和）。外交费（基准 {DIPLO_COST} 金；同实体免费；对方有外交中心则免费），成功才扣。",
            "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True},
                                  "kind": {"type": "string", "enum": ["共同防御"], "description": "类型", "required": True}})}},
        {"type": "function", "function": {
            "name": "respond_proposal", "description": "回应收到的邀约（共同防御/结盟：accept=true 接受 / false 拒绝；联盟创始须全体接受才成立；同实体免费）。★ 你**在联盟里**时，共同防御邀约不是你说了就算：accept 只是把它**提交联盟表决**（随后 vote 表态，赞成>反对才缔结）；拒绝由盟主代表实体对外出面。",
            "parameters": _props({"proposal_id": {"type": "integer", "description": "邀约id（diplomacy面板有）", "required": True},
                                  "accept": {"type": "boolean", "description": "接受? true/false", "required": True}})}},
        {"type": "function", "function": {
            "name": "break_defense", "description": "解除共同防御。★ **战争期间一律不准解除**（条约在战时冻结：缔结与解除都不行，只有议和能停战）——原先「断约即退出该盟约带来的战线」，那是一条打不过就跑路的脱战通道，已封。你在联盟里 → 先转为**联盟投票**，通过后由联盟解除（成员个人无权解约）；你是独立国家 → 直接解除。解除后跟随方身份一并解除，滞留对方领土的军队回合末自动遣返。",
            "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
        {"type": "function", "function": {
            "name": "guarantee", "description": f"宣布保障别国（实体）独立：任何国家攻击它，你方实体将自动参战。**签约方是外交实体**：你在联盟里 → 先转为**联盟投票**，通过后由**联盟**保障它（恩义记在联盟头上、全盟担义务）；你是独立国家 → 你自己就是实体，回合末外交阶段生效。保障与共同防御互斥（共同防御更高一档，缔结它会自动解除保障）。**双方实体都必须在和平状态**（任一方在交战 → 不能保障，先议和）。to=别国（不能自己）。成功扣外交费（基准 {DIPLO_COST} 金）。",
            "parameters": _props({"to": {"type": "string", "description": "被保障国", "required": True}})}},
        {"type": "function", "function": {
            "name": "cancel_guarantee", "description": "撤回独立保障。★ **战争期间不准撤回**（条约在战时冻结，与 break_defense 同口径）。你在联盟里 → 先转为联盟投票，通过后由联盟撤回。",
            "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
        {"type": "function", "function": {
            "name": "declare_war", "description": "对别国宣战（对方必须应战，本轮外交阶段生效；成功扣外交费）。先 countries 选目标，to=别国（不能自己）。★ **交战方是外交实体**：你在联盟里 → 调用即转为**联盟宣战投票**（多数决通过后全盟参战、盟主为进攻主导）；你是独立国家 → 提交后在回合末外交阶段开战。战争传导（**一对实体只走一跳**）：对方的**直接**保障国与**直接**共同防御伙伴自动参战打你（A 保 B、B 与 C 共防 → 打 B 则 C 也上）。★ **援军自己的条约不再往下传**——C 的其它盟友、保障 C 的人都不动（条约的触发条件是「它被打」，不是「它被卷进来」）。唯一**不限跳数**的是**联盟**：打盟员 = 打整个联盟，全盟一起上。★ **传导只在「宣战」这一刻结算一次**——结算完这条战线的参战名单就**冻结**：已经在打的人再 atk 夺地/打人，**不会再触发任何条约、不会再拉进任何国家**（别怕「先动手的会被围」——围不围，在你宣战那一刻就定完了）。若目标正与你方实体的盟友/共同防御伙伴交战，宣战会**并入其现有战线**当跟随方（跟随方不能单独议和，主导者议和整条战线停战）。",
            "parameters": _props({"to": {"type": "string", "description": "对象国", "required": True}})}},
        {"type": "function", "function": {
            "name": "offer_peace", "description": "向对方谈判代表求和（★ **被宣战方同样是主导者（防御主导），一样能主动求和**——求和不是宣战方的特权；每侧代表=主导者；主导者有联盟时=其盟主；普通成员/跟随方不能谈，to=diplomacy 面板所示对方代表；成功扣外交费）。⚠ 盟主求和会先发起联盟投票，多数同意才正式提出。pay=我方向对方赔X金；demand=要求对方赔X金；white=白和。接受后整条战线（含跟随方）停战，且各方实际持有地块重算为核心领土。truce=休战回合数（0=不休战）。★ 战时盟员**不能退盟、不能解散**，议和是唯一的解套路径。",
            "parameters": _props({"to": {"type": "string", "description": "对方谈判代表国", "required": True},
                                  "kind": {"type": "string", "enum": ["pay", "demand", "white"], "description": "pay=我方赔款 / demand=要求对方赔款 / white=白和", "required": True},
                                  "gold": {"type": "integer", "description": "赔款量（pay/demand 必填>0）"},
                                  "truce": {"type": "integer", "description": "休战回合数（自行约定，0=不休战）"},
                                  "note": {"type": "string", "description": "附加条件/说明（可选）"}})}},
        {"type": "function", "function": {
            "name": "accept_peace", "description": "接受对方求和（diplomacy 面板可看提议编号；只有被点名的一方=谈判代表能接受；成功扣外交费）。⚠ 若你是盟主，接受会先发起联盟投票，多数同意才正式生效。",
            "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}},
        {"type": "function", "function": {
            "name": "reject_peace", "description": "拒绝对方求和，战争继续（成功扣外交费）。",
            "parameters": _props({"offer_id": {"type": "integer", "description": "求和提议id", "required": True}})}}
    ]
    return result
