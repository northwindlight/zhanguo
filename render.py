# -*- coding: utf-8 -*-
"""把地图渲成 PNG —— 给多模态模型看的**玩家 UI**。

## 为什么要有它

默认的【国土/视野】面板是**每格一行的坐标列表**（`mp_ai._fmt_atlas`）：它是数据库导出，
不是地图——**几何形状（地图唯一不可替代的东西）在列表里没了**。实测（八国局、规则 AI 驱动，
国土长成斜条时）：

| 回合 | 视野格 | 国土 | 取景框 | 框里无效格 | atlas |
|---|---|---|---|---|---|
| 1 | 21 | 5 | 5×5 | 16% | 334 tok |
| 40 | 102 | 23 | 21×21 | **77%** | 1111 tok |
| 110 | 202 | 81 | 21×21 | 54% | **2056 tok** |

取景框 = 视野包围盒，领土长成条状时框被撑成大方块、**四分之三是 `?.`**（图像里"看不见"
本该只是没有像素）；而且它**线性增长**——越强越花钱读自己的地。ASCII 网格版（`panel=grid`）
同样浪费，还要两行列头 + 图例才能解码。

## 依赖策略：Pillow **可选**

没装 Pillow / 没字体 / 画的时候抛异常 ⇒ 一律 `None` + **大声说一次**，调用方退回文本面板。
**画图永远不许弄死一局。**（`available()` 只探不抛。）

## 一条实测出来的编码约束

8 国在地图上**任意两国都可能相邻**（等价于 choropleth 的 all-pairs 情形），配色验证器实测：
3 槽全 PASS、**4 槽就 FAIL**（黄↔橙 CVD ΔE 4.8 / 常视 10.6）。**颜色最多扛 3 个国家。**
⇒ **身份由「国别字母」承担，颜色只负责找得快**：格心画的是引擎既有的国别代码
（`mp_ai._nation_letters` 的 A/B/C…），**图文互相对得上，模型不用学新词汇**。
地形字母（`game.TERRAIN_CHARS` 的 P/F/H/M/D）同理钉在格角——所以这张图**是可解码的**，
不是"靠猜颜色"。

## 与文本的分工（别再重复）

**图给形状，文给精确。** 文本面板继续给逐格数字与清单；图负责"我的地是什么形状、前线在哪、
敌人在哪、哪块够得着"。两边都画全 = 最亏。
"""
from __future__ import annotations

import io

# ---------------------------------------------------------------------------
# 调色
# ---------------------------------------------------------------------------

#: 底色（深色作战图）：地形**低饱和**——它是静态层，不该跟国家色抢眼
TERRAIN_FILL = {
    "平原": (63, 70, 54),
    "森林": (44, 58, 46),
    "丘陵": (74, 68, 54),
    "山地": (69, 66, 62),
    "沙漠": (77, 68, 51),
}
#: 视野外：近黑（"看不见"在图上就是**没有像素**，不是 `?.`）
FOG_FILL = (20, 22, 26)
BG = (15, 17, 20)
GRID = (32, 35, 40)
INK = (232, 232, 228)          # 主文字
INK_DIM = (138, 140, 138)      # 次要文字（地形字母、刻度）
BARB = (150, 148, 142)         # 野人

#: 8 国国土色 —— 取自**终端 ANSI 16 色**那一系（用户 2026-10-12：「你就用终端支持的 16 个
#: 颜色都够了」）。四组"同色相 + 明暗两档"，是这套标准色盘本来的组织方式：
#:   黄 #cdcd00 / 亮黄 #ffff00　青 #00cdcd / 亮青 #00ffff
#:   品红 #cd00cd / 亮品红 #ff00ff　白 #e5e5e5 ／ 亮蓝 #5c5cff
#:
#: ★★ 关于"分不清"这件事，先把尺子说清楚（2026-10-12 实测）：
#:   · 我先前引的是配色验证器的 **all-pairs ΔE≥15**——那是给**统计图**定的门槛，前提是
#:     "颜色是唯一识别通道、且每项没有标签"。**终端 16 色自己也过不了**（最差 7.8，
#:     CVD 1.3），可它用了几十年没人说分不清 ⇒ **那把尺子在这里量错了东西**。
#:   · 这个场景该用的尺子是：**读图的那个模型认不认得出来**。那是可实测的，
#:     见 `~/` 那份 read-off 测试（给模型一张图 + 问"某坐标是谁的地"，对答案）。
#:   · 真正保证兵牌读得出的是它自己的框（黑描边 + 白内框），不是国土色。
NATION_COLORS = [
    (205, 205, 0),     # 秦 黄     #cdcd00
    (0, 205, 205),     # 魏 青     #00cdcd
    (205, 0, 205),     # 韩 品红   #cd00cd
    (229, 229, 229),   # 赵 白     #e5e5e5
    (255, 255, 0),     # 燕 亮黄   #ffff00
    (0, 255, 255),     # 齐 亮青   #00ffff
    (255, 0, 255),     # 楚 亮品红 #ff00ff
    (92, 92, 255),     # 周 亮蓝   #5c5cff
]

#: 兵牌的阵营色 —— **固定四色，不随国家变**（用户 2026-10-12：「不用形状区分，用颜色，
#: 友军蓝色，自己军队绿色，敌人红色，中立灰色」）。画在兵牌**外层那一圈**上。
SIDE_COLORS = {
    "self": (58, 196, 96),       # 自己：绿
    "ally": (58, 132, 232),      # 友军：蓝
    "enemy": (224, 62, 58),      # 敌人：红
    "neutral": (142, 144, 142),  # 中立：灰
}

#: 国土色的**中文色名**（写进 `legend_text` 给模型对号用，不画在图里）
COLOR_NAMES = ["黄", "青", "品红", "白", "亮黄", "亮青", "亮品红", "亮蓝"]

RES_COLORS = {
    "矿石": (150, 150, 158),
    "黄金": (222, 178, 62),
    "耕地": (126, 176, 84),
    "石油": (108, 106, 128),
    "木头": (140, 106, 66),
}

_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
]
_FONT_INDEX = 2   # NotoSansCJK-Regular.ttc 里 index 2 = SC


# ---------------------------------------------------------------------------
# 可用性探测（只探不抛）
# ---------------------------------------------------------------------------

_warned = False


def available() -> tuple[bool, str]:
    """渲染器能不能用。**绝不抛**——调用方按 (False, 原因) 退回文本面板。"""
    try:
        from PIL import Image, ImageDraw, ImageFont  # noqa: F401
    except Exception as e:                       # pragma: no cover - 环境相关
        return False, f"没装 Pillow（{type(e).__name__}）"
    for p in _FONT_CANDIDATES:
        try:
            from PIL import ImageFont
            ImageFont.truetype(p, 12, index=_FONT_INDEX)
            return True, ""
        except Exception:
            continue
    return False, "找不到中日韩字体（Noto Sans CJK）"


def _warn_once(emit, msg: str) -> None:
    """**大声说一次**就够——每回合刷屏等于没说。"""
    global _warned
    if _warned:
        return
    _warned = True
    if emit:
        emit(f"🖼 地图图片不可用：{msg}（本局退回文本面板）")


# ---------------------------------------------------------------------------
# 画
# ---------------------------------------------------------------------------

def render(world, name: str, *, max_px: int = 820, emit=None) -> bytes | None:
    """画全世界（含迷雾）。成功返回 PNG 字节，失败返回 None 并**大声说一次**。

    ★ 整张图 = **全世界 size×size 一格不落**：线上剧本 `map_size: 24`，34px/格 = 816px，
      一屏装得下（实测约 650 token）。不裁剪、不取景框 ⇒ 上一版 `?.` 占四分之三、
      取景框被条状国土撑爆那两个毛病**从根上不存在**。

    ★ **两个轴每一格都标数字**：坐标是工具调用的入参，读错一个数字 = 白烧一个不可逆的动作。
      所以不做"每 5 格一个刻度"那种要数的设计——每格都写出来。
    ★ `max_px` = 网格的目标边长（格宽按取景大小反推），用来把**每张图的像素量拉平**。
    """
    ok, why = available()
    if not ok:
        _warn_once(emit, why)
        return None
    try:
        return _render(world, name, max_px=max_px)
    except Exception as e:                       # 画图失败绝不许弄死一局
        _warn_once(emit, f"{type(e).__name__}: {str(e)[:160]}")
        return None


def _unit_glyph(d, cx, cy, w, h, kind, col):
    """北约式的兵种符号（画在兵牌内芯里，用**国家色**）。

    步=X（北约的步兵就是方框里一个叉）、骑=单斜杠（北约的骑兵/侦察）、民=实心点。
    """
    if kind == "骑":
        d.line([cx - w, cy + h, cx + w, cy - h], fill=col, width=3)
    elif kind == "民":
        r = max(3, min(w, h) - 1)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    else:
        d.line([cx - w, cy - h, cx + w, cy + h], fill=col, width=3)
        d.line([cx - w, cy + h, cx + w, cy - h], fill=col, width=3)


def _crop_box(vis, own, n, margin=1):
    """取景框 = 视野 ∪ 国土 的包围盒 + margin，夹在世界内。

    ★ 为什么裁：实测（24×24、第 60 回合）视野只占 6×6 ⇒ **整屏 75% 是空的黑**。
      文本面板那份 `_map_frame` 有同样的毛病，但它裁得保守（还要兼顾"行列对齐"），
      图里裁**没有副作用**——没有 `?.` 噪声，裁到哪儿就画到哪儿。
    ★ 裁到哪都行得通的前提是旁边有**小地图**：不然模型不知道自己在天下的哪一角。
    """
    if not vis and not own:
        return 0, n - 1, 0, n - 1
    pts = set(vis) | set(own)
    x0 = max(0, min(p[0] for p in pts) - margin)
    x1 = min(n - 1, max(p[0] for p in pts) + margin)
    y0 = max(0, min(p[1] for p in pts) - margin)
    y1 = min(n - 1, max(p[1] for p in pts) + margin)
    if (x1 - x0 + 1) * (y1 - y0 + 1) > 0.55 * n * n:      # 都快画满了就别裁
        return 0, n - 1, 0, n - 1
    return x0, x1, y0, y1


def legend_text(world, name) -> str:
    """国家↔颜色对照表 —— **放进文本，不要画进图里**。

    ★★ 2026-10-12 实测（同一张图、同一个模型，只换对照表的位置）：

        A 斜纹开 · 对照表**画在图里**  4/12
        B 斜纹关 · 对照表画在图里      4/12   ← 顺带排除"斜纹冲淡了颜色"这个猜想
        C 斜纹开 · 对照表**在文本里**  9/12   ← 2.25 倍

      ⇒ 模型读地图上的色块没问题，**它读不出画在图里那排小色卡**。
        国名↔颜色是张查找表，本来就该是文字（与"图给形状、文给精确"同一条口径）。

    另：颜色名用**中文色名**而不是 hex —— 实测模型对"亮青/品红"这类名字的指认比十六进制稳。
    """
    names = COLOR_NAMES
    parts = []
    for i, nm in enumerate(world.order):
        if nm not in world.nations:
            continue
        parts.append(f"{nm}={names[i % len(names)]}" + ("（你）" if nm == name else ""))
    return "国别颜色对照：" + "　".join(parts) + "。地形见格角字母(P平原 F森林 H丘陵 M山地 D沙漠)。"


def _legend_lines(world, name, vis, own) -> list:
    """图例。**词汇与文本面板同一套**（P/F/H/M/D、国别字母）——图文对读时不该出现两套说法。

    ★ 写得**短**：这段每回合都随图重发，长句是白付的 token。
    ★ 纯文本，**不要写 markdown**——这里是像素，`**` 会原样画出来。
    ★ 有一条是**行为指令**而不是说明（"要番号去按坐标查"）：这版图**故意不画军队身份**，
       不写清楚，模型会去格子上找根本不存在的番号。
    """
    return [
        "底=地形 P平原 F森林 H丘陵 M山地 D沙漠（格角小字）　右上小点=该格产资源　国土只见颜色(见下方色块)",
        "房子=市政厅(国祚，拔光某国全部即灭其国)　城垛楼=城堡(角上数字=等级)",
        "国土：整格铺满国色；有地标(厅/城)或有军队的=平涂，其余=压在国色上的暗斜纹(意为「就是块地」)",
        "军队(同阵营合并，一格最多四种)：牌外圈颜色定阵营 —— 绿=你自己、蓝=友军(同盟)、红=敌人(交战方)、灰=中立；牌内符号=兵种(X步 /骑 · 民)，用国家色；牌右数字=几支，牌下血条=该阵营在这格的总血量；空心圆=野人",
        "★ 图上不写番号：要哪个军，拿坐标去 text 面板查（每格一行、带番号）",
        "白虚线框=本回合可及（够得着、可直接 atk 入驻）　深黑=视野外（看不到≠没有）",
    ]
def _hatch(d, box, color, step=15, width=5):
    """在矩形里画 45° 斜纹（非核心地块的纹理）。

    ★ 密度是这个函数的全部难点（2026-10-12 实测三轮）：
      · **交错 + step=6** 在 62px 的格子里是 20 条线 ⇒ 糊成一块实色，跟"核心实色"分不出来；
      · 改成单向 + step=13 之后能看出是纹理，但**线太细**（用户：「斜纹不能太细」）；
      · 定稿 **单向 + step=15 + 线宽 5**（约 4 条粗线）。★ 光有粗线还不够：线间留白占了大头，
        国色只覆盖格子一两成 ⇒ 反而读不出"这是谁的地"。所以**下面还垫一层淡国色底**
        （见调用点），条纹叠在上面说"这里就是块地"。

    为什么非核心地不涂实色：国土一大片，全实色就成一块色斑，**有内容的格子反而看不出来**。
    纹理把"就是块地"压下去，国色照样在（线就是国色），注意力让给核心格。
    """
    x0, y0, x1, y1 = box
    for c in range(x0 - y1, x1 - y0 + 1, step):
            pts = [(c + y0, y0), (c + y1, y1), (x0, x0 - c), (x1, x1 - c)]
            pts = [(a, b) for (a, b) in pts if x0 <= a <= x1 and y0 <= b <= y1]
            if len(pts) >= 2:
                pts.sort()
                d.line([pts[0], pts[-1]], fill=color, width=width)


def _house(d, cx, cy, s):
    """市政厅（国祚）：简笔画房子——照 🏠 提炼 **三角顶 + 门**（🏙️ 那堆竖条缩到 20px 会糊）。"""
    d.polygon([(cx - s, cy - s * 0.15), (cx + s, cy - s * 0.15), (cx, cy - s * 1.2)],
              fill=(246, 244, 236), outline=(18, 18, 18))
    d.rectangle([cx - s * 0.78, cy - s * 0.15, cx + s * 0.78, cy + s],
                fill=(246, 244, 236), outline=(18, 18, 18))
    d.rectangle([cx - s * 0.26, cy + s * 0.34, cx + s * 0.26, cy + s], fill=(24, 26, 30))


def _castle(d, cx, cy, s):
    """城堡：简笔画——照 🏰 提炼 **城垛 + 拱门**（这两个特征是这个 emoji 的辨识点）。"""
    d.rectangle([cx - s, cy - s * 0.3, cx + s, cy + s],
                fill=(178, 182, 188), outline=(18, 18, 18))
    tw = s * 0.34
    for k in (-1, 0, 1):
        tx = cx + k * s * 0.64
        d.rectangle([tx - tw, cy - s * 0.95, tx + tw, cy - s * 0.3],
                    fill=(178, 182, 188), outline=(18, 18, 18))
    d.pieslice([cx - s * 0.32, cy - s * 0.02, cx + s * 0.32, cy + s * 0.62], 180, 360,
               fill=(30, 32, 36))
    d.rectangle([cx - s * 0.32, cy + s * 0.3, cx + s * 0.32, cy + s], fill=(30, 32, 36))


def _render(world, name: str, *, max_px: int) -> bytes:
    from PIL import Image, ImageDraw, ImageFont
    import mp_ai

    n = world.size
    observer = name is None
    if observer:
        vis_all = {(x, y) for x in range(n) for y in range(n)}   # 世界地图：无迷雾
        own_all = set()
    else:
        vis_all = mp_ai._visible_cells(world, name)
        own_all = set(world.own_tiles(name))
    cx0, cx1, cy0, cy1 = (0, n - 1, 0, n - 1) if observer else _crop_box(vis_all, own_all, n)
    cw, ch = cx1 - cx0 + 1, cy1 - cy0 + 1
    # 格宽按取景大小反推，让**每张图的总像素大致恒定**：取景小就画大点（别浪费画布），
    # 取景大就画小点（别撑爆 token）。夹在 [24, 64]：再小读不出坐标，再大没有收益。
    cell = max(24, min(64, round(max_px / max(cw, ch))))
    pad_l, pad_t, pad_r = 46, 66, 14
    mini_w = 150 if (cw, ch) != (n, n) else 0      # 裁了才需要小地图
    legend_lines = _legend_lines(world, name, vis_all, own_all)
    grid_w, grid_h = cw * cell, ch * cell

    def font(sz, bold=False):
        p = _FONT_CANDIDATES[1 if bold else 0]
        try:
            return ImageFont.truetype(p, sz, index=_FONT_INDEX)
        except Exception:
            return ImageFont.truetype(_FONT_CANDIDATES[0], sz, index=_FONT_INDEX)
    f_tick = font(11)
    f_terr = font(10)
    f_own = font(15, bold=True)
    f_title = font(15, bold=True)
    f_leg = font(12)
    f_leg_s = font(11)
    f_badge = font(10, bold=True)

    # ★ 画布要**装得下最长的那行图例**：不然它被右边缘切掉半句，而模型看到的
    #   是一句残话（比不写更糟——它会把残话当完整说明）。宽度取「网格」与「图例」
    #   两者的大者。
    leg_w = max((int(f_leg_s.getlength(s)) for s in legend_lines), default=0)
    W = max(pad_l + grid_w + pad_r + mini_w, pad_l + leg_w + pad_r)
    H = pad_t + grid_h + 17 * len(legend_lines) + 62
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    vis, own = vis_all, own_all
    letters = mp_ai._nation_letters(world)
    colors = {nm: NATION_COLORS[i % len(NATION_COLORS)]
              for i, nm in enumerate(world.order)}
    reach = set() if observer else (mp_ai._reach_cells(world, name) & vis)

    army_at: dict = {}
    for a in world.armies:
        army_at.setdefault((a["x"], a["y"]), []).append(a)

    def px(x, y):
        return pad_l + (x - cx0) * cell, pad_t + (y - cy0) * cell

    xs = range(cx0, cx1 + 1)
    ys = range(cy0, cy1 + 1)

    f_tick = font(11)
    f_terr = font(10)
    f_own = font(14, bold=True)
    f_title = font(15, bold=True)
    f_leg = font(12)
    f_leg_s = font(11)
    f_badge = font(10, bold=True)
    f_cnt = font(11, bold=True)

    def px(x, y):
        return pad_l + (x - cx0) * cell, pad_t + (y - cy0) * cell

    xs = range(cx0, cx1 + 1)
    ys = range(cy0, cy1 + 1)

    def buildings(x, y):
        return (world.tiles.get((x, y)) or {}).get("buildings") or {}

    def landmark(x, y):
        """这格有没有**地标**：市政厅（国祚）或城堡（防御）。

        ★ 为什么不用"有没有建筑"（2026-10-12 试过两轮）：
          ① `bool(buildings(x,y))` 是错的——那个 dict **16 个键恒在**、没建的值是 0，
             `bool()` 恒为 True ⇒ 每格都算"核心"、斜纹一次没画过，症状还是"看起来正常"；
          ② 就算改对，"有任意建筑"也不合用——规则 AI 几乎**每格都盖农场林场**，
             于是满屏全是核心，纹理照样出不来。
          ③ 这版图本来就只画 3 类地标（厅 / 城 / 军队），**其余一律退成纹理**才立得住：
             "图上的实色块 = 有事的地方"。
        """
        b = buildings(x, y)
        return bool(b.get("市政厅") or b.get("城堡"))

    def is_core(x, y):
        return landmark(x, y) or bool(army_at.get((x, y)))

    # ---- 1. 地形底 / 迷雾 ----
    for y in ys:
        for x in xs:
            x0, y0 = px(x, y)
            box = [x0, y0, x0 + cell - 1, y0 + cell - 1]
            if (x, y) not in vis:
                d.rectangle(box, fill=FOG_FILL)
            else:
                d.rectangle(box, fill=TERRAIN_FILL.get(world.tile_terrain(x, y), (60, 60, 60)))

    # ---- 2. 归属：核心地实色、非核心地**交错线**（用户 2026-10-12 的口径） ----
    # 为什么非核心用交错线：国土一大片，全是实色就成了一块色斑，**有内容的格子反而看不出来**。
    # 交错线把"就是块地"压成纹理，国色照样在（线是国色），注意力让给核心格。
    for y in ys:
        for x in xs:
            o = world.owned_by(x, y)
            if not o or (x, y) not in vis:
                continue
            col = colors.get(o, (200, 200, 200))
            x0, y0 = px(x, y)
            base = TERRAIN_FILL.get(world.tile_terrain(x, y), (60, 60, 60))
            if is_core(x, y):
                d.rectangle([x0 + 1, y0 + 1, x0 + cell - 2, y0 + cell - 2], fill=col)
            else:
                # ★ 用户 2026-10-12：「国色要完整覆盖格子」——底就是**满格国色**，
                #   不留地形底色透出来；条纹改成**国色的暗版**，只负责说"这里就是块地"。
                #   于是：核心格 = 平涂满色，非核心格 = 满色 + 暗条纹。地形改由格角字母承载。
                d.rectangle([x0 + 1, y0 + 1, x0 + cell - 2, y0 + cell - 2], fill=col)
                _hatch(d, (x0 + 1, y0 + 1, x0 + cell - 2, y0 + cell - 2),
                       tuple(int(c * 0.45) for c in col))
            for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nx, ny = x + dx, y + dy
                if cx0 <= nx <= cx1 and cy0 <= ny <= cy1 and world.owned_by(nx, ny) == o:
                    continue
                if dx == 0:
                    yy = y0 if dy < 0 else y0 + cell - 2
                    d.rectangle([x0, yy, x0 + cell - 1, yy + 1], fill=col)
                else:
                    xx = x0 if dx < 0 else x0 + cell - 2
                    d.rectangle([xx, y0, xx + 1, y0 + cell - 1], fill=col)

    # ---- 3. 逐格内容：地形字母 / 资源点 / 归属字母 / 建筑简笔画 / 可及虚线 ----
    for y in ys:
        for x in xs:
            if (x, y) not in vis:
                continue
            x0, y0 = px(x, y)
            o = world.owned_by(x, y)
            t = world.tiles.get((x, y))
            # ★ 国色满格覆盖之后，原来看得清的浅灰小字在亮底上就糊了 ⇒ 按底色明暗选字色
            d.text((x0 + 3, y0 + cell - 12), world.ter_char(x, y).upper(), font=f_terr,
                   fill=(24, 24, 26) if o else INK_DIM)
            if t:
                for r in ("黄金", "石油", "耕地", "矿石", "木头"):
                    if (t.get("res") or {}).get(r, 0) > 0:
                        d.ellipse([x0 + cell - 9, y0 + 4, x0 + cell - 4, y0 + 9],
                                  fill=RES_COLORS[r])
                        break
            b = buildings(x, y)
            # 国土**只给颜色，不给字母**（用户 2026-10-12：「国土不用字母，纯颜色」）。
            # 国别靠 `colors` 那张表 + 图例的色块对照；识别代价见 NATION_COLORS 上那段。
            # 建筑：简笔画（照 🏰/🏠 的 emoji 提炼：**城垛+拱门** / **三角顶+门**；
            #   🏙️ 那堆竖条缩到 20px 会糊，所以"厅"按用户口径画成房子）
            if b.get("市政厅"):
                _house(d, x0 + cell // 2, y0 + cell // 2 + 2, s=max(9, cell // 5))
            elif b.get("城堡"):
                _castle(d, x0 + cell // 2, y0 + cell // 2 + 2, s=max(9, cell // 5))
                d.text((x0 + cell - 6, y0 + cell - 7), str(b["城堡"]), font=f_badge,
                       fill=INK, anchor="mm", stroke_width=2, stroke_fill=(12, 13, 15))
            if (x, y) in reach:
                for i in range(0, cell - 2, 6):
                    j = min(i + 3, cell - 1)
                    d.line([x0 + i, y0 + cell - 1, x0 + j, y0 + cell - 1], fill=(246, 246, 240))
                    d.line([x0 + i, y0, x0 + j, y0], fill=(246, 246, 240))
                    d.line([x0, y0 + i, x0, y0 + j], fill=(246, 246, 240))
                    d.line([x0 + cell - 1, y0 + i, x0 + cell - 1, y0 + j], fill=(246, 246, 240))

    # ---- 4. 军队：**一格最多三张兵牌 = 我 / 敌 / 中立** ----
    # ★ 用户 2026-10-12 的口径：「一个格子最多三个不同兵牌，相同战线合并，只有交战两方和
    #   中立方」+「兵牌带颜色，带这格子全部友军的 hp 和敌军的 hp」。
    #   于是分桶只有三个：**我方 / 敌方 / 中立**——不管一格里堆了几个军、几个国，
    #   画出来永远 ≤3 张牌，堆叠永远挤不爆。
    #   框形按北约约定（先于颜色）：矩形=我方、菱形=敌方、正方形=中立；野人是圆（不属阵营）。
    #   HP = 该阵营在这格的**总血量**（血条 + 数字），所以"这格打不打得动"一眼看得出。
    def bucket_of(nm):
        """四档阵营：自己 / 友军(同盟) / 敌人(交战方) / 中立。"""
        if nm == name:
            return "self"
        blk = world.bloc_of(name)
        if blk and nm in blk["members"]:
            return "ally"
        for w in world.wars:
            try:
                a, dd = world._war_sides(w)
            except Exception:
                continue
            if (name in a and nm in dd) or (name in dd and nm in a):
                return "enemy"
        return "neutral"

    def draw_counter(cx, cy, w, h, ring, ncol, glyph, hp, mx, cnt, tile_x1):
        """一张兵牌 —— **两层嵌套长方体**（用户 2026-10-12 给的结构，由外到内）：

            黑描边  →  阵营色一圈  →  白色内框  →  暗底 + 兵种符号(国家色)

        阵营由**那圈颜色**定：自己绿 / 友军蓝 / 敌人红 / 中立灰。
        牌上数字=这格该阵营有几支；牌下血条=该阵营在这格的**总血量**
        （用户：「带这格子全部友军的 hp，和敌军的 hp」）。

        ★ 这个"框"不是装饰：**符号能在任何底色上读出来，靠的就是这圈框**，
          而不是靠"国土色跟阵营色不一样"（后者几何上做不到，见 `NATION_COLORS` 上那段）。
        """
        hw, hh = w // 2, h // 2
        side = ring
        d.rectangle([cx - hw - 3, cy - hh - 3, cx + hw + 3, cy + hh + 3], fill=(8, 9, 11))
        d.rectangle([cx - hw - 2, cy - hh - 2, cx + hw + 2, cy + hh + 2], fill=side)
        d.rectangle([cx - hw + 1, cy - hh + 1, cx + hw - 1, cy + hh - 1], fill=(248, 248, 244))
        iw, ih = hw - 3, hh - 3
        d.rectangle([cx - iw, cy - ih, cx + iw, cy + ih], fill=(16, 17, 21))
        _unit_glyph(d, cx, cy, iw - 1, ih - 1, glyph, ncol)
        if cnt > 1:
            # ★ 数字必须**夹在格子里**：双牌布局时 cx 在 3/4 处，cx+hw+8 会溢到隔壁格，
            #   而"隔壁格的数字"是彻底的误导（2026-10-12 真踩到）。
            d.text((min(cx + hw + 8, tile_x1 - 6), cy), str(cnt), font=f_cnt,
                   fill=(255, 255, 255), anchor="rm" if cx + hw + 8 > tile_x1 - 6 else "lm",
                   stroke_width=3, stroke_fill=(8, 9, 11))
        bar_w = max(8, w + 4)
        bx = cx - bar_w // 2
        by = cy + hh + 5
        d.rectangle([bx, by, bx + bar_w, by + 3], fill=(26, 26, 28))
        if mx > 0:
            d.rectangle([bx, by, bx + int(bar_w * max(0.0, min(1.0, hp / mx))), by + 3],
                        fill=(126, 226, 126) if hp / mx > 0.5 else (238, 176, 62))
        bar_w = max(6, w - 4)
        bx = cx - bar_w // 2
        by = cy + hh + 2
        d.rectangle([bx, by, bx + bar_w, by + 3], fill=(26, 26, 28))
        if mx > 0:
            d.rectangle([bx, by, bx + int(bar_w * max(0.0, min(1.0, hp / mx))), by + 3],
                        fill=(120, 220, 120) if hp / mx > 0.5 else (235, 170, 60))

    for y in ys:
        for x in xs:
            here = army_at.get((x, y))
            if not here or (x, y) not in vis:
                continue
            x0, y0 = px(x, y)
            groups: dict = {}
            for a in here:
                if a["owner"] == "野人":
                    continue
                k = ("n:" + a["owner"]) if observer else bucket_of(a["owner"])
                groups.setdefault(k, []).append(a)
            if observer:                      # 观察者：按总血量取前 3 个国家
                seq = sorted(groups, key=lambda k: -sum(a.get("hp", 0) for a in groups[k]))[:3]
            else:
                seq = [k for k in ("self", "ally", "enemy", "neutral") if groups.get(k)]
            if not seq:                            # 只有野人：空心圆，不进阵营体系
                r = max(3, cell // 9)
                d.ellipse([x0 + cell // 2 - r, y0 + cell // 2 - r,
                           x0 + cell // 2 + r, y0 + cell // 2 + r], outline=BARB, width=2)
                continue
            ncnt = len(seq)
            # ★ 别叫 cw/ch —— 上面那个 `cw` 是**网格宽**，这里一覆盖，标题的
            #   "全世界 vs 取景"判断就跟着错（2026-10-12 真踩到，症状只出现在标题文字上）。
            kcw = max(14, min(cell // 2 + 2, (cell - 8) // max(1, min(ncnt, 2)) - 4))
            kch = max(14, min(kcw + 4, cell // 2))
            for i, k in enumerate(seq):
                arr = groups[k]
                if ncnt == 1:
                    slots = [(cell // 2, cell // 2 - 2)]
                elif ncnt == 2:
                    slots = [(cell // 4, cell // 2 - 2), (3 * cell // 4, cell // 2 - 2)]
                elif ncnt == 3:
                    slots = [(cell // 4, cell // 4), (3 * cell // 4, cell // 4),
                             (cell // 2, 3 * cell // 4)]
                else:
                    slots = [(cell // 4, cell // 4), (3 * cell // 4, cell // 4),
                             (cell // 4, 3 * cell // 4 + 2), (3 * cell // 4, 3 * cell // 4 + 2)]
                ox, oy = slots[i]
                hp = sum(a.get("hp", 0) for a in arr)
                top = max(arr, key=lambda z: z.get("hp", 0))
                ring = (colors.get(top["owner"], (200, 200, 200)) if observer
                        else SIDE_COLORS.get(k, SIDE_COLORS["neutral"]))
                draw_counter(x0 + ox, y0 + oy, kcw, kch, ring,
                             colors.get(top["owner"], (200, 200, 200)),
                             top.get("type", "步"), hp, 100 * len(arr), len(arr),
                             x0 + cell - 2)


    # ---- 5. 坐标：**四边都标**（用户 2026-10-12：xy 坐标在上下） ----
    # 为什么四边都要：取景框是竖长条时，底下的格子离顶上的列号很远，读坐标要来回找。
    for x in xs:
        x0, _ = px(x, cy0)
        d.text((x0 + cell // 2, pad_t - 9), str(x + 1), font=f_tick, fill=INK, anchor="ms")
        d.text((x0 + cell // 2, pad_t + grid_h + 9), str(x + 1), font=f_tick,
               fill=INK, anchor="mt")
    for y in ys:
        _, y0 = px(cx0, y)
        d.text((pad_l - 8, y0 + cell // 2), str(y + 1), font=f_tick, fill=INK, anchor="rm")
        d.text((pad_l + grid_w + 8, y0 + cell // 2), str(y + 1), font=f_tick,
               fill=INK, anchor="lm")
        d.line([pad_l, y0, pad_l + grid_w, y0], fill=GRID)
    for x in xs:
        x0, _ = px(x, cy0)
        d.line([x0, pad_t, x0, pad_t + grid_h], fill=GRID)

    # ---- 6. 标题 ----
    scope = f"全世界 {n}×{n}" if (cw, ch) == (n, n) else f"取景 x{cx0 + 1}-{cx1 + 1} y{cy0 + 1}-{cy1 + 1}"
    who = "观察者(无迷雾)" if observer else name
    d.text((pad_l, 14), f"{who} · 第 {world.turn} 回合 · {scope}（坐标 1-based）",
           font=f_title, fill=INK)

    # ---- 7. 小地图（裁过才画） ----
    if mini_w:
        mcell = max(2, (mini_w - 12) // n)
        mw = mh = mcell * n
        mx0, my0 = W - mini_w + 6, pad_t
        d.rectangle([mx0 - 3, my0 - 3, mx0 + mw + 2, my0 + mh + 2], fill=(10, 11, 13), outline=GRID)
        for y in range(n):
            for x in range(n):
                o = world.owned_by(x, y)
                if (x, y) not in vis_all:
                    col = (26, 28, 32)
                elif o:
                    col = colors.get(o, (200, 200, 200))
                else:
                    col = (58, 60, 56)
                d.rectangle([mx0 + x * mcell, my0 + y * mcell,
                             mx0 + x * mcell + mcell - 1, my0 + y * mcell + mcell - 1], fill=col)
        d.rectangle([mx0 + cx0 * mcell, my0 + cy0 * mcell,
                     mx0 + (cx1 + 1) * mcell - 1, my0 + (cy1 + 1) * mcell - 1],
                    outline=(250, 250, 245), width=2)
        d.text((mx0, my0 + mh + 8), "全世界/白框=上图取景", font=f_terr, fill=INK_DIM)

    # ---- 8. 图例 ----
    ly = pad_t + grid_h + 26
    # ★ 这里原本画一排"色卡=国名"，2026-10-12 实测**模型读不出来**（4/12 vs 文本 9/12），
    #   改成一行指路：对照表走 `legend_text()`，和状态文本一起发。
    d.text((pad_l, ly), "国别=颜色对照见表外文本；本图只画形状与位置", font=f_leg, fill=INK)
    for i, line in enumerate(legend_lines):
        d.text((pad_l, ly + 22 + 17 * i), line, font=f_leg_s, fill=INK_DIM)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_world(world, *, max_px: int = 1500, emit=None) -> bytes | None:
    """**世界地图**（观察者视角）：不裁、无迷雾、全部军队。

    给看海/复盘用——玩家视角那张是带迷雾的局部图（`render`），这张是全局事实。
    `max_px` 默认比玩家图大（1500），因为世界地图本来就该看得全。
    """
    ok, why = available()
    if not ok:
        _warn_once(emit, why)
        return None
    try:
        return _render(world, None, max_px=max_px)
    except Exception as e:
        _warn_once(emit, f"{type(e).__name__}: {str(e)[:160]}")
        return None


def data_url(png: bytes, *, detail: str | None = None) -> dict:
    """PNG 字节 → OpenAI/DeepSeek 那套 content 块（`detail` 见 DeepSeek 文档：
    `low` 会把图缩到 512×512——实测同一张 1024² 图 652 token → 184 token）。"""
    import base64
    iu: dict = {"url": "data:image/png;base64," + base64.b64encode(png).decode()}
    if detail:
        iu["detail"] = detail
    return {"type": "image_url", "image_url": iu}
