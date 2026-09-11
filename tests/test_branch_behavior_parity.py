# -*- coding: utf-8 -*-
"""分支行为对等：`feat/rl` 与 `main` 在**不打外交**时，引擎行为必须逐字段相同。

跑法：
    python3 -m unittest tests.test_branch_behavior_parity -v
    python3 tests/test_branch_behavior_parity.py          # 独立报告模式（打印分歧细节）

为什么需要它
------------
本分支对 main 做的**全部**改动应当只有「删外交」。但 `git diff main feat/rl -- mp.py`
是 1538 行，里面除了删外交，还搬了 `good_value/build_econ`。**"应该只有外交"
和"确实只有外交"是两件事**——这条测试把后者变成可执行的。

做法（三段式，动作流由规则 AI 生成，不手搓）
--------------------------------------------
1. **录**：在本分支上让 `expand_rule_v9` 打 TURNS 回合。包裹 `world.build/recruit/
   move/attack/retreat/buy/sell`，把**引擎调用本身**（方法名 + 参数 + 成败）录下来。
   录调用而不是录 `(tool, args)` —— 零翻译，回放必然忠实。
2. **放**：把同一段调用流原样回放到两个引擎上（main 的 `game.py`+`mp.py` 从 git
   抽到临时目录，不 checkout、不动工作区）。
3. **比**：逐回合 diff 世界状态的规范摘要 **+ 每条调用的成败**，报第一处分歧的字段路径。

这么做的理由：两边跑同一段动作流，所以**只要引擎一致，状态就必然一致**；一旦
不一致，就是引擎漂了，而不是"策略走出了不同的路"。

★ 驱动用 v9 而不是手搓配方：手搓配方第一版就是空跑（军队恒 0、一次仗没打，
  测试却绿着）。v9 会建产能、征兵、扩张、打野人，场景天然是满的。见 `TestCoverage`。
★ 不比对 RNG 状态：地形/资源/命名已改为 `(seed,x,y)` 的纯函数（两分支都改了），
  但历史让两边的 `self.rng` 流不必同源。**最终状态一致**才是要守的东西。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SEED = 0
TURNS = 60
MAP_SIZE = 16
MARK = "@@DIGEST@@"
WRAPPED = ("build", "recruit", "move", "attack", "retreat", "buy", "sell")


# ---------------------------------------------------------------------------
# 驱动脚本：**一份代码，两种模式**
#   gen    —— 在本分支上用 v9 打一局，录引擎调用流
#   replay —— 在任意代码树上把调用流原样回放，逐回合出摘要
# ---------------------------------------------------------------------------
DRIVER_SRC = r'''
import json, random, sys

mode, root, seed, turns, path, mark = sys.argv[1:7]
seed, turns = int(seed), int(turns)
sys.path.insert(0, root)
import mp

ME, SIZE = "秦", 16


def digest(w):
    """世界状态的规范摘要（只取两分支**都有**的字段）。"""
    tiles = []
    for (x, y) in sorted(w.tiles):
        t = w.tiles[(x, y)]
        tiles.append([x, y, t["owner"], t["terrain"],
                      {k: int(v) for k, v in sorted(t["resources"].items()) if v},
                      {k: int(v) for k, v in sorted(t["buildings"].items()) if v},
                      {k: int(v) for k, v in sorted((t.get("pending") or {}).items()) if v}])
    nations = {n: {"res": {k: int(v) for k, v in sorted(nd.res.items())},
                   "spend": {k: round(float(v), 4)
                             for k, v in sorted((w.spend.get(n) or {}).items())},
                   "grid_short": bool(w.grid_short.get(n)),
                   "energy": list(w.energy_report.get(n) or ())}
               for n, nd in sorted(w.nations.items())}
    arm = [[a["id"], a["owner"], a.get("type", ""), round(float(a["hp"]), 4),
            a["x"], a["y"], bool(a.get("engaged")), a.get("moved_turn")]
           for a in sorted(w.armies, key=lambda a: a["id"])]
    return {"turn": w.turn, "tiles": tiles, "nations": nations, "armies": arm,
            "prices": {k: round(float(v), 6) for k, v in sorted(w.prices.items())},
            "eq": {k: round(float(v), 6) for k, v in sorted(w.equilibrium.items())},
            "spend_total": round(float(w.spend_total(ME)), 6),
            "n_tiles": len(w.own_tiles(ME))}


if mode == "gen":
    from expand_rule_v9 import expand_rule_turn_v9
    import expand_rule_v9 as V9
    V9.HORIZON = turns + 20          # 视野口径 = 每局回合 + 20（与 bc.py/compare.py 一致）

    w = mp.World(size=SIZE, seed=seed, nations=[ME])
    rec = []                          # 每回合一个调用列表
    cur = []                          # 当前回合的调用列表（wrap 的闭包指向它）

    def wrap(meth):
        orig = getattr(w, meth)

        def f(*a, **k):
            r = orig(*a, **k)
            ok = r[0] if isinstance(r, tuple) else bool(r)
            # 只记 (方法, 除国名外的参数, 成败)——参数都是 int/str/list，可直接 JSON
            cur.append([meth, list(a[1:]), bool(ok)])
            return r

        setattr(w, meth, f)

    # ★ 只包一次。写在回合循环里会**每回合再套一层**：第 t 回合的调用会被
    #   t 层壳重复记账，流水直接失真（第一版就是，覆盖率全是假的）。
    for m in ("build", "recruit", "move", "attack", "retreat", "buy", "sell"):
        wrap(m)

    w.begin_turn()
    rng = random.Random(seed)
    for t in range(turns):
        cur = []
        rec.append(cur)
        expand_rule_turn_v9(w, ME, rng, max_actions=10 ** 9)
        w.resolve_turn()
        if t + 1 < turns:
            w.begin_turn()
    print(mark + json.dumps(rec, ensure_ascii=False))
    raise SystemExit(0)

# ---------------- replay ----------------
rec = json.load(open(path, encoding="utf-8"))
w = mp.World(size=SIZE, seed=seed, nations=[ME])
w.begin_turn()
out = []
for t in range(turns):
    led = []
    for meth, args, rec_ok in rec[t]:
        try:
            r = getattr(w, meth)(ME, *args)
            ok, msg = r if isinstance(r, tuple) else (bool(r), "")
        except Exception as e:                       # noqa: BLE001
            ok, msg = False, f"{type(e).__name__}: {e}"
        led.append([meth, args, bool(ok), bool(rec_ok), str(msg)[:80]])
    w.resolve_turn()
    out.append({"digest": digest(w), "log": led})
    if t + 1 < turns:
        w.begin_turn()
print(mark + json.dumps(out, ensure_ascii=False))
'''


def _show(path: str) -> str:
    return subprocess.run(["git", "show", f"main:{path}"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout


def _driver(tmp: Path) -> Path:
    p = tmp / "driver.py"
    p.write_text(DRIVER_SRC, encoding="utf-8")
    return p


def _spawn(driver: Path, args: list[str]) -> str:
    r = subprocess.run([sys.executable, str(driver), *args],
                       cwd=str(ROOT), capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"驱动崩了（{' '.join(args[:2])}）：\n"
                             f"{r.stdout[-1500:]}\n{r.stderr[-3000:]}")
    for line in r.stdout.splitlines():
        if line.startswith(MARK):
            return line[len(MARK):]
    raise AssertionError(f"没拿到输出（{' '.join(args[:2])}）：\n{r.stdout[-1500:]}")


def _materialise_main(tmp: Path) -> Path:
    """把 main 的 game.py + mp.py 抽到临时目录（不动工作区、不 checkout）。"""
    tree = tmp / "main_tree"
    tree.mkdir()
    for f in ("game.py", "mp.py"):
        try:
            (tree / f).write_text(_show(f), encoding="utf-8")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            raise unittest.SkipTest(f"取不到 main:{f}（{e}）") from e
    return tree


def first_diff(a, b, path: str = "") -> str | None:
    """第一处不同（字段路径 + 两边取值），报告用。"""
    if type(a) is not type(b):
        return f"{path}: 类型 {type(a).__name__} vs {type(b).__name__}"
    if isinstance(a, dict):
        if set(a) != set(b):
            return f"{path}: 键不同 {sorted(set(a) ^ set(b))[:8]}"
        for k in sorted(a, key=str):
            d = first_diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: 长度 {len(a)} vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {a!r} vs {b!r}"


def coverage(traj: list) -> dict:
    """这一局**实际打到了什么**。

    空跑的"通过"没有意义 —— 手搓配方那版就是：通过、但军队恒 0、一次仗没打。
    所以把覆盖率量出来，并当成测试的前置条件（见 `TestCoverage`）。
    """
    acc: dict[str, int] = {}
    for e in traj:
        for meth, _args, ok, _rec, _msg in e["log"]:
            if ok:
                acc[meth] = acc.get(meth, 0) + 1
    first, last = traj[0]["digest"], traj[-1]["digest"]
    return {"accepted": acc,
            "tiles_gained": last["n_tiles"] - first["n_tiles"],
            "peak_own_armies": max(len([a for a in e["digest"]["armies"] if a[1] != "野人"])
                                   for e in traj),
            "final_tiles": last["n_tiles"], "final_spend": last["spend_total"]}


# 场景必须至少打到这些，否则测试是空跑（数字取得很宽松，只为挡住"退化成空转"）
MIN_COVERAGE = {"peak_own_armies": 1, "attack": 1, "tiles_gained": 1, "build": 5}


def shortfalls(cov: dict) -> dict:
    """没打到的项。动作类的键在 `accepted` 里，量类的键在顶层。"""
    acc = cov.get("accepted", {})
    return {k: (acc.get(k, 0) if k in WRAPPED else cov.get(k, 0))
            for k, v in MIN_COVERAGE.items()
            if (acc.get(k, 0) if k in WRAPPED else cov.get(k, 0)) < v}


def compare() -> tuple[int, str, dict, dict]:
    """录一次、回放两次、比对。返回 (分歧回合, 说明, 覆盖率, 两侧摘要)。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        tree_main = _materialise_main(tmp)
        driver = _driver(tmp)
        raw = _spawn(driver, ["gen", str(ROOT), str(SEED), str(TURNS), "-", MARK])
        (tmp / "rec.json").write_text(raw, encoding="utf-8")
        ours = json.loads(_spawn(driver, ["replay", str(ROOT), str(SEED), str(TURNS),
                                          str(tmp / "rec.json"), MARK]))
        main = json.loads(_spawn(driver, ["replay", str(tree_main), str(SEED), str(TURNS),
                                          str(tmp / "rec.json"), MARK]))
    cov = coverage(ours)
    for t in range(min(len(main), len(ours))):
        # 先比每条调用的**成败**（引擎对同一动作的接受与否，比状态更早暴露分歧）
        d = first_diff([r[:4] for r in main[t]["log"]],
                       [r[:4] for r in ours[t]["log"]], f"T{t}.调用成败")
        if d:
            return t, d, cov, {"main": main, "ours": ours}
        d = first_diff(main[t]["digest"], ours[t]["digest"], f"T{t}.状态")
        if d:
            return t, d, cov, {"main": main, "ours": ours}
    return min(len(main), len(ours)), "", cov, {"main": main, "ours": ours}


class TestCoverage(unittest.TestCase):
    """先证「这一局真的打到了东西」，再谈对等。

    空跑通过 = 零信息。手搓配方那版就栽在这：军队恒 0、一次仗没打，测试却绿着。
    """

    def test_scenario_is_not_vacuous(self):
        _t, _why, cov, _d = compare()
        short = shortfalls(cov)
        self.assertFalse(short, f"场景退化成空跑（{short}，实测 {cov}）——"
                                f"换驱动或调 TURNS，别让它绿着但什么都没测")


class TestBranchBehaviorParity(unittest.TestCase):
    def test_engine_matches_main_without_diplomacy(self):
        t, why, cov, _d = compare()
        if why:
            self.fail(
                f"第 {t} 回合起与 main 分歧（seed={SEED}, {TURNS} 回合，动作流由 v9 生成、"
                f"全程不含外交）：\n    {why}\n"
                f"  本分支对 main 的改动**应当只有删外交**。这处分歧要么是意外的漂移，\n"
                f"  要么是有意改动但没记档——两种情况都该先弄清楚再往前走。\n"
                f"  覆盖率：{cov}\n"
                f"  细节：python3 tests/test_branch_behavior_parity.py"
            )


def main() -> None:
    print(f"分支行为对等检查：feat/rl vs main    seed={SEED}  {TURNS} 回合  "
          f"地图 {MAP_SIZE}×{MAP_SIZE}  单国「秦」  动作流 = v9（不含外交）\n")
    t, why, cov, _d = compare()
    a = cov["accepted"]
    print(f"覆盖率：领地 +{cov['tiles_gained']}（终局 {cov['final_tiles']} 格）  "
          f"峰值兵力 {cov['peak_own_armies']}  终局消费 {cov['final_spend']:,.0f}")
    print(f"        被接受的调用：" + "  ".join(f"{k}×{v}" for k, v in sorted(a.items())))
    short = shortfalls(cov)
    if short:
        print(f"\n⚠ 场景空跑（{short}）——这份「通过」没有意义，先修驱动\n")
        return
    if why:
        print(f"\n✗ 第 {t} 回合起分歧：\n    {why}\n")
    else:
        print(f"\n✓ {t} 个回合逐字段一致（每条调用的成败 + 地块/建筑/资源/军队/"
              f"市场价/消费）\n")


if __name__ == "__main__":
    main()
