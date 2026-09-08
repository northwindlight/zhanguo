#!/usr/bin/env python3
"""结算机制：解析 mp_save.json 存档，按 GDP/军队/领土/固定资产 打分排名，
然后把各国 AI（抽走全部工具）请进「结算厅」聊天室，自由总结与交换意见，只进行 5 轮。

用法（在含 mp_save.json / mp_config.json 的目录运行，或用 --save/--config 指定）：
    python3 settlement.py                     # 打分 + 结算厅 5 轮
    python3 settlement.py --no-chat           # 只打分，不进聊天室
    python3 settlement.py --save ../zhanguo/mp_save.json

产物：结算报告.md（分数明细 + 结算厅完整对话录）。

────────────────────────────────────────────────────────────────────────
评分口径（全部按基准价结算，不用市价——市价随回合波动，基准价才是恒定标尺）
基准价：粮食2 木头2 矿石4 石油6 装备8 补给5；黄金矿场每座每回合 +10 金。

一、GDP（30%）——生产法（增加值法），按存档快照推算「每回合」流量：
    1. 采集建筑：产出 × 基准价（林场/农场/矿场/石油厂）
    2. 黄金矿场：+10 金/座（直接计货币产出）
    3. 市政厅：5 金基础 + 本地块其他建筑 ×1 金（复现游戏内公式）
    4. 能源厂：增加值 = 发电量 × 电价(3金) − 燃料 × 基准价
       （电不存储、无市价，取影子电价 3金/电：木材厂 2×3−1×2=4；油厂 5×3−1×6=9）
    5. 装备厂：增加值 = 装备×8 − 矿×4 − 油×6（中间投入按基准价扣除）
    6. 军费（政府最终消费，即「军队消费纳入消费」）：Σ军队补给耗(步1骑2)×补给价5
       ——补给厂产出**不计**增加值，其价值在军费项体现，避免双算
    选生产法而弃消费法：存档是存量快照，生产法可从建筑表确定性推算一回合流量；
    消费法需要建造成本/征兵/市场买卖的每回合流水，快照里没有，解析 history 不可靠。

二、军队（25%）：军力 = Σ(当前HP/100) × 兵种权重（步 1.0 / 骑 1.5）。

三、领土（30%）：地块数（plain count，最透明可解释）。

四、固定资产（15%）：重置成本 = Σ(造价金 + 耗木 × 木基准价)；
    城堡按已升到的级数计累计投入（Σ 第1..L级造价）。

归一化：每维度 ÷ 存活国最大值 × 100（榜首=100），加权求和 → 总分（满分 100）。
────────────────────────────────────────────────────────────────────────
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

# ───────────────────────── 常量（与 game.py 基准价保持一致） ─────────────────────────
BASE_PRICE = {"粮食": 2, "木头": 2, "矿石": 4, "石油": 6, "装备": 8, "补给": 5}
GOLD_MINE_PER_TURN = 10          # 黄金矿场 +10 金/回合
TOWN_HALL_BASE = 5               # 市政厅基础金
TOWN_HALL_PER_SLOT = 1           # 市政厅每建筑位 +1 金
ENERGY_PRICE = 3                 # 影子电价（电不存储无市价，取 3 金/电）
UNIT_SUPPLY = {"步": 1, "骑": 2}  # 每支军队每回合补给耗量
UNIT_WEIGHT = {"步": 1.0, "骑": 1.5}
CASTLE_COST = [100, 200, 400, 800, 1600]  # 城堡逐级造价（累计投入求和用）

# 建筑造价/耗木（重置成本用；与 game.py BUILDINGS 表一致）
BUILD_COST = {
    "林场": (50, 5), "农场": (60, 5), "矿场": (80, 5), "石油厂": (150, 8),
    "黄金矿场": (200, 10), "木材能源厂": (120, 15), "石油能源厂": (300, 15),
    "补给厂": (200, 12), "装备厂": (240, 12), "兵营": (350, 20), "市政厅": (500, 40),
}

W_GDP, W_ARMY, W_LAND, W_ASSET = 0.30, 0.25, 0.30, 0.15
CHAT_ROUNDS = 5


# ───────────────────────── 存档解析与打分 ─────────────────────────
def load_save(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def army_type(a: dict) -> str:
    t = a.get("type")
    if t in UNIT_WEIGHT:
        return t
    return "骑" if "骑" in a.get("name", "") else "步"  # 旧档无 type 时从番号推断


def score_gdp(save: dict, nation: str) -> tuple[float, list[str]]:
    """生产法 GDP：按快照推算每回合增加值（基准价）。返回 (gdp, 聚合明细行)。"""
    agg: dict[str, tuple[int, float]] = {}  # 名目 → (数量, 金额)
    gdp = 0.0

    def _add(item: str, cnt: int, v: float) -> None:
        c0, v0 = agg.get(item, (0, 0.0))
        agg[item] = (c0 + cnt, v0 + v)
        nonlocal gdp
        gdp += v

    for t in save["tiles"].values():
        if t.get("owner") != nation:
            continue
        b = t.get("buildings", {})
        for name, cnt in b.items():
            if not cnt:
                continue
            if name == "城堡":
                continue  # 城堡无产出，投入计固定资产
            elif name == "林场":
                _add("林场", cnt, cnt * BASE_PRICE["木头"])
            elif name == "农场":
                _add("农场", cnt, cnt * BASE_PRICE["粮食"])
            elif name == "矿场":
                _add("矿场", cnt, cnt * BASE_PRICE["矿石"])
            elif name == "石油厂":
                _add("石油厂", cnt, cnt * BASE_PRICE["石油"])
            elif name == "黄金矿场":
                _add("金矿", cnt, cnt * GOLD_MINE_PER_TURN)
            elif name == "市政厅":
                others = sum(b.values()) - cnt  # 与游戏内公式一致：不含市政厅自身
                _add(f"市政厅(每座+{others}位)", cnt,
                     cnt * (TOWN_HALL_BASE + others * TOWN_HALL_PER_SLOT))
            elif name == "木材能源厂":
                _add("木材能源厂", cnt, cnt * (2 * ENERGY_PRICE - BASE_PRICE["木头"]))
            elif name == "石油能源厂":
                _add("石油能源厂", cnt, cnt * (5 * ENERGY_PRICE - BASE_PRICE["石油"]))
            elif name == "装备厂":
                _add("装备厂", cnt,
                     cnt * (2 * BASE_PRICE["装备"] - BASE_PRICE["矿石"] - BASE_PRICE["石油"]))
            # 补给厂不计增加值：补给价值在军费（政府最终消费）中体现，防双算
    armies = [a for a in save["armies"] if a.get("owner") == nation]
    if armies:
        mcost = sum(UNIT_SUPPLY[army_type(a)] for a in armies) * BASE_PRICE["补给"]
        _add(f"军费({len(armies)}支)", len(armies), mcost)
    rows = [f"{k}×{c} {v:+.0f}" for k, (c, v) in agg.items()]
    return gdp, rows


def score_army(save: dict, nation: str) -> tuple[float, int]:
    armies = [a for a in save["armies"] if a.get("owner") == nation]
    power = sum(a.get("hp", 0) / 100.0 * UNIT_WEIGHT[army_type(a)] for a in armies)
    return power, len(armies)


def score_land(save: dict, nation: str) -> tuple[int, int]:
    tiles = [t for t in save["tiles"].values() if t.get("owner") == nation]
    return len(tiles), len(tiles)


def score_asset(save: dict, nation: str) -> tuple[float, list[str]]:
    agg: dict[str, tuple[int, float]] = {}
    total = 0.0

    def _add(item: str, cnt: int, inv: float) -> None:
        c0, v0 = agg.get(item, (0, 0.0))
        agg[item] = (c0 + cnt, v0 + inv)

    for t in save["tiles"].values():
        if t.get("owner") != nation:
            continue
        b = t.get("buildings", {})
        for name, cnt in b.items():
            if not cnt:
                continue
            if name == "城堡":
                _add(f"城堡L{cnt}", cnt, sum(CASTLE_COST[:cnt]))  # 逐级累计投入
            else:
                gold, wood = BUILD_COST[name]
                _add(name, cnt, cnt * (gold + wood * BASE_PRICE["木头"]))
    total = sum(v for _, v in agg.values())
    rows = [f"{k}×{c} {v:.0f}金" for k, (c, v) in sorted(agg.items(), key=lambda kv: -kv[1][1])]
    return total, rows


def normalize(vals: dict[str, float]) -> dict[str, float]:
    top = max(vals.values()) if vals else 0
    if top <= 0:
        return {k: 0.0 for k in vals}
    return {k: v / top * 100 for k, v in vals.items()}


def settle(save: dict) -> dict:
    """返回 {nation: {gdp, army, land, asset, scores{...}, total}}。"""
    alive = list(save["nations"].keys())
    out: dict[str, dict] = {}
    for n in alive:
        gdp, gdp_rows = score_gdp(save, n)
        army, n_army = score_army(save, n)
        land, _ = score_land(save, n)
        asset, asset_rows = score_asset(save, n)
        out[n] = {"gdp": gdp, "gdp_rows": gdp_rows, "army": army, "n_army": n_army,
                  "land": land, "asset": asset, "asset_rows": asset_rows}
    s_gdp = normalize({n: out[n]["gdp"] for n in alive})
    s_army = normalize({n: out[n]["army"] for n in alive})
    s_land = normalize({n: out[n]["land"] for n in alive})
    s_asset = normalize({n: out[n]["asset"] for n in alive})
    for n in alive:
        out[n]["s_gdp"] = s_gdp[n]
        out[n]["s_army"] = s_army[n]
        out[n]["s_land"] = s_land[n]
        out[n]["s_asset"] = s_asset[n]
        out[n]["total"] = (s_gdp[n] * W_GDP + s_army[n] * W_ARMY +
                           s_land[n] * W_LAND + s_asset[n] * W_ASSET)
    return out


def scoreboard_text(save: dict, result: dict) -> str:
    turn = save.get("turn", "?")
    rank = sorted(result, key=lambda n: -result[n]["total"])
    lines = [f"《战国》第 {turn} 回合 · 终局结算（GDP 30% · 军队 25% · 领土 30% · 固定资产 15%）", ""]
    lines.append(f"{'排名':<4}{'国家':<6}{'总分':>6} | {'GDP':>6}/{W_GDP:.0%} | {'军力':>6}/{W_ARMY:.0%} | {'领土':>4}/{W_LAND:.0%} | {'资产':>6}/{W_ASSET:.0%}")
    for i, n in enumerate(rank, 1):
        r = result[n]
        lines.append(
            f"{i:<4}{n:<6}{r['total']:>6.1f} | "
            f"{r['s_gdp']:>5.1f}({r['gdp']:>5.0f}) | "
            f"{r['s_army']:>5.1f}({r['army']:>5.1f}) | "
            f"{r['s_land']:>4.1f}({r['land']:>3d}) | "
            f"{r['s_asset']:>5.1f}({r['asset']:>5.0f})")
    lines.append("")
    lines.append("口径：GDP=生产法每回合推算(基准价)；军力=ΣHP%×兵种权重(步1.0/骑1.5)；"
                 "领土=地块数；资产=建筑重置成本(基准价)。括号内为原始值。")
    return "\n".join(lines)


# ───────────────────────── 结算厅（无工具自由聊天，5 轮） ─────────────────────────
SYSTEM_PROMPT = """你在一局大战略游戏《战国》中扮演「{name}」的君主。游戏已在第 {turn} 回合终局，
下面给出最终结算成绩单。现在你被请进「结算厅」：所有君主围坐一堂，**没有工具、不能行动**，
只能用文字发言，共 {rounds} 轮、每轮发言一次。

发言要求：
1. 以你的角色总结这一局：哪些决策英明、哪些是败笔（要诚实，给观海者一个交代）；
2. 对最终排名表态：服或不服，说清理由；
3. 与其他君主交换意见：可以致意、可以反驳、可以互认得失——这是全剧终前的最后对话；
4. 中文，200~500 字，不要客套空话，不要跳出角色。
"""


def run_chat(save: dict, result: dict, cfg: dict, rounds: int, log) -> list[str]:
    from openai import OpenAI
    nations = list(save["nations"].keys())
    clients = {}
    for nc in cfg.get("nations", []):
        if nc.get("base_url") and nc.get("api_key") and nc["name"] in nations:
            extra = {}
            if "thinking" in nc:
                extra["thinking"] = {"type": nc["thinking"]}
            if nc.get("reasoning_effort"):
                extra["reasoning_effort"] = nc["reasoning_effort"]
            clients[nc["name"]] = (OpenAI(base_url=nc["base_url"], api_key=nc["api_key"]),
                                   nc.get("model"), nc.get("temperature", 0.7),
                                   nc.get("max_tokens", 8192), extra)
    missing = [n for n in nations if n not in clients]
    if missing:
        log(f"（缺 API 配置，{ '、'.join(missing) } 不参加结算厅）")

    board = scoreboard_text(save, result)
    transcript: list[str] = []  # [f"【第r轮·{name}】..."]
    for r in range(1, rounds + 1):
        order = nations[(r - 1) % len(nations):] + nations[:(r - 1) % len(nations)]
        log(f"─── 结算厅 第 {r}/{rounds} 轮 ───")
        for name in order:
            if name not in clients:
                continue
            client, model, temp, max_tok, extra = clients[name]
            history = "\n\n".join(transcript) if transcript else "（你是第一位发言者）"
            user = (f"【最终成绩单】\n{board}\n\n【此前发言】\n{history}\n\n"
                    f"【第 {r}/{rounds} 轮】轮到你（{name} 的君主）发言。")
            msgs = [{"role": "system", "content": SYSTEM_PROMPT.format(
                        name=name, turn=save.get("turn", "?"), rounds=rounds)},
                    {"role": "user", "content": user}]
            for attempt in (1, 2, 3):
                try:
                    resp = client.chat.completions.create(
                        model=model, messages=msgs, temperature=temp,
                        max_tokens=max_tok, extra_body=extra or None)
                    text = (resp.choices[0].message.content or "").strip()
                    break
                except Exception as e:
                    log(f"（{name} 第{attempt}次发言失败：{e}）")
                    text = ""
                    time.sleep(3 * attempt)
            if not text:
                transcript.append(f"【第{r}轮·{name}】（发言失败，缺席）")
                continue
            transcript.append(f"【第{r}轮·{name}】{text}")
            log(f"◆ {name}：{text}")
    return transcript


# ───────────────────────── 主流程 ─────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser(description="战国终局结算：打分 + 结算厅")
    ap.add_argument("--save", default="mp_save.json")
    ap.add_argument("--config", default="mp_config.json")
    ap.add_argument("--rounds", type=int, default=CHAT_ROUNDS)
    ap.add_argument("--no-chat", action="store_true", help="只打分，不进结算厅")
    ap.add_argument("--out", default="结算报告.md")
    args = ap.parse_args()

    if not Path(args.save).exists():
        sys.exit(f"找不到存档 {args.save}（用 --save 指定）")
    save = load_save(args.save)
    result = settle(save)

    def log(s: str) -> None:
        print(s, flush=True)

    board = scoreboard_text(save, result)
    log(board)

    transcript: list[str] = []
    if not args.no_chat:
        cfg = {}
        if Path(args.config).exists():
            cfg = json.load(open(args.config, encoding="utf-8"))
        else:
            log(f"（找不到 {args.config}，跳过结算厅）")
        if cfg:
            log("")
            transcript = run_chat(save, result, cfg, args.rounds, log)

    report = [f"# 《战国》终局结算报告\n", f"生成于第 {save.get('turn','?')} 回合存档。\n",
              "## 成绩单\n", f"```\n{board}\n```\n"]
    for n, r in sorted(result.items(), key=lambda kv: -kv[1]["total"]):
        report.append(f"### {n}（总分 {r['total']:.1f}）\n")
        report.append(f"- GDP 原始值 {r['gdp']:.0f}：{'；'.join(r['gdp_rows']) or '无经济产出'}")
        report.append(f"- 固定资产 {r['asset']:.0f}：{'；'.join(r['asset_rows']) or '无建筑'}\n")
    if transcript:
        report.append("## 结算厅对话录\n")
        report.append("\n\n".join(transcript))
    Path(args.out).write_text("\n".join(report), encoding="utf-8")
    log(f"\n📄 报告已写入 {args.out}")


if __name__ == "__main__":
    main()
