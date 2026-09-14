# Zhanguo · 战国

> **Every nation is governed by an LLM. Humans just brew tea and watch.** — A pure-text
> grand-strategy game built *for AI*, and a benchmark designed to test **long-horizon** ability.

[简体中文](README.md) · **English**

---

## Highlights

- 🎮 **AI governs, humans spectate** — N LLM agents rule N nations competing on one shared map.
  Each nation wields *exactly the same* toolset as a human player, with no backdoors:
  building cities, raising armies, trading, allying, backstabbing and suing for peace are all
  the model's own calls. You just watch them grow from 5 tiles to a tripartite world.
- 🧪 **Not just a game — a long-horizon benchmark** — resource production chains, energy gating,
  protracted wars, tool-calling discipline, cross-turn memory, diplomatic games and deception…
  far closer to real long-horizon decision-making than a Q&A leaderboard. Inspired by the
  CivRealm line of thinking, but pure-text, much lighter, and **zero reinforcement-learning dependency**.
- 🔁 **Fully reproducible** — the whole map is determined by `(seed, size)` (blue noise + density
  map modulation). **Same seed + same opening sequence → same map, same history.** Different models
  and configurations compete in identical conditions, and cross-process reproducibility is guarded by tests.
- 🪶 **Lightweight, runnable with zero API keys** — the only dependency is `openai`. Nations without
  keys are played by a **built-in rule AI**; one command on Linux / macOS / Windows.
- 🎛 **Tune balance in one file** — `balance.py` is the single point of control. The engine, economy
  reports, rule AI and the LLM panels all read from the *same objects* — change once, apply everywhere.
- ⚖️ **A metric designed for AI** — standings are ranked by "total consumption" (the skeleton of
  expenditure-side GDP), backed by **adversarial experiments** proving the metric cannot be gamed.
- 🚀 **A worldview that can evolve** — the `feat/rl` branch hosts a complete RL training pipeline
  (BC / PPO), with the rule AI doubling as the BC teacher.
- 🛰 **Long games don't burn context budget** — the context is laid out as
  «long-term memory → archive → recent full replay», and prefix-cache hits are tuned
  (recursive long-term memory + non-sliding periodic compaction + measured hit-rate logging);
  a 300-turn game only rebuilds the prefix once per compaction period.

## Quick start

```bash
./start.sh --turns 10        # Linux / macOS
.\start.ps1 --turns 10       # Windows
```

The first run automatically creates a virtualenv, installs the only dependency (`openai`), and
generates `mp_config.json` from the template. Fill in each nation's API config and start watching;
**leave an entry empty and that nation is played by the built-in rule AI** — playable with no key at all.

```bash
# Already have an environment, run directly:
pip install -r requirements.txt
python3 mp_run.py --turns 10    # resume from save; start fresh if none exists
```

- `Ctrl-C` saves the game at any point (always on a turn boundary); next time just run `./start.sh` to resume.
- Common flags `--new` (force a fresh game), `--config F` / `--save F`, `--ctx-window N` are passed
  straight through to `mp_run.py`.
- Config template lives in `mp_config.example.json`; the source code comments are authoritative for
  each key's meaning and default.

## What a game looks like

- **Turn-based** — nations act in turn, then all resolve together. Fixed settlement order:
  harvesting → power grid → war → supply → market → construction lands → economy report.
- **Economy** — gold / food / wood / ore / oil / gear / supply is a **single national ledger**;
  the power grid is national and cannot be stored — if there isn't enough power, power-hungry
  buildings shut down for the turn. What to build and what to sell is the model's own accounting.
- **Military** — three unit types (infantry / cavalry / militia); **movement is tile-by-tile with
  terrain costs**, mountains and forests are impassable; combat resolves by dice each turn with
  terrain and castle cover. Expansion requires fighting — there is no "colonize from thin air".
- **Diplomacy** — letters, war, peace, defensive pacts, guarantees, spies, alliances and betrayal;
  war can cascade through treaties without limit (A guarantees B, B allies C → attack B and C joins).
  After a nation falls, a global forced truce prevents rapid-fire conquest.
- **Fog of war & information discipline** — each nation only sees what it is entitled to know:
  its own territory, the adjacent ring, alliance-shared vision, watchtower range. The truth about
  other nations must be inferred from letters, border activity and espionage. Want their ledger?
  Send a spy and wait a few turns.
- **Where do the rules live?** — every number is concentrated in `balance.py` (single source of truth);
  in-game, nations can query rules anytime via the `rules` tool; the authoritative detail is the code
  docstrings (`mp.py` / `game.py` / `mapgen.py`).

## The AI layer

Each nation is an LLM agent interacting via function calling:

- **35 tools** cover query / domestic / military / diplomacy / alliance / memory / end-of-turn,
  and accept aliases in Chinese and English (`build`/`建造`…).
- 📇 **Memory search** — `memory_search` (alias 检索记忆 / 翻旧账) finds past snippets by keyword
  across your own history's **body text (no thinking)**, returning the **turn number and adjacent
  context**; together with the ever-present **plan** and the recursive long-term memory, a long
  game can actually remember promises and grudges.
- Events are filtered by the **vision snapshot at write time** — old battle reports from land you
  capture later never retroactively appear.
- Nations without keys are played by the built-in rule AI (`dummy_turn`), version-configurable
  (default `v10`), obeying the same vision discipline and never peeking at the map — competing on
  the same information as LLM players.

## Context & memory (cost & continuity of long games)

Context is assembled by **decreasing stability**, so prefix caching hits as much as possible:

```
[system] → [recursive long-term memory] → [history archive] → [recent full-round replay] → [current state]
```

- **Recursive long-term memory** — rounds sliding out of the replay are folded into a memory by one
  LLM call that **expands on the previous memory** (never drops old facts). It lives as a byte-stable
  head block and carries long-range plans / alliances / lessons — it only changes on compaction.
- **Non-sliding periodic compaction** (`ctx_roll="period"`) — between compactions the replay is
  **append-only, the prefix does not change one byte**; when `ctx_period` is unset the period is
  **auto-derived from the window geometry × measured per-turn size** (set a number to fix it).
  Cold turns drop from "every few rounds" to "once per period".
- **Measured, not guessed** — the `🧠` watch line shows the real rolling prefix-cache hit rate from
  the provider (last 20 requests); slide/compact turns are flagged "prefix cache rebuilt".
- **Memory trio** — ever-present **plan** + **recursive long-term memory** (`long_memory`) +
  **`memory_search`** (keyword search, returns turn + adjacent context).
- Related config (`mp_config.json`): `ctx_window` / `ctx_fill` / `ctx_slide_keep` /
  `ctx_roll` (`"period"`) / `ctx_period` / `ctx_slice_keep` / `ctx_old_reasoning`
  (default `"full"`; **stripping old thinking makes the model short-sighted — not recommended**) /
  `ctx_trim_tool_chars` / `ctx_compact`. Implementation: `ctx.py` + `mp_ai.py` docstrings.

## RL training line (`feat/rl` branch)

RL training is maintained **on a separate branch**: the PyTorch environment, BC / PPO and the
transformer backbone all live in `feat/rl`. That branch is **rebased onto main** — engine files are
byte-identical to main, so rule changes never need cherry-picking. Design docs are in that branch's
`rl/README.md` and `rl/PLAN.md`.

## Project layout

| File / Dir | Purpose |
|------------|---------|
| `balance.py` | **Numeric tables · single tuning entry**: buildings / terrain / units / market / diplomacy costs |
| `game.py` · `mp.py` | Shared rules layer · Multi-nation engine `World` (settlement / combat / diplomacy / alliances / vision / save) |
| `mapgen.py` | Map generation: `(seed, size)` → full terrain & resource map (blue noise + density modulation) |
| `mp_ai.py` | AI layer: 35 tools (incl. memory_search), system prompt, panels, provider-agnostic turn loop |
| `llm_provider.py` | LLM provider adaptation (OpenAI-compatible endpoints + reserved Anthropic slot) |
| `ctx.py` | Context-window management: token estimation, budget allocation, cache-friendly assembly (shrink / non-sliding periodic compaction / recursive long-term memory) |
| `mp_run.py` · `console.py` | Orchestrator · watch-terminal (Markdown rendering, CJK-width aware) |
| `settlement.py` | End-game settlement: total-consumption ranking + settlement chamber |
| `rule_ai.py` · `expand_rule_*.py` | Rule-AI registry · expansion heuristics across generations |
| `experiments/` · `docs/` | Metric probe experiments · human-side docs |
| `tests/` | Unit tests + cross-process determinism watchdog |

## Watching

Live terminal stream plus two artifacts: **`mp_journal.md`** (the full chronicle) and **`mp_map.txt`**
(world map, colored by nation). When the game ends, run `settlement.py` — every nation's AI enters
the "settlement chamber" with its **real in-game memory** to summarize the match and rate each other.

```bash
python3 -m unittest discover -s tests   # run tests
python3 settlement.py                   # end-game settlement (scoring + chamber)
```

## Docs

- [On the consumption-based evaluation metric](docs/消费总量评测指标论证.md) — why rank by "total
  consumption": first-principles argument + three deterministic controlled experiments + an adversarial experiment.
- [Map generation & resource distribution](docs/地图生成与资源分布.md) — human-side reference.

## License

[MPL-2.0](LICENSE) · Copyright (c) 2026 zhang feng