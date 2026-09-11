# -*- coding: utf-8 -*-
"""复现性测试：同 seed 在**不同 PYTHONHASHSEED 的子进程**里必须跑出逐字节相同的存档。

存在的意义：字符串 set 的迭代序受 hash 随机化影响——若任何"迭代序会影响状态"的
裸 set 遍历复活（战争闭包、弃城即陷都栽过），单进程测试永远抓不到（同进程 seed 相同），
只有跨进程对照才现形。这是全项目复现口径的看门狗。

跑法：python3 -m unittest discover -s tests -v
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = str(Path(__file__).resolve().parent.parent)

# 子进程剧本：专门放大"守侧闭包收编顺序"的外交局面——
# 燕有三个保障国齐/赵/楚，其中齐与赵、楚交战（赵楚互不相犯）。闭包若先迭代到齐：
# 赵、楚都因"已与守侧某员(齐)交战"被剪 → followers={齐}；若先迭代到赵或楚：
# 赵楚同收、齐被剪 → followers={赵,楚}。**结局取决于集合首元素**——2 元集翻不动
# 迭代序（表槽位巧合稳定），3 元集对 PYTHONHASHSEED 才真正敏感。
SCRIPT = r"""
import hashlib, random, sys
sys.path.insert(0, {root!r})
import mp, expand_rule_v9
w = mp.World(size=20, seed=42, nations=["秦", "燕", "齐", "赵", "楚"])
for g in ("齐", "赵", "楚"):
    assert w.declare_guarantee(g, "燕")[0]
assert w.declare_war("齐", "赵")[0]
assert w.declare_war("齐", "楚")[0]
ok, msg = w.declare_war("秦", "燕"); assert ok, msg
assert len(w.wars) == 3, w.wars          # 三场战争都在（秦→燕 的传导不报错）
_燕战 = next(x for x in w.wars if x["def"] == "燕")
assert sorted(_燕战["followers"]) == ["楚", "赵"], _燕战   # sorted 修复下：齐被剪，赵楚同收
rng = random.Random(5)
for _ in range(8):
    w.begin_turn()
    for n in w.alive():
        expand_rule_v9.expand_rule_turn_v9(w, n, rng, max_actions=12)
    w.resolve_turn()
w.save({path!r})
print(hashlib.sha256(open({path!r}, "rb").read()).hexdigest())
"""


def _run(hashseed: str) -> str:
    with tempfile.TemporaryDirectory() as d:
        p = str(Path(d) / "save.json")
        env = dict(os.environ, PYTHONHASHSEED=hashseed)
        r = subprocess.run([sys.executable, "-c", SCRIPT.format(root=ROOT, path=p)],
                           capture_output=True, text=True, env=env, cwd=ROOT, timeout=300)
        assert r.returncode == 0, f"子进程失败：{r.stderr[-500:]}"
        return r.stdout.strip().splitlines()[-1]


class TestHashSeedIndependence(unittest.TestCase):
    def test_save_digest_identical_across_hash_seeds(self):
        """两个不同 PYTHONHASHSEED 的子进程 → 同一份存档（哈希序不得影响任何状态）。"""
        h1 = _run("0")
        h2 = _run("123456789")
        self.assertEqual(h1, h2,
                         "同 seed 跨进程分叉：有迭代会影响状态的裸 set/dict 遍历复活了"
                         "（重点排查宣战闭包与弃城即陷）")


if __name__ == "__main__":
    unittest.main()
