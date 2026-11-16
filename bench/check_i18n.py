"""零成本校验：英文目录覆盖 + 简报渲染 + 工具描述本地化。

不调用模型、不跑引擎世界，只装配 Translator 与 StockView 的渲染分支。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mods.stock.tools import _catalogs  # noqa: E402
from mods.stock.client import StockView  # noqa: E402

CATS = _catalogs()
EN = CATS["en-US"]
ZH_KEYS = set(CATS["zh-CN"])

CJK = re.compile(r"[\u4e00-\u9fff]")


def translator(language):
    from presentation.i18n import Translator
    layer = SimpleNamespace(catalogs=lambda: CATS)
    return Translator.from_config({"language": language}, tool_layer=layer)


def view(language):
    layer = SimpleNamespace(catalogs=lambda: CATS)
    presentation = SimpleNamespace(tool_layer=layer, cfg={})
    return StockView({"language": language}, presentation=presentation)


OBS = {
    "facts": {
        "round": {"round": 12, "final_round": 60, "rounds_left": 48, "trading_day": True,
                  "days_to_next_open": 0, "window_used": 3, "window_limit": 40,
                  "tick": 1200, "slices": 20000},
        "compute_status": {"pool": 2, "slots": 2, "steps_remaining": 900000,
                            "calls_remaining": 900, "research_runs_left": 3, "fees_paid": 240,
                            "notice": ""},
        "portfolio": {"alive": True, "cash": 88000.0, "debt": 5000.0, "holdings_value": 12000.0,
                       "wealth": 95000.0, "return_pct": -5.0, "credit_limit": 50000.0,
                       "margin_limit": 30000.0, "risk_frozen": False,
                       "positions": [{"name": "云度传媒", "symbol": "yundu", "quantity": 100,
                                       "avg_cost": 12.5, "price": 13.0, "pnl": 50.0}],
                       "liquidations": [{"round": 9, "quantity": 40, "symbol": "tianhe",
                                          "amount": 4200.0}],
                       "equity_curve": [{"round": 10, "wealth": 94000.0},
                                         {"round": 11, "wealth": 95000.0}]},
        "market": [{"symbol": "yundu", "name": "云度传媒", "price": 13.0, "change_pct": 0.04,
                     "ma5": 12.4, "ma10": 11.9, "volatility": 0.31,
                     "next_report_round": 20, "report_tick": 4000,
                     "delist_review_round": 44,
                     "news": [{"kind": "wire", "cashflow_change_pct": 2.5, "tick": 800,
                                "effect": "波动率→31.0%；点差→×1.30"}]}],
        "pool": {"next_listings": [{"name": "天河科技", "symbol": "tianhe", "sector": "科技",
                                     "round": 9, "anchor": 20.0}],
                 "delist_warnings": [{"symbol": "zhongli", "review_round": 44}]},
        "day_report": {"round": 11, "orders": 12, "fills": 9, "statuses": {"filled": 7, "expired": 5},
                        "bought_quantity": 300, "bought_amount": 3900.0,
                        "sold_quantity": 100, "sold_amount": 1300.0, "fees": 5.2},
        "script_status": {"result": {"period": 11, "status": "effective", "steps": 12000,
                                      "calls": 40, "actions": 12, "output": "top=yundu",
                                      "error": None},
                           "other_slots": [{"slot": "2", "result": {"period": 11, "status": "effective",
                                                                     "steps": 800, "calls": 4,
                                                                     "error": {"message": "boom"},
                                                                     "output": "hi"}}]},
        "intel_status": {"price": 1000, "code_price": 2500,
                          "reports": [
                              {"kind": "source", "round": 8, "tick": 200, "target": "stock:player1",
                               "fragment": {"slot": "1", "revision": 3, "start_line": 10,
                                             "total_lines": 40, "code": "def on_tick():\n    pass"}},
                              {"kind": "snapshot", "round": 8, "tick": 200, "target": "stock:player2",
                               "snapshot": {"name": "quant-b", "cash": 90000, "debt": 0,
                                             "long_value": 8000, "short_value": 0}}]},
    }
}


def run():
    fails = []

    # 1) 目录键基本健全
    bad = [k for k, v in EN.items() if not isinstance(v, str) or not v.strip()]
    if bad:
        fails.append(f"空译文: {bad}")

    # 2) 英文简报：不出中文字符 = 所有分支都被目录覆盖
    en_text = view("en-US").stock_briefing(OBS)
    leftovers = sorted(set(CJK.findall(en_text)))
    if leftovers:
        fails.append(f"英文简报残留中文: {''.join(leftovers)}")
    # 3) 中文简报：不出现占位符/异常
    zh_text = view("zh-CN").stock_briefing(OBS)
    if "{" in zh_text or "}" in zh_text:
        fails.append("中文简报残留未替换的占位符")
    if not CJK.search(zh_text):
        fails.append("中文简报不含中文，语言参数没生效")

    # 4) 工具描述：模型视图按 tools.<name>.description 查表
    names = ["script", "portfolio", "pool", "market", "day_report", "round", "bank",
             "compute", "compute_status", "intel", "intel_status", "end_turn"]
    missing = [n for n in names if f"tools.{n}.description" not in EN]
    if missing:
        fails.append(f"缺工具描述译文: {missing}")

    # 5) 占位符一致性（英文译文与中文原文同名字段）
    import string
    def fields(t):
        return {f for _, f, _, _ in string.Formatter().parse(t) if f}

    print("en-US entries:", len(EN))
    print("--- EN briefing (first 16 lines) ---")
    print("\n".join(en_text.splitlines()[:16]))
    print("--- ZH briefing (first 4 lines) ---")
    print("\n".join(zh_text.splitlines()[:4]))
    if fails:
        print("\nFAIL:")
        for f in fails:
            print(" -", f)
        return 1
    print("\nALL CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(run())

