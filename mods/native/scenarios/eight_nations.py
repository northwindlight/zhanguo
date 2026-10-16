"""Eight-nation objectives and victory program; no engine-specific victory policy."""

ZHOU="周"
IDS={ZHOU:"scenario:zhou"}

CHARTER = (
    "【本剧本之志：六王毕，四海一】\n"
    "天下定于一 —— 八国并立只是暂局，本剧本要求**一统天下**；这一句**覆盖**前面那句"
    "「这局没有预设目标」。这不是看海种田局：苟安者终被吞并，富国而不扩土等于替别人攒家当。\n"
    "★ **赢的两条路**（自己挑）：\n"
    "  · **尊王路线**：周任盟主、联盟名为「周天下」、成员覆盖全部存续国家且总成员至少5国（含周），"
    "在完整回合结算后触发终局：**周胜利，其他成员次要胜利**。仅接受邀请或提前入盟不单独判胜；"
    "条件未齐仍须博弈，退盟者不能保留尚未获得的次要胜利。\n"
    "  · **终极胜利 —— 兼并天下**：把另外七家全灭，自己当那个唯一的主人。\n"
    "★ **结盟的权责**：联盟是完整外交实体；保障、共同防御、宣战、议和须联盟表决，"
    "成员不能个人决定。入盟时原个人保障/共同防御作废，退盟不恢复；盟内互不攻击、共享视野、互通领土，"
    "各国仍自行经营、指挥军队。任一成员交战就锁住全盟退盟与解散；盟主须先转让才能退出。"
    "退盟后向周或任何原盟国宣战，仍与对方整个联盟交战。选择尊王得到的是诸侯的次要胜利，"
    "同时帮助周完成主胜利；若追求独占天下，应权衡结盟后失去的外交自主权和未来的对手规模。\n"
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
    living=set(world.nation_actors)
    if not living:return
    period=world.government.period()-1  # Just-completed cycle, after all domains settle.
    event=None
    if len(living)==1 and len(world.nations.snapshot())>1:
        winner=next(iter(living))
        name=world.nations.get(winner).name
        event={'type':'eight_nations.conquest','period':period,'winners':[winner],
               'secondary_winners':[], 'message':f'兼并天下：{name}成为唯一存续国家，获得终极胜利。'}
    else:
        zhou=IDS[ZHOU]
        bloc=world.diplomacy.alliance(zhou) if zhou in living else None
        if bloc and bloc.chief==zhou and bloc.name=='周天下' and len(bloc.members)>=5 and set(bloc.members)==living:
            others=sorted(living-{zhou})
            names='、'.join(world.nations.get(n).name for n in others)
            event={'type':'eight_nations.zhou_tianxia','period':period,'winners':[zhou],
                   'secondary_winners':others,'alliance':bloc.id,
                   'message':f'周天下覆盖全部{len(living)}个存续国家（含周），周为盟主：周胜利；{names}获得次要胜利。'}
    if event:
        trigger_victory(event)
        world.intelligence.emit('victory',event['message'],public=True,details=event)



class EightNations:
    text=CHARTER

    def __init__(self,world,trigger_victory):
        self.world=world
        self.trigger_victory=trigger_victory

    def __call__(self):
        check_victory(self.world,self.trigger_victory)
