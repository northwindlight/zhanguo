# Zhanguo

Zhanguo is a turn-based grand strategy game built around LLM decision-making. Nations manage their economies, armies and diplomacy in a shared world. Human observers can follow long games and study decisions, failures and memory changes.

The current `main` is the modular implementation, formerly the `正规化` branch. The previous monolithic implementation is preserved on [legacy](https://github.com/northwindlight/zhanguo/tree/legacy). [中文](README.md)

## Real gameplay records

The [research collection](research/game-records/README.md) contains complete supplied saves and unabridged original journals:

| Game | Saved turn | Models |
| --- | --- | --- |
| Eight nations, completed, seed 20260921 | 258 | `qwen3.8flash` and DeepSeek |
| Eight nations, snapshot, seed 20261009 | 158 | `qwen3.8flash` |

Model information comes from the human player. Some saved reasoning signatures in the completed game identify `deepseek-flash[1m]`; no complete per-turn model-switching timeline is inferred. Both branches contain the same collection, with SHA-256 checksums and a manifest. These saves belong to the legacy engine and cannot be loaded by current main.

## Modular implementation

Distributions define games; the host loads their sessions; clients act through the same actor-bound Player API; UI Mods present the resulting facts.

The default `native` distribution implements economic, military, diplomatic and intelligence domains. Generic sessions do not require a map; square-grid distributions can reuse `GridSession`. LLM and v11plus rule clients share structured requests and receipts. Independent UI Mods provide prompts, language catalogs and visible text/SVG/PNG maps.

The optional [experimental distribution](mods/experimental/README.md), currently `0.4.0-beta.1`, adds two-phase council review and monarch replacement, finite-inventory market feedback, and saved Python routines with deterministic work budgets and one execution opportunity per nation per turn. Scripts use the standard API, and successful actions survive a later script error. It also enables [research combat probabilities](mods/experimental/combat_research/README.md) exclusively for rule clients, with optional C acceleration; no probability tools are exposed to LLM clients.

## Run

```bash
cp mp_config.example.json mp_config.json
# Configure nations, clients and model access.
./start.sh --config mp_config.json --new
./start.sh --config mp_config.json
```

Windows users can use `start.ps1`. The launchers create a local virtual environment and install dependencies. Configure stable nation IDs and explicit `client` values: `rule` for offline native games, or `llm` for model access. OpenAI-compatible and Anthropic protocols are supported. For experimental, use its [configuration template](mp_config.experimental.example.json) and configure council model access too.

Use separate save and journal paths for separate games. `--new` overwrites the selected save; `--turns` specifies the target cycle. Saves use `zhanguo.session/2` and require a complete cycle boundary. Before 1.0, old-save compatibility and automatic migration are not provided; mismatched module dependencies are rejected.

Generate the eight-nation scenario with `python -m scenarios.eight_nations --seed 20260921`. Observer commands include `add`, `send`, `say`, `board`, `map` and `quit`. See the [Chinese README](README.md) for detailed usage.

## Development

Start with the [Mod guide](docs/Mod开发指南.md), [Session contract](docs/发行版Session.md), [Player API](docs/客户端接线.md), [native modules](mods/native/README.md), [UI Mod](mods/ui/README.md), [sandbox](mods/experimental/SCRIPTING.md), and [roadmap](PLAN.md).

```bash
.venv/bin/python -m unittest discover -s tests -q
```

The latest code regression passed 405 offline tests using simulated model backends and synthetic worlds. The published real-game records are kept separate from automated test inputs.
