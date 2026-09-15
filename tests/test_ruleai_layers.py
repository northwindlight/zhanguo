# -*- coding: utf-8 -*-
"""分层守卫：`ruleai/` 的经济层与军事层**不许互相 import**。

用户 2026-09-15：「v11 是个经济层和军事层分离的 ruleai，单独放在一个目录里面」。
分层的全部价值就在这条纪律上 —— 一旦经济层开始 import 编组、或者军事层开始管钱，
"换掉一层不用动另一层"就没了，而这正是分层的**唯一**理由。

为什么值得一条测试：这类越界是**渐进**的 —— 先在军事层里读一下国库（"就一行"），
再在编组里调一次 `recruit`（"顺手"），两次之后就分不开了。所以钉死依赖方向：

    入口 v11.py ──→ economy.py            （经济层：balance/game/mp + 公共账本）
                └─→ military.py ──→ grouping/combat/pathfind/targeting
                                     ↑ 军事层内部随便互相 import，但不许指向经济层

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PKG = ROOT / "ruleai" / "v11"
MILITARY = {"military", "grouping", "combat", "pathfind", "targeting"}
ENTRY = "entry.py"


def _imports_of(name: str) -> set[str]:
    """该模块 import 了包内的哪些兄弟模块（相对导入与 `ruleai.x` 都算）。"""
    tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:                                   # from .x import / from . import x
                if node.module:
                    out.add(node.module.split(".")[0])
                out |= {a.name for a in node.names}
            elif (node.module or "").startswith("ruleai."):
                out.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            out |= {a.name.split(".")[1] for a in node.names
                    if a.name.startswith("ruleai.")}
    return out


class TestLayersSeparated(unittest.TestCase):
    """经济层 ↔ 军事层：互不认识。"""

    def test_economy_does_not_know_military(self):
        got = _imports_of("economy.py") & MILITARY
        self.assertEqual(got, set(),
                         f"经济层 import 了军事层的 {sorted(got)} —— 两层分离是这份分层"
                         f"唯一的纪律（经济层不该知道编组怎么编）")

    def test_military_does_not_know_economy(self):
        got = _imports_of("military.py") & {"economy"}
        self.assertEqual(got, set(),
                         "军事层 import 了经济层 —— 产兵是经济层的事"
                         "（用户：产兵由经济引擎决定），军事层只管用已经存在的兵")

    def test_entry_only_glues_the_two_layers(self):
        """入口是**胶水**：只把两层接起来，不夹带自己的逻辑。

        判据：它 import 的必须是包内模块、balance、game 这几种，且**不 import
        军事层的部件**（要点部件说明它在自己做事，那就不叫胶水了）。
        """
        got = _imports_of(ENTRY)
        self.assertIn("economy", got)
        self.assertIn("military", got)
        self.assertEqual(got & {"grouping", "combat", "pathfind", "targeting"}, set(),
                         "入口自己去拿军事层部件了 —— 那说明它在做军事层的活")

    def test_ledger_is_the_only_shared_thing(self):
        """两层共享的只有 `world` 与**动作账本** —— 这是**故意**的。"""
        self.assertTrue((PKG / "ledger.py").exists(), "账本该是独立的一小块")
        src = (PKG / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("不许互相 import", src, "这条纪律得写在包说明里，别只活在测试里")


if __name__ == "__main__":
    unittest.main()