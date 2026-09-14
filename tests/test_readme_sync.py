# -*- coding: utf-8 -*-
"""README / docs ↔ 代码同步守卫：改引擎不改文档，这里会红。

病根（用户原话）：「经常改引擎忘记改文档」。游戏内 `rules` 文本由代码数值表现场生成，
不会漂移；漂移的只会是 README 与 docs（人类入口）。

★ 2026-09-15 起**局内上下文不注入 README**（匈奴的 rules 曾额外附 README 原文全文，
现已与普通国家一致）——`test_no_readme_injection` 盯着不回潮。地图生成分布这类上帝视角
数字仍只住在 `docs/地图生成与资源分布.md`：它不是"README 会被喂给 AI"的缘故了，
而是**README 是给人看的总览**，分布明细属于另一份文档（守卫 `TestReadmeNoGodView`
两边都盯：分布数字不得回流 README）。

三种手段：
  1. 表格解析：README 建筑表/地形表、docs 权重表/期望表逐行与代码对账（行数也要对上）；
  2. 代码常量构造串（如 f"+{ARMY_HEAL_PER_TURN} HP"）必须出现在对应文档——
     引擎数字一变，构造串就变，文档里没有 → 红；文档改错数字 → 同样红；
  3. 引擎里的**内联字面量**（匈奴 ×1.3、骑 8粮8装、灭国休战 +10 这类没有模块级
     常量的）做「源码仍含该字面量 + 文档仍含对应说法」双向检查；
  4. 防泄漏：README 不得包含地图分布内容（TestReadmeNoGodView）。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ctx as ctxlib  # noqa: E402
import mp  # noqa: E402
import mp_ai  # noqa: E402
import settlement  # noqa: E402
from game import (  # noqa: E402
    ARMY_MAX_HP,
    ARMY_HEAL_PER_TURN,
    ARMY_STARVE_DAMAGE,
    BUILDINGS,
    COMBAT_DIE_MOD,
    DIPLO_CENTER_MIN_COST,
    LETTER_CENTER_DISCOUNT,
    LETTER_CHARS_PER_GOLD,
    LETTER_COST,
    LETTER_COST_ALLY,
    LETTER_COST_MIN,
    LETTER_FREE_CHARS,
    MARKET,
    MARKET_DEPTH,
    MARKET_EQ_MAX_RATIO,
    MARKET_EQ_MIN_RATIO,
    MARKET_SPREAD,
    MAX_SLOTS,
    MOVE_COST,
    PRICE_IMPACT,
    PRICE_MAX_RATIO,
    PRICE_MIN_RATIO,
    PRICE_REVERT,
    RETREAT_ATK_PENALTY,
    RETREAT_RANGE,
    TERRAINS,
    TERRAIN_STATS,
    TERRAIN_WEIGHTS,
    UNIT_TYPES,
    building_effect,
)
from mp import (  # noqa: E402
    DIPLO_COST,
    PLAN_MAX_TURNS,
    REPORT_EVERY,
    RETREAT_DEF_COVER,
    SPY_COST,
    SPY_TURNS,
    START_RES,
    World,
)

README = (ROOT / "README.md").read_text(encoding="utf-8")
MAPGEN_DOC = ROOT / "docs" / "地图生成与资源分布.md"
MAPGEN = MAPGEN_DOC.read_text(encoding="utf-8")
MP_SRC = (ROOT / "mp.py").read_text(encoding="utf-8")
MP_AI_SRC = (ROOT / "mp_ai.py").read_text(encoding="utf-8")
MP_RUN_SRC = (ROOT / "mp_run.py").read_text(encoding="utf-8")
PROVIDER_SRC = (ROOT / "llm_provider.py").read_text(encoding="utf-8")

MINUS = "\u2212"   # README 里的负号是 U+2212（−），不是 ASCII -
ENDASH = "\u2013"  # 0–5 的连接号


def _table_rows(heading: str) -> list[list[str]]:
    """取 README 某个 ### 小节里的 markdown 表数据行（跳过表头与分隔行）。"""
    lines = README.splitlines()
    i = next(i for i, l in enumerate(lines) if l.startswith(heading))
    rows, seen_header = [], False
    for l in lines[i + 1:]:
        if l.startswith("#"):
            break
        if not l.strip().startswith("|"):
            continue
        cells = [c.strip() for c in l.strip().strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        if not seen_header:
            seen_header = True
            continue
        rows.append(cells)
    return rows


def _int(cell: str) -> int:
    m = re.search(r"[-+]?\d+", cell.replace(MINUS, "-"))
    assert m, f"单元格里没有数字：{cell!r}"
    return int(m.group())


class TestBuildingTable(unittest.TestCase):
    """README 建筑表 ↔ game.BUILDINGS：行数、造价、耗木、上限口径。"""

    def test_rows_match_buildings(self):
        rows = _table_rows("### 建筑表")
        self.assertEqual(len(rows), len(BUILDINGS),
                         f"README 建筑表 {len(rows)} 行 ≠ 代码 {len(BUILDINGS)} 种建筑")
        seen = set()
        for cells in rows:
            name = cells[0]
            self.assertIn(name, BUILDINGS, f"README 表里的「{name}」不在 BUILDINGS")
            seen.add(name)
            info = BUILDINGS[name]
            # 造价：列表（城堡）或标量
            cost = info["cost"]
            want = "/".join(map(str, cost)) if isinstance(cost, list) else str(cost)
            self.assertEqual(cells[1], want, f"{name} 造价：README {cells[1]} ≠ 代码 {want}")
            # 耗木：单元格开头数字（城堡写「10/级」）
            self.assertEqual(_int(cells[2]), info["wood"], f"{name} 耗木不符")
            # 上限口径：城堡=「N 级」；资源上限「=本地X」；否则「任地可建」或位门槛「本地已用…」
            if info["kind"] == "castle":
                self.assertEqual(cells[3], f"{info['max_level']} 级", "城堡级数上限不符")
            elif info["cap_resource"]:
                self.assertIn(f"=本地{info['cap_resource']}", cells[3], f"{name} 上限口径不符")
            else:
                self.assertTrue("任地可建" in cells[3] or "本地已用" in cells[3],
                                f"{name} 上限口径不符：{cells[3]}")
        self.assertEqual(seen, set(BUILDINGS), "建筑表与 BUILDINGS 键集合不一致")

    def test_building_effect_strings(self):
        g = BUILDINGS["黄金矿场"]
        gain = g["outputs"]["黄金"] * MARKET["黄金"]
        for s in (
            f"每级 +{building_effect('城堡', 'defense_per_level')}% 防御",
            f"半径 {building_effect('瞭望塔', 'vision_radius')} 圆",
            f"建造金价 {MINUS}{building_effect('工程院', 'build_discount')}%",
            f"{building_effect('市政厅', 'gold_base')} 金 + 本地块其他建筑数"
            f"×{building_effect('市政厅', 'gold_per_slot')} 金入国库",
            f"{MAX_SLOTS} 建筑位",
            f"| 黄金矿场 | {g['cost']} | {g['wood']} | =本地黄金 | +{gain} 金入国库 |",
        ):
            self.assertIn(s, README, f"README 缺：{s}")


class TestTerrainTable(unittest.TestCase):
    """README 地形表 ↔ TERRAIN_STATS（防御/建设惩罚是游戏规则，留在 README）。"""

    def test_terrain_rows(self):
        rows = _table_rows("### 地形")
        self.assertEqual(len(rows), len(TERRAIN_STATS))
        for cells in rows:
            ter = cells[0]
            st = TERRAIN_STATS[ter]
            self.assertEqual(_int(cells[1]), st["defense"], f"{ter} 防御不符")
            self.assertEqual(_int(cells[2]), st["build_penalty"], f"{ter} 建设惩罚不符")

    def test_readme_points_to_mapgen_doc(self):
        # 分布数字搬走了，README 只留链接
        self.assertIn("docs/地图生成与资源分布.md", README)


class TestMapgenDoc(unittest.TestCase):
    """docs/地图生成与资源分布.md ↔ TERRAINS/TERRAIN_WEIGHTS/TERRAIN_STATS。

    地图分布数字只许住在这份文档里（README 会原文进匈奴的游戏内 rules 返回）。"""

    RES = ("矿石", "黄金", "耕地", "石油", "木头")

    def _exp_ter(self, ter, r):
        w = TERRAINS[ter][r]
        return sum(i * wt for i, wt in enumerate(w)) / sum(w)

    def _exp_all(self, r):
        tot = sum(TERRAIN_WEIGHTS.values())
        return sum(self._exp_ter(t, r) * w for t, w in TERRAIN_WEIGHTS.items()) / tot

    def _close(self, stated: str, actual: float, what: str):
        dec = len(stated.split(".")[1]) if "." in stated else 0
        self.assertLessEqual(abs(actual - float(stated)), 0.5 * 10 ** -dec + 1e-6,
                             f"{what}：文档写 {stated}，代码实算 {actual:.4f}")

    def test_terrain_priors(self):
        for t, w in TERRAIN_WEIGHTS.items():
            m = re.search(rf"^\| {t} \| (\d+) \| (\d+)% \| ([+\u2212-]\d+)% \| ([+\u2212-]\d+)% \|$",
                          MAPGEN, re.M)
            self.assertIsNotNone(m, f"文档地形先验表缺 {t}")
            self.assertEqual(int(m.group(1)), w, f"{t} 先验权重不符")
            st = TERRAIN_STATS[t]
            self.assertEqual(int(m.group(3).replace(MINUS, "-")), st["defense"])
            self.assertEqual(int(m.group(4).replace(MINUS, "-")), st["build_penalty"])

    def test_weight_tables(self):
        for r in self.RES:
            mx = max(len(TERRAINS[t][r]) - 1 for t in TERRAINS)
            self.assertIn(f"**{r}**（实际最大档 x{mx}）", MAPGEN)
            for t in TERRAINS:
                w = TERRAINS[t][r]
                cells = " | ".join(str(w[i]) if i < len(w) else "\u2014" for i in range(mx + 1))
                self.assertIn(f"| {t} | {cells} |", MAPGEN, f"文档 {r}/{t} 权重行与 TERRAINS 不符")

    def test_expectation_table(self):
        for r in self.RES:
            mx = max(len(TERRAINS[t][r]) - 1 for t in TERRAINS)
            m = re.search(rf"^\| {r} \| ([\d.]+) \| 0{ENDASH}{mx} \|$", MAPGEN, re.M)
            self.assertIsNotNone(m, f"文档期望表缺 {r}")
            self._close(m.group(1), self._exp_all(r), f"{r} 全图期望")

    def test_specialization_numbers(self):
        for ter, r in (("山地", "矿石"), ("森林", "木头"), ("平原", "耕地"), ("沙漠", "石油")):
            m = re.search(ter + r + r" ([\d.]+)", MAPGEN)
            self.assertIsNotNone(m, f"文档缺 {ter}{r} 专精数字")
            self._close(m.group(1), self._exp_ter(ter, r), f"{ter}{r} 专精")
        m = re.search(r"0\.147\u2192([\d.]+)", MAPGEN)   # 重平衡记录：黄金 0.147→X
        self.assertIsNotNone(m)
        self._close(m.group(1), self._exp_all("黄金"), "重平衡记录黄金期望")
        m = re.search(r"0\.27\u2192([\d.]+)", MAPGEN)     # 石油 0.27→X
        self.assertIsNotNone(m)
        self._close(m.group(1), self._exp_all("石油"), "重平衡记录石油期望")


class TestReadmeNoGodView(unittest.TestCase):
    """两道守卫：

    ① **局内不注入 README**（`test_no_readme_injection`）——曾几何时匈奴的 `rules`
       额外附 README 原文全文，于是"README 里能写什么"变成了**规则约束**；
       现在 `rules` 一律返回代码现算的规则文本，所有政体同一份。
    ② **分布数字不回流 README**（`test_no_distribution_leaks`）——地图生成分布
       （档位/倾向/期望/专精）只住在 `docs/地图生成与资源分布.md`：README 是给人看的
       总览，明细属另一份文档。① 若被后人改回去，② 就从"文档整洁"升级成"防开图"。
    """

    # 查**真的读文件**（字符串字面量 / 赋值），不查"注释里提到这个词"——
    # 那条历史教训本身值得留在代码注释里。
    README_READ = re.compile(r"""["']README\.md["']|_README_TEXT\s*=""")

    def test_no_readme_injection(self):
        """`rules` 不许再读 README：所有政体的规则文本必须**同源**（`_help_sections()`）。"""
        hit = self.README_READ.search(MP_AI_SRC)
        self.assertIsNone(hit, f"mp_ai 又把 README 读进局内上下文了（{hit.group() if hit else ''}）"
                               f"——规则文本该由代码现算")

    def test_huns_rules_same_as_others(self):
        """匈奴与普通国家查 `rules` 得到**同一份**文本（政体专属机制在 system prompt 里）。"""
        w = mp.World(size=12, seed=7, nations=["秦", "林胡"])
        w.apply_polity("林胡", "huns")
        for topic in ("", "建筑", "外交"):
            with self.subTest(topic=topic):
                self.assertEqual(mp_ai.execute(w, "林胡", "rules", {"topic": topic}),
                                 mp_ai.execute(w, "秦", "rules", {"topic": topic}),
                                 f"匈奴的 rules({topic!r}) 与普通国家不一致")

    def test_no_distribution_leaks(self):
        for s in ("平均每格资源期望", "资源倾向", "地块资源量", "实际最大档",
                  "山地矿石", "森林木头", "平原耕地", "沙漠石油", "重平衡",
                  "档位", "先验", "期望值", "上帝视角"):
            self.assertNotIn(s, README,
                             f"README 泄漏地图分布内容：「{s}」——只许写在 docs/地图生成与资源分布.md")
        for r in ("矿石", "黄金", "耕地", "石油", "木头"):
            mx = max(len(TERRAINS[t][r]) - 1 for t in TERRAINS)
            self.assertNotIn(f"{r} 0{ENDASH}{mx}", README, f"README 泄漏 {r} 档位范围")


class TestUnits(unittest.TestCase):
    def test_unit_strings(self):
        """兵种行：费用/补给/移动都是从 `UNIT_TYPES` + `MOVE_COST` 现构的串（改表就红）。

        ★ `speed` 现在是**移动力**（不是格数）：README 按地形分开写"平地 N 格 / 崎岖 M 格"，
        这里同样现算，别把口径写回死数。
        """
        b, q, m = UNIT_TYPES["步"], UNIT_TYPES["骑"], UNIT_TYPES["民"]
        cav_open = q["speed"] // min(MOVE_COST["骑"].values())
        cav_slow = q["speed"] // max(MOVE_COST["骑"].values())
        for s in (
            f"每支 **{ARMY_MAX_HP} HP**",
            f"**步兵**（{b['recruit']['粮食']}粮+{b['recruit']['装备']}装，"
            f"动 {b['speed']} 格/回合，耗补给 {b['supply']}）",
            f"**骑兵**（{q['recruit']['粮食']}粮+{q['recruit']['装备']}装，"
            f"平地动 {cav_open} 格、崎岖 {cav_slow} 格，耗补给 {q['supply']}）",
            f"{m['recruit']['黄金']}金+{m['recruit']['粮食']}粮/支",
            f"**{m['hp']}HP**、攻 {m['atk']}，动 {m['speed']} 格/回合",
        ):
            self.assertIn(s, README, f"README 缺：{s}")

    def test_move_cost_table_in_readme(self):
        """移动代价：README 的路面分类与对称口径必须与 `MOVE_COST` 一致（改表就红）。"""
        costs = MOVE_COST["骑"]
        open_t = [t for t in TERRAIN_STATS if costs.get(t, 1) == min(costs.values())]
        slow_t = [t for t in TERRAIN_STATS if t not in open_t]
        self.assertIn(f"**{'/'.join(open_t)}每格 1、{'/'.join(slow_t)}每格 2**", README,
                      "README 的移动代价分类与 balance.MOVE_COST 不一致")
        self.assertIn("出发格与目标格取更贵的那个", README, "README 没写对称口径")
        self.assertIn("穿不过去", README, "README 没写'崎不可穿越'")

    def test_combat_numbers(self):
        dice = "/".join(f"{COMBAT_DIE_MOD[d]:+d}%".replace("-", MINUS) for d in sorted(COMBAT_DIE_MOD))
        td = TERRAIN_STATS["山地"]["defense"]
        cd = BUILDINGS["城堡"]["max_level"] * building_effect("城堡", "defense_per_level")
        pct = 100 - ((100 - td) * (100 - cd)) // 100
        for s in (
            dice,
            f"山地+城堡L5 = {pct}% 减伤",
            f"{MINUS}{ARMY_STARVE_DAMAGE} × 缺口/需求",
            f"+{ARMY_HEAL_PER_TURN} HP",
            f"退相邻 {RETREAT_RANGE} 格",
            f"守方减伤 {RETREAT_DEF_COVER}%",
            f"输出 {MINUS}{RETREAT_ATK_PENALTY}%",
        ):
            self.assertIn(s, README, f"README 缺：{s}")


class TestMarket(unittest.TestCase):
    def test_market_strings(self):
        self.assertEqual(MARKET_DEPTH["石油"], MARKET_DEPTH["补给"], "「油/补给 12」合并写法要求两者相等")
        self.assertEqual(MARKET_DEPTH["粮食"], MARKET_DEPTH["木头"], "「粮/木 24」合并写法要求两者相等")
        depth = (f"粮/木 {MARKET_DEPTH['粮食']}、矿 {MARKET_DEPTH['矿石']}、"
                 f"油/补给 {MARKET_DEPTH['石油']}、装备 {MARKET_DEPTH['装备']}")
        chain_prices = " / ".join(f"{g} {MARKET[g]}" for g in ("粮食", "木头", "矿石", "石油", "装备", "补给"))
        for s in (
            f"基准价：{chain_prices}",
            depth,
            f"基准价 × {PRICE_IMPACT} ÷ 深度",
            f"买卖价差 {round(MARKET_SPREAD * 100)}%",
            f"回归 {round((1 - PRICE_REVERT) * 100)}%",
            f"{PRICE_MIN_RATIO:g}×~{PRICE_MAX_RATIO:g}×",
            f"{MARKET_EQ_MIN_RATIO:g}×~{MARKET_EQ_MAX_RATIO:g}×",
            "现存国家数/4",
        ):
            self.assertIn(s, README, f"README 缺：{s}")

    def test_inline_literals_in_engine(self):
        # 市场深度 ÷4 是内联字面量：源码侧也钉一下，改动时两头一起改
        self.assertIn("len(self.alive())) / 4", MP_SRC)


class TestDiplomacy(unittest.TestCase):
    def test_diplomacy_strings(self):
        chain, c = [], DIPLO_COST
        while True:
            chain.append(str(c))
            if c <= DIPLO_CENTER_MIN_COST:
                break
            c = max(DIPLO_CENTER_MIN_COST, c // 2)
        for s in (
            f"| `spy` | {SPY_COST} 金 |",
            f"{SPY_TURNS} 回合后拿回",
            f"外交基础费 {DIPLO_COST} 金",
            f"{'→'.join(chain)}，下限 {DIPLO_CENTER_MIN_COST}",
            f"联盟内 {LETTER_COST_ALLY} 金 / 非联盟 {LETTER_COST} 金",
            f"含前 {LETTER_FREE_CHARS} 字",
            f"每座 {MINUS}{LETTER_CENTER_DISCOUNT}",
            f"下限 {LETTER_COST_MIN}",
            f"每 {LETTER_CHARS_PER_GOLD} 字 1 金",
            f"每 {PLAN_MAX_TURNS} 回合必须修订",
            f"每 {REPORT_EVERY} 回合自动出一期",
            "强制休战 10 回合",
        ):
            self.assertIn(s, README, f"README 缺：{s}")

    def test_inline_literals_in_engine(self):
        self.assertIn("self.turn + 10", MP_SRC, "灭国全图休战的内联字面量变了？同步 README")


class TestStartAndPolity(unittest.TestCase):
    def test_start_res(self):
        s = (f"黄金 {START_RES['黄金']}、粮 {START_RES['粮食']}、木 {START_RES['木头']}、"
             f"矿 {START_RES['矿石']}、油 {START_RES['石油']}、装备 {START_RES['装备']}、"
             f"补给 {START_RES['补给']}")
        self.assertIn(s, README)

    def test_huns(self):
        """匈奴三处数值**只在 `balance.POLITY` 一处**（2026-09-15 从内核字面量搬过去），
        README 的说法跟着它走；引擎与 AI 文案都不许再写死这几个数。"""
        from balance import POLITY
        h = POLITY["huns"]
        self.assertEqual(h["build_cost_pct"] - 100, 30, "建造惩罚变了：README 说 ×1.3")
        self.assertEqual(UNIT_TYPES["骑"]["recruit"], {"粮食": 12, "装备": 12})
        self.assertEqual(h["recruit"]["骑"], {"粮食": 8, "装备": 8},
                         "匈奴骑兵特价变了：README 说 8 粮 8 装")
        self.assertEqual(h["start"], {"黄金": 1000, "补给": 200, "骑": 6},
                         "匈奴开局变了：README 说 6 骑 / 1000 金 / 200 补给")
        for s in ("建筑造价 ×1.3", "8 粮 8 装", "6 骑 / 1000 金 / 200 补给"):
            self.assertIn(s, README, f"README 缺：{s}")
        for name, src in (("mp.py", MP_SRC), ("mp_ai.py", MP_AI_SRC)):
            for lit in ("13 // 10", '"粮食": 8, "装备": 8', 'start.get("骑", 6)'):
                self.assertNotIn(lit, src,
                                 f"{name} 又写死了政体数值「{lit}」——该走 balance.POLITY")

class TestAiLayer(unittest.TestCase):
    def test_tool_count(self):
        w = World(size=12, seed=7, nations=["秦"])
        n = len(mp_ai.tool_schemas(w, "秦"))
        self.assertIn(f"工具面（{n} 个）", README)

    def test_query_panels(self):
        seg = MP_AI_SRC[MP_AI_SRC.index('if tool in ("query"'):MP_AI_SRC.index(".get(which")]
        keys = re.findall(r'"([a-z_]+)":', seg)
        panels = [k for k in keys if k != "all"]
        self.assertIn(f"{len(panels)} 个面板：", README)
        line = next(l for l in README.splitlines() if "个面板：" in l)
        for p in panels:
            self.assertIn(p, line, f"query 面板 {p} 没列进 README")

    def test_settlement_weights(self):
        w = settlement.UNIT_WEIGHT
        self.assertIn(f"步{w['步']}/骑{w['骑']}/民{w['民']}", README)


class TestConfigDefaults(unittest.TestCase):
    """README 配置表的默认值 ↔ 各入口的 cfg.get(...) 缺省（源码正则抽取）。"""

    def _src_default(self, src: str, key: str) -> str:
        m = re.search(rf'cfg\.get\("{key}",\s*([\d.]+)\)', src)
        self.assertIsNotNone(m, f"源码里找不到 cfg.get({key!r}, …) 的缺省值")
        return m.group(1)

    def test_defaults(self):
        pairs = [
            ("| `map_size` |", self._src_default(MP_RUN_SRC, "map_size")),
            ("| `max_turns` |", self._src_default(MP_RUN_SRC, "max_turns")),
            ("| `max_steps` |", self._src_default(MP_RUN_SRC, "max_steps")),
            ("| `max_actions` |", self._src_default(MP_RUN_SRC, "max_actions")),
            ("| `temperature` |", self._src_default(PROVIDER_SRC, "temperature")),
            ("| `max_tokens` |", self._src_default(PROVIDER_SRC, "max_tokens")),
            ("| `api_timeout` |", self._src_default(PROVIDER_SRC, "api_timeout")),
        ]
        for head, val in pairs:
            self.assertIn(f"{head} {val} |", README, f"README 配置表缺 {head} {val}")
        retries = self._src_default(PROVIDER_SRC, "api_retries")
        wait = self._src_default(PROVIDER_SRC, "api_retry_wait")
        self.assertIn(f"| `api_retries` / `api_retry_wait` | {retries} / {wait} |", README)

    def test_ctx_defaults(self):
        for s in (
            f"| `ctx_fill` | {ctxlib.DEFAULT_FILL} |",
            f"| `ctx_slide_keep` | {ctxlib.DEFAULT_SLIDE_KEEP} |",
            f"| `ctx_archive_fill` / `ctx_archive_max` | {ctxlib.DEFAULT_ARCHIVE_FILL} / \u2014 |",
            f"| `ctx_min_turns` | {ctxlib.DEFAULT_MIN_TURNS} |",
            f"（默认 {ctxlib.DEFAULT_FIXED_TURNS}）回合",
        ):
            self.assertIn(s, README, f"README 缺：{s}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
