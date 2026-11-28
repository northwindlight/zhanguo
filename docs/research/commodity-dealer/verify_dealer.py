"""Offline checks for action replay, booking limits and checkpoint qualification."""
import json
from pathlib import Path
import tempfile

import torch

from country_gate import save, sha, step
from dealer_policy import DealerPolicy, GLOBAL, GOODS, LOCAL, orders


def model_checks():
    torch.manual_seed(28)
    torch.set_num_threads(2)
    model = DealerPolicy()
    public = torch.randn(3, len(GLOBAL))
    stock = torch.randn(3, len(GOODS), len(LOCAL))
    cash, heads, value, flow, memory = model(public, stock)
    chosen, logp, entropy = model.decide(cash, heads)
    cash2, heads2, *_ = model(public, stock)
    _, replay, _ = model.decide(cash2, heads2, chosen)
    torch.testing.assert_close(replay,logp,atol=1e-6,rtol=0)
    assert model(public,stock,memory)[-1].shape == (3,128)
    ratio = torch.exp(replay-logp.detach())
    advantage = torch.tensor([1.,-.7,.3])
    loss = (-torch.minimum(ratio*advantage,ratio.clamp(.8,1.2)*advantage).mean()
            +value.square().mean()+flow.square().mean()-.001*entropy.mean())
    loss.backward()
    assert all(p.grad is not None and torch.isfinite(p.grad).all() for p in model.parameters())
    # Test very cheap ticks, expensive goods, depleted goods, zero or very small cash.
    anchors = [1, 4, 20, 391, 60000, 100000]
    inventory = [0,1,2,50,100,1500]
    for spend in (0,1,5,126,10000):
        for _ in range(50):
            acts, _, _ = model.decide(cash.detach(),[h.detach() for h in heads])
            for cap, bids in zip(acts[0].tolist(),acts[1].tolist()):
                book = orders(cap,bids,anchors,inventory,spend)
                cost = sum((o.price_ticks*o.quantity + 99)//100 for o in book if o.side=="buy")
                assert cost<=spend
                for g,available in zip(GOODS,inventory):
                    buy = [o.price_ticks for o in book if o.good==g and o.side=="buy"]
                    sell = [o.price_ticks for o in book if o.good==g and o.side=="sell"]
                    assert buy==sorted(buy,reverse=True) and sell==sorted(sell)
                    assert not buy or not sell or max(buy)<min(sell)
                    assert sum(o.quantity for o in book if o.good==g and o.side=="sell")<=available
    print("Dealer model:",sum(p.numel() for p in model.parameters()),"parameters; replay and booking passed")


def gate_checks():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);work=root/"work";out=root/"gate";work.mkdir();out.mkdir()
        def batch(n, g):
            name=f"games-{n:04}"
            pt=work/(name+".pt");pt.write_bytes(name.encode())
            row=dict(turns=100,seats=[dict(nominal_gdp=g)]*4,final_nominal_gdp=g)
            save(work/(name+".json"),dict(games=n,initial_sha256="origin",sha256=sha(pt),metrics=[row]*4))
            save(root/"verified.json",dict(games=n,initial_sha256="origin"))
        for n in (4,8,12,16):batch(n,100)
        assert not step(work,out,None) and not (out/"candidate.json").exists()
        batch(20,104)
        assert not step(work,out,None)
        meta=json.loads((out/"candidate.json").read_text())
        assert meta["behavior_games"]==16 and meta["screen_mean"]==101
        (work/"games-0016.pt").unlink()
        assert sha(out/"country.pt")==meta["sha256"]
        evaluator=root/"replay-fixture"
        evaluator.write_text("""#!/usr/bin/python3
import sys,json
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text())
score=101 if m['games']>=36 else 98
obj=dict(sha256=m['sha256'],size=24,turns=100,seeds=list(range(m['seeds_start'],m['seeds_start']+32)),final_nominal_gdp_by_seat=[[110]*4]*16+[[score]*4]*16)
Path(sys.argv[3]).write_text(json.dumps(obj))
""")
        evaluator.chmod(0o755)
        assert not step(work,out,evaluator) and (out/"failed-0020.json").is_file()
        for n in (24,28,32):batch(n,104)
        assert not step(work,out,evaluator) and not (out/"candidate.json").exists()
        batch(36,104)
        assert step(work,out,evaluator)
        result=json.loads((out/"qualified.json").read_text())
        assert result['holdout_means']==[110,101] and result['behavior_games']==32
        assert step(work,out,None)
        print("Checkpoint pin, retry spacing, strict threshold and two-block qualification passed")


if __name__=="__main__":
    model_checks()
    gate_checks()
