# stock — a deterministic economic testbed for long-horizon agents

**English first · 中文见下一节**

This package runs **only the `stock` distribution**: a four-seat exchange simulation in which
the agent cannot click "buy" — it submits a restricted Python strategy before each round, and a
deterministic engine executes it against a PAMS order book. Compute, trading slots, research,
intelligence and borrowing are all paid out of in-game cash, every round, so spending must
justify its own return. Final score is liquidation cash; the whole episode is auditable and
replayable byte for byte.

You do **not** need to spend any money to try it: offline policy seats run with no API key.

## 1. Requirements

* Python 3.11+ (3.12/3.13 tested)
* `pams==0.3.0` — `pip install -r requirements/stock.txt`
* Linux, macOS or Windows; no network needed for offline runs

## 2. First run (no API key, ~1 minute)

```bash
python bench/evaluate.py --client sim --seats 4 --turns 20 --ticks 4000
```

This runs an offline self-test, checks the ledger and writes
`bench/reports/<stamp>-offline/report.md`.

To run a deterministic episode with fixed policies instead:

```bash
python bench/run_eval.py run --spec bench/episodes/quick.json      # ~5 s
python bench/run_eval.py verify --result <the result path printed>  # must print VERIFIED
```

## 3. Run a real model evaluation

Any OpenAI-compatible endpoint works. Keys are read from the environment and never written to
disk or into the result files.

```bash
export OPENAI_API_KEY=sk-...
export STOCK_EVAL_BASE_URL=https://api.deepseek.com/v1
export STOCK_EVAL_MODEL=deepseek-v4-flash

# full protocol: 60 rounds × 20,000 ticks/day × 4 seats (tens of minutes, billed to you)
python bench/evaluate.py --seats 4 --seed 2026111303 --turns 60 --ticks 20000
```

Shorter and cheaper for a first look:

```bash
python bench/evaluate.py --seats 4 --turns 20 --ticks 4000 --out bench/reports/short
```

Mixed line-ups (a model against the offline baseline) are specified in a spec file — see
`bench/README.md` and `bench/episodes/full.json`; a seat can be `client: llm`,
`client: sim` (offline heuristic) or `client: replay` (a fixed policy).

### Output of one evaluation

| file | content |
|---|---|
| `report.md` | score, per-seat table, ledger checks, replay hashes, honest scope |
| `report.json` | the same as data — includes per-seat cost items, fills, equity curve, script status |
| `audit.txt` | one line per ledger check |

Exit code `0` means every ledger identity holds with zero residual. A non-zero exit means the
run is **not publishable**.

## 4. Interactive game (human or model at the console)

```bash
cp mp_config.stock.example.json mp_config.json   # then paste your key / choose models
python mp_run.py --config mp_config.json --language en-US
```

`--check-config` validates the file without starting a game. Save files let you resume:
`python mp_run.py --config mp_config.json` continues the saved episode.

## 5. What is being measured

* **Program-as-action**: no buy/sell tools exist; the only way to trade is to write a strategy.
* **Cost is in the score**: the pool tiers, slots, research runs, intel reports and borrow fees
  are deducted from cash, and reset every round.
* **Deterministic + auditable**: same seed + same scripts ⇒ identical ledger, fills and world
  hash. Any third party can re-run and compare hashes.
* **One exam, one number**: the published score is absolute liquidation cash (index, 100 =
  initial 100,000). It is not a confidence-interval ranking; retakes are optional and every
  published seed must be disclosed.

Known limits, stated up front: the settlement price is the last traded price, and a small
mark-the-close probe shows it can be moved — until the settlement rule moves to a VWAP window,
this package must not be advertised as "un-gameable". Token/dollar cost of the model is
reported separately and is not in the score. Price-level coupling between seats is currently
second order (execution-level coupling is first order).

## 6. Layout

```
bench/            evaluation harness: evaluate.py, run_eval.py, verify, policies, spec examples
engine/           deterministic runtime: script sandbox with step metering, mod loader
mods/stock/       the stock distribution (market, dynamics, portfolio, compute, intel, locales)
mods/native/tools tool-layer helpers the distribution reuses
presentation/     prose renderers and the zh-CN/en-US catalogs
mp_run.py         interactive/headless game host
```

---

# stock — 可复现的经济博弈测试环境（中文说明）

本包**只包含 stock 发行版**，不含桌面 UI 资产、其它玩法与研究档案。

* 四条席位共用一个 PAMS 订单簿；模型不能下单，只能在盘前提交受限 Python 策略；
* 算力池、交易槽、研究、情报、借贷全部从游戏内现金扣费，每回合归零重买；
* 终局按清算现金排名，全过程可审计、可逐字节重放；
* 分数是**一次考试的绝对成绩**（指数，100 = 初始 10 万），不是带置信区间的排行榜。

快速开始（不需要 API key）：

```bash
pip install -r requirements/stock.txt
python bench/evaluate.py --client sim --seats 4 --turns 20 --ticks 4000
```

接模型（任意 OpenAI 兼容端点，密钥只读环境变量，不落盘、不进结果）：

```bash
export OPENAI_API_KEY=sk-...
export STOCK_EVAL_BASE_URL=https://api.deepseek.com/v1
export STOCK_EVAL_MODEL=deepseek-v4-flash
python bench/evaluate.py --seats 4 --turns 60 --ticks 20000
```

交互模式：`cp mp_config.stock.example.json mp_config.json` 填好 key 后
`python mp_run.py --config mp_config.json --language zh-CN`。

已知边界：结算价用当日最后成交价，小额尾盘成交可推动它（mark-the-close 探针已量化），
修复方向是改成最后 N 个 tick 的 VWAP/TWAP；模型 token 成本不进分数；价格层耦合目前是二阶。
---

## Third-party notice

The matching engine is [PAMS](https://pypi.org/project/pams/0.3.0/) (Platform for Artificial
Market Simulations, EPL-1.0), used unmodified from PyPI and pinned in
`requirements/stock.txt`; it is installed by the reviewer, not redistributed in this archive.
The archive itself is licensed under the Mozilla Public License 2.0 (`LICENSE`).
