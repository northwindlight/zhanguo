# zhanguo 0.2.0

Native 4.1.0 provides the game domains; engine Player API v1 provides actor-bound queries and structured action receipts. LLM and v11plus rule players are independent clients of the same API. Earlier rule players and the old v12 branch are retired.

Copy `mp_config.example.json` to your local configuration, set stable nation IDs and client kinds (`llm` or `rule`), then run `./start.sh --config mp_config.json --new`. Omit `--new` to resume. It explicitly overwrites the selected save; use separate paths for separate games. API credentials remain outside saves. Old monolithic saves require their historical code version.

The new host saves only complete cycle boundaries using `zhanguo.session/1`. Provider failures stop the run and preserve the latest complete save. LLM replay, reasoning signatures and recursive memory compression belong to the client. State input is JSON; old PNG vision input is not migrated.

See [the Chinese README](README.md), [player API and client contract](docs/客户端接线.md), and [native modules](mods/native/README.md).

Run synthetic regression tests with `.venv/bin/python -m unittest discover -s tests`. Historical monolithic tests are archived under `tests/retired/`; offline tests use simulated providers. Both DeepSeek protocols were reached in live probes; the user confirmed Claude completed live gameplay, save/resume, and wall-clock checks.
