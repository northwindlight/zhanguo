# Reviewer guide — from zero to a published score in six steps

This guide is written for someone who has never seen the project. Every step is copy-pasteable
on Linux, macOS or Windows (PowerShell notes at the end). Nothing here requires a paid API
until step 5.

---

## Step 0 · Unpack and install (2 minutes)

```bash
unzip stock-bench-<version>.zip
cd stock-bench-<version>
python3 -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -r requirements-stock.txt
```

`requirements-stock.txt` installs one package: `pams==0.3.0` (the matching engine). The stock
distribution refuses to start without it — that is a deliberate guard, not a missing dependency.

Sanity check:

```bash
python -c "import pams; print('pams ok')"
python bench/check_i18n.py        # English/Chinese prose coverage; should print ALL CHECKS PASS
```

---

## Step 1 · Confirm the engine is deterministic (30 seconds, no cost)

```bash
python bench/run_eval.py run --spec bench/episodes/quick.json --name quick
python bench/run_eval.py verify --result bench/results/<file printed above>.json
```

`verify` re-runs the stored spec and compares five fields. You must see:

```
OK   spec_hash
OK   ledger_hash
OK   fill_hash
OK   final_world_hash
OK   score
VERIFIED
```

If any line says `DIFF`, the episode is not reproducible on your machine — please report the
platform and the Python version instead of trusting any score.

---

## Step 2 · Run the offline self-test of the evaluator (1 minute, no cost)

```bash
python bench/evaluate.py --client sim --seats 4 --turns 20 --ticks 4000
```

This exercises the whole reporting path: episode → ledger checks → `report.md` / `report.json` /
`audit.txt`. The seats here are the engine's own offline heuristic client, **not** a model: the
number is meaningless as a capability statement, but `checks N/N ok` and the three hashes prove
the pipeline works.

---

## Step 3 · Point the evaluator at a model

Any OpenAI-compatible chat-completions endpoint works (DeepSeek, Qwen, MiniMax, vLLM, Ollama,
OpenAI itself, a corporate gateway…).

```bash
export OPENAI_API_KEY=sk-your-key
export STOCK_EVAL_BASE_URL=https://api.deepseek.com/v1
export STOCK_EVAL_MODEL=deepseek-v4-flash
python bench/evaluate.py --dry-run          # validates args without calling the API
```

Optional knobs: `--temperature`, `--max-tokens`, `--max-steps`, `--thinking`,
`--reasoning-effort`, `--language en-US|zh-CN`.

If your endpoint needs a different key variable:

```bash
python bench/evaluate.py --api-key "$MY_KEY" --base-url ... --model ...
```

Keys are used in memory only. `report.json` stores the spec with every key replaced by
`<redacted>`.

---

## Step 4 · A cheap first run (a few minutes, small bill)

```bash
python bench/evaluate.py --seats 4 --turns 20 --ticks 4000 --out bench/reports/first-look
```

Twenty rounds and 4,000 ticks per day is a **scaled** protocol: use it to confirm that the
model can write, patch and run strategies at all. Do not report its score as a result.

What to check in `bench/reports/first-look/report.md`:

* `checks 8/8 passed` — the ledger closes for every seat;
* the per-seat table — compute and intelligence costs are non-zero only if the model bought
  them, and every cost item is deducted from cash;
* `script status` lines in `report.json` — a seat with `failed` scripts is losing for a
  concrete reason (sandbox budget, Python subset, rejected tool call), which is diagnostic,
  not a scoring artefact.

---

## Step 5 · The full protocol (60 rounds × 20,000 ticks/day × 4 seats)

```bash
python bench/evaluate.py --seats 4 --seed 2026111303 --turns 60 --ticks 20000 \
    --out bench/reports/full-<model>
```

Cost scales with the model: expect tens of minutes to a couple of hours of model time and a
corresponding bill. The engine itself is CPU-only and runs in minutes; the wall clock is
dominated by model calls.

Publish:

* `bench/reports/full-<model>/report.md`
* the three hashes from `report.json`
* the seed and the protocol line exactly as printed
* the model id **and** the endpoint provider, temperature, `--max-steps` and token limits
* your own dollar/token cost, reported as a separate axis (it is not part of the score)

One session is one exam score. It is not a ranking with error bars. If you publish several
models, run them on the **same seed** (the default) so that every entrant faces the same paper;
retakes with other seeds are allowed but must be labelled and reported separately.

---

## Step 6 · Verify someone else's claim (no key needed)

```bash
python bench/run_eval.py verify --result their-report.json
```

`report.json` carries the complete spec (policies, seed, limits, redacted keys). `verify`
re-runs it from scratch and compares `spec_hash`, `ledger_hash`, `fill_hash`,
`final_world_hash` and `score`. This is the only reason to trust a number: not the author's
reputation, but a hash you can recompute.

For model seats, the *scripts the model wrote* are part of the spec if they were saved into it;
otherwise the honest claim is "the ledger of that recorded episode is intact", and the model's
next run is a new exam.

---

## Interactive play (optional)

```bash
cp mp_config.stock.example.json mp_config.json
# edit mp_config.json: base_url, api_key, model per seat; client can be "llm", "sim" or "replay"
python mp_run.py --check-config --config mp_config.json
python mp_run.py --config mp_config.json --language en-US
```

The console streams each seat's actions, the audit trail and a settlement ranking. `Ctrl-C`
is safe: the game saves at round boundaries and `mp_run.py` resumes from the same file.

---

## Windows / PowerShell notes

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-stock.txt
$env:OPENAI_API_KEY="sk-..."
$env:STOCK_EVAL_BASE_URL="https://api.deepseek.com/v1"
$env:STOCK_EVAL_MODEL="deepseek-v4-flash"
python bench\evaluate.py --seats 4 --turns 20 --ticks 4000
```

Use `python bench\...` (backslashes) and set environment variables with `$env:NAME` for the
current shell only.

---

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `stock requires pams==0.3.0` | run `pip install -r requirements-stock.txt` in the same interpreter used to launch |
| `no API key` | export `OPENAI_API_KEY` or pass `--api-key` |
| HTTP 400 / 401 from provider | wrong base URL or model id; check `--dry-run` output and the provider's OpenAI-compatible path (usually `/v1`) |
| a seat ends with `script failed: budget` | the policy exceeded the per-callback sandbox budget; that is a legitimate outcome (buy a compute tier, or write a cheaper script) |
| `verify` prints `DIFF` | engine/commit mismatch or a modified policy file; report platform + `git rev-parse --short HEAD` |
| round 60 shows no trading | by design: round 60 only settles |
