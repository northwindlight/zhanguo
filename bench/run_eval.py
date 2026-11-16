"""stock-bench/1 入口：python bench/run_eval.py <run|verify|score> ...

不依赖仓库根作为 cwd：脚本会把自己的目录加进 sys.path。
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from stock_eval.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
