# Zhanguo — legacy

This branch preserves the previous monolithic `main`. Current development is on the modular [main branch](https://github.com/northwindlight/zhanguo/tree/main). Use legacy to inspect historical rules, compare real games, or work with the corresponding old save versions. [中文](README.md)

Zhanguo is a turn-based grand strategy game for LLM players. Nations manage resources, construction, armies, diplomacy and wars through tool calls, while humans observe or send announcements and letters.

## Real gameplay collection

The [research directory](research/game-records/README.md) contains complete supplied saves and original journals from two actual games:

| Game | Saved turn | Models |
| --- | --- | --- |
| Eight nations, completed, seed 20260921 | 258 | `qwen3.8flash` and DeepSeek |
| Eight nations, snapshot, seed 20261009 | 158 | `qwen3.8flash` |

The human player confirmed that the completed game used DeepSeek too. Some reasoning signatures contain `deepseek-flash[1m]`; a complete model-switching timeline is not inferred. Files retain their original bytes, with source filenames, a manifest and SHA-256 checksums. These are research records, not a controlled single-model benchmark.

Both saves come from the old World v4 code family. Use the appropriate historical commit when resuming them; current modular main uses a different save format. Journals can include repeated or unfinished turns; the JSON `turn` field determines the saved turn.

## Historical implementation

`game.py` and `mp.py` implement the world and game domains; `balance.py` and `mapgen.py` provide rules and maps; `mp_ai.py`, `ctx.py` and `llm_provider.py` manage LLM actions and memory. Rule players live in `ruleai/`; `mp_run.py`, `console.py` and `render.py` provide the host and observer interface.

```bash
git switch legacy
cp mp_config.example.json mp_config.json
# Configure model access or use rule players.
./start.sh --config mp_config.json --new
```

Use `start.ps1` on Windows. Omit `--new` to resume, and use your own save path. Keep the published research snapshots as reference copies. Private API configuration is not included.

Historical manuals are linked from the [Chinese README](README.md). Independent distributions, the unified Player API, council review, inventory markets and the Python sandbox belong to current main.
