# -*- coding: utf-8 -*-
"""双语守卫：语言只是渲染时的一次查表，**不进存档、不进状态**。

病根：`world.log(f"动工 …")` 过去把**渲染好的中文散文**塞进 `history`（它在 `SAVE_KEYS`
里），语言于是被焊进存档 —— 换语言等于换存档语义，中途改配置还会中英混排。现在引擎
产出 `Msg(键, 参数)`，读取时才定语言。这个形状没人盯着就会漂回去，所以钉成测试：

  1. **完全翻译**：除基准表 `zh` 外，`langs/*.json` 必须逐键齐全、槽位一致
     （缺键 / 多键 / 槽位拼错都当场报错，**不静默退回中文**）；
  2. **英文表里不许有汉字**：模板与词库取值都不许 —— 半翻译就在这儿露馅；
  3. **键自洽**：源码里 `Msg("k")` / `t("k")` 用到的键必须都在表里，表里也不许留死键；
  4. **游戏名词必须全有译文**：`BUILDINGS`/地形/资源/兵种/纪事分类这些**会出现在
     回执与纪事里**的词，英文词库一个都不许漏（地名/国名是专有名词，允许玩家自补）；
  5. **语言不进存档**：同一局分别在中 / 英缺省语言下跑完并存档，**字节必须完全相同**。

另附 `Msg` 的往返 / 未知键报错 / 与字符串比较的兼容桥，以及"迁移不许顺手改中文文案"
的字面对拍。

跑法：python3 -m unittest tests.test_i18n -v
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import balance as B  # noqa: E402
import i18n  # noqa: E402
import mp  # noqa: E402
import mp_ai  # noqa: E402

CJK = re.compile(r"[一-鿿]")


def _src_files() -> list[Path]:
    """会产文案的源码：仓库根的模块 + `ruleai/`（tests 里引用的键不算"用过"）。"""
    out = sorted(ROOT.glob("*.py")) + sorted((ROOT / "ruleai").rglob("*.py"))
    return [p for p in out if "__pycache__" not in str(p)]


def _keys_used() -> set[str]:
    """源码里以**字面量**引用的模板键：`Msg("k", …)` / `t("k", …)`。"""
    used = set()
    for p in _src_files():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call) or not n.args:
                continue
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if fname not in ("Msg", "t"):
                continue
            a = n.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                used.add(a.value)
    return used


def _phases_used() -> set[str]:
    """纪事分类（`phase="外交"` / `"phase": "行动"`）—— 每一个都得有译文。"""
    out = set()
    for p in _src_files():
        s = p.read_text(encoding="utf-8")
        out |= set(re.findall(r'phase="([^"]+)"', s))
        out |= set(re.findall(r'"phase":\s*"([^"]+)"', s))
    return out


class TestCatalog(unittest.TestCase):
    def test_全部语言表都完全翻译(self):
        for lang in i18n.available():
            if lang != i18n.BASE_LANG:
                i18n.check(lang)        # 缺键/多键/槽位不一致会抛 LangError

    def test_英文表里不许有汉字(self):
        cat = i18n.catalog("en")
        bad = [k for k, v in cat["msg"].items() if CJK.search(v)]
        self.assertFalse(bad, f"这些英文模板里还有汉字（=没译完）：{bad}")
        badn = [k for k, v in cat["names"].items() if CJK.search(str(v))]
        self.assertFalse(badn, f"词库里这些取值还是汉字：{badn}")

    def test_游戏名词全有译文(self):
        names = i18n.catalog("en")["names"]
        need = (set(B.BUILDINGS) | set(B.TERRAINS) | set(B.RES_KEYS) | set(B.RESOURCE_MAX)
                | set(B.UNIT_TYPES) | _phases_used())
        missing = sorted(need - set(names))
        self.assertFalse(missing, f"英文词库缺这些游戏名词：{missing}")

    def test_键用得着也表里有(self):
        base = set(i18n.catalog(i18n.BASE_LANG)["msg"])
        used = _keys_used()
        self.assertFalse(used - base, f"源码引用了表里没有的键：{sorted(used - base)[:10]}")
        self.assertFalse(base - used, f"表里这些键没人用（死键，删掉或接上）：{sorted(base - used)}")


class TestMsg(unittest.TestCase):
    def test_嵌套往返(self):
        m = i18n.Msg("build.started", label=i18n.Msg("building.castle_level", lv=2),
                     tile="安镇", note="", disc=i18n.Msg("build.engineer_discount", pct=25),
                     cost=100, wood=5)
        back = i18n.Msg.from_json(json.loads(json.dumps(m.to_json(), ensure_ascii=False)))
        self.assertEqual(back.render(), m.render())
        self.assertEqual(back.render("en"), m.render("en"))
        self.assertNotIn("城堡", back.render("en"))

    def test_未知键当场报错(self):
        with self.assertRaises(i18n.LangError):
            i18n.Msg("nope.not_a_key").render()

    def test_连接符跟缺省语言走(self):
        """不传 lang 也必须认**当前缺省语言**：`lang=None` 不等于"中文"，
        否则英文局里列表会连出顿号。"""
        try:
            i18n.set_lang("en")
            self.assertEqual(i18n.render("{a}", {"a": ["x", "y"]}), "x, y")
        finally:
            i18n.set_lang(i18n.BASE_LANG)
        self.assertEqual(i18n.render("{a}", {"a": ["x", "y"]}), "x、y")

    def test_跟字符串比照旧(self):
        m = i18n.Msg("build.only_own_tile")
        self.assertEqual(m, "只能在自己的地块上建造")     # 存量断言与判断不必改
        self.assertNotEqual(m, "别的什么")

    def test_英文回传能译回规范名(self):
        """英文局里模型发回来的是 `Farm`/`Infantry`，落地前必须回到规范名。"""
        self.assertEqual(i18n.unlabel("Farm", "en", among=B.BUILDINGS), "农场")
        self.assertEqual(i18n.unlabel("Infantry", "en", among=B.UNIT_TYPES), "步")
        self.assertEqual(i18n.unlabel("农场", "en", among=B.BUILDINGS), "农场")   # 回了规范名
        self.assertEqual(i18n.unlabel("随便一句信里的话", "en", among=B.BUILDINGS),
                         "随便一句信里的话")                                       # 自由文本原样
        self.assertEqual(i18n.unlabel("Farm", "zh", among=B.BUILDINGS), "Farm")   # 中文局不反查

    def test_反查必须限定在同一个域(self):
        """「金」(地名词素) 与「黄金」(资源) 英文都叫 Gold —— 不限定域就会认错门。"""
        self.assertEqual(i18n.label("金", "en"), i18n.label("黄金", "en"), "前提：两个词的英文译文撞了")
        self.assertEqual(i18n.unlabel("Gold", "en", among=["黄金"]), "黄金")
        self.assertEqual(i18n.unlabel("Gold", "en", among=["金"]), "金")
        self.assertEqual(i18n.unlabel("Gold", "en", among=B.RES_KEYS), "黄金",
                         "资源域里没有「金」，不该被词素抢走")

    def test_同一域内译文不许撞名(self):
        """撞名会让该域的英文输入有歧义（模型无从知道你说的是哪个）—— 词库不许这样补。"""
        domains = {"建筑": list(B.BUILDINGS), "地形": list(B.TERRAINS),
                   "资源": list(dict.fromkeys(B.RES_KEYS + list(B.RESOURCE_MAX))),
                   "兵种": list(B.UNIT_TYPES)}
        for what, words in domains.items():
            seen: dict[str, list[str]] = {}
            for w in words:
                seen.setdefault(i18n.label(w, "en"), []).append(w)
            dup = {v: ks for v, ks in seen.items() if len(ks) > 1}
            self.assertFalse(dup, f"{what}域这些词的英文译文撞名：{dup}")

    def test_同一局里两国各看各的语言(self):
        """逐国 `lang` 是这轮的功能点：秦中文、楚英文，**同一条回执**两种渲染；
        而存档里存的只是键值（不带语言），所以分国家说话不会让存档分叉。"""
        i18n.set_lang("zh")
        w = mp.World(size=16, seed=5, nations=["秦", "楚"])
        w.begin_turn()
        same = []
        for n, lg in (("秦", "zh"), ("楚", "en")):
            x, y = sorted(w.own_tiles(n))[0]
            ok, msg = False, ""
            for bn in ("农场", "林场", "矿场", "补给厂", "装备厂", "木材能源厂"):
                # 采集类要看本地资源，工业类不挑地 —— 逐个试到成功，别赌开局格长什么样
                ok, msg = w.build(n, x, y, bn)
                if ok:
                    break
            self.assertTrue(ok, msg)
            shown = i18n.show(msg, lg)
            same.append(shown)
            w.action(n, "build", "building=农场", msg)
            entry = w.history[-1]["text"]
            self.assertEqual(entry["kw"]["result"]["$msg"], "build.started",
                             "纪事没按键值存，而是存了渲染结果")
            self.assertNotIn("lang", json.dumps(entry, ensure_ascii=False),
                             "语言标记被写进纪事了")
        self.assertIn("动工", same[0], "秦该看中文")
        self.assertIn("Started", same[1], "楚该看英文（同一条 build.started）")
        self.assertNotIn("动工", same[1], "楚的渲染里混进了中文")
        i18n.set_lang(i18n.BASE_LANG)

    def test_残缺语言表被拒(self):
        """缺键的语言文件必须**加载即报错**并列出缺哪些，而不是撑到运行时才发现。"""
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for f in (i18n.LANGS_DIR / "zh").glob("*.json"):     # 基准表照旧在场
                (d / "zh").mkdir(exist_ok=True)
                (d / "zh" / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            (d / "xx").mkdir(exist_ok=True)
            (d / "xx" / "00_core.json").write_text(json.dumps(
                {"_meta": {"lang": "xx"}, "names": {},
                 "msg": {"build.only_own_tile": "Only your own tiles"}}), encoding="utf-8")
            keep, keepc = i18n.LANGS_DIR, dict(i18n._CATALOGS)
            i18n.LANGS_DIR, i18n._CATALOGS = d, {}
            try:
                with self.assertRaises(i18n.LangError) as cm:
                    i18n.check("xx")
                self.assertIn("缺", str(cm.exception))
                self.assertIn("build.started", str(cm.exception))    # 报的就是漏掉的那些键
            finally:
                i18n.LANGS_DIR, i18n._CATALOGS = keep, keepc


class TestLanguageNotInSave(unittest.TestCase):
    """★ 设计底线：语言不进存档 —— 同一步棋，中/英缺省语言下存出来的字节必须相同。"""

    def _play_and_save(self, lang: str, path: Path) -> str:
        i18n.set_lang(lang)
        try:
            w = mp.World(size=16, seed=5, nations=["秦", "楚"])
            w.begin_turn()
            x, y = sorted(w.own_tiles("秦"))[0]
            ok, msg = w.build("秦", x, y, "农场")
            self.assertTrue(ok, msg)
            w.action("秦", "build", f"tile={x + 1} {y + 1} building=农场", msg, x=x, y=y)
            w.resolve_turn()
            w.save(path)
            return path.read_text(encoding="utf-8")
        finally:
            i18n.set_lang(i18n.BASE_LANG)

    def test_存档字节与语言无关(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = self._play_and_save("zh", Path(tmp) / "zh.json")
            b = self._play_and_save("en", Path(tmp) / "en.json")
            self.assertEqual(a, b, "存档里出现了语言痕迹 —— 纪事没按键值存")
            self.assertIn("$msg", a, "纪事没存成键值（还是渲染好的散文？）")

    def test_中文回执逐字不变(self):
        """键值化只换形状，不许顺手改文案：中文渲染必须与迁移前那行 f-string 一致。"""
        m = i18n.Msg("build.started", label="农场", tile="安镇",
                     note="，丘陵施工+25%", disc="，工程院-25%", cost=62, wood=5)
        self.assertEqual(m.render(),
                         "动工 农场（@安镇，丘陵施工+25%，工程院-25%，本回合在建、下回合生效），-62金 -5木")

    def test_纪事按查看者语言渲染(self):
        i18n.set_lang("zh")
        w = mp.World(size=16, seed=5, nations=["秦", "楚"])
        w.begin_turn()
        x, y = sorted(w.own_tiles("秦"))[0]
        ok, msg = w.build("秦", x, y, "农场")
        self.assertTrue(ok, msg)
        w.action("秦", "build", "building=农场", msg)
        zh = [h for h in w.history if h["phase"] == "行动"]
        en = [w.entry_text(h, "en") for h in zh]
        self.assertTrue(en, "没有行动纪事可渲染")
        for line in en:
            self.assertNotIn("动工", line, f"英文渲染里还有中文回执：{line}")
            self.assertIn("Farm", line, f"词库没生效：{line}")
        self.assertIn("动工", w.entry_text(zh[0]), "中文渲染反而出问题了")
        i18n.set_lang(i18n.BASE_LANG)

    def test_走log_tool的纪事字节也与语言无关(self):
        """★上一条只验了引擎自己 `log` 的路径；`mp_ai.log_tool` 会**自己拼 args** ——
        语言最容易从这条缝里偷渡进存档（发出时 label 过一次，中/英两局就分叉了）。"""
        def run(lang: str, path: Path) -> str:
            i18n.set_lang(lang)
            try:
                w = mp.World(size=16, seed=5, nations=["秦", "楚"])
                w.begin_turn()
                x, y = sorted(w.own_tiles("秦"))[0]
                ok, msg = w.build("秦", x, y, "农场")
                self.assertTrue(ok, msg)
                mp_ai.log_tool(w, "秦", "build",
                               {"tile": f"{x + 1} {y + 1}", "building": "农场"}, msg)
                w.resolve_turn()
                w.save(path)
                return path.read_text(encoding="utf-8")
            finally:
                i18n.set_lang(i18n.BASE_LANG)
        with tempfile.TemporaryDirectory() as tmp:
            a = run("zh", Path(tmp) / "a.json")
            b = run("en", Path(tmp) / "b.json")
        self.assertEqual(a, b, "log_tool 这条路把语言写进存档了")
        self.assertIn("$msg", a)

    def test_存档里的整篇文档也不带语言(self):
        """纪事之外还有三处「整篇存进存档的文档」：`econ_summary`（回合结算摘要）、
        `maps`（换图情报）、`econ_intel`（间谍报告）—— 面板只是把它们原样贴出来。
        它们曾经是渲染好的中文散文：英文局会连着中文一起贴，且**同一份存档**里
        旧文档永远译不过来。所以这三处必须是键值树。
        """
        def play(lang: str, path: Path) -> tuple[str, str]:
            i18n.set_lang(lang)
            try:
                w = mp.World(size=16, seed=5, nations=["秦", "楚"])
                w.begin_turn()
                x, y = sorted(w.own_tiles("秦"))[0]
                self.assertTrue(w.build("秦", x, y, "农场")[0])
                w.resolve_turn()                                  # → econ_summary
                w.begin_turn()
                w.add_res("秦", "黄金", 400)      # 够付换图与间谍的手续费
                self.assertTrue(w.share_map("秦", "楚")[0])         # → _map_snapshot
                self.assertTrue(w.spy("秦", "楚")[0])               # → _econ_snapshot
                for doc in (w.econ_summary["秦"], w.map_pending[-1]["text"],
                            w._econ_snapshot("楚")):
                    self.assertIsInstance(doc, i18n.Msg, f"{lang} 局里有文档没键值化")
                w.save(path)
                return (path.read_text(encoding="utf-8"),
                        i18n.show(w._econ_snapshot("楚"), lang))
            finally:
                i18n.set_lang(i18n.BASE_LANG)

        with tempfile.TemporaryDirectory() as tmp:
            a, doc_zh = play("zh", Path(tmp) / "a.json")
            b, doc_en = play("en", Path(tmp) / "b.json")
        self.assertEqual(a, b, "整篇文档把语言写进存档了")
        left = [t for t in re.findall(r"[一-鿿]+", doc_en) if t not in {"秦", "楚"}]
        self.assertFalse(left, f"英文的间谍报告里还有中文文案：{left} / {doc_en[:120]}")
        self.assertIn("电网", doc_zh, "中文侧反过来被啃掉了")
        self.assertIn("Castle L0", doc_en, "同一份存档没渲染出英文文档")
        self.assertEqual(len(doc_zh.splitlines()), len(doc_en.splitlines()),
                         "中英两版文档行数不同：结构随语言变了")

    def test_读档后仍按键值渲染(self):
        """存档往返之后 `text` 是普通 dict（不再是 Msg），渲染层必须照旧认得。"""
        i18n.set_lang("zh")
        w = mp.World(size=16, seed=5, nations=["秦", "楚"])
        w.begin_turn()
        x, y = sorted(w.own_tiles("秦"))[0]
        ok, msg = w.build("秦", x, y, "农场")
        self.assertTrue(ok, msg)
        w.action("秦", "build", "building=农场", msg)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.json"
            w.save(f)
            back = mp.World.load(f)
        acts = [h for h in back.history if h["phase"] == "行动"]
        self.assertTrue(any(isinstance(h["text"], dict) for h in acts),
                        "存档里的纪事没存成键值")
        line_en = back.entry_text(acts[0], "en")
        self.assertIn("Farm", line_en, f"读档后英文渲染失效：{line_en}")
        self.assertIn("动工", back.entry_text(acts[0], "zh"), f"读档后中文渲染变了：{line_en}")
        i18n.set_lang(i18n.BASE_LANG)


if __name__ == "__main__":
    unittest.main(verbosity=2)
