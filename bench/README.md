# stock-bench/1 — a deterministic, auditable harness for the stock distribution

`stock-bench` wraps one stock episode into three commands: **run**, **verify**, **score**.
No LLM, no network, no paid API is needed to run the harness itself: every seat is a
restricted-Python policy executed by the in-engine sandbox, exactly like a model-submitted
script.

This is the Layer-1 (reading) half of the evaluation protocol described in
[`research/papers/20261116-stock-agent-benchmark/`](../../research/papers/20261116-stock-agent-benchmark/README.md):
one episode, one absolute score, a full ledger, and a replay hash anyone can recompute.

## Quick start

```bash
# 1. one episode (scaled protocol: 4000 ticks/day, 8 rounds — a smoke test, not a result)
python bench/run_eval.py run --spec bench/episodes/quick.json --name smoke

# 2. recompute the same episode and compare hashes
python bench/run_eval.py verify --result bench/results/<file>.json

# 3. print an archived result
python bench/run_eval.py score --result bench/results/<file>.json
```

Requirements: Python 3.11+ with `pams==0.3.0` installed (the stock distribution refuses to
start otherwise), and the repository root on `sys.path` (the wrapper handles this).

## What an episode is

A spec is a JSON file:

```json
{
  "protocol": "stock-bench/1",
  "seed": 2026111303,
  "ticks_per_day": 20000,
  "turns": 60,
  "language": "en-US",
  "seats": [
    {"name": "cash", "policy": "../policies/cash.py"},
    {"name": "hold", "policy": "../policies/hold.py"},
    {"name": "mom",  "policy": "../policies/momentum.py"}
  ]
}
```

* `seed` — the world seed. Every episode of one evaluation round should share the same seed
  (all seats face the same exam; see the protocol section of the paper).
* `ticks_per_day` / `turns` — the full protocol is `20000` ticks/day and `60` rounds
  (round 60 settles only). Anything smaller is a **scaled protocol**: useful for pipeline
  tests and mechanism probes, never for headline numbers.
* `seats` — 1 to 4 seats. A seat is defined in one of three ways:
  * `"policy": "path.py"` — one script in force from round 1 on;
  * `"sources": {"1": "a.py", "25": "b.py"}` — rewrite the policy at chosen rounds
    (a model-submitted script would land here);
  * `"scripts": {"1": "<source text>"}` — inline source.
* `language` — `zh-CN` (default) or `en-US`; affects only the prose the model reads
  (briefing and tool descriptions), never the world state, ledger or hashes.
* `purchases` / `purchase_pool` — optional per-round resource purchases
  (compute pool tiers, trading slots), charged from in-game cash like any player action.

Policies are the same language the models write: `def on_tick(): ...` with
`api.call('stock:market', 'buy', symbol=..., quantity=...)`. There is no separate DSL and no
host Python escape hatch — the sandbox meters every step, so a chatty policy can exhaust its
per-callback budget (a real outcome, recorded in the result as `script.status = failed`).

## What a result contains

| Field | Meaning |
|---|---|
| `score.index` | mean final liquidation cash of the seats, as an index (100 = initial 100,000) |
| `score.mean_final_cash` | the same number in cash |
| `seats[]` | per seat: `final_cash`, `final_rank`, `realized_pnl`, the five cost items, turnover, fees, fill counts by side, end holdings, equity curve, and the last script status/error |
| `checks[]` | per-seat accounting identity and `cash flow = realized_pnl`, both must be zero-residual |
| `audit` | `ledger_hash`, `fill_hash`, `final_world_hash`, per-round world hashes |
| `beta` | equal-weight buy-and-hold on this seed — a **disclosure**, not a denominator |
| `engine` | distribution, PAMS version, Python version, engine commit |

`verify` re-runs the spec stored in the result and compares `spec_hash`, `ledger_hash`,
`fill_hash`, `final_world_hash` and `score`. Byte-identical means the episode is reproducible
by a third party; any mismatch means the engine, the policies or the harness changed.

## Bundled policies

| File | Behaviour |
|---|---|
| `policies/cash.py` | never trades (inaction baseline) |
| `policies/hold.py` | equal-weight buy and hold on the first trading day |
| `policies/momentum.py` | once per day, buy the strongest mover and sell the weakest holding |

These are **not** a scale: they are constants, and a constant's reading says nothing about the
resolution of the ruler (see the paper's §3.3). Use them to check the harness and to expose
rule loopholes, never as an upper bound, a lower bound or a normalising denominator.

## Honest status

* Determinism is verified for *given scripts*; model sampling is a separate matter.
* The settlement price is currently the last traded price (±0.1%), and a dose probe shows a
  small order can move it (mark-the-close). Until the settlement rule moves to a VWAP/TWAP
  window, this harness must not be advertised as "un-gameable".
* Cost of the evaluation in tokens/dollars is **not** part of the score (in-game cash cost is).
