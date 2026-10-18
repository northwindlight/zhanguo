"""Eight-nation hooks: privileged Zhou tributaries and terminal objectives."""
from engine.scenario_mod import ScenarioMod

ZHOU="周"
IDS={ZHOU:"scenario:zhou"}

CHARTER = (
    "【本剧本之志：六王毕，四海一】\n"
    "天下定于一 —— 八国并立只是暂局，本剧本要求**一统天下**；这一句**覆盖**前面那句"
    "「这局没有预设目标」。这不是看海种田局：苟安者终被吞并，富国而不扩土等于替别人攒家当。\n"
    "★ **赢的两条路**（自己挑）：\n"
    "  · **尊王路线**：周任盟主、联盟名为「周天下」，周及其周天下附庸覆盖全部存续国家且总成员至少5国（含周），"
    "在完整回合结算后触发终局：**周与其他成员共同胜利**。仅接受邀请或提前入盟不单独判胜；"
    "条件未齐仍须博弈，退盟者不能保留尚未获得的共同胜利。\n"
    "★ **周天下附庸不是免贡盟友**：周为盟主时，其他盟友自动成为周天下附庸，保留正常盟友全部权力，"
    "但每期必须向周上交实际生产的10%（金币及各类物资按每期全国同类总产出计贡，向下取整，不足1不收，余数不累计；逐项先扣，电力除外，收到的贡品不再抽成）。"
    "向周称臣会自动创建或加入「周天下」，适用10%和盟友权力。其他宗主的普通附庸上交15%，"
    "完全丧失外交权，随宗主参战，无表决权；仅宗主可和平释放，附庸只能通过独立战争脱离。"
    "普通附庸不计入周天下胜利覆盖。加入是持续让渡生产收益，不能只比较一次外交手续费。\n"
    "★ **原附庸移交**：任何宗主加入周天下时，其全部原附庸一并移交周，直接成为周天下附庸，"
    "各自向周交10%、享有正常盟友权力；不保留诸侯兼任宗主的双层结构。周天下成员随后取得的附庸也直接归周。"
    "普通宗主灭国或向其他普通宗主称臣时，其原附庸则在回合末释放，不形成双层附庸；周天下的移交是本剧本例外。例如秦原有楚为普通附庸，秦加入周天下后，秦与楚均直接臣属于周，秦失去楚的贡赋与外交控制。\n"
    "★ **王室不可称臣**：周不能成为任何国家的附庸，称臣和平条款也不能适用于周；周只能保持非附庸地位或灭国。"
    "周任周天下盟主时不能移交盟主权力。周若灭国，周天下按普通联盟规则继任盟主，保留剩余盟友关系，"
    "但朝贡和原附庸自动移交机制全部失效，剩余成员为普通盟友，不再有周天下胜利。\n"
    "★ **称臣与建盟结算**：可用 submit_vassalage(target=周的国家键) 主动向周称臣；行动期只提交，"
    "回合末外交结算时，若周尚未建盟，直接扣周100金、自动创建「周天下」并由周任盟主，无须周再接受；"
    "周资金不足则本次称臣失败，不透支。已有周天下时不再收这100金，申请称臣仍由现有成员表决，"
    "以保留盟友的入盟表决权。周主动邀请正常创盟也按建盟费100金，仅收一次。"
    "称臣、关系转换和扣费均在所有国家行动结束后结算；外交中的宣战最后处理。"
    "全覆盖时当回合结算即判胜，不另加上贡等待期。\n"
    "  · **终极胜利 —— 兼并天下**：把另外七家全灭，自己当那个唯一的主人。\n"
    "★ **结盟的权责**：联盟是完整外交实体；保障、共同防御、宣战、议和须联盟表决，"
    "成员不能个人决定。入盟时原个人保障/共同防御作废，退盟不恢复；盟内互不攻击、共享视野、互通领土，"
    "各国仍自行经营、指挥军队。任一成员交战就锁住全盟退盟与解散；盟主须先转让才能退出。"
    "退盟后向周或任何原盟国宣战，仍与对方整个联盟交战。选择尊王可与周及其他成员共同胜利；"
    "若追求独占天下，应权衡结盟后失去的外交自主权和未来的对手规模。\n"
    "★ **周王室的志向**（各家都该知道）：**复振王纲** —— 它不靠杀，靠**诸侯来朝**，会四处"
    "拉人入「周天下」。别以为它只会尊王：**周也一样能走「兼并天下」那条终极胜利的路**，"
    "也一样能**先动手打别国**。它的护身符是手上那七张**保障**纸——**单向保护**："
    "只有别人**宣战**它时才生效；共同防御才是双向的。\n"
    "★★★ **大字 · 反击无罪**（每家都给我记住）：那七张纸**只在「宣战」那一刻响一次** —— "
    "**宣战**周 ⇒ 当时仍有有效保障义务且未被休战阻挡的国家自动防守参战；义务只传导一跳，不递归。"
    "已经在和周打的人，再 atk 夺它的地、砸它的军、一路推进、直捣王城，"
    "**一个外国都不会因此参战**：引擎里**没有**「谁动周谁挨打」这种隐藏机制，一个字都没有。"
    "反向也不成立——周自己去打别国，六国一动不动（**那是盾，不是矛**）。"
    "新的合法宣战仍可能增援既有战线；攻击本身不再次拉人，军事支出仍照常。\n"
    "★ 但那七张纸**会烧**：保障**只要不在战时，随时可以撤回**（`cancel_guarantee`，付外交费；"
    "战时条约冻结，撤不了）；周**实际入盟或创盟成立**时，原个人保障全部作废，"
    "仅发出创盟邀请不会烧掉保障。旧保障失效后，联盟本身仍提供全盟防守；不能据此把盟主当成落单国家。"
)


def check_victory(world, trigger_victory):
    """Scenario program: inspect world and submit any declared victory event."""
    call=lambda cap,method,*args,**kwargs:world.runtime.object_call(world,cap,method,*args,**kwargs)
    living=set(call('native:nation-inspection','ids'))
    if not living:return
    period=call('native:government','period')-1  # Just-completed cycle, after all domains settle.
    event=None
    if len(living)==1 and len(call('native:nation-inspection','snapshot'))>1:
        winner=next(iter(living))
        name=call('native:nation-inspection','get',winner).name
        event={'type':'eight_nations.conquest','period':period,'winners':[winner],
               'secondary_winners':[], 'message':f'兼并天下：{name}成为唯一存续国家，获得终极胜利。'}
    else:
        zhou=IDS[ZHOU]
        bloc=call('native:diplomacy','alliance',zhou) if zhou in living else None
        subjects={s.subject for s in call('native:diplomacy','snapshot',zhou).vassals
                  if s.overlord==zhou and s.kind=='tributary'} if zhou in living else set()
        if (bloc and bloc.chief==zhou and bloc.name=='周天下' and len(subjects)+1>=5
            and subjects|{zhou}==living and set(bloc.members)==living):
            others=sorted(living-{zhou})
            names='、'.join(call('native:nation-inspection','get',n).name for n in others)
            event={'type':'eight_nations.zhou_tianxia','period':period,'winners':[zhou,*others],
                   'secondary_winners':[],'alliance':bloc.id,
                   'message':f'周及周天下附庸覆盖全部{len(living)}个存续国家（含周），周为盟主、附庸强制上贡10%：周与{names}共同胜利。'}
    if event:
        trigger_victory(event)
        call('native:intelligence','emit','victory',event['message'],public=True,details=event)



class EightNationsMod(ScenarioMod):
    text=CHARTER

    def on_start(self,world):
        self.runtime.object_call(world,'native:diplomacy','configure_subject_alliance',
                                 IDS[ZHOU],'scenario:zhou-tianxia','周天下',10,100,True,True,True)

    def on_settlement(self,world,trigger_victory):
        check_victory(world,trigger_victory)
