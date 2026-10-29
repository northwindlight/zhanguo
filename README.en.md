# Zhanguo

> Let models govern. Sit back and watch history unfold.

Zhanguo is a turn-based grand strategy game played by AI nations. Starting with a handful of tiles, each nation builds an economy, raises armies, exchanges letters, forms alliances and wages war. Humans watch plans meet reality: shortages, defeats, broken promises and unexpected recoveries.

You can put different models in the same world, mix them with built-in rule players, or run an entirely offline game.

[中文](README.md) · [Game manual, Chinese](mods/native/docs/游戏说明书.md) · [Real games](research/game-records/README.md) · [Developer documentation](docs/README.md)

## What happens in a game

- **An economy that must support its ambitions.** Factories consume food, ore, oil and electricity to produce supplies and equipment. Power plants need fuel; armies need upkeep and repairs.
- **Expansion takes time and coordination.** Land provides room to build, but national army totals do not tell you how many units can reach a particular front.
- **War under fog of war.** Players rely on their own vision, watchtowers, published reports, spies and correspondence. The human observer's full map is not given to the players.
- **Diplomacy changes who fights.** Guarantees, mutual defense and alliances can bring other nations into a war. Alliance membership also means voting, losing personal treaties and being unable to leave during wartime.
- **A history you can follow.** National plans, action results, economic reports and searchable records show how decisions affect later turns.

Nations take turns acting, then the world settles one complete round. Battles with defenders, diplomacy and construction have pending stages. A nation falls when it has no completed town hall left, even if it still owns other land.

## Watch an offline game first

The terminal game recommends **Python 3.13** (3.11/3.12 also supported). Create `mp_config.json` in the repository root:

```json
{
  "map_size": 24,
  "seed": 20261024,
  "max_turns": 300,
  "save": "mp_save.json",
  "journal": "mp_journal.md",
  "nations": [
    {"id": "scenario:qin", "name": "Qin", "client": "rule"},
    {"id": "scenario:chu", "name": "Chu", "client": "rule"}
  ]
}
```

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 mp_run.py --config mp_config.json --check-config
python3 mp_run.py --config mp_config.json --new --turns 10
python3 mp_run.py --config mp_config.json --turns 30
```

On Windows, use `python` instead of `python3` and activate with `.\.venv\Scripts\Activate.ps1`. `--turns` is the target round number: the final command continues the same game to round 30. **`--new` overwrites the selected save.** Give each new game separate save and journal paths.

New games default to v13. The [v14 joint economic planner](ruleai/v14/README.md) is an opt-in candidate (`"rule_ai": "v14"`). Pure economic results improved, while the combined recruitment and investment policy still needs validation before default promotion. It requires NumPy/SciPy, included in the new Windows package, and no model access. Existing saves retain their strategy; v11plus remains available. `./start.sh --offline` runs a separate zero-dependency developer example.

## Put models in charge

Copy the [configuration template](mp_config.example.json) for a new game. Set each model-controlled nation to `"client": "llm"` and fill in its `provider`, `base_url`, `api_key` and `model`. Keep other nations as `client=rule` if desired. OpenAI-compatible, OpenAI Responses and Anthropic protocols are supported, all implemented natively on the standard library (no vendor SDK).

On Linux or macOS:

```bash
./start.sh --config mp_config.json --check
./start.sh --config mp_config.json --new
```

The shell launcher prepares a local virtual environment and dependencies, including the v14 candidate solver. Windows users can use `.\start.ps1 --config mp_config.json --new`, or run `mp_run.py` in a prepared Python environment. Missing LLM credentials cause a configuration error. Credentials stay in the local configuration and are not written into game saves.

### The eight-nation scenario

Generate Qin, Wei, Han, Zhao, Yan, Qi, Chu and Zhou on a 24×24 map, using your own configuration as a template:

```bash
python3 -m scenarios.eight_nations --seed 20261024 --template mp_config.json
python3 mp_run.py --config mp_config.8国.20261024.json --check-config
python3 mp_run.py --config mp_config.8国.20261024.json
```

The generator creates a configuration and initial save, refusing to overwrite existing files. It uses the first LLM nation's client settings, or the first nation's settings when no LLM is present. Edit the generated roster to choose a different model for each nation. The final command continues the generated initial save.

The seven powers initially guarantee Zhou's independence. A sole surviving nation wins by conquest. Zhou also wins if Zhou and its special subjects cover all surviving nations, with at least five nations including Zhou. Those other members receive **preservation as subjects, neither victory nor defeat**. Joining Zhou does not immediately end the game. See the [scenario rules](mods/native/docs/游戏说明书.md#八国剧本).

## The observer's seat

The terminal shows national strength, maps, statements and action results. Reasoning is collapsed by default and retained in the journal. Enter `help` for the complete command list.

| Command | Purpose |
| --- | --- |
| `board` | World overview |
| `map` / `map Qin` | Full map / one nation's vision |
| `map world.svg` | Export a map; text and PNG are also supported |
| `thoughts` / `thoughts on` / `thoughts off` | Read recent reasoning / expand / collapse |
| `say Message` | Broadcast an observer announcement |
| `send Qin Message` | Send an observer letter to one nation |
| `pause` / `resume` | Pause after a complete round is saved / continue |
| `quit` | Finish the current round, save and exit |

Commands are processed between model calls. `Ctrl-C` interrupts immediately and keeps the latest complete save; unfinished actions may appear only in the journal. Back up the adjacent `<save>.audit/` directory with the save to retain archived history. For an offline scoreboard, run `python3 settlement.py --save mp_save.json --table`.

### Desktop view

![Desktop observer](docs/design/gui-live-actor-20261024.png)

The desktop prototype provides a full-window map, political colors that fade on zoom, busy residents, dense troop groups and overlay drawers. Country banners, camera follow and five playback speeds show individual actions, followed by end-of-round marching, production, upkeep and each battle. Run `python3 desktop.py` after installing Godot .NET, the .NET SDK and Python; see the [desktop guide](mods/ui/desktop/README.md) for exact requirements.

The start menu supports normal v13 rule games without gifted troops, and configurable LLM games. Model reasoning and prose use actual provider stream fragments; transparent half-body portraits keep their aspect ratio. Attaching to a running terminal process and standalone installers remain pending. See the [32-second window recording](docs/design/gui-live-battle-tour-20261024.mp4) and [implementation review](docs/reviews/GUI逐动作与聚落军团-20261024.md).

## Optional experiments

The default **native** game includes national plans, manuals and the finite-inventory market. It runs **without a council**. Briefing tips and prewar checks provide reminders while leaving decisions to the players.

The optional [experimental distribution](mods/experimental/README.md) retains council review. Initial plans take effect directly; revisions are reviewed against prior plans and actions. Rejection in three consecutive rounds triggers monarch replacement. It also provides saved Python routines and research combat probabilities available only to rule clients. Use its [configuration template](mp_config.experimental.example.json) and a separate save.

## Observing models

The [real-game collection](research/game-records/README.md) and [research articles](research/papers/README.md) document planning, execution, memory, diplomacy and failure. Results depend on prompts, tool descriptions, memory, reasoning budgets and opponents. Identical prompts do not ensure equal suitability for every model; a single game's outcome or subjective score is not a general capability ranking.

Each published game documents its source and version. Historical saves require matching code and module dependencies; current development builds do not automatically migrate them. The older monolithic implementation remains on [legacy](https://github.com/northwindlight/zhanguo/tree/legacy).

## Read more and contribute

Start with the [game manual](mods/native/docs/游戏说明书.md), [basic guide](mods/native/docs/基础指南.md), [economics](mods/native/docs/经济学手册.md), [warfare](mods/native/docs/战争手册.md) and [strategy](mods/native/docs/战略手册.md) handbooks. These documents are in Chinese and can also be requested inside the game.

For development, see the [documentation site](https://northwindlight.github.io/zhanguo/), [Mod guide](docs/Mod开发指南.md), [documentation index](docs/README.md) and [changelog](CHANGELOG.md). Rules live in `mods/native/`, model clients in `clients/`, rule players in `ruleai/`, and presentation in `mods/ui/` and `presentation/`. The terminal entry point is `mp_run.py`; the desktop entry point is `desktop.py`.

```bash
python3 -m unittest discover -s tests -q
python3 scripts/api_doc.py --check
python3 docs/site/build.py
```

The manual and strategy handbooks are maintained by hand; API reference pages are generated from code. Source release archives exclude the separate real-game collection.

[MPL-2.0](LICENSE)
