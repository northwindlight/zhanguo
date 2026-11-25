#!/usr/bin/env bash
# Start the stock distribution interactively (model seats or offline seats).
# Usage: ./start-stock.sh [--check|--language en-US|zh-CN] [extra mp_run.py flags]
set -euo pipefail
cd "$(dirname "$0")"

PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python

if [ ! -f mp_config.json ]; then
  cp mp_config.stock.example.json mp_config.json
  echo "created mp_config.json from the template — put your base_url / api_key / model in it,"
  echo "then run ./start-stock.sh again. (client \"sim\" seats need no key.)"
  exit 0
fi

if ! "$PY" -c "import pams" 2>/dev/null; then
  echo "pams==0.3.0 is missing: $PY -m pip install -r requirements/stock.txt" >&2
  exit 2
fi

LANG_ARG="${STOCK_LANGUAGE:-en-US}"
exec "$PY" mp_run.py --config mp_config.json --language "$LANG_ARG" "$@"
