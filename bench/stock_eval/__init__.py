"""stock-bench/1：确定性、可审计的 stock 评测封装。

入口见 `bench/run_eval.py` 或 `python -m stock_eval`（在 bench/ 目录下）。
"""
from .episode import PROTOCOL, INITIAL_CASH, digest, load_spec, run_episode, spec_hash  # noqa: F401
