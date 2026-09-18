# -*- coding: utf-8 -*-
"""i18n：把所有给人/给模型看的文本变成**键 + 参数**，语言只是渲染时的一次查表。

## 为什么要绕这一圈（2026-09-18 定）

以前 `world.log(f"动工 {bn}（@{t['name']}）…")` 直接把**渲染好的中文散文**塞进
`history`，而 `history` 在 `SAVE_KEYS` 里 —— 语言于是被焊进了存档。想加双语只有两条路：
发出时就译（同一种子的中/英两局存档文本不同，中途换语言还会中英混排，跟当年
"已占格=旧算法"的混图旧档是同一类病），或者**存键值、读取时渲染**。这里走后者。

三条口径：

1. **引擎与存档只存规范名（中文）**：`BUILDINGS` 的键、地块 `owner`、地形名、兵种名
   全是**字典键与状态**，不是文案 —— 译它们等于改状态格式。翻译只发生在渲染那一刻。
2. **`Msg` 是值类型，不是字符串**：`str(msg)` 按**当前缺省语言**渲染（⇒ 存量调用点与
   测试基本不用动），`msg.render("en")` 按指定语言渲染。引擎方法该 `return` 文案的地方
   一律 `return Msg(...)`，由**边界**（工具回执 / 面板 / 纪事 / journal）决定用谁的语言。
3. **模板必须完全翻译**：语言文件缺一个 `msg` 键就直接报错并列出缺哪些，
   **不静默退回中文** —— 静默退回会让"英文局"永远是半英文，且没人知道漏在哪。
   专有名词词库（`names`）相反：**允许只补一部分**，没补的原样输出中文
   （国名/地名是专有名词，玩家自己按口味补）。

## 语言文件

一种语言 = `langs/` 下的**一个目录**，目录里按域分文件（`00_core.json`、`10_engine.json`
`20_panel.json`…），加载时合并 —— 域分文件是为了让不同子系统的文案各写各的、并行改
也不互相覆盖。玩家加一门语言 = 复制一个目录、逐文件翻译。

    langs/zh/   **基准**（所有键的出处；内部规范名就是中文，所以拿它当尺子）
    langs/en/   英文
    langs/xx/   玩家自增 —— 放进来就算注册，`"lang": "xx"` 直接用

每个文件的结构：

    {"_meta": {"lang": "en", "name": "English"},
     "names": {"农场": "Farm", "北": "North", "川": "River", ...},   # 词库，可只补一部分
     "msg":   {"build.started": "…{building} @{tile}…", ...}}        # 模板，必须全

同一语言内**一个键只许出现在一个文件里**（两处取值不同 = 加载即报错）。
模板用 `str.format` 的槽位（`{building}`、`{n}`）；**槽位名两边必须一致**，
加载时逐键比对，不一致直接报错（拼错一个槽位会在游戏里才炸，太难查）。
槽位值可以是数字、`Msg`（递归按目标语言渲染）、或**内部规范名**——后者由渲染器
过一遍 `names` 词库，所以调用点只管传 `"农场"`，不用自己想起英文名。

## 用法

    import i18n
    i18n.set_lang("en")                 # mp_run 读到 config 的 "lang" 时调一次
    i18n.t("mail.subject", n=3)         # 直接要一句
    msg = i18n.Msg("build.started", tile="安镇", building="农场", gold=50)
    str(msg)                            # 当前语言
    msg.render("en")                    # 指定语言
    msg.to_json()  /  i18n.Msg.from_json(d)   # 进存档 / 读档

## 键值化的约定（改代码时照这五条，别自创第六种）

1. **键名 `<域>.<事实>`**，小写点分：`build.started`、`war.declare`、`err.no_gold`。
   域 = 那个事实归属的子系统，**不是**它被谁读到（同一句战报会被纪事、面板、
   模型上下文三处读到，只有一个键）。
2. **参数传规范名，不传译文**：`building="农场"`，渲染器过词库出 `Farm`；
   在调用点先 `label()` 会把英文写进存档，语言就又焊住了。
3. **要嵌一句现成的话，就嵌 `Msg` 本身**：`Msg("build.failed", why=Msg("err.no_gold", need=60))`
   —— 递归渲染才带得动语言；把已渲染的字符串塞进参数 = 又焊回缺省语言。
4. **引擎方法的回执返回 `Msg`**，`world.log()` 原样返回给它；`str()` 走缺省语言，
   所以存量调用点与断言中文的测试不受影响。逐国语言的差别只在**边界**生效
   （工具回执、纪事渲染），不在引擎内部拼接。
5. **一句一个键，不拼字符串**：`f"{a}，{b}"` 这种在英文里语序未必对，必须整句进模板；
   列表连接由渲染器按语言做（中文顿号、英文逗号），代码里不许再写 `"、".join(...)`。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LANGS_DIR = ROOT / "langs"
BASE_LANG = "zh"          # 基准语言：键的出处，也是内部规范名的语言

_PLACEHOLDER = re.compile(r"\{([A-Za-z_]\w*)")


class LangError(ValueError):
    """语言表的问题：缺键、槽位对不上、文件读不出。启动时就报，不留到游戏里。"""


_CATALOGS: dict[str, dict] = {}
_LANG = BASE_LANG


def available() -> list[str]:
    """`langs/` 里全部可用的语言码（每个 = 一个目录，里面有 `*.json`）。"""
    if not LANGS_DIR.is_dir():
        return [BASE_LANG]
    return sorted(p.name for p in LANGS_DIR.iterdir()
                  if p.is_dir() and any(p.glob("*.json")))


def _merge_into(dst: dict, src: dict, what: str, where: str, lang: str) -> None:
    """把一个文件并进语言表：同一个键只许出现一次（两处取值不同 = 立刻报错）。"""
    for k, v in src.items():
        if k in dst:
            if dst[k] != v:
                raise LangError(f"语言 {lang} 的键 {k!r}（{what}）出现两次且取值不同，"
                                f"本次来自 {where} —— 一个键只许住在一个文件里")
            continue
        dst[k] = v


def _load(lang: str) -> dict:
    d = LANGS_DIR / lang
    files = sorted(d.glob("*.json")) if d.is_dir() else []
    if not files:
        raise LangError(f"没有语言目录 {d.name}/（可用：{'/'.join(available())}）")
    msg: dict[str, str] = {}
    names: dict[str, str] = {}
    meta: dict[str, str] = {}
    for p in files:
        try:
            cat = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise LangError(f"{p} 不是合法 JSON：{e}") from e
        if not isinstance(cat.get("msg"), dict):
            raise LangError(f"{p} 缺 `msg` 段（结构是 {{_meta, names, msg}}）")
        _merge_into(msg, cat["msg"], "模板", p.name, lang)
        _merge_into(names, cat.get("names") or {}, "词库", p.name, lang)
        meta.update(cat.get("_meta") or {})
    return {"_meta": meta, "names": names, "msg": msg}


def catalog(lang: str) -> dict:
    if lang not in _CATALOGS:
        _CATALOGS[lang] = _load(lang)
    return _CATALOGS[lang]


def _check_against_base(lang: str) -> None:
    """非基准语言必须**逐键齐全、槽位一致**。缺就列出来，一次报完。"""
    base, cat = catalog(BASE_LANG), catalog(lang)
    missing = sorted(set(base["msg"]) - set(cat["msg"]))
    extra = sorted(set(cat["msg"]) - set(base["msg"]))
    bad = [(k, sorted(_ph(base["msg"][k]) ^ _ph(cat["msg"][k])))
           for k in cat["msg"] if k in base["msg"] and _ph(base["msg"][k]) != _ph(cat["msg"][k])]
    if not (missing or extra or bad):
        return
    say = []
    if missing:
        # 一次报完：这张清单就是待办，截断=把要补的键藏起来（表越大藏得越多）
        say.append(f"缺 {len(missing)} 个键（必须完全翻译，不退回中文）：" + ", ".join(missing))
    if extra:
        say.append(f"多出 {len(extra)} 个基准表没有的键：" + ", ".join(extra))
    for k, ph in bad:
        say.append(f"槽位不一致 {k}：{'/'.join(ph)}")
    raise LangError(f"语言 {lang} 与基准 {BASE_LANG} 对不上 —— " + "；".join(say))


def _ph(tpl: str) -> set[str]:
    return set(_PLACEHOLDER.findall(tpl or ""))


def set_lang(lang: str | None) -> str:
    """设缺省语言（config 顶层的 `"lang"`）。加载即校验完整性，**当场报错**。"""
    global _LANG
    lg = str(lang or BASE_LANG).strip() or BASE_LANG
    cat = catalog(lg)
    if lg != BASE_LANG:
        _check_against_base(lg)
    _LANG = lg
    return lg


def check(lang: str) -> str:
    """只校验不改缺省：开局前把各国条目里的 `lang` 都验一遍（同 `rule_ai` 的做法）。"""
    lg = str(lang or BASE_LANG).strip() or BASE_LANG
    catalog(lg)
    if lg != BASE_LANG:
        _check_against_base(lg)
    return lg


def get_lang() -> str:
    return _LANG


def label(value: str, lang: str | None = None) -> str:
    """内部规范名 → 显示名。`lang=None` = 用当前缺省语言；词库没收录的词**原样返回**
    （专有名词允许不译，玩家自补）。

    地名这类**语素拼出来的词**走第二条路：整串查不到、但**每个字都在词库里**，就逐字拼
    （`启塔` → `First` + `Pagoda`）。长度上限 4 是防误伤：句子级的中文串不可能逐字命中，
    而 `roll_tile_name` 生成的名字最长就是「前缀+后缀(+序号)」。
    """
    lg = lang or _LANG
    if lg == BASE_LANG:
        return value
    names = catalog(lg)["names"]
    if value in names:
        return names[value] or value
    if 1 < len(value) <= 4 and all(ch in names for ch in value):
        return "".join(names[ch] for ch in value)
    return value


def unlabel(value: str, lang: str | None = None, among=None) -> str:
    """显示名 → 内部规范名（模型在英文局里会回传 `Farm`/`Infantry`，落地前得译回来）。

    ★`among`：**必须把候选限定在同一个域里**（传那一份键集合，如 `BUILDINGS` / `GOODS` /
      `UNIT_TYPES`）。全词库平坦反查会认错：地名词素「金」与资源「黄金」英文都叫 `Gold`，
      不加限定 `buy good=Gold` 就会被映射成不存在的「金」。调用点知道自己这个参数是什么域，
      所以这个限定不花钱。

    实现就是"把每个候选正着译一遍再比"（`label(k) == value`）—— 这样整串命中与语素拼出来的
    地名（`启塔`→`FirstPagoda`）两条路都反查得回来，不必维护两份表。

    认不出来就**原样返回**：多半它本来就是规范名，或者是自由文本（信件正文、国策句子）。
    """
    lg = lang or _LANG
    if lg == BASE_LANG or not isinstance(value, str) or not value:
        return value
    for k in (among or ()):
        if k == value:
            return value          # 模型直接回了规范名
        if label(k, lg) == value:
            return k
    return value


def render(tpl: str, kw: dict, lang: str | None = None) -> str:
    """把模板套上参数；槽位值递归渲染，并且**内部规范名自动过词库**。"""
    lg = lang or _LANG        # ★不传 = 用当前缺省语言；连接符与词库都必须跟着它，
                              #   否则英文局里列表会连出顿号（`lang=None` 被当成中文）。

    def val(v):
        if isinstance(v, Msg):
            return v.render(lg)
        if isinstance(v, (list, tuple)):
            # 列表里的裸值（数字等）也得上道：模板槽位可以传 [100, 200] 这种造价表。
            return ("、" if lg == BASE_LANG else ", ").join(str(val(x)) for x in v)
        if isinstance(v, dict):
            return ("，" if lg == BASE_LANG else ", ").join(f"{val(k)}={val(x)}" for k, x in v.items())
        if isinstance(v, str):
            return label(v, lg)
        return v

    return tpl.format_map({k: val(v) for k, v in kw.items()})


class Msg:
    """一条**未渲染**的文案：`(模板键, 参数)`。存进存档的就是这个形状。"""

    __slots__ = ("key", "kw")

    def __init__(self, key: str, **kw):
        self.key = key
        self.kw = kw

    def tpl(self, lang: str | None = None) -> str:
        cat = catalog(lang or _LANG)
        try:
            return cat["msg"][self.key]
        except KeyError:
            raise LangError(f"语言表 {lang or _LANG} 里没有键 {self.key!r}"
                            f"（基准表共 {len(catalog(BASE_LANG)['msg'])} 个键）") from None

    def render(self, lang: str | None = None) -> str:
        return render(self.tpl(lang), self.kw, lang)

    def to_json(self) -> dict:
        """进存档的形状：参数里的 `Msg` 递归成同样的形状。"""
        def enc(v):
            if isinstance(v, Msg):
                return {"$msg": v.key, "kw": {k: enc(x) for k, x in v.kw.items()}}
            if isinstance(v, dict):
                return {k: enc(x) for k, x in v.items()}
            if isinstance(v, (list, tuple)):
                return [enc(x) for x in v]
            return v

        return {"$msg": self.key, "kw": {k: enc(v) for k, v in self.kw.items()}}

    @staticmethod
    def from_json(d) -> "Msg | object":
        """读档：不是 `Msg` 形状的值原样返回（数字/字符串照旧）。"""
        def dec(v):
            if isinstance(v, dict) and "$msg" in v:
                return Msg(v["$msg"], **{k: dec(x) for k, x in (v.get("kw") or {}).items()})
            if isinstance(v, dict):
                return {k: dec(x) for k, x in v.items()}
            if isinstance(v, list):
                return [dec(x) for x in v]
            return v

        return dec(d)

    def __str__(self) -> str:
        return self.render()

    def __repr__(self) -> str:
        return f"Msg({self.key!r}, {self.kw!r})"

    def __eq__(self, other) -> bool:
        if isinstance(other, Msg):
            return (self.key, self.kw) == (other.key, other.kw)
        if isinstance(other, str):
            # 跟渲染结果比：存量那些 `回执 == "✅ …"` 的断言与判断不必跟着改
            return self.render() == other
        return NotImplemented

    def __contains__(self, sub: str) -> bool:
        """`"上限" in 回执` 照旧成立：断言与判断里把 Msg 当它渲染出的那句话用。"""
        return sub in self.render()

    def __hash__(self) -> int:
        return hash((self.key, tuple(sorted((k, repr(v)) for k, v in self.kw.items()))))


Text = Msg | str        # 「一段文案」：已键值化的 Msg，或还没迁完的裸原文


def t(key: str, lang: str | None = None, **kw) -> str:
    """要一句话：`t("mail.subject", n=3)`。缺键直接报错，不返回键名糊弄过去。"""
    return Msg(key, **kw).render(lang)


def enc(value):
    """进存档的形状：`Msg` → 键值 dict，其余（数字/字符串/旧条目）原样。

    容器**递归下去**：存档里有些字段是「国名 → 一段文案」的表（`econ_summary`、
    `maps`、`econ_intel`），它们的值现在也是 `Msg`，不递归就会把不可序列化的对象
    写进 JSON。反过来读档用 `Msg.from_json`（它本来就递归）。
    """
    if isinstance(value, Msg):
        return value.to_json()
    if isinstance(value, dict):
        return value if "$msg" in value else {k: enc(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [enc(v) for v in value]
    return value


def show(value, lang: str | None = None) -> str:
    """渲染一个「可能是 Msg、可能是存档里的键值 dict、也可能就是句原文」的值。

    存量调用点里还有一批裸中文字符串（尚未键值化），它们到这里原样通过 ——
    这正是"半英文"会在 EN 冒烟局里被看出来的地方，别拿它当兜底。
    """
    if isinstance(value, Msg):
        return value.render(lang)
    if isinstance(value, dict) and "$msg" in value:
        return Msg.from_json(value).render(lang)
    return str(value)


# 缺省加载基准表：让 `import i18n` 之后不 set_lang 也能用（测试与离线脚本都靠它）
set_lang(BASE_LANG)
