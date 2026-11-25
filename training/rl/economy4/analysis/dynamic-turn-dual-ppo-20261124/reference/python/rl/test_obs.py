"""观测编码的自检：形状、无 NaN、迷雾不泄漏、指针范围合法。`python3 -m rl.test_obs`"""
import numpy as np
import ruleai.v11plus.grouping as g
from rl.env import Env
from rl.obs import N_DIM, T_DIM, Observer, collate, visible_set
from rl.opponents import rule_turn, make_w1


def main():
    g.clear()
    e = Env(13, 2, max_turns=300, opponents={1: make_w1(60, 3)}).reset(7)
    ob = Observer(e.world, "国0")
    for _ in range(40):
        s = e.advance()
        if s is None:
            break
        e.end_turn(s)
    s = e.advance()
    assert s is not None, "对局已结束，测试夹具需调整"
    w = e.world
    cs = e.candidates(s)
    o = ob.encode(w, cs, 300)
    assert not any(np.isnan(v).any() for v in o.values() if v.dtype.kind == "f")
    size = w.size
    assert o["tiles"].shape == (size * size, T_DIM) and o["nations"].shape[1] == N_DIM
    p = o["cand_ptr"]
    assert p[:, 0].max() < size * size and p[:, 1].max() < len(o["armies"]) and p[:, 2].max() < len(o["nations"])
    vis = visible_set(w, "国0")
    hid = [q for q, t in w.tiles.items() if t["owner"] == "国1" and q not in vis]
    if hid:
        before = ob.encode(w, cs, 300)["tiles"].copy()
        w.tiles[hid[0]]["buildings"]["兵营"] = 3
        assert (before == ob.encode(w, cs, 300)["tiles"]).all(), "迷雾泄漏"
    b = collate([o, o])
    assert b["cand"].shape[0] == 2
    print("ok", {k: v.shape for k, v in o.items()})


if __name__ == "__main__":
    main()
