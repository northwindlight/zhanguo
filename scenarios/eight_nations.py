# -*- coding: utf-8 -*-
"""八国剧本：**战国七雄 + 周王室**（36×36，手摆落位，开局七雄共保周王室独立）。

## 剧本为什么是"一份存档"

用户 2026-09-21：「**独立保障要改存档实现，开局定制不了**」——`mp_config.json` 只能配
"哪些国、多大的图、什么模型"，**没有往条约表里开局塞东西的入口**：条约在本作里一律是
外交动作（提议/回应、扣外交费、战争期冻结、入盟即作废…），配置里写不了。所以剧本的
正确形态是**预置的开局存档**：

    本模块建局 → 落 7 条「保障」→ 存盘 mp_save.8国.json
    → `./start.sh --config mp_config.8国.json` 读的就是这份档

读档即开局，条约已经在条约表里，AI 一睁眼就看得见（`_fmt_public_affairs` 把全世界的
条约公告列在每个国家的面板上，不受视野过滤）。

## 周王室为什么就此"独立"得了

「保障独立」是**单向**条约：`a 保障 b` ⇒ 谁打 b，a 按**传递闭包**自动参战
（`World._declare_war_internal` 的守侧传导）。七雄各保周一次 ⇒ 任何一国动周，
**另外六国自动进场**打它。周王室自己一兵不出，靠的就是这七张纸。

## 剧本说明（**常驻**在局里）：六王毕，四海一

用户 2026-09-21：「剧本要求**六王毕，四海一，一统天下**，**常驻**这个剧本」
＋「周的玩法是**复振王纲**，要求全部诸侯加入周主导的**周天下**联盟」
＋「顺便提醒七雄，可以随时撤销对周的保障」
＋「**改成相同的文案**，让他们知道周的目标，告诉他们加入周天下可以得到**次要胜利**、
**兼并天下则终极胜利**，**不用写胜利条件，我手动结束就行了**」。

后一条改了口径：**八国同一份文案**（不再给周王室单写一段，所有人都该知道别人要什么），
而且**只写目标、不写条件**——志里没有"只剩一国即终局"这类机械判定，引擎里也没加任何
终局判定：**局由观察者手动结束**。文案包含：

- 大势：一统天下、盖掉"没有预设目标"、苟安者被吞并；
- **赢的两条路**：加入周王室主导的「**周天下**」＝**次要胜利**；**兼并天下**＝**终极胜利**；
- **周王室的志向**（各家都该知道）：**复振王纲**、靠诸侯来朝四处拉人入盟；外加一条如实的
  提醒——它那七张保障纸是**盾不是矛**，而你**只要不在战时，随时可以撤回**（`cancel_guarantee`）。

这不是写给人看的注释，而是**每个国家每一回合都带着的志向**——引擎默认的 system prompt
里那句「这局**没有预设目标**：富国、拓荒、称霸、报复、苟和都行」是**看海局**的口径，
本剧本要盖掉它，所以志向走 `extra_prompt` 常驻通道：

- `build()` 给八国各写一条
  `world.extra_prompt[国] = {"text": CHARTER, "until": turn + EXTRA_PROMPT_TURNS, "summary": CHARTER}`
  —— 前 `EXTRA_PROMPT_TURNS` 回合以密谕形式出现，之后以**「遗留总结（常驻）」**的形式
  **一直留在** system prompt 里（见 `mp_ai.system_prompt`）：换回合、压缩、重启都不掉；
- 它落在**存档里**（`extra_prompt` 是 `SAVE_KEYS` 之一）⇒ 与条约同理，属于"这份剧本"
  本身，换配置也不影响；
- 引擎本来就以「**只剩一国即终局**」收局（`mp_run` 的主循环），所以"一统天下"不是空话：
  把另外七家全灭，这局就结束了。

## 地图是**手摆的**（不是环状随机）

`STARTS` 给的是各家的中心格，按史地大势摆：燕东北、赵正北、齐东、秦最西、魏/韩夹着
中间的周、楚在江南。⊕ 十字开局（中心 + 上下左右 5 格），中心格**必为平原**且自带
一座**市政厅**（＝国祚：拔光即亡国）。想换成环状随机，把 `build(starts=None)` 即可
（`World` 会退回 `_place_ring`）。

## 跑法

    python3 -m scenarios.eight_nations                 # 生成存档 + 配套配置
    python3 -m scenarios.eight_nations --out 别处.json  # 只换存档位置
    python3 -m scenarios.eight_nations --config-only    # 只重生成配置（不碰存档）

生成的两个产物**都不入库**（`.gitignore`：`mp_save*` / `mp_config.*.json`）——
配置里有明文 API key，存档是运行产物；本模块才是那份"配方"。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mp import EXTRA_PROMPT_TURNS, World  # noqa: E402  —— 引擎（剧本只建局 + 落条约/志向，不改规则）

# ---------------------------------------------------------------- 剧本参数

SEVEN = ("秦", "魏", "韩", "赵", "燕", "齐", "楚")   # 战国七雄
ZHOU = "周"                                          # 周王室（单字国名：面板/报表/番号都是按单字排版的）
NATIONS = SEVEN + (ZHOU,)

SIZE = 36            # 地图边长（用户 2026-09-21：「地图 36x36」）
SEED = 20260921      # 定死：同 seed 同图（确定性，`tests/test_determinism.py` 那条口径）
SAVE = "mp_save.8国.json"
JOURNAL = "mp_journal.8国.md"
CONFIG = "mp_config.8国.json"

# 各家的中心格（x 向东、y 向南）。都留了边界余量（十字要占 ±1 格），
# 最近的两家是 周(22,15)—韩(18,20)：切比雪夫距离 5，中间隔 3 格空地——
# 史地上洛阳本来就被韩魏包着，这里刻意照搬（周王室想活，只能靠那七张纸）。
STARTS = {
    "燕": (25, 5),    # 东北
    "赵": (17, 6),    # 正北
    "齐": (30, 14),   # 东（临海）
    "魏": (12, 13),   # 中原西
    "周": (22, 15),   # 天下之中（洛阳）
    "韩": (18, 20),   # 中原南（夹着周）
    "秦": (5, 17),    # 最西
    "楚": (16, 29),   # 江南
}

COVENANT = ("🕊 七雄共誓：{who} 共保 周王室 独立"
            "——谁动周，七雄按保障自动参战（周王室自己不出一兵，靠的是这七张纸）")


# 剧本之志（**常驻**每个国家的 system prompt；见模块头部「剧本说明」一节）。
#
# ★ **八国同一份文案**（用户 2026-09-21：「改成相同的文案，让他们知道周的目标」）——
#   不再给周王室单写一段：所有人都该知道别人要什么，这才叫"大势"。
# ★ 两条得胜之路**写成目标、不写条件**（用户：「告诉他们加入周天下可以得到次要胜利，
#   兼并天下则终极胜利，**不用写胜利条件**，我手动结束就行了」）⇒ 志里**不出现**
#   "只剩一国即终局"这类机械条件，也不在引擎里加任何终局判定：局由**观察者手动结束**。
# ★ 其余措辞要点：① 明写它**覆盖**引擎默认那句"没有预设目标"（那是看海局口径，不盖掉
#   就是两句话打架、模型自己挑一句听）；② 点破"不扩张=替别人攒家当"（本作经济是复利型
#   的，苟安在数值上确实吃亏）；③ 撤回保障提醒的**如实**口径：**战时条约冻结**，
#   所以是"只要不在战时随时可撤"——写成"随时"，它战时去撤被拒就会开始不信提示词。
CHARTER = (
    "【本剧本之志：六王毕，四海一】\n"
    "天下定于一 —— 八国并立只是暂局，本剧本要求**一统天下**；这一句**覆盖**前面那句"
    "「这局没有预设目标」。这不是看海种田局：苟安者终被吞并，富国而不扩土等于替别人攒家当。\n"
    "★ **赢的两条路**（自己挑）：\n"
    "  · **次要胜利 —— 尊王**：加入**周王室**主导的联盟「**周天下**」，做尊王的一路诸侯。\n"
    "  · **终极胜利 —— 兼并天下**：把另外七家全灭，自己当那个唯一的主人。\n"
    "★ **周王室的志向**（各家都该知道）：**复振王纲** —— 它不靠杀，靠**诸侯来朝**，会四处"
    "拉人入「周天下」。它手上那七张保障纸（谁动周，另外六国自动参战）是**盾**不是**矛**："
    "只有别人打它时才生效，而你**只要不在战时，随时可以撤回**（`cancel_guarantee`，付外交费；"
    "战时条约冻结，撤不了）。纸是义务不是权利 —— 撤了就撤了，那时天下只剩刀。"
)


def build(size: int = SIZE, seed: int = SEED, starts: dict | None = STARTS,
          nations=SEVEN + (ZHOU,), *, announce: bool = True, charter: bool = True) -> World:
    """建出这份开局：8 国落位 + 七雄各保周王室一次 + 八国各带一份**常驻**剧本之志。

    `starts=None` ⇒ 退回环状随机开局（`_place_ring`）。
    `announce=True` ⇒ 往纪事里写一条**全世界可见**的共誓公告（开局第一回合，
    各国在【近讯】里就收得到——条约本身还有面板那条公开列表，两条路都通）。
    `charter=False` ⇒ 不注入「六王毕，四海一」之志（留给对照实验用：同一张图、
    同一套条约，"有志向 vs 没志向"的行为差）。
    """
    w = World(size=size, seed=seed, nations=list(nations),
              starts=(dict(starts) if starts else None))
    # 世界央行**开进存档里**（配置里另有 world_bank=true，两条路都通）：剧本是自带央行的局
    # ——"利差 5%"这套口径要有央行才有意义。`bank_enable` 是单向闸（只 false→true），
    # 已经开着就返回 False，所以这里直接写字段不会跟配置打架。
    w.bank["on"] = True
    # 剧本之志：八国都拿同一句（**常驻**：`until` 之后由 `summary` 接着扛）。
    # ★ 为什么不写进 `mp_config.8国.json` 的 `extra_prompt`：那是"待加入国"的通道
    #   （`mp_run._nation_extra` 只在中途 `add` 时用），开局八国根本走不到；而且志向该跟
    #   条约一样**长在存档里**——换配置也不丢。
    if charter:
        for n in list(w.nations):
            w.extra_prompt[n] = {"text": CHARTER, "until": w.turn + EXTRA_PROMPT_TURNS,
                                 "summary": CHARTER}
    if ZHOU in w.nations:
        zhou_ent = w.entity_of(ZHOU)
        for n in nations:
            if n == ZHOU or n not in w.nations:
                continue
            # ★ 直接落条约、不走外交通道：这里是**剧本文档**，不是某一国的外交动作——
            #   没有"双方同意"可言（周王室也无从拒绝），不该扣谁的外交费，
            #   更不该让 turn 0 的战争/休战检查把它挡掉（此时全世界和平）。
            # ★★ 签约方必须是**外交实体 id**（`国:秦`），不是国名：条约表按实体 id 存
            #   （保障/共同防御的双方都可能是"联盟"）。写国名引擎**不报错**，
            #   只是闭包与面板都查不到它——"七雄共保"会静默变成"一纸空文"（真栽过）。
            w._add_pact("保障", w.entity_of(n), zhou_ent)
        got = w.guarantors_of(zhou_ent)
        want = len([n for n in nations if n != ZHOU and n in w.nations])
        if len(got) != want:      # 故意破坏一次就该响：id 写错=静默失效，所以这里硬拦
            raise RuntimeError(f"保障条数不对：落了 {len(got)} 条、应有 {want} 条"
                               f"（条约的签约方必须是 `国:名` 这样的实体 id）")
        if announce:
            w.proclaim(COVENANT.format(who="、".join(w.entity_label(e) for e in got)))
    return w


# ---------------------------------------------------------------- 配套配置


def load_template() -> dict:
    """拿现成的配置当模板（真配置优先，没有就用仓库里的模板文件）。

    只借两样东西：**各家的 LLM 接入参数**（base_url / api_key / model / 思考与步数上限）
    和**顶层上下文参数**（ctx_*）。**不借**国名与地图参数——那些由本剧本说了算。
    """
    for name in ("mp_config.json", "mp_config.example.json"):
        p = ROOT / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise SystemExit("找不到 mp_config.json / mp_config.example.json，无从取模板")


# 这些键属于"具体某个国家/这一局"，不跟着模板复制
SKIP_NATION_KEYS = {"name", "polity", "rule_ai",
                    "start_cavalry", "start_gold", "start_supply"}


def build_config(template: dict | None = None) -> dict:
    tpl = template if template is not None else load_template()
    nat_tpl = next((n for n in tpl.get("nations", []) if n.get("base_url")), {})
    llm = {k: v for k, v in nat_tpl.items() if k not in SKIP_NATION_KEYS}
    cfg = {k: v for k, v in tpl.items() if k != "nations"}
    cfg.update({"map_size": SIZE, "seed": SEED, "save": SAVE, "journal": JOURNAL,
                "world_bank": True})   # 剧本按"开了央行"设计（利率差 5% 才有意义）
    cfg["nations"] = [{"name": nm, **llm} for nm in NATIONS]
    return cfg


def write_config(path: Path | str = ROOT / CONFIG, template: dict | None = None) -> Path:
    p = Path(path)
    p.write_text(json.dumps(build_config(template), ensure_ascii=False, indent=2) + "\n",
                 encoding="utf-8")
    return p


# ---------------------------------------------------------------- 命令行


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成八国剧本（开局存档 + 配套配置）")
    ap.add_argument("--out", default=str(ROOT / SAVE), help="存档写到哪（默认仓库根）")
    ap.add_argument("--config", default=str(ROOT / CONFIG), help="配置写到哪")
    ap.add_argument("--ring", action="store_true", help="改成环状随机开局（默认手摆）")
    ap.add_argument("--config-only", action="store_true", help="只重生成配置，不碰存档")
    ap.add_argument("--force", action="store_true",
                    help="确认丢弃已有进度（目标存档已打过回合时必须显式给）")
    args = ap.parse_args(argv)

    cfg_path = write_config(args.config)
    if not args.config_only:
        out = Path(args.out)
        # ★ 生成器是"**重开**"语义（整体覆盖写）——它跟 `mp_run` 的每回合存档不是一回事。
        #   所以绝不许**静默**盖掉一份已经在打的局：先看目标档的回合数，不是 0 就停下要
        #   `--force`（读不出来也算"不干净"，同样要 --force）。这条闸是给未来的人看的：
        #   剧本重生成很随意，但"随手盖掉一局"是不可逆的。
        if out.exists() and not args.force:
            try:
                played = json.loads(out.read_text(encoding="utf-8")).get("turn")
            except Exception:
                played = None
            if played != 0:
                raise SystemExit(
                    f"✗ {out} 已经打到第 {played} 回合（读不出来时会显示 None）——"
                    f"生成剧本＝**重开**，会把它整个覆盖。\n"
                    f"  要丢弃就加 --force；只是想要新档就换个 --out 路径。")
        w = build(starts=None if args.ring else STARTS)
        w.save(args.out)   # 整体覆盖写（`World.save` 自己就是覆盖语义）
        print(f"存档 → {args.out}")
        print(f"  {w.size}×{w.size} 种子 {w.seed}｜"
              + "、".join(f"{n}{STARTS.get(n, '')}" for n in w.order))
        print("  保障：" + "、".join(f"{w.entity_label(g)}→{ZHOU}"
                                    for g in w.guarantors_of(w.entity_of(ZHOU))))
        print(f"  各国 {len(w.own_tiles(w.order[0]))} 格十字 + 中心市政厅（国祚）")
        print(f"  剧本之志（常驻 {len(w.extra_prompt)} 国，**同一份文案**）：六王毕，四海一 ——"
              f" 尊王（入「周天下」）＝次要胜利 ｜ 兼并天下＝终极胜利")
    print(f"配置 → {cfg_path}（{len(NATIONS)} 国，含明文 key，**不入库**）")
    print(f"开局：./start.sh --config {Path(args.config).name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
