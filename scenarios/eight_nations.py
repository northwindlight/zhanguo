"""八国剧本：24×24 手摆开局，七雄共保周，目标文案随整局存档保存。

生成器只读取仓库示例配置；实际接入参数通过 --template 显式指定。
build 返回 GameHost，玩家通过 host.player(actor) 接口行动。
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from host import GameHost, ROOT, write_json

SEVEN = ("秦", "魏", "韩", "赵", "燕", "齐", "楚")   # 战国七雄
ZHOU = "周"                                          # 周王室（单字国名：面板/报表/番号都是按单字排版的）
NATIONS = SEVEN + (ZHOU,)

# 地图边长。2026-09-26 从 36 缩到 24 —— 依据是八国局第 184 回合的尸检
# （`archive/reports/摆烂尸检.8国.md`；对局产物不入库，下同）：
# 36×36=1296 格**全是陆地**，八国开局只占 40 格，剩 1256 格无主荒野（每格一支不动的野人）
# = 每国 157 格 = 开局国土的 **31 倍**；实测清野 2.24 格/回合 ⇒ **填满要 561 回合**，
# 而一局上限 300 ⇒ **荒野永远用不完，"零风险的免费地"永远比攻城划算**，于是全员摆烂。
# 缩到 24 之后：荒野 536 格 = 每国 67 格、约 239 回合填满（战争期落到后半局）。
# ★ 为什么是 24 而不是更小（用户 2026-09-26）：「还有山地，大家绕着走，其实你可以当成水域，
#   可用的平原其实不多」——本图山地 + 沙漠占 **25%**，24 的可用地 432 格 = 每国 54 格，
#   正好抵得上 20 的全地（50 格/国）。再小就没有建设的余地了。
SIZE = 24
SEED = 20260921      # 定死：同 seed 同图（确定性，`tests/test_determinism.py` 那条口径）
def names(seed: int = SEED) -> dict:
    """这一把的三个文件名 —— **按种子分家：一局一套，永不覆盖、日志不混**。

    ★ 用户 2026-10-09：「**每把日志独立，别再混了**」。原先三个名字都不带种子，而 journal
      是**追加**写的（`mp_run` 用 mode="a"）⇒ 换个种子重开一局，新局的战史就**接在旧局
      后面**，两把混成一份，读的人分不清哪条属于哪一局；存档与配置也会被就地覆盖。
      现在一局一个三元组：`mp_save.8国.<seed>.json` / `mp_journal.8国.<seed>.md` /
      `mp_config.8国.<seed>.json`——重开新种子 = 新文件，上一把原样留着，随时可续。
    """
    return {"save": f"mp_save.8国.{seed}.json",
            "journal": f"mp_journal.8国.{seed}.md",
            "config": f"mp_config.8国.{seed}.json"}


# 默认那把（`SEED`）的三个名字。别名保留：外部按老名字 import 也能用。
SAVE = names()["save"]
JOURNAL = names()["journal"]
CONFIG = names()["config"]

# 各家的中心格（x 向东、y 向南）。都留了边界余量（十字要占 ±1 格）。
# 2026-09-26 缩图时**按 24/36 等比缩放**得来（史地关系原样保留：燕东北、赵正北、齐东、
# 秦最西、楚江南），并逐格核过八国的十字互不挤占：
# 最近的两家仍是 周(15,10)—韩(12,13)：切比雪夫距离 **3**（原图 5，中间隔 1 格空地）——
# 史地上洛阳本来就被韩魏包着，这里刻意照搬（周王室想活，只能靠那七张纸）；
# 秦仍是最孤立的一家（距燕 14、距齐 17）。
# ★ 3 是硬下限：`tests/test_scenario_8nations.py` 要求任意两家切比雪夫 ≥3（十字不许贴脸）。
STARTS = {
    "燕": (17, 3),    # 东北
    "赵": (11, 4),    # 正北
    "齐": (20, 9),    # 东（临海）
    "魏": (8, 9),     # 中原西
    "周": (15, 10),   # 天下之中（洛阳）
    "韩": (12, 13),   # 中原南（夹着周）
    "秦": (3, 11),    # 最西
    "楚": (11, 19),   # 江南
}

COVENANT = ("🕊 七雄共誓：{who} 共保 周王室 独立"
            "——谁**宣战**周，七雄按保障自动参战（周王室自己不出一兵，靠的是这七张纸）。"
            "★★ 大字：这张纸**只在「宣战」那一刻响一次**，此后已在战线上的人再打它、夺它的地，"
            "**不再触发任何条约**——**反击无罪，敢打就打**")


# 剧本之志（**常驻**每个国家的 system prompt；见模块头部「剧本说明」一节）。
#
# ★ **八国同一份文案**（用户 2026-09-21：「改成相同的文案，让他们知道周的目标」）——
#   不再给周王室单写一段：所有人都该知道别人要什么，这才叫"大势"。
# 尊王路线由轻量剧本钩子在完整周期末判定，周与全部周天下成员共同胜利。
# ★ 2026-09-22 用户补的一条（原话）：「**周一样可以完成最终胜利，且周可以打别国，别国不能
#   打周，独立保障是单项保护，防御条约才双向，一旦周开始建立周天下，那么所有的独立保障
#   立刻失效**」——**这不是新机制，是现有机制**（用户明说「这不是机制，是现有机制就这样，
#   别人宣战周会触发 7 雄对周的独立保障」），所以只**写进文案**、不动引擎：
#   ① 周也能走兼并天下（引擎里本来就没有任何"周不能赢"的限制）；
#   ② 周可以宣战别国（保障是**单向**的：只保它不被别人动，不禁止它动手）；
#   ③ 别人打周 ⇒ 七雄按保障自动进守侧（`_declare_war_internal`，一跳取齐，已实测）；
#   ④ 周一旦立盟（当盟主）⇒ 那七张纸**立刻全废**——这也**已经是**引擎行为：
#      「入盟即放弃个人条约」的 `_absorb_personal_pacts` 走 `drop_pacts_of(ent_nation(周))`，
#      把以周为**任一方的**条约全清（保障的 b 侧就是周），并全世界播报。测试钉住了这条。
# ★ 其余措辞要点：① 明写它**覆盖**引擎默认那句"没有预设目标"（那是看海局口径，不盖掉
#   就是两句话打架、模型自己挑一句听）；② 点破"不扩张=替别人攒家当"（本作经济是复利型
#   的，苟安在数值上确实吃亏）；③ 撤回保障提醒的**如实**口径：**战时条约冻结**，
#   所以是"只要不在战时随时可撤"——写成"随时"，它战时去撤被拒就会开始不信提示词。
# 2026-10-16：用户明确加入可执行胜利钩子，覆盖上文历史上的“只写志向、手动结束”。
# 条件归本剧本程序；引擎只接收 trigger_victory(event)，不识别周、联盟名或胜利等级。
from mods.scenarios.eight_nations.mod import CHARTER


IDS=dict(zip(NATIONS,('scenario:qin','scenario:wei','scenario:han','scenario:zhao',
                      'scenario:yan','scenario:qi','scenario:chu','scenario:zhou')))



def load_template():
    return json.loads((ROOT/'mp_config.example.json').read_text(encoding='utf-8'))


def build_config(template=None, seed=SEED):
    tpl=template if template is not None else load_template()
    nat_tpl=next((n for n in tpl.get('nations',[]) if n.get('client')=='llm'),
                 next(iter(tpl.get('nations',[])),{}))
    excluded={'id','name','position','resources','polity','appearance','enabled',
              'enable_turn','enable_turn_max','start_cavalry','start_gold','start_supply',
              'extra_prompt','extra_prompt_file','extra_summary','extra_summary_file'}
    client={k:v for k,v in nat_tpl.items() if k not in excluded}
    client.setdefault('client','rule');client['rule_ai']='v11plus'
    cfg={k:v for k,v in tpl.items() if k not in ('nations','world_bank')}
    cfg.update(map_size=SIZE,seed=seed,rule_ai='v11plus',save=names(seed)['save'],journal=names(seed)['journal'],
               scenario="eight_nations")
    cfg['nations']=[dict(client,id=IDS[name],name=name,position=list(STARTS[name]),appearance='initial') for name in NATIONS]
    return cfg


def build(size=SIZE, seed=SEED, starts=STARTS, nations=NATIONS, *, announce=True,
          charter=True, config=None):
    cfg=build_config({'nations':[{'client':'rule'}]},seed) if config is None else json.loads(json.dumps(config))
    cfg.update(map_size=size,seed=seed)
    cfg['scenario']='eight_nations'
    by_name={item['name']:item for item in cfg['nations']}
    cfg['nations']=[dict(by_name[name]) for name in nations]
    for item in cfg['nations']:
        if starts is None:item.pop('position',None)
        else:item['position']=list(starts[item['name']])
    host=GameHost.new(cfg)
    if charter:
        host.scenario_text=CHARTER
        for actor,client in host.clients.items():
            if client.kind=='llm':
                item=next(n for n in host.cfg['nations'] if n['id']==actor)
                client.cfg=host._client_cfg(item)
    ids={item['name']:item['id'] for item in host.cfg['nations']}
    if ZHOU in ids:
        for name in nations:
            if name!=ZHOU:host.world.diplomacy.seed_pact('guarantee',ids[name],ids[ZHOU])
        if announce:host.world.intelligence.emit('announcement',COVENANT.format(who='、'.join(n for n in nations if n!=ZHOU)),public=True)
    return host


def write_config(path=None, template=None, seed=SEED):
    path=Path(path) if path is not None else ROOT/names(seed)['config']
    write_json(path,build_config(template,seed));return path


def main(argv=None):
    ap=argparse.ArgumentParser(description='生成八国整局存档与配置')
    ap.add_argument('--seed',type=int,default=SEED)
    ap.add_argument('--out');ap.add_argument('--config');ap.add_argument('--template')
    ap.add_argument('--ring',action='store_true');ap.add_argument('--config-only',action='store_true')
    ap.add_argument('--force',action='store_true',help='覆盖已有目标文件')
    args=ap.parse_args(argv)
    out=Path(args.out) if args.out else ROOT/names(args.seed)['save']
    cfg_path=Path(args.config) if args.config else ROOT/names(args.seed)['config']
    targets=[cfg_path] if args.config_only else [out,cfg_path]
    if not args.force and any(p.exists() for p in targets):
        raise SystemExit('目标文件已存在；换路径或明确使用 --force 重建')
    template=json.loads(Path(args.template).read_text(encoding='utf-8')) if args.template else load_template()
    cfg=build_config(template,args.seed);cfg['save']=str(out)
    if args.ring:
        for item in cfg['nations']:item.pop('position',None)
    if not args.config_only:
        host=build(seed=args.seed,starts=None if args.ring else STARTS,config=cfg)
        host.save(out);print(f'存档 → {out}（玩家 API v1，七条保障，剧本之志常驻）')
    write_json(cfg_path,cfg);print(f'配置 → {cfg_path}')
    print(f'开局：./start.sh --config {cfg_path}')
    return 0


if __name__=='__main__':raise SystemExit(main())
